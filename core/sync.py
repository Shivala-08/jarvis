"""Sync Layer — decoupled export/reconcile for cross-device memory sync.

Phase C of the build manual. Handles the machine-state half of the sync:
- export_state_delta(): snapshot modified memories → JSONL in sync/export/
- ingest_pending_deltas(): reconcile incoming deltas from other devices

NEVER points Syncthing at live Qdrant/SQLite files. Always export → sync → ingest.

Synced folders (configure in Syncthing):
    1. vault/                    — Obsidian notes (human-readable, direct sync)
    2. sync/export/              — append-only state dumps (this module)

Usage:
    from core.sync import export_state_delta, ingest_pending_deltas, get_sync_status

    # On shutdown:
    export_state_delta(memory_engine)

    # On startup:
    ingest_pending_deltas(memory_engine)
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Where deltas live — Syncthing watches this folder
EXPORT_DIR = Path("sync/export")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# Track what was last exported to avoid duplicate work
_LAST_EXPORT_MARKER = EXPORT_DIR / ".last_export"


def _get_last_export_time() -> float:
    """Return the timestamp of the last successful export."""
    if _LAST_EXPORT_MARKER.exists():
        try:
            return float(_LAST_EXPORT_MARKER.read_text().strip())
        except (ValueError, OSError):
            return 0.0
    return 0.0


def _set_last_export_time(ts: float):
    """Record the timestamp of a successful export."""
    _LAST_EXPORT_MARKER.write_text(str(ts))


def export_state_delta(memory_engine: Any) -> dict:
    """Export memories modified since last export as a JSONL delta file.

    This is the ONLY way machine state leaves the machine. The delta file
    is a plain JSONL that Syncthing can sync to other devices.

    Args:
        memory_engine: An ADHDMemoryEngine instance with a .memory attribute
                       that supports .get_all() and .user_id.

    Returns:
        {"exported": int, "file": str} or {"exported": 0, "reason": str}
    """
    try:
        result = memory_engine.memory.get_all(
            filters={"user_id": memory_engine.user_id}
        )
        memories = result.get("results", [])
    except Exception as e:
        logger.error(f"Failed to fetch memories for export: {e}")
        return {"exported": 0, "reason": str(e)}

    if not memories:
        return {"exported": 0, "reason": "no memories to export"}

    # Filter to memories modified after last export
    last_export = _get_last_export_time()
    modified = []
    for mem in memories:
        # Mem0 stores metadata; check for updated_at or created_at
        meta = mem.get("metadata", {})
        updated_str = meta.get("updated_at") or meta.get("timestamp") or ""
        try:
            if updated_str:
                # Parse ISO timestamp
                updated_dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                updated_ts = updated_dt.timestamp()
            else:
                updated_ts = 0.0
        except (ValueError, TypeError):
            updated_ts = 0.0

        # Include if modified after last export, or if we've never exported
        if last_export == 0.0 or updated_ts > last_export:
            modified.append(mem)

    if not modified:
        return {"exported": 0, "reason": "no new modifications since last export"}

    # Write delta file
    now = int(time.time())
    export_path = EXPORT_DIR / f"delta_{now}.jsonl"
    try:
        with open(export_path, "w") as f:
            for record in modified:
                # Ensure each record is JSON-serializable
                line = json.dumps(record, default=str)
                f.write(line + "\n")
        _set_last_export_time(now)
        logger.info(f"Exported {len(modified)} memories to {export_path}")
        return {"exported": len(modified), "file": str(export_path)}
    except Exception as e:
        logger.error(f"Failed to write delta file: {e}")
        return {"exported": 0, "reason": str(e)}


def ingest_pending_deltas(memory_engine: Any) -> dict:
    """Ingest delta files delivered by Syncthing from other devices.

    Reads each delta_*.jsonl file in sync/export/, upserts the records
    into the local memory store, then renames the file to .ingested
    to prevent double-processing.

    Conflict strategy: last_write_wins (the delta from the other device
    is assumed to be newer).

    Args:
        memory_engine: An ADHDMemoryEngine instance with a .memory attribute
                       that supports .add() and .user_id.

    Returns:
        {"ingested_files": int, "records_upserted": int}
    """
    delta_files = sorted(EXPORT_DIR.glob("delta_*.jsonl"))
    if not delta_files:
        return {"ingested_files": 0, "records_upserted": 0}

    total_files = 0
    total_records = 0

    for delta_file in delta_files:
        records = []
        skipped = 0

        try:
            with open(delta_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        skipped += 1
                        logger.warning(f"Skipping corrupt line in {delta_file.name}")
        except Exception as e:
            logger.error(f"Failed to read {delta_file.name}: {e}")
            continue

        if not records:
            # Empty or all-corrupt — mark as ingested to avoid retry loop
            delta_file.rename(delta_file.with_suffix(".ingested"))
            total_files += 1
            continue

        # Upsert each record into the local memory store
        for record in records:
            memory_text = record.get("memory", "")
            if not memory_text:
                continue

            metadata = record.get("metadata", {})
            # Preserve the original timestamp for conflict resolution
            metadata["sync_source"] = "delta_import"
            metadata["sync_file"] = delta_file.name

            try:
                memory_engine.memory.add(
                    memory_text,
                    user_id=memory_engine.user_id,
                    metadata=metadata,
                )
                total_records += 1
            except Exception as e:
                logger.warning(f"Failed to upsert record: {e}")

        # Mark as ingested (don't delete — keep for audit trail)
        delta_file.rename(delta_file.with_suffix(".ingested"))
        total_files += 1
        logger.info(f"Ingested {len(records)} records from {delta_file.name}" +
                    (f" ({skipped} skipped)" if skipped else ""))

    return {"ingested_files": total_files, "records_upserted": total_records}


def get_sync_status() -> dict:
    """Get the current sync layer status.

    Returns info about pending deltas, last export time, and folder state.
    """
    delta_files = list(EXPORT_DIR.glob("delta_*.jsonl"))
    ingested_files = list(EXPORT_DIR.glob("*.ingested"))
    last_export = _get_last_export_time()

    return {
        "export_dir": str(EXPORT_DIR),
        "pending_deltas": len(delta_files),
        "completed_exports": len(ingested_files),
        "last_export_time": (
            datetime.fromtimestamp(last_export, tz=timezone.utc).isoformat()
            if last_export > 0
            else None
        ),
        "syncthing_folders": {
            "vault": "vault/ (Obsidian — direct sync)",
            "export": "sync/export/ (machine state — delta sync)",
        },
    }


def cleanup_old_ingested(max_age_days: int = 30) -> int:
    """Remove ingested delta files older than max_age_days.

    Keeps the audit trail but prevents unbounded disk usage.

    Returns:
        Number of files removed.
    """
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0

    for ingested_file in EXPORT_DIR.glob("*.ingested"):
        try:
            # Extract timestamp from filename: delta_1234567890.ingested
            stem = ingested_file.stem  # delta_1234567890
            ts_str = stem.replace("delta_", "")
            file_ts = float(ts_str)
            if file_ts < cutoff:
                ingested_file.unlink()
                removed += 1
        except (ValueError, OSError):
            continue

    return removed

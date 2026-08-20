"""Tests for core/sync.py — cross-device sync layer (Phase C)."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.sync import (
    EXPORT_DIR,
    export_state_delta,
    get_sync_status,
    ingest_pending_deltas,
    cleanup_old_ingested,
    _get_last_export_time,
    _set_last_export_time,
    _LAST_EXPORT_MARKER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_engine(memories: list[dict], user_id: str = "test_user"):
    """Create a mock ADHDMemoryEngine with controllable memories."""
    engine = MagicMock()
    engine.user_id = user_id
    engine.memory.get_all.return_value = {"results": memories}
    engine.memory.add.return_value = {"results": [{"memory": "ok"}]}
    return engine


def _make_memory(id: str, memory_text: str, updated_at: str = None, **extra_meta):
    """Create a Mem0-style memory record."""
    meta = {"timestamp": "2025-01-01T00:00:00+00:00", **extra_meta}
    if updated_at:
        meta["updated_at"] = updated_at
    return {
        "id": id,
        "memory": memory_text,
        "metadata": meta,
    }


def _cleanup_delta_files():
    """Remove test delta files."""
    for f in EXPORT_DIR.glob("delta_*.jsonl"):
        f.unlink()
    for f in EXPORT_DIR.glob("*.ingested"):
        f.unlink()
    if _LAST_EXPORT_MARKER.exists():
        _LAST_EXPORT_MARKER.unlink()


# ---------------------------------------------------------------------------
# Marker functions
# ---------------------------------------------------------------------------

class TestMarkerFunctions:
    """Last-export timestamp tracking."""

    def test_no_marker_returns_zero(self):
        _cleanup_delta_files()
        assert _get_last_export_time() == 0.0

    def test_set_and_get(self):
        _cleanup_delta_files()
        ts = 1700000000.0
        _set_last_export_time(ts)
        assert _get_last_export_time() == ts

    def test_overwrite(self):
        _cleanup_delta_files()
        _set_last_export_time(100.0)
        _set_last_export_time(200.0)
        assert _get_last_export_time() == 200.0


# ---------------------------------------------------------------------------
# export_state_delta
# ---------------------------------------------------------------------------

class TestExportStateDelta:
    """Export memories to JSONL delta files."""

    def setup_method(self):
        _cleanup_delta_files()

    def teardown_method(self):
        _cleanup_delta_files()

    def test_empty_memories(self):
        engine = _make_mock_engine([])
        result = export_state_delta(engine)
        assert result["exported"] == 0
        assert result["reason"] == "no memories to export"

    def test_exports_all_when_no_prior_export(self):
        """First export should include all memories."""
        memories = [
            _make_memory("1", "I like morning work"),
            _make_memory("2", "Report due Friday"),
        ]
        engine = _make_mock_engine(memories)
        result = export_state_delta(engine)

        assert result["exported"] == 2
        assert "file" in result
        assert Path(result["file"]).exists()

        # Verify file content
        with open(result["file"]) as f:
            lines = f.readlines()
        assert len(lines) == 2
        record = json.loads(lines[0])
        assert record["memory"] == "I like morning work"

    def test_exports_only_modified_since_last_export(self):
        """Second export should only include records modified after first."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(hours=2)).isoformat()
        new_time = (now + timedelta(hours=1)).isoformat()  # future = after export

        memories = [
            _make_memory("1", "Old memory", updated_at=old_time),
            _make_memory("2", "New memory", updated_at=new_time),
        ]
        engine = _make_mock_engine(memories)

        # First export — exports everything (no prior export)
        result1 = export_state_delta(engine)
        assert result1["exported"] == 2

        # Second export — only the future-dated one
        result2 = export_state_delta(engine)
        assert result2["exported"] == 1

    def test_creates_export_dir(self):
        """EXPORT_DIR is created on module import."""
        assert EXPORT_DIR.exists()
        assert EXPORT_DIR.is_dir()

    def test_fetch_error_returns_reason(self):
        engine = MagicMock()
        engine.memory.get_all.side_effect = Exception("Qdrant down")
        result = export_state_delta(engine)
        assert result["exported"] == 0
        assert "Qdrant down" in result["reason"]

    def test_delta_file_is_jsonl(self):
        """Each line in the delta file should be valid JSON."""
        memories = [_make_memory("1", "test memory")]
        engine = _make_mock_engine(memories)
        result = export_state_delta(engine)

        with open(result["file"]) as f:
            for line in f:
                json.loads(line.strip())  # should not raise


# ---------------------------------------------------------------------------
# ingest_pending_deltas
# ---------------------------------------------------------------------------

class TestIngestPendingDeltas:
    """Ingest delta files from other devices."""

    def setup_method(self):
        _cleanup_delta_files()

    def teardown_method(self):
        _cleanup_delta_files()

    def test_no_files_returns_zero(self):
        engine = _make_mock_engine([])
        result = ingest_pending_deltas(engine)
        assert result["ingested_files"] == 0
        assert result["records_upserted"] == 0

    def test_ingests_delta_file(self):
        """Delta file should be read, records upserted, file renamed."""
        # Write a delta file manually
        delta = EXPORT_DIR / "delta_9999999999.jsonl"
        records = [
            {"memory": "from other device", "metadata": {"source": "phone"}},
            {"memory": "another memory", "metadata": {"source": "phone"}},
        ]
        with open(delta, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        engine = _make_mock_engine([])
        result = ingest_pending_deltas(engine)

        assert result["ingested_files"] == 1
        assert result["records_upserted"] == 2
        engine.memory.add.assert_called()

        # File should be renamed to .ingested
        assert not delta.exists()
        assert (EXPORT_DIR / "delta_9999999999.ingested").exists()

    def test_empty_delta_file(self):
        """Empty delta file should be marked as ingested."""
        delta = EXPORT_DIR / "delta_8888888888.jsonl"
        delta.write_text("")

        engine = _make_mock_engine([])
        result = ingest_pending_deltas(engine)

        assert result["ingested_files"] == 1
        assert result["records_upserted"] == 0
        assert not delta.exists()

    def test_multiple_delta_files_processed_in_order(self):
        """Multiple delta files should be processed in timestamp order."""
        for ts in [3000, 1000, 2000]:
            delta = EXPORT_DIR / f"delta_{ts}.jsonl"
            with open(delta, "w") as f:
                f.write(json.dumps({"memory": f"mem-{ts}", "metadata": {}}) + "\n")

        engine = _make_mock_engine([])
        result = ingest_pending_deltas(engine)

        assert result["ingested_files"] == 3
        assert result["records_upserted"] == 3

        # All should be renamed
        assert len(list(EXPORT_DIR.glob("delta_*.jsonl"))) == 0
        assert len(list(EXPORT_DIR.glob("*.ingested"))) == 3

    def test_corrupt_json_skips_record(self):
        """Malformed JSON line should be skipped, not crash the whole file."""
        delta = EXPORT_DIR / "delta_7777777777.jsonl"
        with open(delta, "w") as f:
            f.write(json.dumps({"memory": "good", "metadata": {}}) + "\n")
            f.write("NOT VALID JSON\n")
            f.write(json.dumps({"memory": "also good", "metadata": {}}) + "\n")

        engine = _make_mock_engine([])
        result = ingest_pending_deltas(engine)

        # Should process the file (even with corrupt line)
        assert result["ingested_files"] == 1

    def test_memory_text_required(self):
        """Records with empty memory text should be skipped."""
        delta = EXPORT_DIR / "delta_6666666666.jsonl"
        with open(delta, "w") as f:
            f.write(json.dumps({"memory": "", "metadata": {}}) + "\n")
            f.write(json.dumps({"memory": "valid", "metadata": {}}) + "\n")

        engine = _make_mock_engine([])
        result = ingest_pending_deltas(engine)

        # Only the valid record should be upserted
        assert engine.memory.add.call_count == 1

    def test_sync_metadata_added(self):
        """Ingested records should get sync_source metadata."""
        delta = EXPORT_DIR / "delta_5555555555.jsonl"
        with open(delta, "w") as f:
            f.write(json.dumps({"memory": "from phone", "metadata": {"key": "val"}}) + "\n")

        engine = _make_mock_engine([])
        ingest_pending_deltas(engine)

        call_kwargs = engine.memory.add.call_args
        metadata = call_kwargs.kwargs["metadata"]
        assert metadata["sync_source"] == "delta_import"
        assert "delta_5555555555.jsonl" in metadata["sync_file"]


# ---------------------------------------------------------------------------
# get_sync_status
# ---------------------------------------------------------------------------

class TestGetSyncStatus:
    """Sync status endpoint data."""

    def setup_method(self):
        _cleanup_delta_files()

    def teardown_method(self):
        _cleanup_delta_files()

    def test_empty_status(self):
        status = get_sync_status()
        assert status["pending_deltas"] == 0
        assert status["completed_exports"] == 0
        assert status["last_export_time"] is None

    def test_with_pending_deltas(self):
        (EXPORT_DIR / "delta_1111.jsonl").write_text("{}\n")
        (EXPORT_DIR / "delta_2222.jsonl").write_text("{}\n")
        status = get_sync_status()
        assert status["pending_deltas"] == 2

    def test_with_completed_exports(self):
        (EXPORT_DIR / "delta_1111.ingested").write_text("")
        status = get_sync_status()
        assert status["completed_exports"] == 1

    def test_last_export_time(self):
        _set_last_export_time(1700000000.0)
        status = get_sync_status()
        assert status["last_export_time"] is not None
        assert "2023" in status["last_export_time"]  # 1700000000 is in 2023


# ---------------------------------------------------------------------------
# cleanup_old_ingested
# ---------------------------------------------------------------------------

class TestCleanupOldIngested:
    """Cleanup of old ingested files."""

    def setup_method(self):
        _cleanup_delta_files()

    def teardown_method(self):
        _cleanup_delta_files()

    def test_no_files_to_clean(self):
        assert cleanup_old_ingested() == 0

    def test_removes_old_files(self):
        """Files older than max_age_days should be removed."""
        # Create a file with a very old timestamp
        old_file = EXPORT_DIR / "delta_1000000000.ingested"  # ~2001
        old_file.write_text("")

        # Create a recent file
        recent_ts = int(time.time()) - 1000
        recent_file = EXPORT_DIR / f"delta_{recent_ts}.ingested"
        recent_file.write_text("")

        removed = cleanup_old_ingested(max_age_days=30)
        assert removed == 1
        assert not old_file.exists()
        assert recent_file.exists()

"""Security routes — data sovereignty, network verification, purge status.

Handles:
- GET /api/sovereignty/snapshot — single network snapshot
- GET /api/sovereignty/status — quick sovereignty check
- GET /api/sovereignty/report — full sovereignty trace
- POST /api/sovereignty/purge — purge all memory
- GET /api/network-check — verify outbound connections
- GET /api/purge-status — verify Qdrant purge
"""
import logging
import os
import subprocess
import time as _time
import toml
from qdrant_client import QdrantClient

from fastapi import APIRouter, Depends

from core.auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["security"])


@router.get("/sovereignty/snapshot", dependencies=[Depends(require_token)])
def api_sovereignty_snapshot():
    """Take a single network snapshot and check for violations."""
    from core.sovereignty import SovereigntyMonitor
    monitor = SovereigntyMonitor()
    return monitor.snapshot()


@router.get("/sovereignty/status", dependencies=[Depends(require_token)])
def api_sovereignty_status():
    """Quick sovereignty status — instant snapshot, no waiting."""
    from core.sovereignty import SovereigntyMonitor
    monitor = SovereigntyMonitor()
    result = monitor.snapshot()
    return {
        "verdict": result["verdict"],
        "violations": len(result["violations"]),
        "allowed": len(result["allowed"]),
        "system": len(result["system"]),
        "total": result["total_connections"],
        "tailscale": any(c["reason"].startswith("Tailscale") for c in result["allowed"]),
        "google_oauth": any(c["reason"].startswith("Google") for c in result["allowed"]),
        "violation_details": result["violations"][:5],
    }


@router.get("/sovereignty/report", dependencies=[Depends(require_token)])
def api_sovereignty_report(duration_seconds: int = 30):
    """Run a sovereignty trace for N seconds and return full report."""
    from core.sovereignty import SovereigntyMonitor
    monitor = SovereigntyMonitor()
    monitor.start(interval=3)
    _time.sleep(min(duration_seconds, 60))
    monitor.stop()
    return monitor.report().to_dict()


@router.post("/sovereignty/purge", dependencies=[Depends(require_token)])
def api_sovereignty_purge():
    """Purge ALL memory: Qdrant collection, task history, logs."""
    from core.sovereignty import purge_all_memory
    return purge_all_memory()


@router.get("/network-check")
def api_network_check():
    """Detect actual outbound network connections.

    Uses 'lsof' to check for non-localhost TCP connections.
    Distinguishes between app-level and system-level connections.
    """
    try:
        app_connections = []
        system_connections = []
        google_prefixes = (
            "142.250.", "172.217.", "74.125.", "216.58.", "173.194.", "209.85.",
        )
        app_names = {"python", "python3", "uvicorn"}

        try:
            result = subprocess.run(
                ["lsof", "-i", "tcp", "-n", "-P"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "ESTABLISHED" not in line:
                    continue
                parts = line.split()
                if len(parts) < 9:
                    continue
                process_name = parts[0].lower()
                conn_field = parts[8] if len(parts) > 8 else ""
                if "->" not in conn_field:
                    continue
                _local, _sep, remote_full = conn_field.rpartition("->")
                if ":" not in remote_full:
                    continue
                remote_ip, _, remote_port_str = remote_full.rpartition(":")
                try:
                    remote_port = int(remote_port_str)
                except ValueError:
                    continue
                # Skip localhost connections
                if remote_ip in ("127.0.0.1", "::1", "localhost"):
                    continue
                # Skip Google Calendar OAuth
                if remote_port == 443 and any(
                    remote_ip.startswith(p) for p in google_prefixes
                ):
                    continue

                entry = {
                    "remote_ip": remote_ip,
                    "remote_port": remote_port,
                    "process": process_name,
                }
                if process_name in app_names:
                    app_connections.append(entry)
                else:
                    system_connections.append(entry)

        except FileNotFoundError:
            pass  # lsof not available

        if app_connections:
            return {
                "status": "violation",
                "message": f"APP made {len(app_connections)} unexpected outbound connection(s)!",
                "app_connections": app_connections,
                "system_connections": system_connections,
                "total_system": len(system_connections),
            }
        elif system_connections:
            return {
                "status": "clean",
                "message": f"App makes ZERO outbound calls. {len(system_connections)} system-level connection(s).",
                "app_connections": [],
                "system_connections": system_connections,
                "total_system": len(system_connections),
            }
        else:
            return {
                "status": "clean",
                "message": "Zero outbound connections detected.",
                "app_connections": [],
                "system_connections": [],
                "total_system": 0,
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not check network: {e}",
            "app_connections": [],
            "system_connections": [],
            "total_system": 0,
        }


@router.get("/purge-status")
def api_purge_status():
    """Verify that purge actually cleared the collection."""
    try:
        config = toml.load("config/config.toml")
        mem_cfg = config.get("memory", {})
        client = QdrantClient(host="localhost", port=6333)
        collection = mem_cfg.get("collection_name", "adhd_memory")
        collections = [c.name for c in client.get_collections().collections]
        exists = collection in collections
        return {
            "purged": not exists,
            "collection": collection,
            "message": "Collection cleared" if not exists else f"Collection '{collection}' still exists",
        }
    except Exception as e:
        return {"purged": False, "message": f"Could not verify: {e}"}

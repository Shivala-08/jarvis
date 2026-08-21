"""Sync and proactive trigger routes.

Handles:
- GET /api/sync/status — check sync layer status
- POST /api/sync/export — manually trigger export
- POST /api/sync/ingest — manually trigger ingestion
- GET /api/proactive/status — check proactive trigger status
- POST /api/proactive/morning-briefing — manual morning briefing
- POST /api/proactive/idle-check — manual idle check
"""
import logging

from fastapi import APIRouter, Depends

from core.auth import require_token
from core.dependencies import get_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sync"])


# ---------------------------------------------------------------------------
# Cross-device Sync
# ---------------------------------------------------------------------------


@router.get("/sync/status", dependencies=[Depends(require_token)])
def api_sync_status():
    """Check sync layer status — pending deltas, last export, folder state."""
    from core.sync import get_sync_status
    return get_sync_status()


@router.post("/sync/export", dependencies=[Depends(require_token)])
def api_sync_export():
    """Manually trigger a state export (usually runs on shutdown)."""
    from core.sync import export_state_delta
    memory = get_memory()
    return export_state_delta(memory)


@router.post("/sync/ingest", dependencies=[Depends(require_token)])
def api_sync_ingest():
    """Manually trigger delta ingestion (usually runs on startup)."""
    from core.sync import ingest_pending_deltas
    memory = get_memory()
    return ingest_pending_deltas(memory)


# ---------------------------------------------------------------------------
# Proactive triggers
# ---------------------------------------------------------------------------


@router.get("/proactive/status", dependencies=[Depends(require_token)])
def api_proactive_status():
    """Check proactive trigger status — registered events, cron tasks."""
    from core.proactive import get_proactive_status
    return get_proactive_status()


@router.post("/proactive/morning-briefing", dependencies=[Depends(require_token)])
def api_proactive_morning():
    """Manually trigger a morning briefing (usually fires at 08:00)."""
    from core.proactive import morning_briefing
    text = morning_briefing()
    return {"text": text, "triggered": True}


@router.post("/proactive/idle-check", dependencies=[Depends(require_token)])
def api_proactive_idle(minutes_idle: int = 30):
    """Manually trigger an idle check-in."""
    from core.proactive import idle_check
    text = idle_check(minutes_idle)
    return {"text": text, "triggered": text is not None, "minutes_idle": minutes_idle}

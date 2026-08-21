"""Memory management routes.

Handles:
- GET /api/memories — list all memories
- POST /api/memories/search — search memories
- POST /api/purge — purge all memories
"""
import logging

from fastapi import APIRouter, Depends

from core.auth import require_token
from core.dependencies import get_memory
from core.error_models import SearchRequest
from core.event_bus import EventType, publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["memory"])


@router.get("/memories")
def api_memories():
    """List all memories."""
    memory = get_memory()
    memories = memory.get_all_memories()
    return {"memories": memories, "count": len(memories)}


@router.post("/memories/search")
def api_memory_search(req: SearchRequest):
    """Search memories with latency tracking."""
    from core.eval_metrics import get_latency_tracker

    latency_tracker = get_latency_tracker()
    with latency_tracker.track("memory_search"):
        memory = get_memory()
        results = memory.retrieve_context_for_task(req.query)
    return {"results": results}


@router.post("/purge", dependencies=[Depends(require_token)])
def api_purge():
    """Purge all memories."""
    memory = get_memory()
    result = memory.purge_all()

    # Publish event
    publish(
        EventType.MEMORY_PURGED,
        {"status": result.get("status")},
        source="api",
    )

    return result

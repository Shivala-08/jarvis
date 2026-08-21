"""Brain dump and conversation routes.

Handles:
- POST /api/braindump — process raw text into structured tasks
- POST /api/conversation — multi-turn conversation with context
- GET /api/conversations — list all conversations
- GET /api/conversations/{id} — get conversation history
- DELETE /api/conversations/{id} — delete a conversation
"""
import asyncio
import logging
import threading
import time

from fastapi import APIRouter, Depends

from core.auth import require_token
from core.dependencies import (
    get_conversation_memory,
    get_memory,
    get_memory_svc,
)
from core.error_models import BrainDumpRequest, ConversationRequest
from core.event_bus import EventType, publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["braindump"])


@router.post("/braindump")
async def api_braindump(req: BrainDumpRequest):
    """Process a brain dump with latency tracking and conversation context."""
    from agents.braindump_agent import process_braindump
    from core.eval_metrics import get_latency_tracker

    # Build conversation context if conversation_id provided
    conv_context = ""
    if req.conversation_id:
        conv_mem = get_conversation_memory()
        conv_context = conv_mem.get_context(req.conversation_id)

    # Track latency — run LLM call in thread pool to avoid blocking
    latency_tracker = get_latency_tracker()
    with latency_tracker.track("braindump"):
        result = await asyncio.to_thread(
            process_braindump, req.text, context=conv_context if conv_context else None
        )

    # Store conversation turns
    if req.conversation_id:
        conv_mem = get_conversation_memory()
        conv_mem.add_turn(req.conversation_id, "user", req.text)
        summary = f"Captured {len(result.get('thoughts', []))} thoughts. {result.get('suggested_first_step', '')}"
        conv_mem.add_turn(req.conversation_id, "assistant", summary)

    # Run memory storage in background thread (don't block response)
    def _store():
        try:
            mem = get_memory()
            for thought in result.get("thoughts", []):
                mem.store_task(
                    thought["text"],
                    estimated_minutes=thought.get("estimated_minutes", 15),
                    priority=thought.get("priority", "soon"),
                )
            mem.capture_brain_dump(req.text, braindump_result=result)
        except Exception as e:
            logger.warning(f"Memory storage failed: {e}")

    threading.Thread(target=_store, daemon=True).start()

    # Publish event for background memory service
    publish(
        EventType.BRAINDUMP_COMPLETED,
        {"text": req.text, "result": result},
        source="api",
    )

    return result


@router.post("/conversation")
async def api_conversation(req: ConversationRequest):
    """Multi-turn conversation with context memory."""
    from agents.braindump_agent import process_braindump
    from core.eval_metrics import get_latency_tracker

    conv_id = req.conversation_id or f"conv_{int(time.time())}"
    conv_mem = get_conversation_memory()

    # Get conversation context
    context = conv_mem.get_context(conv_id)

    # Process with context — run LLM in thread pool to avoid blocking the event loop
    latency_tracker = get_latency_tracker()
    with latency_tracker.track("conversation"):
        result = await asyncio.to_thread(
            process_braindump, req.message, context=context if context else None
        )

    # Store turns
    conv_mem.add_turn(conv_id, "user", req.message)
    summary = f"Captured {len(result.get('thoughts', []))} thoughts. {result.get('suggested_first_step', '')}"
    conv_mem.add_turn(conv_id, "assistant", summary)

    # Store in semantic memory
    memory = get_memory()
    for thought in result.get("thoughts", []):
        memory.store_task(
            thought["text"],
            estimated_minutes=thought.get("estimated_minutes", 15),
            priority=thought.get("priority", "soon"),
        )

    publish(
        EventType.BRAINDUMP_COMPLETED,
        {"text": req.message, "result": result, "conversation_id": conv_id},
        source="conversation",
    )

    return {
        "conversation_id": conv_id,
        **result,
    }


@router.get("/conversations")
def api_list_conversations():
    """List all conversation IDs."""
    conv_mem = get_conversation_memory()
    ids = conv_mem.get_conversation_ids()
    return {
        "conversations": [
            {"id": cid, "stats": conv_mem.get_stats(cid)}
            for cid in ids
        ]
    }


@router.get("/conversations/{conversation_id}")
def api_get_conversation(conversation_id: str, limit: int = 20):
    """Get conversation history."""
    conv_mem = get_conversation_memory()
    turns = conv_mem.get_turns(conversation_id, limit=limit)
    return {
        "conversation_id": conversation_id,
        "turns": turns,
        "stats": conv_mem.get_stats(conversation_id),
    }


@router.delete(
    "/conversations/{conversation_id}",
    dependencies=[Depends(require_token)],
)
def api_delete_conversation(conversation_id: str):
    """Delete a conversation."""
    conv_mem = get_conversation_memory()
    deleted = conv_mem.delete_conversation(conversation_id)
    return {"deleted": deleted, "conversation_id": conversation_id}

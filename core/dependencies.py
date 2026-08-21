"""Shared FastAPI dependencies — lazy singletons and common patterns.

Eliminates duplicate singleton management and task-fetching logic
across route handlers. Each getter is thread-safe and lazy.
"""
import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-safe lazy singletons
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_memory = None
_memory_service = None
_conversation_memory = None
_task_tracker = None


def get_memory():
    """Get or create the ADHDMemoryEngine singleton."""
    global _memory
    if _memory is None:
        with _lock:
            if _memory is None:
                from memory.adhd_memory import ADHDMemoryEngine
                _memory = ADHDMemoryEngine()
    return _memory


def get_memory_svc():
    """Get or build the background memory service."""
    global _memory_service
    if _memory_service is None:
        with _lock:
            if _memory_service is None:
                from core.memory_service import build_memory_service
                memory = get_memory()
                _memory_service = build_memory_service(memory)
    return _memory_service


def get_conversation_memory():
    """Get or create the conversation memory store."""
    global _conversation_memory
    if _conversation_memory is None:
        with _lock:
            if _conversation_memory is None:
                from memory.adhd_memory import ConversationMemory
                _conversation_memory = ConversationMemory()
    return _conversation_memory


def get_task_tracker():
    """Get or create the task completion tracker."""
    global _task_tracker
    if _task_tracker is None:
        with _lock:
            if _task_tracker is None:
                from memory.adhd_memory import TaskCompletionTracker
                _task_tracker = TaskCompletionTracker()
    return _task_tracker


# ---------------------------------------------------------------------------
# Common data fetching patterns
# ---------------------------------------------------------------------------

def get_task_list() -> List[Dict[str, Any]]:
    """Fetch all tasks from memory — used by schedule, rebalance, calendar.

    Returns a list of task dicts with text, estimated_minutes, priority.
    """
    memory = get_memory()
    tasks = []
    for mem in memory.get_all_memories():
        meta = mem.get("metadata", {})
        if meta.get("type") == "task":
            tasks.append({
                "text": mem.get("memory", ""),
                "estimated_minutes": meta.get("estimated_minutes", 25),
                "priority": meta.get("priority", "soon"),
            })
    return tasks if tasks else [{"text": "No tasks yet", "estimated_minutes": 5, "priority": "soon"}]


def get_tasks_or_empty() -> List[Dict[str, Any]]:
    """Fetch tasks from memory, returning empty list if none exist."""
    memory = get_memory()
    tasks = []
    for mem in memory.get_all_memories():
        meta = mem.get("metadata", {})
        if meta.get("type") == "task":
            tasks.append({
                "text": mem.get("memory", ""),
                "estimated_minutes": meta.get("estimated_minutes", 25),
                "priority": meta.get("priority", "soon"),
            })
    return tasks


def stop_memory_services() -> None:
    """Gracefully stop all background services."""
    global _memory_service
    if _memory_service is not None:
        try:
            from core.memory_service import stop_memory_service
            stop_memory_service(_memory_service)
        except Exception as e:
            logger.warning(f"Failed to stop memory service: {e}")
        _memory_service = None

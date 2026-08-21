"""Schedule, rebalance, sprint, and task completion routes.

Handles:
- GET /api/schedule — build today's schedule
- POST /api/rebalance — rebalance after missed block
- POST /api/sprint — generate micro-sprint
- POST /api/tasks/start — record task start
- POST /api/tasks/complete — record task completion
- GET /api/tasks/completions — get completion history
- GET /api/tasks/alpha — get current time-scaling alpha
"""
import logging

from fastapi import APIRouter, Depends

from core.auth import require_token
from core.dependencies import get_task_list, get_task_tracker
from core.error_models import (
    RebalanceRequest,
    SprintRequest,
    TaskCompleteRequest,
    TaskStartRequest,
)
from core.event_bus import EventType, publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["schedule"])


@router.get("/schedule")
def api_schedule():
    """Build schedule with latency tracking."""
    from agents.scheduler_agent import build_schedule, _estimate_alpha
    from core.eval_metrics import get_latency_tracker
    from memory.adhd_memory import get_history

    latency_tracker = get_latency_tracker()
    with latency_tracker.track("schedule"):
        tasks = get_task_list()
        alpha = _estimate_alpha(get_history())
        schedule = build_schedule(tasks, alpha)

    return {"schedule": schedule, "alpha": alpha}


@router.post("/rebalance", dependencies=[Depends(require_token)])
def api_rebalance(req: RebalanceRequest):
    """Rebalance schedule with event publishing."""
    from agents.scheduler_agent import build_schedule, rebalance, _estimate_alpha
    from memory.adhd_memory import get_history

    tasks = get_task_list()
    alpha = _estimate_alpha(get_history())
    schedule = build_schedule(tasks, alpha)
    remaining, suggestion = rebalance(schedule, missed_block_id=req.missed_block_id, history=get_history())

    # Publish event
    publish(
        EventType.SCHEDULE_UPDATED,
        {"remaining_blocks": len(remaining), "suggestion": suggestion},
        source="api",
    )

    return {"schedule": remaining, "suggestion": suggestion}


@router.post("/sprint")
def api_sprint(req: SprintRequest):
    """Generate micro-sprint suggestion."""
    from agents.scheduler_agent import generate_micro_sprint
    suggestion = generate_micro_sprint(req.task)
    return {"suggestion": suggestion}


# ---------------------------------------------------------------------------
# Task completion tracking
# ---------------------------------------------------------------------------


@router.post("/tasks/start", dependencies=[Depends(require_token)])
def api_start_task(req: TaskStartRequest):
    """Record that a task has started."""
    tracker = get_task_tracker()
    estimated = req.estimated_minutes or 25
    result = tracker.start_task(req.task_text, estimated)
    return {"status": "started", **result}


@router.post("/tasks/complete", dependencies=[Depends(require_token)])
def api_complete_task(req: TaskCompleteRequest):
    """Record that a task has been completed."""
    tracker = get_task_tracker()
    record = tracker.complete_task(req.task_text, req.actual_minutes)

    publish(
        EventType.TASK_DURATION_RECORDED,
        {
            "task": req.task_text,
            "estimated": record.get("estimated_minutes"),
            "actual": req.actual_minutes,
            "ratio": record.get("ratio"),
        },
        source="api",
    )

    return {
        "status": "completed",
        **record,
        "alpha": tracker.get_alpha(),
    }


@router.get("/tasks/completions")
def api_get_completions(limit: int = 20):
    """Get task completion history."""
    tracker = get_task_tracker()
    return {
        "completions": tracker.get_completion_history(limit=limit),
        "stats": tracker.get_stats(),
    }


@router.get("/tasks/alpha")
def api_get_alpha():
    """Get the current time-scaling alpha."""
    tracker = get_task_tracker()
    return {
        "alpha": tracker.get_alpha(),
        "stats": tracker.get_stats(),
    }

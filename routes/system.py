"""System routes — health, diagnostics, dashboard, skills, scheduler, monitor.

Handles:
- GET /api/health — health check
- GET /api/diagnostics — model diagnostics
- GET /api/dashboard — performance dashboard
- GET /api/recommendations — performance recommendations
- GET /api/skills — list skills
- POST /api/skills/{name}/invoke — invoke a skill
- GET /api/scheduler/tasks — list scheduled tasks
- POST /api/scheduler/tasks — create scheduled task
- POST /api/scheduler/tasks/{id}/pause — pause task
- POST /api/scheduler/tasks/{id}/resume — resume task
- GET /api/monitor/stats — monitoring stats
"""
import logging

from fastapi import APIRouter, Depends

from core.auth import require_token
from core.dependencies import get_memory_svc, get_task_list
from core.error_models import ScheduledTaskRequest
from core.skill_manager import SkillManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def api_health():
    """Health check endpoint with system status."""
    from core.cron_scheduler import get_scheduler
    from core.dependencies import _memory_service

    mem_svc = get_memory_svc()
    scheduler = get_scheduler()
    return {
        "status": "ok",
        "service": "adhd-copilot",
        "version": "0.2.0",
        "components": {
            "memory_service": "active" if mem_svc and mem_svc.is_running else "lazy",
            "event_bus": "active",
            "skills": len(SkillManager.list_skills()),
            "scheduler": "active" if scheduler._running else "stopped",
        },
    }


@router.get("/diagnostics")
def api_diagnostics():
    """Retrieve model configurations and local AI tags diagnostics."""
    from core.diagnostics import run_model_diagnostics
    return run_model_diagnostics()


@router.get("/dashboard")
def api_dashboard():
    """Get performance dashboard with metrics."""
    from core.eval_metrics import get_dashboard
    dashboard = get_dashboard()
    return dashboard.get_dashboard(window_minutes=60)


@router.get("/recommendations")
def api_recommendations():
    """Get performance recommendations."""
    from core.eval_metrics import get_dashboard
    dashboard = get_dashboard()
    return {"recommendations": dashboard.get_recommendations()}


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills")
def api_skills():
    """List available skills and their statistics."""
    skills = SkillManager.list_skills()
    return {
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "type": skill.skill_type.value,
                "tags": skill.tags,
                "stats": skill.get_stats(),
            }
            for skill in skills
        ],
        "catalog": SkillManager.get_skill_catalog(),
    }


@router.post("/skills/{skill_name}/invoke", dependencies=[Depends(require_token)])
def api_invoke_skill(skill_name: str, args: dict = {}):
    """Invoke a skill by name."""
    try:
        result = SkillManager.invoke_skill(skill_name, **args)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Cron Scheduler
# ---------------------------------------------------------------------------


@router.get("/scheduler/tasks")
def api_list_scheduled_tasks():
    """List all scheduled tasks."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    tasks = scheduler.list_tasks()
    return {
        "tasks": [
            {
                "task_id": task.task_id,
                "prompt": task.prompt,
                "cron_expression": task.cron_expression,
                "agent_type": task.agent_type,
                "enabled": task.enabled,
                "last_run": task.last_run,
                "next_run": task.next_run,
                "run_count": task.run_count,
            }
            for task in tasks
        ]
    }


@router.post("/scheduler/tasks", dependencies=[Depends(require_token)])
def api_create_scheduled_task(req: ScheduledTaskRequest):
    """Create a new scheduled task."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    task = scheduler.create_task(
        task_id=req.task_id,
        prompt=req.prompt,
        cron_expression=req.cron_expression,
        agent_type=req.agent_type,
    )
    return {
        "task_id": task.task_id,
        "next_run": task.next_run,
        "status": "created",
    }


@router.post("/scheduler/tasks/{task_id}/pause", dependencies=[Depends(require_token)])
def api_pause_scheduled_task(task_id: str):
    """Pause a scheduled task."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    success = scheduler.pause_task(task_id)
    return {"success": success, "task_id": task_id}


@router.post("/scheduler/tasks/{task_id}/resume", dependencies=[Depends(require_token)])
def api_resume_scheduled_task(task_id: str):
    """Resume a scheduled task."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    success = scheduler.resume_task(task_id)
    return {"success": success, "task_id": task_id}


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


@router.get("/monitor/stats")
def api_monitor_stats():
    """Get monitoring statistics from the monitor operative."""
    from agents.monitor_operative import FocusMonitor, TaskMonitor
    focus_monitor = FocusMonitor()
    task_monitor = TaskMonitor()

    return {
        "focus": focus_monitor.get_focus_stats(),
        "tasks": task_monitor.get_task_stats(),
    }

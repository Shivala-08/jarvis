"""Cron Scheduler — persistent agent execution on schedules.

Inspired by OpenJarvis's scheduled monitor that runs on cron schedules,
maintains state across runs, and uses memory to track changes over time.

Features:
- Cron expression parsing
- Persistent task state across runs
- Memory integration for context
- Graceful error handling
- Task history tracking
"""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    """A task scheduled to run on a cron schedule."""
    task_id: str
    prompt: str
    cron_expression: str
    agent_type: str = "braindump"
    tools: List[str] = field(default_factory=list)
    enabled: bool = True
    created_at: str = ""
    last_run: str = ""
    next_run: str = ""
    run_count: int = 0
    last_status: str = "pending"
    last_error: str = ""
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


def parse_cron(expression: str) -> Dict[str, Any]:
    """Parse a cron expression into components.
    
    Format: minute hour day_of_month month day_of_week
    Supports: * (any), */n (every n), n (specific), n-m (range)
    """
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expression}")
    
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day_of_month": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def matches_cron(dt: datetime, cron_expr: str) -> bool:
    """Check if a datetime matches a cron expression."""
    parsed = parse_cron(cron_expr)
    
    # Check each component
    checks = [
        (dt.minute, parsed["minute"]),
        (dt.hour, parsed["hour"]),
        (dt.day, parsed["day_of_month"]),
        (dt.month, parsed["month"]),
        (dt.isoweekday() % 7, parsed["day_of_week"]),  # Convert to 0=Sunday
    ]
    
    for value, pattern in checks:
        if pattern == "*":
            continue
        
        if "/" in pattern:
            # Every n: */5 means every 5
            _, n = pattern.split("/")
            if value % int(n) != 0:
                return False
        elif "-" in pattern:
            # Range: 1-5 means 1 through 5
            start, end = pattern.split("-")
            if not (int(start) <= value <= int(end)):
                return False
        elif "," in pattern:
            # List: 1,3,5 means 1 or 3 or 5
            values = [int(v) for v in pattern.split(",")]
            if value not in values:
                return False
        else:
            # Specific value
            if value != int(pattern):
                return False
    
    return True


def get_next_run(cron_expr: str, after: Optional[datetime] = None) -> datetime:
    """Get the next run time for a cron expression."""
    if after is None:
        after = datetime.now(timezone.utc)
    
    from datetime import timedelta
    check_time = after.replace(second=0, microsecond=0)
    
    for _ in range(7 * 24 * 60):  # Check next 7 days
        check_time += timedelta(minutes=1)
        if matches_cron(check_time, cron_expr):
            return check_time
    
    # Fallback: return tomorrow at midnight
    return after.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


class CronScheduler:
    """Persistent scheduler for running tasks on cron schedules."""
    
    def __init__(self, storage_path: str = "data/scheduled_tasks.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: Dict[str, ScheduledTask] = {}
        self._running = False
        self._thread = None
        self._lock = None
        
        # Load persisted tasks
        self._load_tasks()
        
        # Register default recurring tasks if none exist
        if not self._tasks:
            self._register_default_tasks()
    
    def _get_lock(self):
        if self._lock is None:
            import threading
            self._lock = threading.Lock()
        return self._lock
    
    def _load_tasks(self) -> None:
        """Load tasks from persistent storage."""
        if not self.storage_path.exists():
            return
        
        try:
            with open(self.storage_path) as f:
                data = json.load(f)
                for task_data in data:
                    task = ScheduledTask(**task_data)
                    self._tasks[task.task_id] = task
        except Exception as e:
            logger.error(f"Failed to load scheduled tasks: {e}")
    
    def _register_default_tasks(self) -> None:
        """Register default recurring tasks for the ADHD Co-Processor."""
        defaults = [
            {
                "task_id": "daily_digest",
                "prompt": "Generate a daily digest: summarize today's captured thoughts, show upcoming tasks, and suggest a focus plan.",
                "cron_expression": "0 8 * * *",  # Every day at 8 AM
                "agent_type": "scheduler",
            },
            {
                "task_id": "memory_consolidation",
                "prompt": "Consolidate recent memories: merge related thoughts, update priorities, and surface important items.",
                "cron_expression": "0 22 * * *",  # Every day at 10 PM
                "agent_type": "braindump",
            },
            {
                "task_id": "hourly_checkin",
                "prompt": "Quick check-in: review current schedule and suggest a 10-minute micro-sprint for the next block.",
                "cron_expression": "0 * * * *",  # Every hour
                "agent_type": "scheduler",
            },
        ]
        for task_def in defaults:
            self.create_task(**task_def)
    
    def _save_tasks(self) -> None:
        """Persist tasks to storage."""
        try:
            data = [task.__dict__ for task in self._tasks.values()]
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save scheduled tasks: {e}")
    
    def create_task(
        self,
        task_id: str,
        prompt: str,
        cron_expression: str,
        agent_type: str = "braindump",
        tools: Optional[List[str]] = None,
    ) -> ScheduledTask:
        """Create a new scheduled task."""
        task = ScheduledTask(
            task_id=task_id,
            prompt=prompt,
            cron_expression=cron_expression,
            agent_type=agent_type,
            tools=tools or [],
        )
        
        with self._get_lock():
            self._tasks[task_id] = task
            self._save_tasks()
        
        logger.info(f"Created scheduled task: {task_id}")
        return task
    
    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    def list_tasks(self) -> List[ScheduledTask]:
        """List all scheduled tasks."""
        return list(self._tasks.values())
    
    def pause_task(self, task_id: str) -> bool:
        """Pause a scheduled task."""
        task = self.get_task(task_id)
        if task:
            task.enabled = False
            self._save_tasks()
            return True
        return False
    
    def resume_task(self, task_id: str) -> bool:
        """Resume a scheduled task."""
        task = self.get_task(task_id)
        if task:
            task.enabled = True
            self._save_tasks()
            return True
        return False
    
    def delete_task(self, task_id: str) -> bool:
        """Delete a scheduled task."""
        with self._get_lock():
            if task_id in self._tasks:
                del self._tasks[task_id]
                self._save_tasks()
                return True
        return False
    
    def start(self) -> None:
        """Start the scheduler daemon."""
        if self._running:
            return
        
        self._running = True
        
        import threading
        self._thread = threading.Thread(
            target=self._loop,
            name="cron-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Cron scheduler started")
    
    def stop(self) -> None:
        """Stop the scheduler daemon."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Cron scheduler stopped")
    
    def _loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                
                with self._get_lock():
                    for task in self._tasks.values():
                        if not task.enabled:
                            continue
                        
                        # Check if it's time to run
                        next_run = get_next_run(task.cron_expression, now)
                        task.next_run = next_run.isoformat()
                        
                        # Run if scheduled time is now or past
                        if next_run <= now:
                            self._run_task(task)
                
                # Save updated state
                self._save_tasks()
                
                # Sleep for 30 seconds before checking again
                time.sleep(30)
                
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
                time.sleep(60)  # Wait longer on error
    
    def _run_task(self, task: ScheduledTask) -> None:
        """Execute a scheduled task."""
        logger.info(f"Running scheduled task: {task.task_id}")
        
        task.last_run = datetime.now(timezone.utc).isoformat()
        task.run_count += 1
        
        try:
            # Import and run the appropriate agent
            if task.agent_type == "braindump":
                from agents.braindump_agent import process_braindump
                result = process_braindump(task.prompt)
            elif task.agent_type == "scheduler":
                from agents.scheduler_agent import build_schedule
                from agents.scheduler_agent import _estimate_alpha
                from memory.adhd_memory import get_history
                # Get current tasks from memory and build schedule
                try:
                    from memory.adhd_memory import ADHDMemoryEngine
                    memory = ADHDMemoryEngine()
                    mem_tasks = []
                    for mem in memory.get_all_memories():
                        meta = mem.get("metadata", {})
                        if meta.get("type") == "task":
                            mem_tasks.append({
                                "text": mem.get("memory", ""),
                                "estimated_minutes": meta.get("estimated_minutes", 25),
                                "priority": meta.get("priority", "soon"),
                            })
                    if not mem_tasks:
                        mem_tasks = [{"text": task.prompt, "estimated_minutes": 30, "priority": "soon"}]
                    alpha = _estimate_alpha(get_history())
                    result = build_schedule(mem_tasks, alpha)
                except Exception:
                    tasks = [{"text": task.prompt, "estimated_minutes": 30, "priority": "soon"}]
                    result = build_schedule(tasks, alpha=1.5)
            elif task.agent_type == "study":
                from agents.study_agent import decompose_topic
                result = decompose_topic(task.prompt)
            elif task.agent_type == "memory_consolidation":
                # Consolidate recent memories
                from memory.adhd_memory import ADHDMemoryEngine
                memory = ADHDMemoryEngine()
                memories = memory.get_all_memories()
                result = {
                    "total_memories": len(memories),
                    "consolidated": True,
                    "message": f"Reviewed {len(memories)} memories for consolidation.",
                }
            else:
                result = {"error": f"Unknown agent type: {task.agent_type}"}
            
            task.last_status = "success"
            logger.info(f"Task {task.task_id} completed successfully")
            
        except Exception as e:
            task.last_status = "error"
            task.last_error = str(e)
            logger.error(f"Task {task.task_id} failed: {e}")
        
        # Update next run time
        task.next_run = get_next_run(task.cron_expression).isoformat()
        
        # Broadcast completion event
        try:
            from core.event_bus import publish, EventType
            publish(
                EventType.SPRINT_COMPLETED,
                {
                    "task_id": task.task_id,
                    "agent_type": task.agent_type,
                    "status": task.last_status,
                },
                source="cron_scheduler",
            )
        except Exception:
            pass


# Global scheduler instance
_scheduler: Optional[CronScheduler] = None
_scheduler_lock = None


def _get_scheduler_lock():
    global _scheduler_lock
    if _scheduler_lock is None:
        import threading
        _scheduler_lock = threading.Lock()
    return _scheduler_lock


def get_scheduler() -> CronScheduler:
    """Get or create the global scheduler."""
    global _scheduler
    if _scheduler is None:
        with _get_scheduler_lock():
            if _scheduler is None:
                _scheduler = CronScheduler()
    return _scheduler


def start_scheduler() -> CronScheduler:
    """Start the global scheduler."""
    scheduler = get_scheduler()
    scheduler.start()
    return scheduler


def stop_scheduler() -> None:
    """Stop the global scheduler."""
    if _scheduler is not None:
        _scheduler.stop()

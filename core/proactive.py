"""Proactive Triggers — speech that initiates, not just responds.

Phase D.2 of the build manual. A scheduled/event-driven layer that can
generate proactive speech: morning briefings, missed-block nudges,
idle-time check-ins.

Design:
    - Trigger functions generate speech text (strings)
    - They publish to the event bus for delivery (WebSocket, TTS, notifications)
    - They register with the cron scheduler for time-based triggers
    - They subscribe to scheduler events for reactive triggers

Usage:
    from core.proactive import register_proactive_triggers, get_proactive_status

    # On startup:
    register_proactive_triggers()

    # The proactive system then:
    # - Fires morning briefing at 08:00 daily
    # - Speaks when a scheduled block is missed
    # - Check in after prolonged idle time
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event type for proactive speech
# ---------------------------------------------------------------------------

# We define a custom event type for proactive speech delivery.
# The PWA server / TTS pipeline subscribes to this.

PROACTIVE_SPEECH_EVENT = "proactive_speech"


def _publish_speech(text: str, source: str, context: Optional[Dict] = None):
    """Publish a proactive speech event for delivery.

    Args:
        text: The speech text to deliver.
        source: What triggered this (e.g., 'morning_briefing', 'missed_block').
        context: Optional metadata about the trigger.
    """
    try:
        from core.event_bus import publish, EventType

        # Use SPRINT_COMPLETED as the carrier event (it's already wired up)
        # The PWA server can listen for source="proactive" to distinguish
        publish(
            EventType.SPRINT_COMPLETED,
            {
                "proactive": True,
                "source": source,
                "text": text,
                "context": context or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            source="proactive",
        )
        logger.info(f"Proactive speech [{source}]: {text[:80]}...")

        # Also send a browser notification if available
        try:
            from core.notifications import send_notification
            send_notification(
                title="Jarvis",
                body=text,
                tag=f"proactive_{source}",
            )
        except Exception:
            pass  # Notifications not configured

    except Exception as e:
        logger.warning(f"Failed to publish proactive speech: {e}")


# ---------------------------------------------------------------------------
# Trigger: Morning Briefing
# ---------------------------------------------------------------------------

def morning_briefing() -> Optional[str]:
    """Generate a calm morning briefing with today's schedule.

    Returns the speech text (for testing), and also publishes it.
    """
    try:
        from agents.scheduler_agent import _estimate_alpha, build_schedule
        from memory.adhd_memory import ADHDMemoryEngine, get_history

        memory = ADHDMemoryEngine()

        # Gather today's tasks from memory
        all_memories = memory.get_all_memories()
        tasks = []
        for mem in all_memories:
            meta = mem.get("metadata", {})
            if meta.get("type") == "task":
                tasks.append({
                    "text": mem.get("memory", ""),
                    "estimated_minutes": meta.get("estimated_minutes", 25),
                    "priority": meta.get("priority", "soon"),
                })

        if not tasks:
            text = "Good morning. No tasks on the agenda today — enjoy the freedom."
            _publish_speech(text, "morning_briefing")
            return text

        # Build schedule to get timing
        alpha = _estimate_alpha(get_history())
        schedule = build_schedule(tasks[:5], alpha)  # Top 5 tasks

        first = schedule[0] if schedule else None
        if first and first.get("type") == "task":
            text = (
                f"Good morning. {len(tasks)} thing{'s' if len(tasks) != 1 else ''} "
                f"on the list today, starting with "
                f"'{first.get('label', 'a task')}' "
                f"at {first.get('start', '?')[:16].replace('T', ' ')}."
            )
        else:
            text = f"Good morning. {len(tasks)} things on the agenda today."

        _publish_speech(text, "morning_briefing", {"task_count": len(tasks)})
        return text

    except Exception as e:
        logger.warning(f"Morning briefing failed: {e}")
        text = "Good morning. I had trouble pulling today's schedule — check in when you're ready."
        _publish_speech(text, "morning_briefing", {"error": str(e)})
        return text


# ---------------------------------------------------------------------------
# Trigger: Missed Block
# ---------------------------------------------------------------------------

def on_missed_block(block: Dict[str, Any]) -> Optional[str]:
    """Generate a calm nudge when a scheduled block is missed.

    Args:
        block: The missed schedule block dict with 'label', 'start', 'end'.

    Returns the speech text.
    """
    label = block.get("label", "that task")
    scaled_mins = block.get("scaled_minutes", block.get("estimated_minutes", 25))

    text = (
        f"I've noticed '{label}' slipped. "
        f"Still room to try the first 15 minutes — "
        f"want to give it a go, or shall we move on?"
    )

    _publish_speech(text, "missed_block", {
        "block_label": label,
        "scheduled_minutes": scaled_mins,
    })
    return text


# ---------------------------------------------------------------------------
# Trigger: Rebalance Suggestion
# ---------------------------------------------------------------------------

def on_rebalance(remaining_blocks: List[Dict], suggestion: str) -> Optional[str]:
    """Publish a rebalance suggestion from the scheduler.

    Args:
        remaining_blocks: Updated schedule blocks after rebalance.
        suggestion: The calm suggestion text from the scheduler.

    Returns the speech text.
    """
    text = suggestion or "I've adjusted the schedule. Take a breath — we've got this."
    _publish_speech(text, "rebalance", {
        "remaining_blocks": len(remaining_blocks),
    })
    return text


# ---------------------------------------------------------------------------
# Trigger: Idle Check-in
# ---------------------------------------------------------------------------

def idle_check(minutes_idle: int) -> Optional[str]:
    """Generate a gentle check-in after prolonged idle time.

    Args:
        minutes_idle: How many minutes the user has been idle.

    Returns the speech text, or None if not idle enough to nudge.
    """
    if minutes_idle < 30:
        return None  # Don't nudge for short pauses

    if minutes_idle < 60:
        text = (
            f"You've been quiet for about {minutes_idle} minutes. "
            f"No rush — just checking in. Want to pick something up?"
        )
    else:
        text = (
            f"It's been {minutes_idle} minutes. "
            f"Remember, even 5 minutes of progress counts. "
            f"What feels right right now?"
        )

    _publish_speech(text, "idle_check", {"minutes_idle": minutes_idle})
    return text


# ---------------------------------------------------------------------------
# Trigger: Session End
# ---------------------------------------------------------------------------

def session_end_summary(
    tasks_completed: int,
    total_minutes: float,
) -> Optional[str]:
    """Generate an end-of-session summary.

    Args:
        tasks_completed: Number of tasks completed this session.
        total_minutes: Total minutes spent.

    Returns the speech text.
    """
    if tasks_completed == 0:
        text = (
            "Session done. Even showing up counts — "
            "tomorrow's a fresh start."
        )
    elif tasks_completed == 1:
        text = (
            f"Nice work — you finished 1 task in about {int(total_minutes)} minutes. "
            f"That's real progress."
        )
    else:
        text = (
            f"Strong session — {tasks_completed} tasks done "
            f"in about {int(total_minutes)} minutes. "
            f"You're building momentum."
        )

    _publish_speech(text, "session_end", {
        "tasks_completed": tasks_completed,
        "total_minutes": total_minutes,
    })
    return text


# ---------------------------------------------------------------------------
# Event bus wiring
# ---------------------------------------------------------------------------

def _on_schedule_updated(data: Dict[str, Any]):
    """React to schedule updates — check for missed blocks."""
    try:
        missed = data.get("missed_block")
        if missed:
            on_missed_block(missed)

        rebalance = data.get("rebalance_suggestion")
        if rebalance:
            remaining = data.get("remaining_blocks", [])
            on_rebalance(remaining, rebalance)
    except Exception as e:
        logger.warning(f"Schedule update handler error: {e}")


def _on_task_completed(data: Dict[str, Any]):
    """React to task completion — check if session should end."""
    try:
        # Track completions for session summary
        if not hasattr(_on_task_completed, "_completions"):
            _on_task_completed._completions = []
            _on_task_completed._start_time = datetime.now(timezone.utc)

        _on_task_completed._completions.append(data)

        # After 3+ completions or 2+ hours, suggest a break
        elapsed = (datetime.now(timezone.utc) - _on_task_completed._start_time).total_seconds() / 60
        if len(_on_task_completed._completions) >= 3 and elapsed > 30:
            session_end_summary(len(_on_task_completed._completions), elapsed)
            _on_task_completed._completions = []
            _on_task_completed._start_time = datetime.now(timezone.utc)

    except Exception as e:
        logger.warning(f"Task completion handler error: {e}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_proactive_triggers():
    """Register proactive triggers with the event bus and cron scheduler.

    Call once on startup.
    """
    try:
        from core.event_bus import subscribe, EventType

        # Subscribe to scheduler events
        subscribe(EventType.SCHEDULE_UPDATED, _on_schedule_updated)
        subscribe(EventType.TASK_COMPLETED, _on_task_completed)
        logger.info("Proactive triggers registered with event bus")

    except Exception as e:
        logger.warning(f"Failed to register event bus triggers: {e}")

    # Register morning briefing as a cron task (08:00 daily)
    try:
        from core.cron_scheduler import get_scheduler
        scheduler = get_scheduler()

        # Only add if not already present
        existing = [t.task_id for t in scheduler.list_tasks()]
        if "proactive_morning" not in existing:
            scheduler.create_task(
                task_id="proactive_morning",
                prompt="Morning briefing — generate proactive schedule overview",
                cron_expression="0 8 * * *",  # 08:00 daily
                agent_type="braindump",  # Uses braindump agent for scheduling
            )
            logger.info("Morning briefing registered: 08:00 daily")
    except Exception as e:
        logger.warning(f"Failed to register cron triggers: {e}")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_proactive_status() -> Dict[str, Any]:
    """Get the current proactive trigger status."""
    try:
        from core.cron_scheduler import get_scheduler
        scheduler = get_scheduler()
        tasks = scheduler.list_tasks()
        proactive_tasks = [t for t in tasks if t.task_id.startswith("proactive_")]
    except Exception:
        proactive_tasks = []

    return {
        "triggers": {
            "morning_briefing": "08:00 daily",
            "missed_block": "event-driven (schedule_updated)",
            "idle_check": "event-driven (30+ min idle)",
            "session_end": "event-driven (3+ tasks completed)",
        },
        "registered_cron_tasks": [
            {"task_id": t.task_id, "cron": t.cron_expression, "enabled": t.enabled}
            for t in proactive_tasks
        ],
        "status": "active",
    }

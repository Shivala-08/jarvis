"""Integration routes — calendar, Obsidian, notifications.

Handles:
- GET /api/calendar/status — check calendar config
- GET /api/calendar/today — get today's events
- POST /api/calendar/sync — sync schedule to calendar
- POST /api/calendar/clear — clear copilot events
- GET /api/obsidian — check Obsidian status
- GET /api/obsidian/notes — list vault notes
- GET /api/notifications — get notifications
- POST /api/notifications/send — send notification
- POST /api/notifications/{id}/read — mark read
- POST /api/notifications/read-all — mark all read
- DELETE /api/notifications — clear all
- GET /api/notifications/preferences — get prefs
- POST /api/notifications/preferences — update prefs
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends

from core.auth import require_token
from core.dependencies import get_task_list

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["integrations"])


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


@router.get("/calendar/status")
def api_calendar_status():
    """Check if Google Calendar is configured and reachable."""
    from agents.calendar_sync import calendar_available
    return {"available": calendar_available()}


@router.get("/calendar/today")
def api_calendar_today():
    """Get today's calendar events and busy blocks."""
    from agents.calendar_sync import CalendarSync
    sync = CalendarSync()
    events = sync.get_events_for_day()
    busy = sync.get_busy_blocks()
    return {
        "events": events,
        "busy_blocks": busy,
        "count": len(events),
    }


@router.post("/calendar/sync", dependencies=[Depends(require_token)])
def api_calendar_sync():
    """Sync task schedule to Google Calendar."""
    from agents.calendar_sync import CalendarSync
    from agents.scheduler_agent import _estimate_alpha
    from memory.adhd_memory import get_history

    tasks = get_task_list()
    alpha = _estimate_alpha(get_history())
    sync = CalendarSync()
    result = sync.sync_today(tasks, alpha)
    return result


@router.post("/calendar/clear", dependencies=[Depends(require_token)])
def api_calendar_clear():
    """Clear all copilot events from today's calendar."""
    from agents.calendar_sync import CalendarSync
    sync = CalendarSync()
    deleted = sync.clear_copilot_events()
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Obsidian
# ---------------------------------------------------------------------------


@router.get("/obsidian")
def api_obsidian_status():
    """Check Obsidian vault status."""
    try:
        from memory.adhd_memory import ObsidianClient
        client = ObsidianClient()
        server_up = client.is_available()
        vault_path = client.vault_path
        note_count = len(list(vault_path.glob("*.md"))) if vault_path.exists() else 0
        return {
            "status": "ok",
            "server_reachable": server_up,
            "vault_path": str(vault_path),
            "note_count": note_count,
            "message": (
                f"Obsidian server reachable, {note_count} notes in vault"
                if server_up
                else f"Obsidian server offline (vault has {note_count} notes on disk)"
            ),
        }
    except Exception as e:
        return {
            "status": "error",
            "server_reachable": False,
            "vault_path": "vault",
            "note_count": 0,
            "message": f"Obsidian check failed: {e}",
        }


@router.get("/obsidian/notes")
def api_obsidian_notes():
    """List recent notes in the Obsidian vault."""
    try:
        from memory.adhd_memory import ObsidianClient
        client = ObsidianClient()
        vault_path = client.vault_path
        if not vault_path.exists():
            return {"notes": []}
        notes = sorted(
            [f.name for f in vault_path.glob("*.md") if f.name != "Dashboard.md"],
            reverse=True,
        )
        return {"notes": notes}
    except Exception as e:
        return {"notes": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@router.get("/notifications")
def api_get_notifications(
    category: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 20,
):
    """Get notifications."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    notifs = manager.get_notifications(
        category=category, unread_only=unread_only, limit=limit
    )
    return {
        "notifications": [
            {
                "id": n.id,
                "title": n.title,
                "body": n.body,
                "category": n.category,
                "timestamp": n.timestamp,
                "read": n.read,
            }
            for n in notifs
        ],
        "unread_count": manager.get_unread_count(),
    }


@router.post("/notifications/send", dependencies=[Depends(require_token)])
def api_send_notification(
    title: str,
    body: str,
    category: str = "general",
):
    """Send a notification (admin/testing)."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    notif = manager.send_notification(title, body, category, force=True)
    return {
        "sent": notif is not None,
        "notification_id": notif.id if notif else None,
    }


@router.post("/notifications/{notification_id}/read", dependencies=[Depends(require_token)])
def api_mark_read(notification_id: str):
    """Mark a notification as read."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    success = manager.mark_read(notification_id)
    return {"marked_read": success, "unread_count": manager.get_unread_count()}


@router.post("/notifications/read-all", dependencies=[Depends(require_token)])
def api_mark_all_read():
    """Mark all notifications as read."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    count = manager.mark_all_read()
    return {"marked_read": count, "unread_count": 0}


@router.delete("/notifications", dependencies=[Depends(require_token)])
def api_clear_notifications():
    """Clear all notifications."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    count = manager.clear_all()
    return {"cleared": count}


@router.get("/notifications/preferences")
def api_notification_preferences():
    """Get notification preferences."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    return manager.get_preferences()


@router.post("/notifications/preferences", dependencies=[Depends(require_token)])
def api_update_notification_preferences(preferences: dict):
    """Update notification preferences."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    updated = manager.update_preferences(**preferences)
    return manager.get_preferences()

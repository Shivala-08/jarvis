"""Notification Manager — desktop and PWA notifications for scheduled events.

Features:
- Browser notification permission management
- Desktop notification triggers for scheduled events
- PWA push notification support
- Event-driven: subscribes to event bus for automatic notifications
- Notification history and preferences

Usage:
    from core.notifications import NotificationManager
    manager = NotificationManager()
    manager.send_notification("Task Reminder", "Time to start: Finish report")
    manager.request_permission()  # from browser context
"""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


@dataclass
class NotificationPreferences:
    """User notification preferences."""
    enabled: bool = True
    task_reminders: bool = True
    focus_nudges: bool = True
    schedule_updates: bool = True
    daily_digest: bool = True
    quiet_hours_start: int = 22  # 10 PM
    quiet_hours_end: int = 7    # 7 AM
    sound_enabled: bool = True


@dataclass
class Notification:
    """A single notification."""
    id: str
    title: str
    body: str
    category: str  # task_reminder, focus_nudge, schedule_update, daily_digest
    timestamp: str
    read: bool = False
    data: Dict[str, Any] = field(default_factory=dict)


class NotificationManager:
    """Manages notifications for the ADHD Co-Processor.
    
    Tracks notification preferences, stores history, and provides
    methods to send notifications via the event bus to connected clients.
    """
    
    def __init__(self, storage_path: str = "data/notifications.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.preferences = NotificationPreferences()
        self._notifications: List[Notification] = []
        self._subscribers: List[str] = []  # WebSocket connection IDs
        self._load()
    
    def _load(self) -> None:
        """Load notification history and preferences from disk."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path) as f:
                    data = json.load(f)
                    self._notifications = [
                        Notification(**n) for n in data.get("notifications", [])
                    ]
                    prefs = data.get("preferences", {})
                    if prefs:
                        self.preferences = NotificationPreferences(**prefs)
            except Exception as e:
                logger.error(f"Failed to load notifications: {e}")
    
    def _save(self) -> None:
        """Persist notifications and preferences to disk."""
        try:
            data = {
                "notifications": [
                    {
                        "id": n.id,
                        "title": n.title,
                        "body": n.body,
                        "category": n.category,
                        "timestamp": n.timestamp,
                        "read": n.read,
                        "data": n.data,
                    }
                    for n in self._notifications[-100:]  # Keep last 100
                ],
                "preferences": {
                    "enabled": self.preferences.enabled,
                    "task_reminders": self.preferences.task_reminders,
                    "focus_nudges": self.preferences.focus_nudges,
                    "schedule_updates": self.preferences.schedule_updates,
                    "daily_digest": self.preferences.daily_digest,
                    "quiet_hours_start": self.preferences.quiet_hours_start,
                    "quiet_hours_end": self.preferences.quiet_hours_end,
                    "sound_enabled": self.preferences.sound_enabled,
                },
            }
            with open(self.storage_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save notifications: {e}")
    
    def _is_quiet_hours(self) -> bool:
        """Check if we're in quiet hours."""
        hour = datetime.now().hour
        start = self.preferences.quiet_hours_start
        end = self.preferences.quiet_hours_end
        if start > end:  # Spans midnight
            return hour >= start or hour < end
        return start <= hour < end
    
    def send_notification(
        self,
        title: str,
        body: str,
        category: str = "general",
        data: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[Notification]:
        """Send a notification.
        
        Args:
            title: Notification title
            body: Notification body text
            category: One of: task_reminder, focus_nudge, schedule_update, daily_digest, general
            data: Optional extra data
            force: If True, bypass quiet hours and preferences
        
        Returns: The Notification object, or None if suppressed
        """
        if not self.preferences.enabled and not force:
            return None
        
        # Check category preferences
        category_prefs = {
            "task_reminder": self.preferences.task_reminders,
            "focus_nudge": self.preferences.focus_nudges,
            "schedule_update": self.preferences.schedule_updates,
            "daily_digest": self.preferences.daily_digest,
        }
        if category in category_prefs and not category_prefs[category] and not force:
            return None
        
        # Check quiet hours
        if self._is_quiet_hours() and not force:
            logger.debug(f"Notification suppressed (quiet hours): {title}")
            return None
        
        notification = Notification(
            id=f"notif_{int(time.time() * 1000)}",
            title=title,
            body=body,
            category=category,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=data or {},
        )
        
        self._notifications.append(notification)
        self._save()
        
        # Broadcast to connected clients via event bus
        try:
            from core.event_bus import publish, EventType
            publish(
                EventType.HEALTH_CHECK,  # Using health_check as notification event
                {
                    "type": "notification",
                    "notification": {
                        "id": notification.id,
                        "title": notification.title,
                        "body": notification.body,
                        "category": notification.category,
                        "timestamp": notification.timestamp,
                    },
                },
                source="notification_manager",
            )
        except Exception:
            pass
        
        logger.info(f"Notification sent: [{category}] {title}")
        return notification
    
    def get_notifications(
        self,
        category: Optional[str] = None,
        unread_only: bool = False,
        limit: int = 20,
    ) -> List[Notification]:
        """Get notifications, optionally filtered."""
        notifs = self._notifications
        if category:
            notifs = [n for n in notifs if n.category == category]
        if unread_only:
            notifs = [n for n in notifs if not n.read]
        return notifs[-limit:]
    
    def mark_read(self, notification_id: str) -> bool:
        """Mark a notification as read."""
        for n in self._notifications:
            if n.id == notification_id:
                n.read = True
                self._save()
                return True
        return False
    
    def mark_all_read(self) -> int:
        """Mark all notifications as read. Returns count marked."""
        count = 0
        for n in self._notifications:
            if not n.read:
                n.read = True
                count += 1
        if count:
            self._save()
        return count
    
    def clear_all(self) -> int:
        """Clear all notifications. Returns count cleared."""
        count = len(self._notifications)
        self._notifications.clear()
        self._save()
        return count
    
    def get_unread_count(self) -> int:
        """Get count of unread notifications."""
        return sum(1 for n in self._notifications if not n.read)
    
    def update_preferences(self, **kwargs) -> NotificationPreferences:
        """Update notification preferences."""
        for key, value in kwargs.items():
            if hasattr(self.preferences, key):
                setattr(self.preferences, key, value)
        self._save()
        return self.preferences
    
    def get_preferences(self) -> Dict[str, Any]:
        """Get current notification preferences."""
        return {
            "enabled": self.preferences.enabled,
            "task_reminders": self.preferences.task_reminders,
            "focus_nudges": self.preferences.focus_nudges,
            "schedule_updates": self.preferences.schedule_updates,
            "daily_digest": self.preferences.daily_digest,
            "quiet_hours_start": self.preferences.quiet_hours_start,
            "quiet_hours_end": self.preferences.quiet_hours_end,
            "sound_enabled": self.preferences.sound_enabled,
        }


# Global instance
_notification_manager: Optional[NotificationManager] = None


def get_notification_manager() -> NotificationManager:
    """Get or create the global notification manager."""
    global _notification_manager
    if _notification_manager is None:
        _notification_manager = NotificationManager()
    return _notification_manager


def setup_notification_handlers() -> None:
    """Set up event-driven notification handlers.
    
    Subscribes to relevant events and sends appropriate notifications.
    """
    from core.event_bus import subscribe, EventType
    
    manager = get_notification_manager()
    
    def on_braindump(event):
        count = len(event.data.get("result", {}).get("thoughts", []))
        if count > 0:
            manager.send_notification(
                "Brain Dump Captured",
                f"Extracted {count} thoughts from your input.",
                category="task_reminder",
            )
    
    def on_nudge(event):
        text = event.data.get("text", "Time to refocus")
        manager.send_notification(
            "Focus Check-in",
            text,
            category="focus_nudge",
        )
    
    def on_schedule_updated(event):
        suggestion = event.data.get("suggestion", "")
        if suggestion:
            manager.send_notification(
                "Schedule Updated",
                suggestion,
                category="schedule_update",
            )
    
    subscribe(EventType.BRAINDUMP_COMPLETED, on_braindump)
    subscribe(EventType.NUDGE_FIRED, on_nudge)
    subscribe(EventType.SCHEDULE_UPDATED, on_schedule_updated)
    
    logger.info("Notification handlers registered")

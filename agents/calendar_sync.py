"""Calendar Sync — merges Google Calendar events with the adaptive scheduler.

Features:
- Fetch today's calendar events and block them out in the schedule
- Push scheduled task blocks back to Google Calendar
- Auto-create "buffer" events for transition breaks
- All changes are additive — never deletes existing calendar events

Usage:
    from agents.calendar_sync import CalendarSync
    sync = CalendarSync()
    today_blocks = sync.get_today_blocks()  # merged calendar + tasks
    sync.push_schedule(blocks)              # write tasks to calendar
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import toml
    CONFIG = toml.load("config/config.toml")
except (FileNotFoundError, Exception):
    CONFIG = {}

SCHEDULER_CFG = CONFIG.get("scheduler", {})
OBSIDIAN_CFG = CONFIG.get("obsidian", {})


# ---------------------------------------------------------------------------
# Calendar service (lazy, shared with scheduler_agent)
# ---------------------------------------------------------------------------

_calendar_service = None


def _get_calendar_service():
    """Return an authorized Google Calendar service, caching the result."""
    global _calendar_service
    if _calendar_service is not None:
        return _calendar_service

    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API libraries not installed. "
            "Run: uv add google-api-python-client google-auth google-auth-oauthlib"
        )

    SCOPES = ["https://www.googleapis.com/auth/calendar"]
    token_path = Path("config/google_token.json")
    creds_path = Path("config/google_client_secret.json")

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    "Google OAuth client secret not found at config/google_client_secret.json. "
                    "Download from Google Cloud Console → APIs & Services → Credentials.\n"
                    "Place the file at config/google_client_secret.json"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    _calendar_service = build("calendar", "v3", credentials=creds)
    return _calendar_service


def calendar_available() -> bool:
    """Check if Google Calendar is configured and reachable."""
    try:
        _get_calendar_service()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Calendar operations
# ---------------------------------------------------------------------------

class CalendarSync:
    """Two-way sync between Google Calendar and the adaptive scheduler."""

    CALENDAR_TAG = "adhd-copilot"  # prefix for events we create
    BUFFER_LABEL = "☕ Transition break"

    def __init__(self, calendar_id: str = "primary"):
        self.calendar_id = calendar_id
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = _get_calendar_service()
        return self._service

    # ---------- Read ----------

    def get_events_for_day(self, date: Optional[datetime] = None) -> list[dict]:
        """Fetch all events for a given day (default: today).

        Returns list of {"id", "title", "start", "end", "is_adhd_copilot"} dicts.
        """
        if date is None:
            date = datetime.now(timezone.utc)

        # Day boundaries in local time (UTC±0 for simplicity; adjust if needed)
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        try:
            events_result = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start_of_day.isoformat(),
                    timeMax=end_of_day.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except Exception as e:
            print(f"  ⚠️  Calendar fetch failed: {e}")
            return []

        events = []
        for item in events_result.get("items", []):
            summary = item.get("summary", "")
            start = item.get("start", {})
            end = item.get("end", {})

            # Parse start/end — handle all-day events
            start_dt = start.get("dateTime") or start.get("date")
            end_dt = end.get("dateTime") or end.get("date")

            if not start_dt or not end_dt:
                continue

            events.append({
                "id": item["id"],
                "title": summary,
                "start": start_dt,
                "end": end_dt,
                "is_adhd_copilot": summary.startswith(f"[{self.CALENDAR_TAG}]"),
                "description": item.get("description", ""),
            })

        return events

    def get_busy_blocks(self, date: Optional[datetime] = None) -> list[dict]:
        """Return busy time blocks from existing calendar events (non-copilot).

        These are events the user created manually — we treat them as
        immovable and build the schedule around them.
        """
        events = self.get_events_for_day(date)
        busy = []
        for ev in events:
            if not ev["is_adhd_copilot"]:
                busy.append({
                    "type": "calendar_block",
                    "label": ev["title"],
                    "start": ev["start"],
                    "end": ev["end"],
                    "event_id": ev["id"],
                })
        return busy

    # ---------- Write ----------

    def push_schedule(self, blocks: list[dict], date: Optional[datetime] = None) -> int:
        """Write scheduled task blocks to Google Calendar.

        Creates events with the [adhd-copilot] prefix so they're identifiable.
        Only creates events that don't already exist (dedup by title + time).

        Returns the number of events created.
        """
        if date is None:
            date = datetime.now(timezone.utc)

        created = 0
        existing = self.get_events_for_day(date)
        existing_titles = {e["title"] for e in existing}

        for block in blocks:
            label = block.get("label", "")
            if not label or block.get("type") == "buffer":
                # Skip buffer blocks or unnamed blocks
                if block.get("type") == "buffer":
                    label = self.BUFFER_LABEL
                else:
                    continue

            title = f"[{self.CALENDAR_TAG}] {label}"

            # Skip if already on calendar today
            if title in existing_titles:
                continue

            event_body = {
                "summary": title,
                "start": {
                    "dateTime": block["start"],
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": block["end"],
                    "timeZone": "UTC",
                },
                "description": (
                    f"Auto-scheduled by ADHD Co-Processor\n"
                    f"Estimated: {block.get('estimated_minutes', '?')} min\n"
                    f"Scaled: {block.get('scaled_minutes', '?')} min\n"
                    f"Priority: {block.get('priority', 'soon')}"
                ),
                "colorId": "5",  # Google Calendar's green-ish color
            }

            try:
                self.service.events().insert(
                    calendarId=self.calendar_id,
                    body=event_body,
                ).execute()
                created += 1
            except Exception as e:
                print(f"  ⚠️  Failed to create event '{title}': {e}")

        return created

    def clear_copilot_events(self, date: Optional[datetime] = None) -> int:
        """Remove all [adhd-copilot] events for a given day.

        Used before re-syncing to avoid duplicates.
        Returns the number of events deleted.
        """
        events = self.get_events_for_day(date)
        deleted = 0

        for ev in events:
            if ev["is_adhd_copilot"]:
                try:
                    self.service.events().delete(
                        calendarId=self.calendar_id,
                        eventId=ev["id"],
                    ).execute()
                    deleted += 1
                except Exception as e:
                    print(f"  ⚠️  Failed to delete event '{ev['title']}': {e}")

        return deleted

    def sync_today(self, tasks: list[dict], alpha: float) -> dict:
        """Full sync: clear old copilot events, build schedule around busy blocks, push.

        Args:
            tasks: list of {"text", "estimated_minutes", "priority"} dicts
            alpha: time-scaling multiplier

        Returns:
            {"busy_blocks": int, "tasks_scheduled": int, "events_created": int}
        """
        today = datetime.now(timezone.utc)

        # 1. Clear old copilot events
        deleted = self.clear_copilot_events(today)

        # 2. Get existing busy blocks (manual calendar events)
        busy = self.get_busy_blocks(today)

        # 3. Build schedule around busy blocks
        from agents.scheduler_agent import build_schedule, _add_buffers

        buffer_minutes = SCHEDULER_CFG.get("transition_buffer_minutes", 15)
        blocks = self._build_around_busy(tasks, alpha, busy, today, buffer_minutes)

        # 4. Push to calendar
        created = self.push_schedule(blocks, today)

        return {
            "busy_blocks": len(busy),
            "tasks_scheduled": len([b for b in blocks if b.get("type") == "task"]),
            "events_created": created,
            "events_cleared": deleted,
            "blocks": blocks,
        }

    def _build_around_busy(
        self,
        tasks: list[dict],
        alpha: float,
        busy_blocks: list[dict],
        start_time: datetime,
        buffer_minutes: int,
    ) -> list[dict]:
        """Build a schedule that avoids existing calendar events."""
        from datetime import datetime as dt

        # Sort busy blocks by start time
        busy_sorted = sorted(busy_blocks, key=lambda b: b["start"])

        # Build free slots
        free_slots = []
        cursor = start_time

        for busy in busy_sorted:
            busy_start = dt.fromisoformat(busy["start"])
            busy_end = dt.fromisoformat(busy["end"])

            # If there's time before this busy block, it's a free slot
            if busy_start > cursor:
                free_slots.append({
                    "start": cursor,
                    "end": busy_start,
                    "label": busy.get("label", "Busy"),
                })
            # Move cursor past the busy block + buffer
            cursor = busy_end + timedelta(minutes=buffer_minutes)

        # Add remaining time after last busy block (until end of day)
        end_of_day = start_time.replace(hour=22, minute=0, second=0, microsecond=0)
        if cursor < end_of_day:
            free_slots.append({"start": cursor, "end": end_of_day})

        # Fill free slots with tasks
        priority_order = {"now": 0, "soon": 1, "someday": 2}
        sorted_tasks = sorted(tasks, key=lambda t: priority_order.get(t.get("priority", "soon"), 1))

        blocks = []
        task_idx = 0

        for slot in free_slots:
            slot_cursor = slot["start"]
            slot_end = slot["end"]

            # Insert busy block marker
            if slot.get("label") and slot["label"] != "Busy":
                blocks.append({
                    "type": "calendar_block",
                    "label": slot["label"],
                    "start": slot["start"].isoformat(),
                    "end": slot["end"].isoformat(),
                })

            while task_idx < len(sorted_tasks) and slot_cursor < slot_end:
                task = sorted_tasks[task_idx]
                estimated = task.get("estimated_minutes", 25)
                scaled_minutes = int(estimated * alpha)
                task_end = slot_cursor + timedelta(minutes=scaled_minutes)

                # If task overflows the slot, try to fit it or skip
                if task_end > slot_end:
                    available = (slot_end - slot_cursor).total_seconds() / 60
                    if available < 5:  # Less than 5 min left — skip to next slot
                        break
                    # Truncate to available time
                    scaled_minutes = int(available) - buffer_minutes
                    task_end = slot_cursor + timedelta(minutes=scaled_minutes)

                blocks.append({
                    "type": "task",
                    "label": task["text"],
                    "start": slot_cursor.isoformat(),
                    "end": task_end.isoformat(),
                    "estimated_minutes": estimated,
                    "scaled_minutes": scaled_minutes,
                    "priority": task.get("priority", "soon"),
                })

                # Add buffer
                slot_cursor = task_end + timedelta(minutes=buffer_minutes)
                task_idx += 1

        # Add any remaining tasks (overtime)
        while task_idx < len(sorted_tasks):
            task = sorted_tasks[task_idx]
            estimated = task.get("estimated_minutes", 25)
            scaled_minutes = int(estimated * alpha)
            blocks.append({
                "type": "task",
                "label": task["text"],
                "start": slot_cursor.isoformat(),
                "end": (slot_cursor + timedelta(minutes=scaled_minutes)).isoformat(),
                "estimated_minutes": estimated,
                "scaled_minutes": scaled_minutes,
                "priority": task.get("priority", "soon"),
                "note": "overtime",
            })
            slot_cursor = slot_cursor + timedelta(minutes=scaled_minutes + buffer_minutes)
            task_idx += 1

        return blocks


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    sync = CalendarSync()

    if len(sys.argv) > 1 and sys.argv[1] == "today":
        print("📅 Today's calendar events:")
        events = sync.get_events_for_day()
        if not events:
            print("  (no events)")
        for ev in events:
            marker = "🤖" if ev["is_adhd_copilot"] else "📅"
            print(f"  {marker} {ev['title']}  {ev['start'][:16]} → {ev['end'][:16]}")

        print("\n🔒 Busy blocks (from other calendars):")
        busy = sync.get_busy_blocks()
        if not busy:
            print("  (none)")
        for b in busy:
            print(f"  📅 {b['label']}  {b['start'][:16]} → {b['end'][:16]}")

    elif len(sys.argv) > 1 and sys.argv[1] == "sync":
        # Demo sync with sample tasks
        from agents.scheduler_agent import _estimate_alpha
        from memory.adhd_memory import get_history

        tasks = [
            {"text": "Finish quarterly report", "estimated_minutes": 90, "priority": "now"},
            {"text": "Reply to Alice's email", "estimated_minutes": 15, "priority": "now"},
            {"text": "Research new framework", "estimated_minutes": 45, "priority": "soon"},
        ]
        alpha = _estimate_alpha(get_history())
        result = sync.sync_today(tasks, alpha)
        print(f"✅ Sync complete:")
        print(f"  📅 Busy blocks: {result['busy_blocks']}")
        print(f"  📋 Tasks scheduled: {result['tasks_scheduled']}")
        print(f"  ✅ Events created: {result['events_created']}")

    elif len(sys.argv) > 1 and sys.argv[1] == "clear":
        deleted = sync.clear_copilot_events()
        print(f"🗑️  Cleared {deleted} copilot events from today")

    else:
        print("📅 Calendar Sync — CLI Mode")
        print("  Usage:")
        print("    python -m agents.calendar_sync today   — Show today's events")
        print("    python -m agents.calendar_sync sync    — Sync sample schedule")
        print("    python -m agents.calendar_sync clear   — Clear copilot events")

"""Adaptive Scheduler Agent — calendar-aware, zero-guilt rescheduling.

Features:
- Google Calendar OAuth (local token, never leaves machine)
- Time-scaling multiplier (alpha) from historical actual-vs-estimated data
- 15-minute transition buffers auto-inserted
- Silent re-balance on missed blocks: no red flags, no overdue counts
- Produces calm, spoken micro-sprint suggestions

All inference runs locally via Ollama; calendar API is free-tier Google.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ollama
import toml

CONFIG = toml.load("config/config.toml")
SCHEDULER_CFG = CONFIG["scheduler"]

# ---------------------------------------------------------------------------
# Google Calendar helpers (free-tier, personal account)
# ---------------------------------------------------------------------------

# Lazy imports — only needed when calendar features are actually used
_calendar_service = None


def _get_calendar_service():
    """Return an authorized Google Calendar service, caching the result."""
    global _calendar_service
    if _calendar_service is not None:
        return _calendar_service

    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

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
                    "Download from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    _calendar_service = build("calendar", "v3", credentials=creds)
    return _calendar_service


def get_events(start: datetime, end: datetime) -> list[dict]:
    """Fetch events from Google Calendar within a time range."""
    service = _get_calendar_service()
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return events_result.get("items", [])


def update_event(event_id: str, updates: dict):
    """Update a single calendar event."""
    service = _get_calendar_service()
    service.events().patch(
        calendarId="primary", eventId=event_id, body=updates
    ).execute()


# ---------------------------------------------------------------------------
# Time-scaling: alpha from historical data
# ---------------------------------------------------------------------------

def _estimate_alpha(history: list[dict]) -> float:
    """Compute the time-scaling alpha from past actual-vs-estimated durations.

    alpha = median(actual / estimated), clamped to [alpha_min, alpha_max].
    """
    ratios = []
    for entry in history:
        if entry.get("estimated_minutes") and entry.get("actual_minutes"):
            ratio = entry["actual_minutes"] / entry["estimated_minutes"]
            ratios.append(ratio)
    if not ratios:
        alpha_min = SCHEDULER_CFG["time_scaling_alpha_min"]
        alpha_max = SCHEDULER_CFG["time_scaling_alpha_max"]
        return (alpha_min + alpha_max) / 2  # midpoint default
    ratios.sort()
    mid = len(ratios) // 2
    median_ratio = ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2
    return max(SCHEDULER_CFG["time_scaling_alpha_min"],
               min(SCHEDULER_CFG["time_scaling_alpha_max"], median_ratio))


# ---------------------------------------------------------------------------
# Schedule building
# ---------------------------------------------------------------------------

def _add_buffers(blocks: list[dict], buffer_minutes: int = 15) -> list[dict]:
    """Insert transition buffers between consecutive blocks."""
    buffered = []
    for i, block in enumerate(blocks):
        buffered.append(block)
        if i < len(blocks) - 1:
            buffered.append({
                "type": "buffer",
                "start": block["end"],
                "end": (datetime.fromisoformat(block["end"]) + timedelta(minutes=buffer_minutes)).isoformat(),
                "label": "Transition break",
            })
    return buffered


def build_schedule(tasks: list[dict], alpha: float, start_time: Optional[datetime] = None) -> list[dict]:
    """Build a time-blocked schedule with scaled durations and transition buffers.

    Each task dict: {"text": str, "estimated_minutes": int, "priority": str}
    """
    if start_time is None:
        start_time = datetime.now(timezone.utc)

    buffer_minutes = SCHEDULER_CFG["transition_buffer_minutes"]
    blocks = []
    cursor = start_time

    # Sort: now > soon > someday
    priority_order = {"now": 0, "soon": 1, "someday": 2}
    tasks.sort(key=lambda t: priority_order.get(t.get("priority", "soon"), 1))

    for task in tasks:
        estimated = task.get("estimated_minutes", 25)
        scaled_minutes = int(estimated * alpha)
        end_time = cursor + timedelta(minutes=scaled_minutes)
        blocks.append({
            "type": "task",
            "label": task["text"],
            "start": cursor.isoformat(),
            "end": end_time.isoformat(),
            "estimated_minutes": estimated,
            "scaled_minutes": scaled_minutes,
        })
        # Add buffer after this block
        cursor = end_time + timedelta(minutes=buffer_minutes)

    return blocks


# ---------------------------------------------------------------------------
# Silent re-balance (no failure states)
# ---------------------------------------------------------------------------

def rebalance(
    blocks: list[dict],
    missed_block_id: Optional[int] = None,
    history: Optional[list[dict]] = None,
) -> tuple[list[dict], str]:
    """Silently rebalance remaining schedule after a missed/overrun block.

    Returns (updated_blocks, spoken_suggestion) — never an error state.
    """
    if history is None:
        history = []
    alpha = _estimate_alpha(history)

    # Find current time and filter to future blocks
    now = datetime.now(timezone.utc)
    remaining = []
    for b in blocks:
        block_end = datetime.fromisoformat(b["end"])
        if block_end > now and b.get("type") == "task":
            remaining.append(b)

    if not remaining:
        return [], "All done for now. Take a breather — you've earned it."

    # Shrink remaining tasks slightly to fit the lost time
    if missed_block_id is not None and 0 <= missed_block_id < len(blocks):
        missed = blocks[missed_block_id]
        missed_minutes = missed.get("scaled_minutes", 25)
        # Redistribute: shrink each remaining block proportionally
        total_remaining = sum(b.get("scaled_minutes", 25) for b in remaining)
        if total_remaining > 0:
            shrink_factor = max(0.7, 1 - (missed_minutes / (total_remaining + missed_minutes)))
            cursor = now
            rebalanced = []
            for b in remaining:
                new_scaled = max(5, int(b.get("scaled_minutes", 25) * shrink_factor))
                end = cursor + timedelta(minutes=new_scaled)
                rebalanced.append({**b, "start": cursor.isoformat(), "end": end.isoformat(), "scaled_minutes": new_scaled})
                cursor = end + timedelta(minutes=SCHEDULER_CFG["transition_buffer_minutes"])
            remaining = rebalanced

    # Generate calm spoken suggestion
    next_block = remaining[0] if remaining else None
    if next_block:
        suggestion = (
            f"How about we start with '{next_block['label']}'? "
            f"It should take about {next_block.get('scaled_minutes', 25)} minutes."
        )
    else:
        suggestion = "Everything looks clear. What would you like to do next?"

    return remaining, suggestion


# ---------------------------------------------------------------------------
# Micro-sprint generator
# ---------------------------------------------------------------------------

def generate_micro_sprint(task_text: str, model: str = None) -> str:
    """Generate a spoken calm micro-sprint prompt for the current task."""
    if model is None:
        from core.config import get_default_model
        model = get_default_model()
    response = ollama.chat(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a calm, supportive ADHD co-processor. "
                    "Given a task, produce a single short sentence (≤20 words) that "
                    "suggests a 5–15 minute micro-sprint. Use supportive phrasing, "
                    "never imperative commands. Example: 'How about spending 10 minutes "
                    "drafting the outline?' not 'Do the outline now.'"
                ),
            },
            {"role": "user", "content": task_text},
        ],
        options={"temperature": 0.5},
    )
    return response["message"]["content"].strip()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_tasks = [
        {"text": "Finish quarterly report", "estimated_minutes": 90, "priority": "now"},
        {"text": "Reply to Alice's email", "estimated_minutes": 15, "priority": "now"},
        {"text": "Research new framework", "estimated_minutes": 45, "priority": "soon"},
    ]
    alpha = _estimate_alpha([])
    schedule = build_schedule(sample_tasks, alpha)
    print("=== Today's Schedule ===")
    for block in schedule:
        marker = "🔖" if block["type"] == "task" else "☕"
        print(f"  {marker} {block.get('label', block['type'])}  "
              f"{block['start'][:16]} → {block['end'][:16]}  "
              f"({block.get('scaled_minutes', '—')} min)")

    # Simulate missing a block
    print("\n=== After missing block 0 ===")
    remaining, suggestion = rebalance(schedule, missed_block_id=0)
    print(f"  💬 {suggestion}")
    for block in remaining:
        print(f"  🔖 {block.get('label')}  {block['start'][:16]} → {block['end'][:16]}")

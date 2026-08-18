"""Body Double Agent — passive, non-intrusive ambient focus monitoring.

Features:
- Local process/window-focus monitoring (psutil + OS APIs)
- Soft-nudge via spoken Kokoro prompt (not popups, not badges)
- Anti-patterns enforced: no red badges, no overdue counters, no modals
- Cooldown to avoid nagging

All monitoring stays local. No telemetry SDKs.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil

try:
    import ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

try:
    import toml
    CONFIG = toml.load("config/config.toml")
except (FileNotFoundError, Exception):
    CONFIG = {}

BODY_CFG = CONFIG.get("body_double", {})
DRIFT_THRESHOLD_MIN = BODY_CFG.get("drift_threshold_minutes", 10)
NUDGE_COOLDOWN_MIN = BODY_CFG.get("nudge_cooldown_minutes", 15)


# ---------------------------------------------------------------------------
# Window focus detection (cross-platform)
# ---------------------------------------------------------------------------

def get_active_window_info() -> Optional[dict]:
    """Get info about the currently focused window. Returns None if unavailable."""
    try:
        import subprocess, platform
        system = platform.system()

        if system == "Darwin":
            # macOS: use osascript
            script = (
                'tell application "System Events"\n'
                "  set frontApp to first application process whose frontmost is true\n"
                "  set appName to name of frontApp\n"
                "end tell\n"
                "return appName"
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            return {"app": result.stdout.strip(), "platform": "darwin"}

        elif system == "Windows":
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return {"app": buf.value, "platform": "windows"}

        elif system == "Linux":
            # xdotool fallback
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=5,
            )
            return {"app": result.stdout.strip(), "platform": "linux"}

    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

class FocusMonitor:
    """Tracks focus drift over time and triggers gentle nudges."""

    def __init__(
        self,
        target_apps: Optional[list[str]] = None,
        drift_threshold_min: int = DRIFT_THRESHOLD_MIN,
        nudge_cooldown_min: int = NUDGE_COOLDOWN_MIN,
    ):
        self.target_apps = [a.lower() for a in (target_apps or [])]
        self.drift_threshold = drift_threshold_min * 60  # seconds
        self.nudge_cooldown = nudge_cooldown_min * 60
        self.last_focus_time: Optional[float] = None
        self.last_nudge_time: float = 0
        self._log_path = Path("data/focus_log.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _is_focused(self, app_info: Optional[dict]) -> bool:
        """Check if the current window is a target app (or if no targets, always True)."""
        if not self.target_apps or not app_info:
            return True
        app_name = app_info.get("app", "").lower()
        return any(target in app_name for target in self.target_apps)

    def _log_event(self, event: str, details: dict):
        """Append a structured log entry."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **details,
        }
        with open(self._log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _generate_nudge(self) -> str:
        """Generate a calm, supportive spoken nudge."""
        nudges = [
            "Hey, just checking in — want to come back to what you were working on?",
            "Gentle nudge: your focus drifted a bit. No rush, just a heads-up.",
            "How about returning to the task at hand? You've got this.",
            "Just a soft reminder — your main task is still waiting.",
            "No pressure, but you might want to refocus when you're ready.",
        ]
        # Use Ollama for a contextual nudge if available
        if HAS_OLLAMA:
            try:
                response = ollama.chat(
                    model="llama3.1:latest",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a calm ADHD body-double. "
                                "Generate a single, gentle nudge sentence (≤15 words) "
                                "to help someone refocus. Be supportive, never scolding. "
                                "No exclamation marks. Return ONLY the sentence."
                            ),
                        },
                        {"role": "user", "content": "The user drifted from their task."},
                    ],
                    options={"temperature": 0.7},
                )
                return response["message"]["content"].strip()
            except Exception:
                pass
        import random
        return random.choice(nudges)

    def tick(self) -> Optional[dict]:
        """Call periodically (e.g., every 30s). Returns nudge info if one should fire."""
        now = time.time()
        app_info = get_active_window_info()
        is_focused = self._is_focused(app_info)

        if is_focused:
            self.last_focus_time = now
            self._log_event("focus", {"app": app_info.get("app", "unknown") if app_info else "unknown"})
            return None

        # Drift detected
        if self.last_focus_time is None:
            self.last_focus_time = now
            return None

        drift_seconds = now - self.last_focus_time
        self._log_event("drift", {"drift_seconds": int(drift_seconds), "app": app_info.get("app", "unknown") if app_info else "unknown"})

        # Only nudge if past threshold AND cooldown
        if drift_seconds >= self.drift_threshold and (now - self.last_nudge_time) >= self.nudge_cooldown:
            self.last_nudge_time = now
            nudge_text = self._generate_nudge()
            self._log_event("nudge", {"nudge": nudge_text, "drift_seconds": int(drift_seconds)})
            return {"text": nudge_text, "drift_seconds": int(drift_seconds)}

        return None


# ---------------------------------------------------------------------------
# Memory purge (Data Sovereignty — Phase 9)
# ---------------------------------------------------------------------------

def purge_all_memory():
    """One-click purge: delete all stored memory from Qdrant.

    Returns dict with status and verification.
    """
    try:
        from qdrant_client import QdrantClient
        mem_cfg = CONFIG.get("memory", {})
        client = QdrantClient(host="localhost", port=6333)
        collection = mem_cfg.get("collection_name", "adhd_memory")
        client.delete_collection(collection_name=collection)
        # Verify deletion
        collections = [c.name for c in client.get_collections().collections]
        if collection not in collections:
            print(f"✅ Collection '{collection}' deleted and verified.")
            return {"status": "success", "message": f"Collection '{collection}' purged and verified."}
        else:
            print(f"⚠️  Collection '{collection}' still exists after delete attempt.")
            return {"status": "error", "message": f"Collection '{collection}' could not be deleted."}
    except Exception as e:
        print(f"⚠️  Purge failed: {e}")
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("🔍 Body Double Agent — monitoring focus (Ctrl+C to stop)")
    monitor = FocusMonitor()
    try:
        while True:
            nudge = monitor.tick()
            if nudge:
                print(f"  💬 [{nudge['drift_seconds']}s drift] {nudge['text']}")
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped.")

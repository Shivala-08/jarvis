"""Monitor Operative Agent — persistent monitoring with state across sessions.

Inspired by OpenJarvis's monitor_operative agent that maintains state
across runs and uses memory to track changes over time.

Features:
- Persistent state across monitoring sessions
- Memory integration for context
- Change detection and alerting
- Compression and retrieval of historical data
- Long-horizon monitoring (days, weeks, months)
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


class MonitorState:
    """Persistent state for the monitor operative."""
    
    def __init__(self, state_path: str = "data/monitor_state.json"):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state: Dict[str, Any] = {}
        self._load()
    
    def _load(self) -> None:
        """Load state from disk."""
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    self._state = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load monitor state: {e}")
                self._state = {}
    
    def _save(self) -> None:
        """Persist state to disk."""
        try:
            with open(self.state_path, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save monitor state: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self._state.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set a state value."""
        self._state[key] = value
        self._save()
    
    def update(self, data: Dict[str, Any]) -> None:
        """Update multiple state values."""
        self._state.update(data)
        self._save()
    
    def clear(self) -> None:
        """Clear all state."""
        self._state = {}
        self._save()


class MonitorOperative:
    """Persistent monitoring agent that maintains state across sessions.
    
    Usage:
        monitor = MonitorOperative("email_monitor")
        
        # First run
        monitor.start_session()
        emails = check_emails()
        monitor.record_observation("new_emails", len(emails))
        monitor.end_session()
        
        # Later run - state persists
        monitor.start_session()
        prev_count = monitor.get_state("new_emails", 0)
        # Compare with current state
    """
    
    def __init__(self, monitor_id: str, memory_engine: Any = None):
        self.monitor_id = monitor_id
        self.memory_engine = memory_engine
        self.state = MonitorState(f"data/monitor_{monitor_id}.json")
        self._session_start = None
        self._observations: List[Dict[str, Any]] = []
    
    def start_session(self) -> None:
        """Start a monitoring session."""
        self._session_start = datetime.now(timezone.utc)
        self._observations = []
        
        # Update session count
        session_count = self.state.get("session_count", 0) + 1
        self.state.set("session_count", session_count)
        self.state.set("last_session_start", self._session_start.isoformat())
        
        logger.info(f"Monitor {self.monitor_id} session started (#{session_count})")
    
    def end_session(self) -> None:
        """End the current monitoring session."""
        if self._session_start is None:
            return
        
        session_duration = (datetime.now(timezone.utc) - self._session_start).total_seconds()
        
        # Update state
        self.state.set("last_session_end", datetime.now(timezone.utc).isoformat())
        self.state.set("last_session_duration", session_duration)
        self.state.set("last_session_observations", len(self._observations))
        
        # Store session summary in memory if available
        if self.memory_engine and self._observations:
            summary = self._generate_session_summary()
            self.memory_engine.memory.add(
                f"Monitor {self.monitor_id} session: {summary}",
                user_id="monitor_operative",
                metadata={
                    "source": "monitor_operative",
                    "monitor_id": self.monitor_id,
                    "session_duration": session_duration,
                    "observations": len(self._observations),
                },
            )
        
        logger.info(
            f"Monitor {self.monitor_id} session ended "
            f"({session_duration:.1f}s, {len(self._observations)} observations)"
        )
        
        self._session_start = None
        self._observations = []
    
    def record_observation(self, key: str, value: Any, metadata: Optional[Dict] = None) -> None:
        """Record an observation during the session."""
        observation = {
            "key": key,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        
        self._observations.append(observation)
        
        # Update running stats
        stats = self.state.get("stats", {})
        if key not in stats:
            stats[key] = {"count": 0, "values": []}
        
        stats[key]["count"] += 1
        stats[key]["values"].append(value)
        
        # Keep only last 100 values per key
        if len(stats[key]["values"]) > 100:
            stats[key]["values"] = stats[key]["values"][-100:]
        
        self.state.set("stats", stats)
    
    def detect_changes(self, key: str, threshold: float = 0.1) -> Dict[str, Any]:
        """Detect significant changes in a monitored metric."""
        stats = self.state.get("stats", {})
        if key not in stats or len(stats[key]["values"]) < 2:
            return {"changed": False, "reason": "insufficient_data"}
        
        values = stats[key]["values"]
        recent = values[-1]
        historical_avg = sum(values[:-1]) / len(values[:-1])
        
        if historical_avg == 0:
            change_pct = 100 if recent != 0 else 0
        else:
            change_pct = abs(recent - historical_avg) / historical_avg * 100
        
        changed = change_pct > threshold * 100
        
        return {
            "changed": changed,
            "current": recent,
            "historical_avg": historical_avg,
            "change_pct": change_pct,
            "threshold": threshold * 100,
        }
    
    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a state value."""
        return self.state.get(key, default)
    
    def set_state(self, key: str, value: Any) -> None:
        """Set a state value."""
        self.state.set(key, value)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        stats = self.state.get("stats", {})
        summary = {}
        for key, data in stats.items():
            values = data["values"]
            if values:
                summary[key] = {
                    "count": data["count"],
                    "latest": values[-1],
                    "avg": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                }
        return summary
    
    def _generate_session_summary(self) -> str:
        """Generate a human-readable summary of the session."""
        if not self._observations:
            return "No observations recorded"
        
        # Count observations by type
        obs_counts = {}
        for obs in self._observations:
            key = obs["key"]
            obs_counts[key] = obs_counts.get(key, 0) + 1
        
        # Build summary
        parts = []
        for key, count in obs_counts.items():
            parts.append(f"{count} {key} observations")
        
        return ", ".join(parts)
    
    def compress_history(self, days_to_keep: int = 30) -> int:
        """Compress old observation history to save space.
        
        Returns the number of observations removed.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - (days_to_keep * 86400)
        
        stats = self.state.get("stats", {})
        total_removed = 0
        
        for key in stats:
            values = stats[key]["values"]
            # Keep only recent values (simplified compression)
            if len(values) > 100:
                removed = len(values) - 100
                stats[key]["values"] = values[-100:]
                total_removed += removed
        
        self.state.set("stats", stats)
        return total_removed


# ---------------------------------------------------------------------------
# Pre-configured monitors for ADHD Co-Processor
# ---------------------------------------------------------------------------

class FocusMonitor(MonitorOperative):
    """Monitor for tracking focus and attention patterns."""
    
    def __init__(self, memory_engine: Any = None):
        super().__init__("focus", memory_engine)
    
    def record_focus_event(self, focused: bool, app: str = "", duration_seconds: float = 0) -> None:
        """Record a focus event."""
        self.record_observation(
            "focus",
            1 if focused else 0,
            {"app": app, "duration": duration_seconds},
        )
    
    def get_focus_stats(self) -> Dict[str, Any]:
        """Get focus statistics."""
        stats = self.state.get("stats", {})
        focus_data = stats.get("focus", {})
        values = focus_data.get("values", [])
        
        if not values:
            return {"total_sessions": 0, "focus_ratio": 0}
        
        total = len(values)
        focused = sum(values)
        
        return {
            "total_sessions": total,
            "focused_sessions": focused,
            "focus_ratio": focused / total if total > 0 else 0,
            "last_focus": values[-1] if values else None,
        }


class TaskMonitor(MonitorOperative):
    """Monitor for tracking task completion patterns."""
    
    def __init__(self, memory_engine: Any = None):
        super().__init__("tasks", memory_engine)
    
    def record_task_start(self, task_text: str, estimated_minutes: int) -> None:
        """Record task start."""
        self.record_observation(
            "task_start",
            estimated_minutes,
            {"task": task_text},
        )
    
    def record_task_completion(self, task_text: str, actual_minutes: int, estimated_minutes: int) -> None:
        """Record task completion."""
        accuracy = actual_minutes / estimated_minutes if estimated_minutes > 0 else 1
        self.record_observation(
            "task_completion",
            actual_minutes,
            {"task": task_text, "estimated": estimated_minutes, "accuracy": accuracy},
        )
    
    def get_task_stats(self) -> Dict[str, Any]:
        """Get task completion statistics."""
        stats = self.state.get("stats", {})
        start_data = stats.get("task_start", {})
        completion_data = stats.get("task_completion", {})
        
        starts = start_data.get("count", 0)
        completions = completion_data.get("count", 0)
        
        # Calculate average accuracy
        accuracy_values = []
        for obs in self._observations:
            if obs["key"] == "task_completion" and "accuracy" in obs.get("metadata", {}):
                accuracy_values.append(obs["metadata"]["accuracy"])
        
        avg_accuracy = sum(accuracy_values) / len(accuracy_values) if accuracy_values else 1
        
        return {
            "tasks_started": starts,
            "tasks_completed": completions,
            "completion_rate": completions / starts if starts > 0 else 0,
            "avg_time_accuracy": avg_accuracy,
        }

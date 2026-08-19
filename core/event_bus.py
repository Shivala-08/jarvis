"""Event Bus — decoupled communication between agents and services.

Inspired by OpenJarvis's event-driven architecture for clean component
integration and extensibility.

Usage:
    from core.event_bus import EventBus, EventType, publish, subscribe
    
    # Subscribe to events
    def on_braindump(data):
        print(f"New braindump: {data}")
    
    subscribe(EventType.BRAINDUMP_COMPLETED, on_braindump)
    
    # Publish events
    publish(EventType.BRAINDUMP_COMPLETED, {"text": "my thoughts", "count": 3})
"""
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Event types for the ADHD Co-Processor."""
    # Agent events
    BRAINDUMP_COMPLETED = "braindump_completed"
    SCHEDULE_UPDATED = "schedule_updated"
    STUDY_PLAN_GENERATED = "study_plan_generated"
    SPRINT_COMPLETED = "sprint_completed"
    
    # Memory events
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"
    MEMORY_PURGED = "memory_purged"
    
    # Voice events
    VOICE_TRANSCRIBED = "voice_transcribed"
    VOICE_RESPONSE_GENERATED = "voice_response_generated"
    
    # Focus events
    FOCUS_DRIFT_DETECTED = "focus_drift_detected"
    NUDGE_FIRED = "nudge_fired"
    
    # System events
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    HEALTH_CHECK = "health_check"
    
    # Learning events
    TASK_COMPLETED = "task_completed"
    TASK_DURATION_RECORDED = "task_duration_recorded"
    ALPHA_UPDATED = "alpha_updated"


class Event:
    """An event with type, data, and metadata."""
    
    def __init__(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        source: str = "",
    ):
        self.event_type = event_type
        self.data = data
        self.source = source
        self.timestamp = None  # Will be set if needed
    
    def __repr__(self) -> str:
        return f"Event({self.event_type.value}, data={self.data}, source={self.source})"


class EventBus:
    """Thread-safe event bus for decoupled communication.
    
    Features:
    - Thread-safe subscription and publishing
    - Synchronous event handling (blocking)
    - Error isolation: handler errors don't affect other handlers
    - Event history for debugging
    """
    
    def __init__(self, max_history: int = 100):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._lock = threading.Lock()
        self._event_history: List[Event] = []
        self._max_history = max_history
        self._running = False
    
    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """Subscribe to an event type."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)
                logger.debug(f"Subscribed to {event_type.value}: {handler.__name__}")
    
    def unsubscribe(self, event_type: EventType, handler: Callable) -> None:
        """Unsubscribe from an event type."""
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                    logger.debug(f"Unsubscribed from {event_type.value}: {handler.__name__}")
                except ValueError:
                    pass
    
    def publish(self, event: Event) -> None:
        """Publish an event to all subscribers.
        
        Events are handled synchronously. Handler errors are logged
        but don't prevent other handlers from running.
        """
        with self._lock:
            # Record event history
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history.pop(0)
        
        # Get subscribers (outside lock to avoid deadlock)
        subscribers = []
        with self._lock:
            subscribers = list(self._subscribers.get(event.event_type, []))
        
        # Call each subscriber
        for handler in subscribers:
            try:
                handler(event)
            except Exception as e:
                logger.error(
                    f"Error in event handler {handler.__name__} for {event.event_type.value}: {e}"
                )
    
    def get_history(self, event_type: Optional[EventType] = None, limit: int = 50) -> List[Event]:
        """Get recent events, optionally filtered by type."""
        with self._lock:
            events = self._event_history
            if event_type:
                events = [e for e in events if e.event_type == event_type]
            return events[-limit:]
    
    def clear_history(self) -> None:
        """Clear event history."""
        with self._lock:
            self._event_history.clear()


# Global event bus instance
_global_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Get or create the global event bus."""
    global _global_bus
    if _global_bus is None:
        with _bus_lock:
            if _global_bus is None:
                _global_bus = EventBus()
    return _global_bus


def publish(event_type: EventType, data: Dict[str, Any], source: str = "") -> None:
    """Convenience function to publish an event."""
    bus = get_event_bus()
    event = Event(event_type, data, source)
    bus.publish(event)


def subscribe(event_type: EventType, handler: Callable) -> None:
    """Convenience function to subscribe to an event."""
    bus = get_event_bus()
    bus.subscribe(event_type, handler)


def unsubscribe(event_type: EventType, handler: Callable) -> None:
    """Convenience function to unsubscribe from an event."""
    bus = get_event_bus()
    bus.unsubscribe(event_type, handler)


# ---------------------------------------------------------------------------
# Event handlers for common patterns
# ---------------------------------------------------------------------------

def _log_all_events(event: Event) -> None:
    """Log all events for debugging."""
    logger.info(f"Event: {event.event_type.value} | Source: {event.source} | Data: {event.data}")


def setup_default_handlers() -> None:
    """Set up default event handlers for logging and common operations."""
    bus = get_event_bus()
    
    # Log all events (optional, can be disabled)
    # bus.subscribe(EventType.BRAINDUMP_COMPLETED, _log_all_events)


# Auto-setup when module is imported
setup_default_handlers()

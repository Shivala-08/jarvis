"""Core modules for the ADHD Co-Processor.

Inspired by OpenJarvis's architecture for building local-first personal AI.
"""
from core.event_bus import (
    EventBus,
    Event,
    EventType,
    publish,
    subscribe,
    unsubscribe,
    get_event_bus,
)

from core.memory_service import (
    MemoryService,
    FactExtractor,
    build_memory_service,
    get_memory_service,
    stop_memory_service,
)

from core.skill_manager import (
    SkillManager,
    Skill,
    SkillType,
    SkillStep,
)

from core.eval_metrics import (
    MetricsCollector,
    LatencyTracker,
    EnergyEstimator,
    CostTracker,
    PerformanceDashboard,
    get_metrics_collector,
    get_latency_tracker,
    get_energy_estimator,
    get_cost_tracker,
    get_dashboard,
)

from core.cron_scheduler import (
    CronScheduler,
    ScheduledTask,
    parse_cron,
    matches_cron,
    get_next_run,
    get_scheduler,
    start_scheduler,
    stop_scheduler,
)

__all__ = [
    # Event Bus
    "EventBus",
    "Event",
    "EventType",
    "publish",
    "subscribe",
    "unsubscribe",
    "get_event_bus",
    
    # Memory Service
    "MemoryService",
    "FactExtractor",
    "build_memory_service",
    "get_memory_service",
    "stop_memory_service",
    
    # Skill Manager
    "SkillManager",
    "Skill",
    "SkillType",
    "SkillStep",
    
    # Evaluation Metrics
    "MetricsCollector",
    "LatencyTracker",
    "EnergyEstimator",
    "CostTracker",
    "PerformanceDashboard",
    "get_metrics_collector",
    "get_latency_tracker",
    "get_energy_estimator",
    "get_cost_tracker",
    "get_dashboard",
    
    # Cron Scheduler
    "CronScheduler",
    "ScheduledTask",
    "parse_cron",
    "matches_cron",
    "get_next_run",
    "get_scheduler",
    "start_scheduler",
    "stop_scheduler",
]

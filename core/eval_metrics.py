"""Evaluation Metrics — efficiency and performance tracking.

Inspired by OpenJarvis's approach to treating energy, FLOPs, latency,
and cost as first-class constraints alongside accuracy.

Features:
- Latency tracking for inference and operations
- Energy efficiency estimation
- Cost tracking (even for local inference)
- Performance dashboards
- Adaptive recommendations based on metrics
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
class MetricEntry:
    """A single metric measurement."""
    name: str
    value: float
    unit: str
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class MetricsCollector:
    """Collects and stores performance metrics."""
    
    def __init__(self, storage_path: str = "data/metrics.jsonl"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._metrics: List[MetricEntry] = []
        self._lock = None
    
    def _get_lock(self):
        if self._lock is None:
            import threading
            self._lock = threading.Lock()
        return self._lock
    
    def record(
        self,
        name: str,
        value: float,
        unit: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a metric."""
        entry = MetricEntry(
            name=name,
            value=value,
            unit=unit,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=metadata or {},
        )
        
        with self._get_lock():
            self._metrics.append(entry)
            # Keep only last 1000 metrics in memory
            if len(self._metrics) > 1000:
                self._metrics = self._metrics[-1000:]
        
        # Persist to file
        self._persist(entry)
    
    def _persist(self, entry: MetricEntry) -> None:
        """Persist metric to file."""
        try:
            with open(self.storage_path, "a") as f:
                data = {
                    "name": entry.name,
                    "value": entry.value,
                    "unit": entry.unit,
                    "timestamp": entry.timestamp,
                    "metadata": entry.metadata,
                }
                f.write(json.dumps(data) + "\n")
        except Exception as e:
            logger.debug(f"Failed to persist metric: {e}")
    
    def get_recent(self, name: Optional[str] = None, limit: int = 100) -> List[MetricEntry]:
        """Get recent metrics, optionally filtered by name."""
        with self._get_lock():
            metrics = self._metrics
            if name:
                metrics = [m for m in metrics if m.name == name]
            return metrics[-limit:]
    
    def get_stats(self, name: str, window_minutes: int = 60) -> Dict[str, float]:
        """Get statistics for a metric over a time window."""
        cutoff = time.time() - (window_minutes * 60)
        
        with self._get_lock():
            values = []
            for m in self._metrics:
                if m.name == name:
                    # Parse timestamp
                    try:
                        ts = datetime.fromisoformat(m.timestamp.replace("Z", "+00:00"))
                        if ts.timestamp() >= cutoff:
                            values.append(m.value)
                    except:
                        pass
        
        if not values:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "sum": 0}
        
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "sum": sum(values),
        }


class LatencyTracker:
    """Tracks latency for operations."""
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
    
    def track(self, operation: str, metadata: Optional[Dict[str, Any]] = None):
        """Context manager to track operation latency."""
        return LatencyContext(self, operation, metadata)


class LatencyContext:
    """Context manager for latency tracking."""
    
    def __init__(self, tracker: LatencyTracker, operation: str, metadata: Optional[Dict[str, Any]]):
        self.tracker = tracker
        self.operation = operation
        self.metadata = metadata or {}
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is not None:
            duration_ms = (time.time() - self.start_time) * 1000
            self.tracker.collector.record(
                name=f"latency.{self.operation}",
                value=duration_ms,
                unit="ms",
                metadata={
                    **self.metadata,
                    "success": exc_type is None,
                },
            )
        return False


class EnergyEstimator:
    """Estimates energy consumption for local inference.
    
    Based on OpenJarvis's Intelligence Per Watt research showing that
    local language models handle 88.7% of queries with 5.3x efficiency
    improvement from 2023-2025.
    """
    
    # Approximate power consumption (watts) for different inference backends
    POWER_RATINGS = {
        "cpu": 15,      # Typical CPU inference
        "cuda": 75,     # GPU inference
        "mps": 25,      # Apple Silicon
        "unknown": 20,  # Default estimate
    }
    
    # Approximate tokens per second for different model sizes
    TOKENS_PER_SECOND = {
        "7b": 30,      # 7B parameter model
        "14b": 15,     # 14B parameter model
        "70b": 3,      # 70B parameter model
    }
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
    
    def estimate_inference_energy(
        self,
        model_size: str = "14b",
        backend: str = "cpu",
        tokens_generated: int = 100,
        duration_seconds: float = 0,
    ) -> Dict[str, float]:
        """Estimate energy consumption for an inference call.
        
        Returns energy in watt-hours and equivalent carbon emissions.
        """
        # Get power rating
        power_watts = self.POWER_RATINGS.get(backend, self.POWER_RATINGS["unknown"])
        
        # Estimate duration if not provided
        if duration_seconds <= 0:
            tps = self.TOKENS_PER_SECOND.get(model_size, 10)
            duration_seconds = tokens_generated / tps
        
        # Calculate energy
        energy_wh = (power_watts * duration_seconds) / 3600  # watt-hours
        
        # Carbon emissions (US average: 0.4 kg CO2 per kWh)
        carbon_kg = energy_wh * 0.0004  # kg CO2
        
        # Record metrics
        self.collector.record(
            name="energy.inference",
            value=energy_wh,
            unit="wh",
            metadata={
                "model_size": model_size,
                "backend": backend,
                "tokens": tokens_generated,
                "duration_seconds": duration_seconds,
                "carbon_kg": carbon_kg,
            },
        )
        
        return {
            "energy_wh": energy_wh,
            "carbon_kg": carbon_kg,
            "power_watts": power_watts,
            "duration_seconds": duration_seconds,
        }


class CostTracker:
    """Tracks computational cost (even for local inference).
    
    OpenJarvis treats cost as a first-class constraint. For local inference,
    we track "compute cost" in terms of GPU/CPU time and opportunity cost.
    """
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
    
    def record_inference_cost(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        duration_seconds: float,
        backend: str = "ollama",
    ) -> Dict[str, Any]:
        """Record the cost of an inference call.
        
        For local inference, cost is measured in:
        - Compute time (seconds)
        - Tokens per second (throughput)
        - Queue wait time (if applicable)
        """
        throughput = tokens_out / duration_seconds if duration_seconds > 0 else 0
        
        cost_data = {
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_seconds": duration_seconds,
            "throughput_tps": throughput,
            "backend": backend,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        self.collector.record(
            name="cost.inference",
            value=duration_seconds,
            unit="seconds",
            metadata=cost_data,
        )
        
        return cost_data


class PerformanceDashboard:
    """Aggregates metrics into a performance dashboard."""
    
    def __init__(self, collector: MetricsCollector):
        self.collector = collector
    
    def get_dashboard(self, window_minutes: int = 60) -> Dict[str, Any]:
        """Get a comprehensive performance dashboard."""
        return {
            "latency": {
                "braindump": self.collector.get_stats("latency.braindump", window_minutes),
                "schedule": self.collector.get_stats("latency.schedule", window_minutes),
                "study": self.collector.get_stats("latency.study", window_minutes),
                "memory_search": self.collector.get_stats("latency.memory_search", window_minutes),
            },
            "energy": {
                "inference": self.collector.get_stats("energy.inference", window_minutes),
            },
            "cost": {
                "inference": self.collector.get_stats("cost.inference", window_minutes),
            },
            "system": {
                "uptime_minutes": window_minutes,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    
    def get_recommendations(self) -> List[str]:
        """Get recommendations based on metrics."""
        recommendations = []
        
        # Check latency
        braindump_stats = self.collector.get_stats("latency.braindump", window_minutes=60)
        if braindump_stats["avg"] > 5000:  # > 5 seconds
            recommendations.append(
                f"Braindump latency is high ({braindump_stats['avg']:.0f}ms avg). "
                "Consider using a smaller model or quantized version."
            )
        
        # Check energy
        energy_stats = self.collector.get_stats("energy.inference", window_minutes=60)
        if energy_stats["sum"] > 1:  # > 1 Wh
            recommendations.append(
                f"High energy consumption ({energy_stats['sum']:.2f} Wh). "
                "Consider batching inference calls."
            )
        
        # Check throughput
        cost_stats = self.collector.get_stats("cost.inference", window_minutes=60)
        if cost_stats["count"] > 0:
            avg_throughput = sum(
                m.metadata.get("throughput_tps", 0)
                for m in self.collector.get_recent("cost.inference", limit=10)
            ) / min(10, cost_stats["count"])
            
            if avg_throughput < 10:
                recommendations.append(
                    f"Low throughput ({avg_throughput:.1f} tokens/sec). "
                    "Consider upgrading hardware or using a more efficient model."
                )
        
        return recommendations


# Global instances
_collector: Optional[MetricsCollector] = None
_latency_tracker: Optional[LatencyTracker] = None
_energy_estimator: Optional[EnergyEstimator] = None
_cost_tracker: Optional[CostTracker] = None
_dashboard: Optional[PerformanceDashboard] = None
_lock = None


def _get_lock():
    global _lock
    if _lock is None:
        import threading
        _lock = threading.RLock()
    return _lock


def get_metrics_collector() -> MetricsCollector:
    """Get or create the global metrics collector."""
    global _collector
    if _collector is None:
        with _get_lock():
            if _collector is None:
                _collector = MetricsCollector()
    return _collector


def get_latency_tracker() -> LatencyTracker:
    """Get or create the global latency tracker."""
    global _latency_tracker
    if _latency_tracker is None:
        with _get_lock():
            if _latency_tracker is None:
                _latency_tracker = LatencyTracker(get_metrics_collector())
    return _latency_tracker


def get_energy_estimator() -> EnergyEstimator:
    """Get or create the global energy estimator."""
    global _energy_estimator
    if _energy_estimator is None:
        with _get_lock():
            if _energy_estimator is None:
                _energy_estimator = EnergyEstimator(get_metrics_collector())
    return _energy_estimator


def get_cost_tracker() -> CostTracker:
    """Get or create the global cost tracker."""
    global _cost_tracker
    if _cost_tracker is None:
        with _get_lock():
            if _cost_tracker is None:
                _cost_tracker = CostTracker(get_metrics_collector())
    return _cost_tracker


def get_dashboard() -> PerformanceDashboard:
    """Get or create the global performance dashboard."""
    global _dashboard
    if _dashboard is None:
        with _get_lock():
            if _dashboard is None:
                _dashboard = PerformanceDashboard(get_metrics_collector())
    return _dashboard

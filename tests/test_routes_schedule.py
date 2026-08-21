"""Tests for routes/schedule.py — schedule, rebalance, sprint, task tracking.

Tests route handler functions directly. Patches lazy imports inside function bodies.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /api/schedule
# ---------------------------------------------------------------------------


class TestScheduleEndpoint:
    @patch("agents.scheduler_agent.build_schedule")
    @patch("agents.scheduler_agent._estimate_alpha", return_value=1.5)
    @patch("routes.schedule.get_task_list")
    @patch("core.eval_metrics.get_latency_tracker")
    def test_returns_schedule(self, mock_latency, mock_tasks, mock_alpha, mock_build):
        from routes.schedule import api_schedule

        mock_latency.return_value = MagicMock()
        mock_tasks.return_value = [
            {"text": "Task A", "estimated_minutes": 30, "priority": "now"}
        ]
        mock_build.return_value = [
            {"type": "task", "label": "Task A", "start": "2026-08-22T09:00", "end": "2026-08-22T09:30", "scaled_minutes": 30}
        ]

        result = api_schedule()
        assert "schedule" in result
        assert "alpha" in result
        assert result["alpha"] == 1.5


# ---------------------------------------------------------------------------
# POST /api/rebalance
# ---------------------------------------------------------------------------


class TestRebalanceEndpoint:
    @patch("agents.scheduler_agent.rebalance")
    @patch("agents.scheduler_agent.build_schedule")
    @patch("agents.scheduler_agent._estimate_alpha", return_value=1.5)
    @patch("routes.schedule.get_task_list")
    def test_rebalance_returns_suggestion(self, mock_tasks, mock_alpha, mock_build, mock_rebalance):
        from routes.schedule import api_rebalance
        from core.error_models import RebalanceRequest

        mock_tasks.return_value = [{"text": "Task", "estimated_minutes": 30, "priority": "now"}]
        mock_build.return_value = [{"type": "task", "label": "Task"}]
        mock_rebalance.return_value = (
            [{"type": "task", "label": "Task"}],
            "Take a breath — we've got this.",
        )

        req = RebalanceRequest(missed_block_id=None)
        result = api_rebalance(req)
        assert "schedule" in result
        assert "suggestion" in result


# ---------------------------------------------------------------------------
# POST /api/sprint
# ---------------------------------------------------------------------------


class TestSprintEndpoint:
    @patch("agents.scheduler_agent.generate_micro_sprint")
    def test_returns_suggestion(self, mock_sprint):
        from routes.schedule import api_sprint
        from core.error_models import SprintRequest

        mock_sprint.return_value = "Try the first 15 minutes — you can always stop."

        req = SprintRequest(task="Write report")
        result = api_sprint(req)
        assert "suggestion" in result


# ---------------------------------------------------------------------------
# POST /api/tasks/start
# ---------------------------------------------------------------------------


class TestTaskStartEndpoint:
    def test_starts_task(self):
        from routes.schedule import api_start_task
        from core.error_models import TaskStartRequest

        req = TaskStartRequest(task_text="Write report", estimated_minutes=30)
        result = api_start_task(req)
        assert result["status"] == "started"

    def test_default_estimate(self):
        from routes.schedule import api_start_task
        from core.error_models import TaskStartRequest

        req = TaskStartRequest(task_text="Quick task")
        result = api_start_task(req)
        assert result["status"] == "started"


# ---------------------------------------------------------------------------
# POST /api/tasks/complete
# ---------------------------------------------------------------------------


class TestTaskCompleteEndpoint:
    def test_completes_task(self):
        from routes.schedule import api_complete_task
        from core.error_models import TaskCompleteRequest

        req = TaskCompleteRequest(task_text="Write report", actual_minutes=25.5, estimated_minutes=30)
        result = api_complete_task(req)
        assert result["status"] == "completed"
        assert "alpha" in result


# ---------------------------------------------------------------------------
# GET /api/tasks/completions
# ---------------------------------------------------------------------------


class TestTaskCompletionsEndpoint:
    def test_returns_history(self):
        from routes.schedule import api_get_completions

        result = api_get_completions()
        assert "completions" in result
        assert "stats" in result


# ---------------------------------------------------------------------------
# GET /api/tasks/alpha
# ---------------------------------------------------------------------------


class TestTaskAlphaEndpoint:
    def test_returns_alpha(self):
        from routes.schedule import api_get_alpha

        result = api_get_alpha()
        assert "alpha" in result
        assert "stats" in result

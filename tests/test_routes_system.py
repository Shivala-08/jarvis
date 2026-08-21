"""Tests for routes/system.py — health, diagnostics, dashboard, skills, scheduler.

Tests route handler functions directly (avoids TestClient startup issues).
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_ok(self):
        from routes.system import api_health

        result = api_health()
        assert result["status"] == "ok"
        assert result["service"] == "adhd-copilot"
        assert "version" in result
        assert "components" in result

    def test_components_structure(self):
        from routes.system import api_health

        result = api_health()
        components = result["components"]
        assert "memory_service" in components
        assert "event_bus" in components
        assert "skills" in components
        assert "scheduler" in components


# ---------------------------------------------------------------------------
# GET /api/diagnostics
# ---------------------------------------------------------------------------


class TestDiagnosticsEndpoint:
    @patch("core.diagnostics.run_model_diagnostics")
    def test_returns_diagnostics(self, mock_diag):
        from routes.system import api_diagnostics

        mock_diag.return_value = {"model": "qwen3.5:9b", "status": "available"}
        result = api_diagnostics()
        assert result["model"] == "qwen3.5:9b"


# ---------------------------------------------------------------------------
# GET /api/dashboard
# ---------------------------------------------------------------------------


class TestDashboardEndpoint:
    @patch("core.eval_metrics.get_dashboard")
    def test_returns_dashboard(self, mock_get_dash):
        from routes.system import api_dashboard

        mock_dashboard = MagicMock()
        mock_dashboard.get_dashboard.return_value = {
            "latency": {"braindump": {"avg": 1500}},
            "energy": {"inference": {"sum": 0.5}},
        }
        mock_get_dash.return_value = mock_dashboard

        result = api_dashboard()
        assert "latency" in result


# ---------------------------------------------------------------------------
# GET /api/recommendations
# ---------------------------------------------------------------------------


class TestRecommendationsEndpoint:
    @patch("core.eval_metrics.get_dashboard")
    def test_returns_recommendations(self, mock_get_dash):
        from routes.system import api_recommendations

        mock_dashboard = MagicMock()
        mock_dashboard.get_recommendations.return_value = [
            "Consider using a faster model"
        ]
        mock_get_dash.return_value = mock_dashboard

        result = api_recommendations()
        assert "recommendations" in result


# ---------------------------------------------------------------------------
# GET /api/skills
# ---------------------------------------------------------------------------


class TestSkillsEndpoint:
    @patch("routes.system.SkillManager")
    def test_returns_skills(self, MockSkillManager):
        from routes.system import api_skills

        mock_skill = MagicMock()
        mock_skill.name = "test-skill"
        mock_skill.description = "A test skill"
        mock_skill.skill_type.value = "builtin"
        mock_skill.tags = ["test"]
        mock_skill.get_stats.return_value = {"invocations": 5, "success_rate": 100.0}

        MockSkillManager.list_skills.return_value = [mock_skill]
        MockSkillManager.get_skill_catalog.return_value = {"available_skills": ["test-skill"]}

        result = api_skills()
        assert "skills" in result
        assert len(result["skills"]) == 1
        assert result["skills"][0]["name"] == "test-skill"


# ---------------------------------------------------------------------------
# POST /api/skills/{name}/invoke
# ---------------------------------------------------------------------------


class TestSkillInvokeEndpoint:
    @patch("routes.system.SkillManager")
    def test_invoke_success(self, MockSkillManager):
        from routes.system import api_invoke_skill

        MockSkillManager.invoke_skill.return_value = {"result": "done"}
        result = api_invoke_skill("test-skill")
        assert result["success"] is True

    @patch("routes.system.SkillManager")
    def test_invoke_failure(self, MockSkillManager):
        from routes.system import api_invoke_skill

        MockSkillManager.invoke_skill.side_effect = ValueError("Skill not found")
        result = api_invoke_skill("unknown")
        assert result["success"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# GET /api/scheduler/tasks
# ---------------------------------------------------------------------------


class TestSchedulerTasksEndpoint:
    @patch("core.cron_scheduler.get_scheduler")
    def test_returns_tasks(self, mock_get_sched):
        from routes.system import api_list_scheduled_tasks

        mock_scheduler = MagicMock()
        mock_task = MagicMock()
        mock_task.task_id = "daily_digest"
        mock_task.prompt = "Summarize today"
        mock_task.cron_expression = "0 8 * * *"
        mock_task.agent_type = "braindump"
        mock_task.enabled = True
        mock_task.last_run = ""
        mock_task.next_run = "2026-08-23T08:00:00"
        mock_task.run_count = 5
        mock_scheduler.list_tasks.return_value = [mock_task]
        mock_get_sched.return_value = mock_scheduler

        result = api_list_scheduled_tasks()
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["task_id"] == "daily_digest"


# ---------------------------------------------------------------------------
# GET /api/monitor/stats
# ---------------------------------------------------------------------------


class TestMonitorStatsEndpoint:
    @patch("agents.monitor_operative.FocusMonitor")
    @patch("agents.monitor_operative.TaskMonitor")
    def test_returns_stats(self, MockFocus, MockTask):
        from routes.system import api_monitor_stats

        mock_focus = MagicMock()
        mock_focus.get_focus_stats.return_value = {"focus_ratio": 0.85, "total_sessions": 100}
        MockFocus.return_value = mock_focus

        mock_task = MagicMock()
        mock_task.get_task_stats.return_value = {"completed": 10, "pending": 3}
        MockTask.return_value = mock_task

        result = api_monitor_stats()
        assert "focus" in result
        assert "tasks" in result

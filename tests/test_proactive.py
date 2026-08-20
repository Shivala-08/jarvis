"""Tests for core/proactive.py — proactive speech triggers (Phase D)."""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mock_publish():
    """Patch the event bus publish to capture calls."""
    return patch("core.proactive.publish")


# ---------------------------------------------------------------------------
# Morning Briefing
# ---------------------------------------------------------------------------

class TestMorningBriefing:
    """Morning briefing should generate a calm schedule overview."""

    @patch("core.proactive._publish_speech")
    @patch("agents.scheduler_agent.build_schedule", return_value=[
        {"type": "task", "label": "Finish report", "start": "2025-01-15T09:00", "end": "2025-01-15T10:30", "scaled_minutes": 90},
    ])
    @patch("memory.adhd_memory.get_history", return_value=[])
    @patch("agents.scheduler_agent._estimate_alpha", return_value=1.6)
    @patch("memory.adhd_memory.ADHDMemoryEngine")
    def test_with_tasks(self, mock_engine_cls, mock_alpha, mock_history, mock_schedule, mock_publish):
        """With tasks on the list, should mention count and first task."""
        mock_engine = MagicMock()
        mock_engine.get_all_memories.return_value = [
            {"memory": "Finish report", "metadata": {"type": "task", "priority": "now", "estimated_minutes": 60}},
            {"memory": "Reply to email", "metadata": {"type": "task", "priority": "soon", "estimated_minutes": 15}},
        ]
        mock_engine_cls.return_value = mock_engine

        from core.proactive import morning_briefing
        text = morning_briefing()

        assert "2 thing" in text
        assert "Finish report" in text
        mock_publish.assert_called_once()

    @patch("core.proactive._publish_speech")
    @patch("memory.adhd_memory.ADHDMemoryEngine")
    def test_no_tasks(self, mock_engine_cls, mock_publish):
        """With no tasks, should say the day is free."""
        mock_engine = MagicMock()
        mock_engine.get_all_memories.return_value = []
        mock_engine_cls.return_value = mock_engine

        from core.proactive import morning_briefing
        text = morning_briefing()

        assert "No tasks" in text or "no tasks" in text.lower()
        assert "freedom" in text.lower()

    @patch("core.proactive._publish_speech")
    def test_error_returns_graceful_message(self, mock_publish):
        """If memory engine fails, should still return a message."""
        with patch("memory.adhd_memory.ADHDMemoryEngine", side_effect=Exception("Qdrant down")):
            from core.proactive import morning_briefing
            text = morning_briefing()

        assert "morning" in text.lower() or "trouble" in text.lower()
        mock_publish.assert_called_once()


# ---------------------------------------------------------------------------
# Missed Block
# ---------------------------------------------------------------------------

class TestMissedBlock:
    """Missed block should generate a calm nudge."""

    @patch("core.proactive._publish_speech")
    def test_basic_nudge(self, mock_publish):
        from core.proactive import on_missed_block
        block = {"label": "Finish report", "start": "09:00", "scaled_minutes": 60}
        text = on_missed_block(block)

        assert "Finish report" in text
        assert "slipped" in text.lower() or "missed" in text.lower()
        mock_publish.assert_called_once()

    @patch("core.proactive._publish_speech")
    def test_no_imperative_commands(self, mock_publish):
        """Should use supportive phrasing, not commands."""
        from core.proactive import on_missed_block
        text = on_missed_block({"label": "task", "scaled_minutes": 30})

        # Should not contain imperative phrases
        assert "do the" not in text.lower()
        assert "must" not in text.lower()
        assert "now" not in text.lower()


# ---------------------------------------------------------------------------
# Rebalance
# ---------------------------------------------------------------------------

class TestRebalance:
    """Rebalance should publish the scheduler's suggestion."""

    @patch("core.proactive._publish_speech")
    def test_publishes_suggestion(self, mock_publish):
        from core.proactive import on_rebalance
        text = on_rebalance(
            [{"label": "remaining task"}],
            "How about we start with 'remaining task'?",
        )

        assert "remaining task" in text
        mock_publish.assert_called_once()

    @patch("core.proactive._publish_speech")
    def test_empty_suggestion_gets_default(self, mock_publish):
        from core.proactive import on_rebalance
        text = on_rebalance([], "")

        assert "adjusted" in text.lower() or "got this" in text.lower()


# ---------------------------------------------------------------------------
# Idle Check
# ---------------------------------------------------------------------------

class TestIdleCheck:
    """Idle check should nudge only after sufficient idle time."""

    @patch("core.proactive._publish_speech")
    def test_short_idle_returns_none(self, mock_publish):
        """Less than 30 min idle → no nudge."""
        from core.proactive import idle_check
        result = idle_check(15)
        assert result is None
        mock_publish.assert_not_called()

    @patch("core.proactive._publish_speech")
    def test_30_min_idle_nudges(self, mock_publish):
        """30+ min idle → gentle check-in."""
        from core.proactive import idle_check
        text = idle_check(35)

        assert text is not None
        assert "35 minutes" in text
        mock_publish.assert_called_once()

    @patch("core.proactive._publish_speech")
    def test_hour_long_idle_stronger_nudge(self, mock_publish):
        """60+ min idle → stronger encouragement."""
        from core.proactive import idle_check
        text = idle_check(75)

        assert text is not None
        assert "75 minutes" in text
        assert "5 minutes" in text  # mentions small progress

    @patch("core.proactive._publish_speech")
    def test_no_imperative_in_idle(self, mock_publish):
        """Idle nudge should be supportive, not commanding."""
        from core.proactive import idle_check
        text = idle_check(45)

        assert "should" not in text.lower()
        assert "need to" not in text.lower()


# ---------------------------------------------------------------------------
# Session End
# ---------------------------------------------------------------------------

class TestSessionEnd:
    """Session end summary should reflect accomplishments."""

    @patch("core.proactive._publish_speech")
    def test_no_tasks(self, mock_publish):
        from core.proactive import session_end_summary
        text = session_end_summary(0, 0)

        assert "showing up" in text.lower() or "fresh start" in text.lower()

    @patch("core.proactive._publish_speech")
    def test_one_task(self, mock_publish):
        from core.proactive import session_end_summary
        text = session_end_summary(1, 25)

        assert "1 task" in text
        assert "25 minutes" in text

    @patch("core.proactive._publish_speech")
    def test_multiple_tasks(self, mock_publish):
        from core.proactive import session_end_summary
        text = session_end_summary(5, 90)

        assert "5 tasks" in text
        assert "90 minutes" in text
        assert "momentum" in text.lower()


# ---------------------------------------------------------------------------
# Event Bus Wiring
# ---------------------------------------------------------------------------

class TestEventWiring:
    """Proactive triggers should subscribe to event bus correctly."""

    @patch("core.cron_scheduler.get_scheduler")
    @patch("core.event_bus.subscribe")
    def test_register_subscribes_to_events(self, mock_subscribe, mock_scheduler):
        """register_proactive_triggers should subscribe to schedule/task events."""
        mock_sched = MagicMock()
        mock_sched.list_tasks.return_value = []
        mock_scheduler.return_value = mock_sched

        from core.proactive import register_proactive_triggers
        register_proactive_triggers()

        # Should have subscribed to at least 2 events
        assert mock_subscribe.call_count >= 2

    @patch("core.cron_scheduler.get_scheduler")
    @patch("core.event_bus.subscribe")
    def test_register_creates_morning_cron(self, mock_subscribe, mock_scheduler):
        """Morning briefing should be registered as a cron task."""
        mock_sched = MagicMock()
        mock_sched.list_tasks.return_value = []
        mock_scheduler.return_value = mock_sched

        from core.proactive import register_proactive_triggers
        register_proactive_triggers()

        # Should have created the morning briefing task
        mock_sched.create_task.assert_called_once()
        call_kwargs = mock_sched.create_task.call_args.kwargs
        assert call_kwargs["task_id"] == "proactive_morning"
        assert "0 8" in call_kwargs["cron_expression"]

    @patch("core.cron_scheduler.get_scheduler")
    @patch("core.event_bus.subscribe")
    def test_register_skips_if_already_exists(self, mock_subscribe, mock_scheduler):
        """Should not duplicate morning briefing if already registered."""
        mock_sched = MagicMock()
        mock_task = MagicMock()
        mock_task.task_id = "proactive_morning"
        mock_sched.list_tasks.return_value = [mock_task]
        mock_scheduler.return_value = mock_sched

        from core.proactive import register_proactive_triggers
        register_proactive_triggers()

        mock_sched.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class TestProactiveStatus:
    """get_proactive_status should report trigger info."""

    @patch("core.cron_scheduler.get_scheduler")
    def test_status_structure(self, mock_scheduler):
        mock_sched = MagicMock()
        mock_sched.list_tasks.return_value = []
        mock_scheduler.return_value = mock_sched

        from core.proactive import get_proactive_status
        status = get_proactive_status()

        assert "triggers" in status
        assert "morning_briefing" in status["triggers"]
        assert "missed_block" in status["triggers"]
        assert status["status"] == "active"

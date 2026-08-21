"""Tests for core/dependencies.py — thread-safe singletons, task fetching.

Tests that singletons are created lazily, thread-safe, and return
consistent instances. Task fetching logic is tested with mocked memory.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Singleton getters — thread safety and lazy initialization
# ---------------------------------------------------------------------------


class TestGetMemory:
    """ADHDMemoryEngine singleton."""

    def test_returns_same_instance(self):
        from core.dependencies import _memory
        # Reset global to ensure fresh creation
        import core.dependencies as deps
        deps._memory = None
        with patch("memory.adhd_memory.ADHDMemoryEngine") as MockEngine:
            MockEngine.return_value = MagicMock()
            m1 = deps.get_memory()
            m2 = deps.get_memory()
            assert m1 is m2
            MockEngine.assert_called_once()  # Only created once

    def test_thread_safety(self):
        """Multiple threads should get the same instance."""
        import core.dependencies as deps
        deps._memory = None
        instances = []

        with patch("memory.adhd_memory.ADHDMemoryEngine") as MockEngine:
            mock_instance = MagicMock()
            MockEngine.return_value = mock_instance

            def get_mem():
                instances.append(deps.get_memory())

            threads = [threading.Thread(target=get_mem) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert all(i is mock_instance for i in instances)
        assert len(instances) == 10


class TestGetConversationMemory:
    """ConversationMemory singleton."""

    def test_returns_same_instance(self):
        import core.dependencies as deps
        deps._conversation_memory = None
        with patch("memory.adhd_memory.ConversationMemory") as MockConv:
            MockConv.return_value = MagicMock()
            c1 = deps.get_conversation_memory()
            c2 = deps.get_conversation_memory()
            assert c1 is c2
            MockConv.assert_called_once()


class TestGetTaskTracker:
    """TaskCompletionTracker singleton."""

    def test_returns_same_instance(self):
        import core.dependencies as deps
        deps._task_tracker = None
        with patch("memory.adhd_memory.TaskCompletionTracker") as MockTracker:
            MockTracker.return_value = MagicMock()
            t1 = deps.get_task_tracker()
            t2 = deps.get_task_tracker()
            assert t1 is t2
            MockTracker.assert_called_once()


class TestGetMemorySvc:
    """Memory service singleton — depends on get_memory."""

    def test_returns_same_instance(self):
        import core.dependencies as deps
        deps._memory_service = None
        deps._memory = MagicMock()
        with patch("core.memory_service.build_memory_service") as mock_build:
            mock_build.return_value = MagicMock()
            s1 = deps.get_memory_svc()
            s2 = deps.get_memory_svc()
            assert s1 is s2
            mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# Task fetching helpers
# ---------------------------------------------------------------------------


class TestGetTaskList:
    """get_task_list — fetches tasks from memory engine."""

    def test_returns_tasks_from_memory(self):
        import core.dependencies as deps
        mock_memory = MagicMock()
        mock_memory.get_all_memories.return_value = [
            {"memory": "Finish report", "metadata": {"type": "task", "estimated_minutes": 30, "priority": "now"}},
            {"memory": "Call dentist", "metadata": {"type": "task", "estimated_minutes": 10, "priority": "soon"}},
            {"memory": "Random note", "metadata": {"type": "note"}},  # Should be filtered out
        ]
        deps._memory = mock_memory

        tasks = deps.get_task_list()
        assert len(tasks) == 2
        assert tasks[0]["text"] == "Finish report"
        assert tasks[0]["estimated_minutes"] == 30
        assert tasks[0]["priority"] == "now"
        assert tasks[1]["text"] == "Call dentist"

    def test_returns_default_when_no_tasks(self):
        import core.dependencies as deps
        mock_memory = MagicMock()
        mock_memory.get_all_memories.return_value = []
        deps._memory = mock_memory

        tasks = deps.get_task_list()
        assert len(tasks) == 1
        assert "No tasks yet" in tasks[0]["text"]

    def test_handles_metadata_defaults(self):
        """Missing metadata fields should use sensible defaults."""
        import core.dependencies as deps
        mock_memory = MagicMock()
        mock_memory.get_all_memories.return_value = [
            {"memory": "Bare task", "metadata": {"type": "task"}},
        ]
        deps._memory = mock_memory

        tasks = deps.get_task_list()
        assert tasks[0]["estimated_minutes"] == 25
        assert tasks[0]["priority"] == "soon"


class TestGetTasksOrEmpty:
    """get_tasks_or_empty — returns empty list when no tasks exist."""

    def test_returns_tasks(self):
        import core.dependencies as deps
        mock_memory = MagicMock()
        mock_memory.get_all_memories.return_value = [
            {"memory": "Task", "metadata": {"type": "task", "estimated_minutes": 15, "priority": "soon"}},
        ]
        deps._memory = mock_memory

        tasks = deps.get_tasks_or_empty()
        assert len(tasks) == 1

    def test_returns_empty_list_when_none(self):
        import core.dependencies as deps
        mock_memory = MagicMock()
        mock_memory.get_all_memories.return_value = []
        deps._memory = mock_memory

        tasks = deps.get_tasks_or_empty()
        assert tasks == []


class TestStopMemoryServices:
    """stop_memory_services — graceful cleanup."""

    def test_stops_running_service(self):
        import core.dependencies as deps
        mock_svc = MagicMock()
        mock_svc.is_running = True
        deps._memory_service = mock_svc

        with patch("core.memory_service.stop_memory_service") as mock_stop:
            deps.stop_memory_services()
            mock_stop.assert_called_once_with(mock_svc)
        assert deps._memory_service is None

    def test_handles_none_service(self):
        import core.dependencies as deps
        deps._memory_service = None
        deps.stop_memory_services()  # Should not raise

    def test_handles_stop_error(self):
        import core.dependencies as deps
        deps._memory_service = MagicMock()
        with patch("core.memory_service.stop_memory_service", side_effect=Exception("boom")):
            deps.stop_memory_services()  # Should not raise

"""Tests for routes/memory.py — memory CRUD, search, purge.

Tests route handler functions directly. Patches lazy imports inside function bodies.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /api/memories
# ---------------------------------------------------------------------------


class TestMemoriesEndpoint:
    @patch("routes.memory.get_memory")
    def test_returns_memories(self, mock_get_memory):
        from routes.memory import api_memories

        mock_memory = MagicMock()
        mock_memory.get_all_memories.return_value = [
            {"memory": "Finish report", "metadata": {"type": "task"}},
            {"memory": "Call dentist", "metadata": {"type": "task"}},
        ]
        mock_get_memory.return_value = mock_memory

        result = api_memories()
        assert "memories" in result
        assert result["count"] == 2

    @patch("routes.memory.get_memory")
    def test_empty_memories(self, mock_get_memory):
        from routes.memory import api_memories

        mock_memory = MagicMock()
        mock_memory.get_all_memories.return_value = []
        mock_get_memory.return_value = mock_memory

        result = api_memories()
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/memories/search
# ---------------------------------------------------------------------------


class TestMemorySearchEndpoint:
    @patch("routes.memory.get_memory")
    def test_returns_results(self, mock_get_memory):
        from routes.memory import api_memory_search
        from core.error_models import SearchRequest

        mock_memory = MagicMock()
        mock_memory.retrieve_context_for_task.return_value = [
            {"memory": "Report due Friday", "score": 0.95},
        ]
        mock_get_memory.return_value = mock_memory

        req = SearchRequest(query="report")
        result = api_memory_search(req)
        assert "results" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["score"] == 0.95

    @patch("routes.memory.get_memory")
    def test_empty_query(self, mock_get_memory):
        from routes.memory import api_memory_search
        from core.error_models import SearchRequest

        mock_memory = MagicMock()
        mock_memory.retrieve_context_for_task.return_value = []
        mock_get_memory.return_value = mock_memory

        req = SearchRequest(query="")
        result = api_memory_search(req)
        assert "results" in result


# ---------------------------------------------------------------------------
# POST /api/purge
# ---------------------------------------------------------------------------


class TestPurgeEndpoint:
    @patch("routes.memory.get_memory")
    def test_purges_memories(self, mock_get_memory):
        from routes.memory import api_purge

        mock_memory = MagicMock()
        mock_memory.purge_all.return_value = {"status": "success", "message": "All memories purged."}
        mock_get_memory.return_value = mock_memory

        result = api_purge()
        assert result["status"] == "success"

    @patch("routes.memory.get_memory")
    def test_purge_failure(self, mock_get_memory):
        from routes.memory import api_purge

        mock_memory = MagicMock()
        mock_memory.purge_all.return_value = {"status": "error", "message": "Failed to purge"}
        mock_get_memory.return_value = mock_memory

        result = api_purge()
        assert result["status"] == "error"

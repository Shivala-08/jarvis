"""Tests for routes/braindump.py — braindump and conversation endpoints.

Tests route handler functions directly. The async api_braindump is tested
via TestClient in integration tests; here we test the synchronous endpoints.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.error_models import BrainDumpRequest, ConversationRequest


# ---------------------------------------------------------------------------
# GET /api/conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    @patch("routes.braindump.get_conversation_memory")
    def test_empty_list(self, mock_conv_mem):
        from routes.braindump import api_list_conversations

        mock_conv = MagicMock()
        mock_conv.get_conversation_ids.return_value = []
        mock_conv_mem.return_value = mock_conv

        result = api_list_conversations()
        assert "conversations" in result
        assert result["conversations"] == []

    @patch("routes.braindump.get_conversation_memory")
    def test_with_conversations(self, mock_conv_mem):
        from routes.braindump import api_list_conversations

        mock_conv = MagicMock()
        mock_conv.get_conversation_ids.return_value = ["conv_1", "conv_2"]
        mock_conv.get_stats.return_value = {"turns": 5}
        mock_conv_mem.return_value = mock_conv

        result = api_list_conversations()
        assert len(result["conversations"]) == 2
        assert result["conversations"][0]["id"] == "conv_1"


# ---------------------------------------------------------------------------
# GET /api/conversations/{id}
# ---------------------------------------------------------------------------


class TestGetConversation:
    @patch("routes.braindump.get_conversation_memory")
    def test_returns_turns(self, mock_conv_mem):
        from routes.braindump import api_get_conversation

        mock_conv = MagicMock()
        mock_conv.get_turns.return_value = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        mock_conv.get_stats.return_value = {"turns": 2}
        mock_conv_mem.return_value = mock_conv

        result = api_get_conversation("test_id", limit=10)
        assert result["conversation_id"] == "test_id"
        assert len(result["turns"]) == 2
        mock_conv.get_turns.assert_called_once_with("test_id", limit=10)

    @patch("routes.braindump.get_conversation_memory")
    def test_empty_conversation(self, mock_conv_mem):
        from routes.braindump import api_get_conversation

        mock_conv = MagicMock()
        mock_conv.get_turns.return_value = []
        mock_conv.get_stats.return_value = {"turns": 0}
        mock_conv_mem.return_value = mock_conv

        result = api_get_conversation("empty_id")
        assert result["turns"] == []


# ---------------------------------------------------------------------------
# DELETE /api/conversations/{id}
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    @patch("routes.braindump.get_conversation_memory")
    def test_deletes(self, mock_conv_mem):
        from routes.braindump import api_delete_conversation

        mock_conv = MagicMock()
        mock_conv.delete_conversation.return_value = True
        mock_conv_mem.return_value = mock_conv

        result = api_delete_conversation("test_id")
        assert result["deleted"] is True
        assert result["conversation_id"] == "test_id"

    @patch("routes.braindump.get_conversation_memory")
    def test_nonexistent(self, mock_conv_mem):
        from routes.braindump import api_delete_conversation

        mock_conv = MagicMock()
        mock_conv.delete_conversation.return_value = False
        mock_conv_mem.return_value = mock_conv

        result = api_delete_conversation("nonexistent")
        assert result["deleted"] is False


# ---------------------------------------------------------------------------
# Braindump route structure validation
# ---------------------------------------------------------------------------


class TestBraindumpRouteStructure:
    """Verify the route module defines the expected endpoints."""

    def test_has_api_braindump(self):
        from routes.braindump import api_braindump
        import asyncio
        # Just verify it's callable (async)
        assert asyncio.iscoroutinefunction(api_braindump)

    def test_has_api_conversation(self):
        from routes.braindump import api_conversation
        assert callable(api_conversation)

    def test_has_api_list_conversations(self):
        from routes.braindump import api_list_conversations
        assert callable(api_list_conversations)

    def test_has_api_get_conversation(self):
        from routes.braindump import api_get_conversation
        assert callable(api_get_conversation)

    def test_has_api_delete_conversation(self):
        from routes.braindump import api_delete_conversation
        assert callable(api_delete_conversation)

    def test_request_models_valid(self):
        """Verify Pydantic models accept valid inputs."""
        req = BrainDumpRequest(text="test")
        assert req.text == "test"

        req = ConversationRequest(message="hello")
        assert req.message == "hello"

"""Tests for routes/agents.py — coding, web task, vision, study.

Tests route handler functions directly (avoids TestClient startup issues).
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# POST /api/study
# ---------------------------------------------------------------------------


class TestStudyEndpoint:
    @pytest.mark.anyio
    @patch("routes.agents.asyncio.to_thread")
    @patch("agents.study_agent.decompose_topic")
    async def test_returns_plan(self, mock_decompose, mock_to_thread):
        from routes.agents import api_study

        mock_decompose.return_value = {
            "topic": "Binary Search",
            "units": [
                {"id": "u1", "title": "Concept", "estimated_minutes": 15, "prerequisites": ["none"], "active_recall_questions": ["Q?"]}
            ],
        }
        # Simulate to_thread calling the function synchronously
        mock_to_thread.side_effect = lambda f, *args, **kwargs: f(*args, **kwargs)

        result = await api_study(topic="Binary Search")
        assert "units" in result
        assert len(result["units"]) == 1


# ---------------------------------------------------------------------------
# POST /api/code
# ---------------------------------------------------------------------------


class TestCodeEndpoint:
    @patch("agents.coding_agent.CodeAssistant")
    def test_fix_action(self, MockAssistant):
        from routes.agents import api_code
        from core.error_models import CodeRequest

        mock_instance = MagicMock()
        mock_instance.fix_bug.return_value = {
            "action": "fix",
            "summary": "Fixed the off-by-one error",
            "files_changed": [],
            "explanation": "Changed < to <=",
            "confidence": "high",
            "warnings": [],
        }
        MockAssistant.return_value = mock_instance

        req = CodeRequest(instruction="Fix the off-by-one bug", action="fix")
        result = api_code(req)
        assert result["action"] == "fix"
        assert "Fixed" in result["summary"]

    @patch("agents.coding_agent.CodeAssistant")
    def test_auto_detect_explain(self, MockAssistant):
        from routes.agents import api_code
        from core.error_models import CodeRequest

        mock_instance = MagicMock()
        mock_instance.explain.return_value = {
            "action": "explain",
            "summary": "Explained the function",
            "files_changed": [],
            "explanation": "The function does X",
            "confidence": "high",
            "warnings": [],
        }
        MockAssistant.return_value = mock_instance

        req = CodeRequest(instruction="What does this function do?")
        result = api_code(req)
        mock_instance.explain.assert_called_once()

    @patch("agents.coding_agent.CodeAssistant")
    def test_auto_detect_review(self, MockAssistant):
        from routes.agents import api_code
        from core.error_models import CodeRequest

        mock_instance = MagicMock()
        mock_instance.review.return_value = {
            "action": "review",
            "summary": "Code review",
            "files_changed": [],
            "explanation": "Looks good",
            "confidence": "high",
            "warnings": [],
        }
        MockAssistant.return_value = mock_instance

        req = CodeRequest(instruction="Review main.py")
        result = api_code(req)
        mock_instance.review.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/code/apply
# ---------------------------------------------------------------------------


class TestCodeApplyEndpoint:
    @patch("agents.coding_agent.CodeAssistant")
    def test_apply_changes(self, MockAssistant):
        from routes.agents import api_code_apply

        mock_instance = MagicMock()
        mock_instance.apply_changes.return_value = {"applied": 1, "errors": 0}
        MockAssistant.return_value = mock_instance

        result = api_code_apply(
            result={"files_changed": [{"path": "main.py", "new_code": "print('hello')"}]},
            dry_run=True,
        )
        assert result["applied"] == 1


# ---------------------------------------------------------------------------
# POST /api/web-task
# ---------------------------------------------------------------------------


class TestWebTaskEndpoint:
    @patch("agents.web_task_agent.WebTaskAgent")
    def test_search(self, MockAgent):
        from routes.agents import api_web_task
        from core.error_models import WebTaskRequest

        mock_instance = MagicMock()
        mock_instance.search.return_value = {
            "results": [{"title": "Python Docs", "url": "https://docs.python.org"}]
        }
        MockAgent.return_value = mock_instance

        req = WebTaskRequest(task="Search for Python docs", action="search")
        result = api_web_task(req)
        assert "results" in result

    @patch("agents.web_task_agent.WebTaskAgent")
    def test_scrape(self, MockAgent):
        from routes.agents import api_web_task
        from core.error_models import WebTaskRequest

        mock_instance = MagicMock()
        mock_instance.scrape.return_value = {
            "url": "https://example.com",
            "text": "Page content",
            "text_length": 12,
        }
        MockAgent.return_value = mock_instance

        req = WebTaskRequest(task="Scrape this page", url="https://example.com", action="scrape")
        result = api_web_task(req)
        assert result["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# GET /api/web-task/search
# ---------------------------------------------------------------------------


class TestWebSearchEndpoint:
    @patch("agents.web_task_agent.WebTaskAgent")
    def test_quick_search(self, MockAgent):
        from routes.agents import api_web_search

        mock_instance = MagicMock()
        mock_instance.search.return_value = {"results": []}
        MockAgent.return_value = mock_instance

        result = api_web_search(q="test query")
        assert "results" in result


# ---------------------------------------------------------------------------
# GET /api/vision/status
# ---------------------------------------------------------------------------


class TestVisionStatusEndpoint:
    @patch("agents.vision_agent.get_vision_status")
    def test_returns_status(self, mock_status):
        from routes.agents import api_vision_status

        mock_status.return_value = {"model": "available", "dependencies": "ok"}
        result = api_vision_status()
        assert result["model"] == "available"

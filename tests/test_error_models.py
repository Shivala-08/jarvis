"""Tests for core/error_models.py — Pydantic request/response model validation.

Ensures all models enforce their schemas correctly and handle edge cases.
"""

import pytest
from pydantic import ValidationError

from core.error_models import (
    BrainDumpRequest,
    CodeRequest,
    ConversationRequest,
    ErrorResponse,
    HealthResponse,
    PaginatedResponse,
    RebalanceRequest,
    ScheduledTaskRequest,
    SearchRequest,
    SprintRequest,
    SuccessResponse,
    TaskCompleteRequest,
    TaskStartRequest,
    VisionRequest,
    WebTaskRequest,
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class TestBrainDumpRequest:
    def test_valid(self):
        req = BrainDumpRequest(text="My thoughts")
        assert req.text == "My thoughts"
        assert req.conversation_id is None

    def test_with_conversation_id(self):
        req = BrainDumpRequest(text="Hello", conversation_id="conv_123")
        assert req.conversation_id == "conv_123"

    def test_empty_text_allowed(self):
        req = BrainDumpRequest(text="")
        assert req.text == ""

    def test_missing_text_raises(self):
        with pytest.raises(ValidationError):
            BrainDumpRequest()


class TestStudyRequest:
    def test_valid(self):
        from core.error_models import StudyRequest
        req = StudyRequest(topic="Binary Search")
        assert req.topic == "Binary Search"

    def test_missing_topic_raises(self):
        from core.error_models import StudyRequest
        with pytest.raises(ValidationError):
            StudyRequest()


class TestSearchRequest:
    def test_valid(self):
        req = SearchRequest(query="report deadline")
        assert req.query == "report deadline"

    def test_missing_query_raises(self):
        with pytest.raises(ValidationError):
            SearchRequest()


class TestRebalanceRequest:
    def test_default(self):
        req = RebalanceRequest()
        assert req.missed_block_id is None

    def test_with_block_id(self):
        req = RebalanceRequest(missed_block_id=3)
        assert req.missed_block_id == 3


class TestSprintRequest:
    def test_valid(self):
        req = SprintRequest(task="Finish report")
        assert req.task == "Finish report"

    def test_missing_task_raises(self):
        with pytest.raises(ValidationError):
            SprintRequest()


class TestScheduledTaskRequest:
    def test_defaults(self):
        req = ScheduledTaskRequest(
            task_id="daily_digest",
            prompt="Summarize today",
            cron_expression="0 8 * * *",
        )
        assert req.agent_type == "braindump"

    def test_custom_agent_type(self):
        req = ScheduledTaskRequest(
            task_id="test",
            prompt="test",
            cron_expression="0 * * * *",
            agent_type="scheduler",
        )
        assert req.agent_type == "scheduler"


class TestConversationRequest:
    def test_valid(self):
        req = ConversationRequest(message="Hello")
        assert req.message == "Hello"
        assert req.conversation_id is None


class TestCodeRequest:
    def test_defaults(self):
        req = CodeRequest(instruction="Fix the bug")
        assert req.action == "auto"
        assert req.file_path is None

    def test_explicit_action(self):
        req = CodeRequest(instruction="Add feature", action="add")
        assert req.action == "add"


class TestWebTaskRequest:
    def test_defaults(self):
        req = WebTaskRequest(task="Search for Python docs")
        assert req.action == "auto"
        assert req.url is None

    def test_with_url(self):
        req = WebTaskRequest(task="Scrape", url="https://example.com", action="scrape")
        assert req.url == "https://example.com"


class TestVisionRequest:
    def test_defaults(self):
        req = VisionRequest()
        assert req.prompt == "What's on screen? Describe any errors or issues."
        assert req.image_url is None

    def test_custom_prompt(self):
        req = VisionRequest(prompt="Read the error message", image_url="/tmp/screenshot.png")
        assert req.prompt == "Read the error message"


class TestTaskStartRequest:
    def test_valid(self):
        req = TaskStartRequest(task_text="Write report")
        assert req.task_text == "Write report"
        assert req.estimated_minutes is None

    def test_with_estimate(self):
        req = TaskStartRequest(task_text="Write report", estimated_minutes=30)
        assert req.estimated_minutes == 30


class TestTaskCompleteRequest:
    def test_valid(self):
        req = TaskCompleteRequest(task_text="Write report", actual_minutes=25.5)
        assert req.actual_minutes == 25.5


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_valid(self):
        resp = ErrorResponse(error="not_found", detail="Resource not found", status_code=404)
        assert resp.status_code == 404

    def test_default_status_code(self):
        resp = ErrorResponse(error="error", detail="Something went wrong")
        assert resp.status_code == 500


class TestSuccessResponse:
    def test_defaults(self):
        resp = SuccessResponse()
        assert resp.status == "ok"
        assert resp.data is None
        assert resp.message is None

    def test_with_data(self):
        resp = SuccessResponse(data={"count": 5}, message="Found 5 items")
        assert resp.data["count"] == 5


class TestHealthResponse:
    def test_valid(self):
        resp = HealthResponse(
            status="ok",
            service="adhd-copilot",
            version="0.2.0",
            components={"memory": "active"},
        )
        assert resp.status == "ok"


class TestPaginatedResponse:
    def test_defaults(self):
        resp = PaginatedResponse()
        assert resp.items == []
        assert resp.count == 0

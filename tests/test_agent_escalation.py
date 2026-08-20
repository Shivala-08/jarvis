"""Tests for agent escalation paths — verifies each agent routes through llm_call correctly.

These tests mock at the llm_call boundary to verify:
1. Each agent passes the correct task_type to llm_call
2. Cloud escalation responses are properly parsed
3. Local fallback works when escalation fails
4. Error handling is graceful in each agent
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mock_ollama(response: str = "response"):
    """Create a mock ollama module."""
    mock = MagicMock()
    mock.chat.return_value = {"message": {"content": response}}
    return mock


def _patch_llm_call(return_value: str = "response"):
    """Patch core.escalation.llm_call to return a fixed value."""
    return patch("core.escalation.llm_call", return_value=return_value)


# ===========================================================================
# braindump_agent
# ===========================================================================

class TestBraindumpEscalation:
    """braindump_agent should route through llm_call with task_type='braindump'."""

    def test_calls_llm_with_correct_task_type(self):
        """process_braindump should use llm_call with task_type='braindump'."""
        mock_json = json.dumps({
            "thoughts": [{"text": "test", "type": "task", "priority": "soon", "estimated_minutes": 10, "tags": []}],
            "mood_hint": "focused",
            "suggested_first_step": "Start",
        })
        with patch("agents.braindump_agent.llm_call", return_value=mock_json) as mock_call:
            from agents.braindump_agent import process_braindump
            result = process_braindump("Need to finish report", model="test-model")

        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["task_type"] == "braindump"
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["temperature"] == 0.3
        assert "Need to finish report" in call_kwargs["prompt"]

    def test_system_prompt_passed(self):
        """The braindump system prompt should be passed to llm_call."""
        mock_json = json.dumps({
            "thoughts": [], "mood_hint": "ok", "suggested_first_step": "none"
        })
        with patch("agents.braindump_agent.llm_call", return_value=mock_json) as mock_call:
            from agents.braindump_agent import process_braindump
            process_braindump("test", model="m")

        call_kwargs = mock_call.call_args.kwargs
        assert "brain-dump processor" in call_kwargs["system_prompt"]

    def test_context_appended_to_prompt(self):
        """When context is provided, it should be prepended to the prompt."""
        mock_json = json.dumps({
            "thoughts": [], "mood_hint": "ok", "suggested_first_step": "none"
        })
        with patch("agents.braindump_agent.llm_call", return_value=mock_json) as mock_call:
            from agents.braindump_agent import process_braindump
            process_braindump("new thought", model="m", context="Previous context here")

        call_kwargs = mock_call.call_args.kwargs
        assert "Previous context here" in call_kwargs["prompt"]
        assert "new thought" in call_kwargs["prompt"]

    def test_cloud_response_parsed_as_json(self):
        """A cloud-returned JSON string should be parsed correctly."""
        cloud_response = json.dumps({
            "thoughts": [{"text": "cloud task", "type": "task", "priority": "now", "estimated_minutes": 5, "tags": ["work"]}],
            "mood_hint": "productive",
            "suggested_first_step": "Open the document",
        })
        with patch("agents.braindump_agent.llm_call", return_value=cloud_response):
            from agents.braindump_agent import process_braindump
            result = process_braindump("Finish the doc", model="m")

        assert result["mood_hint"] == "productive"
        assert len(result["thoughts"]) == 1
        assert result["thoughts"][0]["text"] == "cloud task"

    def test_markdown_fences_stripped_before_parse(self):
        """JSON wrapped in ``` fences should still parse."""
        fenced = '```json\n{"thoughts": [], "mood_hint": "calm", "suggested_first_step": "breathe"}\n```'
        with patch("agents.braindump_agent.llm_call", return_value=fenced):
            from agents.braindump_agent import process_braindump
            result = process_braindump("test", model="m")

        assert result["mood_hint"] == "calm"

    def test_invalid_json_returns_fallback(self):
        """Non-JSON response should return a fallback structure."""
        with patch("agents.braindump_agent.llm_call", return_value="This is not JSON at all"):
            from agents.braindump_agent import process_braindump
            result = process_braindump("original text here", model="m")

        assert "thoughts" in result
        assert result["mood_hint"] == "unclear"
        assert result["thoughts"][0]["text"] == "original text here"

    def test_empty_response_returns_fallback(self):
        """Empty string response should return fallback."""
        with patch("agents.braindump_agent.llm_call", return_value=""):
            from agents.braindump_agent import process_braindump
            result = process_braindump("my thoughts", model="m")

        assert "thoughts" in result
        assert result["thoughts"][0]["text"] == "my thoughts"

    def test_llm_call_exception_returns_fallback(self):
        """If llm_call raises, should return fallback gracefully."""
        with patch("agents.braindump_agent.llm_call", side_effect=RuntimeError("Ollama down")):
            from agents.braindump_agent import process_braindump
            result = process_braindump("fallback test", model="m")

        assert "thoughts" in result
        assert result["thoughts"][0]["text"] == "fallback test"

    def test_default_model_used_when_none(self):
        """When model is None, should use get_default_model()."""
        mock_json = json.dumps({
            "thoughts": [], "mood_hint": "ok", "suggested_first_step": "none"
        })
        with patch("agents.braindump_agent.llm_call", return_value=mock_json) as mock_call:
            from agents.braindump_agent import process_braindump
            process_braindump("test")  # model=None

        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["model"] is not None  # should be the default model


# ===========================================================================
# scheduler_agent
# ===========================================================================

class TestSchedulerEscalation:
    """scheduler_agent should route through llm_call with task_type='scheduler'."""

    def test_calls_llm_with_correct_task_type(self):
        """generate_micro_sprint should use llm_call with task_type='scheduler'."""
        with patch("agents.scheduler_agent.llm_call", return_value="How about 10 minutes on this?") as mock_call:
            from agents.scheduler_agent import generate_micro_sprint
            result = generate_micro_sprint("Finish report", model="test-model")

        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["task_type"] == "scheduler"
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["temperature"] == 0.5

    def test_system_prompt_supportive(self):
        """The scheduler system prompt should use supportive phrasing."""
        with patch("agents.scheduler_agent.llm_call", return_value="sprint") as mock_call:
            from agents.scheduler_agent import generate_micro_sprint
            generate_micro_sprint("test", model="m")

        call_kwargs = mock_call.call_args.kwargs
        assert "calm, supportive" in call_kwargs["system_prompt"]
        assert "never imperative" in call_kwargs["system_prompt"]

    def test_cloud_response_returned_directly(self):
        """Cloud-returned text should be passed through as-is (no JSON parsing)."""
        with patch("agents.scheduler_agent.llm_call", return_value="Spend 10 minutes drafting"):
            from agents.scheduler_agent import generate_micro_sprint
            result = generate_micro_sprint("task", model="m")

        assert result == "Spend 10 minutes drafting"

    def test_default_model_used(self):
        """When model is None, should resolve to default."""
        with patch("agents.scheduler_agent.llm_call", return_value="ok") as mock_call:
            from agents.scheduler_agent import generate_micro_sprint
            generate_micro_sprint("task")  # model=None

        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["model"] is not None


# ===========================================================================
# web_task_agent
# ===========================================================================

class TestWebTaskEscalation:
    """web_task_agent should route through llm_call with task_type='web_task'."""

    def test_plan_task_calls_llm_with_correct_task_type(self):
        """_plan_task should use llm_call with task_type='web_task'."""
        plan_json = json.dumps({
            "task_summary": "Find frameworks",
            "steps": [{"step_id": 1, "action": "search", "target": "Python frameworks", "selector": None, "description": "Search"}],
            "output_format": "text",
            "extraction_schema": None,
        })
        with patch("agents.web_task_agent.llm_call", return_value=plan_json) as mock_call:
            from agents.web_task_agent import _plan_task
            result = _plan_task("Find top Python frameworks", model="test-model")

        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["task_type"] == "web_task"
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["temperature"] == 0.2
        assert result["task_summary"] == "Find frameworks"

    def test_plan_task_cloud_json_parsed(self):
        """Cloud-returned JSON plan should be parsed correctly."""
        plan = {
            "task_summary": "Scrape prices",
            "steps": [
                {"step_id": 1, "action": "fetch", "target": "https://example.com", "selector": ".price", "description": "Get prices"},
            ],
            "output_format": "structured",
            "extraction_schema": {"price": ".price"},
        }
        with patch("agents.web_task_agent.llm_call", return_value=json.dumps(plan)):
            from agents.web_task_agent import _plan_task
            result = _plan_task("Scrape prices")

        assert result["task_summary"] == "Scrape prices"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["action"] == "fetch"

    def test_plan_task_fenced_json_parsed(self):
        """JSON in markdown fences should still parse."""
        plan = {"task_summary": "test", "steps": [], "output_format": "text", "extraction_schema": None}
        fenced = f"```json\n{json.dumps(plan)}\n```"
        with patch("agents.web_task_agent.llm_call", return_value=fenced):
            from agents.web_task_agent import _plan_task
            result = _plan_task("test task")

        assert result["task_summary"] == "test"

    def test_plan_task_invalid_json_returns_fallback(self):
        """Non-JSON response should return a fallback plan."""
        with patch("agents.web_task_agent.llm_call", return_value="not json"):
            from agents.web_task_agent import _plan_task
            result = _plan_task("my task")

        assert result["task_summary"] == "my task"
        assert result["steps"][0]["action"] == "fetch"
        assert "Fallback" in result["steps"][0]["description"]

    def test_plan_task_llm_exception_returns_fallback(self):
        """LLM call failure should return fallback plan."""
        with patch("agents.web_task_agent.llm_call", side_effect=RuntimeError("down")):
            from agents.web_task_agent import _plan_task
            result = _plan_task("task description")

        assert result["task_summary"] == "task description"
        assert result["steps"][0]["action"] == "fetch"

    def test_synthesize_results_calls_llm_with_correct_task_type(self):
        """_synthesize_results should use llm_call with task_type='web_task'."""
        with patch("agents.web_task_agent.llm_call", return_value="Synthesized answer") as mock_call:
            from agents.web_task_agent import _synthesize_results
            result = _synthesize_results(
                "Find frameworks",
                [{"url": "https://example.com", "text": "Flask is great"}],
                model="test-model",
            )

        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["task_type"] == "web_task"
        assert call_kwargs["model"] == "test-model"
        assert "Find frameworks" in call_kwargs["prompt"]
        assert "Flask" in call_kwargs["prompt"]

    def test_synthesize_results_includes_urls_in_context(self):
        """URLs from results should be included in the synthesis prompt."""
        with patch("agents.web_task_agent.llm_call", return_value="ok") as mock_call:
            from agents.web_task_agent import _synthesize_results
            _synthesize_results(
                "task",
                [
                    {"url": "https://first.com", "text": "first result"},
                    {"url": "https://second.com", "text": "second result"},
                ],
            )

        call_kwargs = mock_call.call_args.kwargs
        assert "https://first.com" in call_kwargs["prompt"]
        assert "https://second.com" in call_kwargs["prompt"]

    def test_synthesize_results_truncates_long_text(self):
        """Long result text should be truncated to avoid context overflow."""
        long_text = "x" * 5000
        with patch("agents.web_task_agent.llm_call", return_value="ok") as mock_call:
            from agents.web_task_agent import _synthesize_results
            _synthesize_results("task", [{"url": "url", "text": long_text}])

        call_kwargs = mock_call.call_args.kwargs
        # Text should be truncated to 1000 chars per result
        assert len(call_kwargs["prompt"]) < 5000


# ===========================================================================
# coding_agent
# ===========================================================================

class TestCodingEscalation:
    """coding_agent._query_llm should route through escalation or local Ollama."""

    def test_parse_response_valid_json(self):
        """_parse_response should parse valid JSON."""
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant._parse_response('{"action": "fix", "summary": "Fixed bug", "files_changed": [], "explanation": "done", "confidence": "high", "warnings": []}')
        assert result["action"] == "fix"
        assert result["summary"] == "Fixed bug"

    def test_parse_response_fenced_json(self):
        """_parse_response should handle markdown-fenced JSON."""
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        fenced = '```json\n{"action": "explain", "summary": "test", "files_changed": [], "explanation": "ok", "confidence": "medium", "warnings": []}\n```'
        result = assistant._parse_response(fenced)
        assert result["action"] == "explain"
        assert result["confidence"] == "medium"

    def test_parse_response_invalid_json_returns_fallback(self):
        """_parse_response should return fallback for non-JSON."""
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant._parse_response("This is not JSON at all")
        assert result["action"] == "explain"
        assert result["confidence"] == "low"
        assert "non-JSON" in result["summary"]

    @patch("agents.coding_agent.ollama")
    @patch("agents.coding_agent.get_available_providers", return_value=[])
    def test_query_llm_local_path(self, mock_providers, mock_ollama):
        """Normal coding task with no cloud providers → local Ollama."""
        mock_ollama.chat.return_value = {
            "message": {"content": '{"action": "fix", "summary": "Fixed", "files_changed": [], "explanation": "done", "confidence": "high", "warnings": []}'}
        }
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant._query_llm("Fix the bug in login.py")

        mock_ollama.chat.assert_called_once()
        assert result["action"] == "fix"

    @patch("agents.coding_agent.escalate", return_value='{"action": "explain", "summary": "cloud", "files_changed": [], "explanation": "via cloud", "confidence": "high", "warnings": []}')
    @patch("agents.coding_agent.get_available_providers", return_value=["groq"])
    @patch("agents.coding_agent.should_escalate", return_value="groq")
    def test_query_llm_cloud_path(self, mock_should, mock_providers, mock_escalate):
        """When escalation triggers → cloud provider called, not local Ollama."""
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant._query_llm("Complex architecture task")

        mock_escalate.assert_called_once()
        assert result["action"] == "explain"
        assert result["summary"] == "cloud"

    @patch("agents.coding_agent.escalate")
    @patch("agents.coding_agent.get_available_providers", return_value=["groq"])
    @patch("agents.coding_agent.should_escalate", return_value="groq")
    @patch("agents.coding_agent.ollama")
    def test_query_llm_cloud_fails_falls_back_to_local(self, mock_ollama, mock_should, mock_providers, mock_escalate):
        """Cloud failure should fall back to local Ollama."""
        from core.cloud_router import CloudRouterError
        mock_escalate.side_effect = CloudRouterError("exhausted")
        mock_ollama.chat.return_value = {
            "message": {"content": '{"action": "fix", "summary": "local fix", "files_changed": [], "explanation": "done", "confidence": "high", "warnings": []}'}
        }
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant._query_llm("test")

        mock_ollama.chat.assert_called_once()
        assert result["summary"] == "local fix"

    @patch("agents.coding_agent.ollama")
    @patch("agents.coding_agent.get_available_providers", return_value=[])
    def test_query_llm_ollama_exception_returns_error(self, mock_providers, mock_ollama):
        """Ollama failure should return error dict, not crash."""
        mock_ollama.chat.side_effect = Exception("Connection refused")
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant._query_llm("test")

        assert result["action"] == "explain"
        assert result["confidence"] == "low"
        assert "Connection refused" in result["summary"]

    @patch("agents.coding_agent.ollama")
    @patch("agents.coding_agent.get_available_providers", return_value=[])
    def test_query_llm_passes_system_prompt(self, mock_providers, mock_ollama):
        """System prompt should be included in ollama messages."""
        mock_ollama.chat.return_value = {
            "message": {"content": '{"action": "explain", "summary": "ok", "files_changed": [], "explanation": "ok", "confidence": "high", "warnings": []}'}
        }
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        assistant._query_llm("test")

        call_kwargs = mock_ollama.chat.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert "coding assistant" in messages[0]["content"].lower()

    @patch("agents.coding_agent.ollama")
    @patch("agents.coding_agent.get_available_providers", return_value=[])
    def test_query_llm_context_prepended(self, mock_providers, mock_ollama):
        """Context should be prepended to the prompt."""
        mock_ollama.chat.return_value = {
            "message": {"content": '{"action": "explain", "summary": "ok", "files_changed": [], "explanation": "ok", "confidence": "high", "warnings": []}'}
        }
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        assistant._query_llm("Fix this", context="file content here")

        call_kwargs = mock_ollama.chat.call_args
        user_msg = call_kwargs.kwargs["messages"][1]["content"]
        assert "file content here" in user_msg
        assert "Fix this" in user_msg

    @patch("agents.coding_agent.escalate")
    @patch("agents.coding_agent.get_available_providers", return_value=["groq"])
    @patch("agents.coding_agent.should_escalate", return_value="groq")
    def test_cloud_escalation_sensitive_data_stays_local(self, mock_should, mock_providers, mock_escalate):
        """Sensitive data in prompt should prevent cloud escalation even if triggered."""
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")

        # should_escalate will return None for sensitive data
        mock_should.return_value = None
        with patch("agents.coding_agent.ollama") as mock_ollama:
            mock_ollama.chat.return_value = {
                "message": {"content": '{"action": "fix", "summary": "safe", "files_changed": [], "explanation": "ok", "confidence": "high", "warnings": []}'}
            }
            result = assistant._query_llm("Fix the api_key handling")

        # Should NOT have called cloud
        mock_escalate.assert_not_called()
        # Should have called local
        mock_ollama.chat.assert_called_once()

    @patch("agents.coding_agent.ollama")
    @patch("agents.coding_agent.get_available_providers", return_value=[])
    def test_fix_bug_sets_action(self, mock_providers, mock_ollama):
        """fix_bug should override action to 'fix'."""
        mock_ollama.chat.return_value = {
            "message": {"content": '{"action": "explain", "summary": "fixed", "files_changed": [], "explanation": "ok", "confidence": "high", "warnings": []}'}
        }
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant.fix_bug("off-by-one error")

        assert result["action"] == "fix"

    @patch("agents.coding_agent.ollama")
    @patch("agents.coding_agent.get_available_providers", return_value=[])
    def test_add_feature_sets_action(self, mock_providers, mock_ollama):
        """add_feature should override action to 'add_feature'."""
        mock_ollama.chat.return_value = {
            "message": {"content": '{"action": "fix", "summary": "added", "files_changed": [], "explanation": "ok", "confidence": "high", "warnings": []}'}
        }
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant(model="test-model")
        result = assistant.add_feature("dark mode toggle")

        assert result["action"] == "add_feature"

"""Tests for core/escalation.py — escalation rules, sensitive data detection, llm_call."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from core.escalation import (
    MAX_LOCAL_CONTEXT_TOKENS,
    SENSITIVE_PATTERNS,
    contains_sensitive_data,
    estimate_tokens,
    should_escalate,
)


# ---------------------------------------------------------------------------
# contains_sensitive_data
# ---------------------------------------------------------------------------

class TestContainsSensitiveData:
    """Sensitive data detection — must never let these patterns leave the machine."""

    def test_password_plain(self):
        assert contains_sensitive_data("my password is abc123") is True

    def test_password_in_config(self):
        assert contains_sensitive_data("db_password=s3cret") is True

    def test_api_key(self):
        assert contains_sensitive_data("api_key=sk-12345") is True

    def test_api_key_with_dash(self):
        assert contains_sensitive_data("api-key: xyz") is True

    def test_ssh_key(self):
        assert contains_sensitive_data("ssh_key: AAAA...") is True

    def test_ssh_key_with_spaces(self):
        assert contains_sensitive_data("ssh key goes here") is True

    def test_private_key(self):
        assert contains_sensitive_data("private_key: -----BEGIN") is True

    def test_secret_key(self):
        assert contains_sensitive_data("secret_key = 'abc'") is True

    def test_access_token(self):
        assert contains_sensitive_data("access_token=eyJhbGciOi") is True

    def test_bearer_token(self):
        assert contains_sensitive_data("Bearer eyJhbGciOiJIUzI1NiJ9") is True

    def test_authorization_header(self):
        assert contains_sensitive_data("Authorization: Basic dXNlcjpwYXNz") is True

    def test_ssn_pattern(self):
        assert contains_sensitive_data("My SSN is 123-45-6789") is True

    def test_credit_card(self):
        assert contains_sensitive_data("Card: 4111 1111 1111 1111") is True

    def test_credit_card_no_spaces(self):
        assert contains_sensitive_data("Card: 4111111111111111") is True

    def test_credit_card_with_dashes(self):
        assert contains_sensitive_data("Card: 4111-1111-1111-1111") is True

    def test_private_key_block(self):
        assert contains_sensitive_data("-----BEGIN RSA PRIVATE KEY-----") is True

    def test_private_key_openssh(self):
        assert contains_sensitive_data("-----BEGIN OPENSSH PRIVATE KEY-----") is True

    def test_case_insensitive(self):
        assert contains_sensitive_data("PASSWORD=secret") is True
        assert contains_sensitive_data("Api-Key: abc") is True

    def test_clean_text(self):
        assert contains_sensitive_data("Need to finish the report by Friday") is False

    def test_clean_code(self):
        assert contains_sensitive_data("def calculate_sum(a, b): return a + b") is False

    def test_empty_string(self):
        assert contains_sensitive_data("") is False

    def test_partial_match_not_triggered(self):
        # "pass" alone should not trigger "password"
        assert contains_sensitive_data("pass the salt") is False

    def test_all_patterns_are_strings(self):
        """Every pattern in SENSITIVE_PATTERNS must be a valid regex string."""
        import re
        for pattern in SENSITIVE_PATTERNS:
            re.compile(pattern)  # should not raise


# ---------------------------------------------------------------------------
# should_escalate
# ---------------------------------------------------------------------------

class TestShouldEscalate:
    """Escalation routing — deterministic, no vibes."""

    def test_sensitive_data_stays_local(self):
        """Rule 1: sensitive data NEVER escalates, even if other triggers fire."""
        # Large context + sensitive data → should stay local
        result = should_escalate(
            task_type="coding",
            context_tokens=20_000,
            local_attempts_failed=3,
            text="my password is abc123",
        )
        assert result is None

    def test_context_overflow_to_google(self):
        """Rule 2: >16K tokens → Google AI Studio (1M context)."""
        result = should_escalate(
            task_type="coding",
            context_tokens=MAX_LOCAL_CONTEXT_TOKENS + 1,
            local_attempts_failed=0,
            text="normal text here",
        )
        assert result == "google_ai_studio"

    def test_context_exact_limit_stays_local(self):
        """Exactly at the limit should stay local."""
        result = should_escalate(
            task_type="coding",
            context_tokens=MAX_LOCAL_CONTEXT_TOKENS,
            local_attempts_failed=0,
            text="normal text here",
        )
        assert result is None

    def test_local_failures_to_groq(self):
        """Rule 3: 2+ local failures → Groq for fast repair."""
        result = should_escalate(
            task_type="coding",
            context_tokens=5_000,
            local_attempts_failed=2,
            text="normal text",
        )
        assert result == "groq"

    def test_one_failure_stays_local(self):
        """Only 1 failure is not enough."""
        result = should_escalate(
            task_type="coding",
            context_tokens=5_000,
            local_attempts_failed=1,
            text="normal text",
        )
        assert result is None

    def test_complex_reasoning_to_openrouter(self):
        """Rule 4: architecture/complex/multi-step → OpenRouter free tier."""
        for task_type in ("architecture_design", "complex_reasoning", "multi_step_planning"):
            result = should_escalate(
                task_type=task_type,
                context_tokens=5_000,
                local_attempts_failed=0,
                text="normal text",
            )
            assert result == "openrouter_free", f"Failed for task_type={task_type}"

    def test_normal_task_stays_local(self):
        """Rule 5: everything else → stay local."""
        for task_type in ("coding", "braindump", "web_task", "scheduler", "general"):
            result = should_escalate(
                task_type=task_type,
                context_tokens=5_000,
                local_attempts_failed=0,
                text="normal text",
            )
            assert result is None, f"Failed for task_type={task_type}"

    def test_priority_ordering(self):
        """Sensitive data check runs before context overflow check."""
        # Sensitive data + overflow → should be blocked (not google)
        result = should_escalate(
            task_type="coding",
            context_tokens=50_000,
            local_attempts_failed=0,
            text="api_key=secret123",
        )
        assert result is None

    def test_context_overflow_plus_failures(self):
        """Context overflow takes priority over failure count."""
        result = should_escalate(
            task_type="coding",
            context_tokens=20_000,
            local_attempts_failed=3,
            text="normal text",
        )
        assert result == "google_ai_studio"  # not groq


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    """Rough token estimation for routing decisions."""

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_single_word(self):
        assert estimate_tokens("hello") == 1  # int(1 * 1.3) = 1

    def test_ten_words(self):
        result = estimate_tokens("one two three four five six seven eight nine ten")
        assert result == 13  # 10 * 1.3 = 13

    def test_sentence(self):
        text = "The quick brown fox jumps over the lazy dog"
        result = estimate_tokens(text)
        assert 10 <= result <= 15  # 9 words × 1.3 ≈ 11

    def test_large_text(self):
        text = "word " * 1000
        result = estimate_tokens(text)
        assert result == 1300  # 1000 * 1.3

    def test_returns_int(self):
        assert isinstance(estimate_tokens("hello world"), int)


# ---------------------------------------------------------------------------
# llm_call (mocked)
# ---------------------------------------------------------------------------

def _mock_ollama_chat(response: str = "response"):
    """Create a mock ollama module with a chat() that returns the given response."""
    mock = MagicMock()
    mock.chat.return_value = {"message": {"content": response}}
    return mock


class TestLlmCall:
    """llm_call with mocked Ollama and cloud router.

    Since llm_call does `import ollama` locally, we mock via sys.modules.
    """

    @patch("core.cloud_router.get_available_providers", return_value=[])
    def test_local_call_normal_task(self, mock_providers):
        """Normal task with no cloud providers → local Ollama."""
        mock_mod = _mock_ollama_chat("  hello world  ")
        with patch.dict("sys.modules", {"ollama": mock_mod}):
            # Re-import to pick up the mock
            import importlib
            import core.escalation
            importlib.reload(core.escalation)
            from core.escalation import llm_call
            result = llm_call(prompt="say hello", model="test-model")

        assert result == "hello world"
        mock_mod.chat.assert_called_once()
        call_kwargs = mock_mod.chat.call_args
        assert call_kwargs.kwargs["model"] == "test-model"

    @patch("core.cloud_router.escalate", return_value="cloud response")
    @patch("core.cloud_router.get_available_providers", return_value=["groq"])
    @patch("core.escalation.should_escalate", return_value="groq")
    def test_cloud_escalation(self, mock_should, mock_providers, mock_escalate):
        """When escalation is triggered and provider available → cloud call."""
        mock_mod = _mock_ollama_chat()
        with patch.dict("sys.modules", {"ollama": mock_mod}):
            import importlib
            import core.escalation
            importlib.reload(core.escalation)
            from core.escalation import llm_call
            result = llm_call(
                prompt="complex task",
                model="test-model",
                task_type="complex_reasoning",
                local_attempts_failed=2,
            )

        assert result == "cloud response"
        mock_escalate.assert_called_once()
        # Should NOT have called local ollama
        mock_mod.chat.assert_not_called()

    @patch("core.cloud_router.escalate")
    @patch("core.cloud_router.get_available_providers", return_value=["groq"])
    @patch("core.escalation.should_escalate", return_value="groq")
    def test_cloud_fails_falls_back_to_local(self, mock_should, mock_providers, mock_escalate):
        """Cloud fails → falls back to local Ollama."""
        from core.cloud_router import CloudRouterError
        mock_escalate.side_effect = CloudRouterError("all providers exhausted")
        mock_mod = _mock_ollama_chat("local fallback")
        with patch.dict("sys.modules", {"ollama": mock_mod}):
            import importlib
            import core.escalation
            importlib.reload(core.escalation)
            from core.escalation import llm_call
            result = llm_call(prompt="test", model="test-model")

        assert result == "local fallback"
        mock_mod.chat.assert_called_once()

    @patch("core.cloud_router.get_available_providers", return_value=[])
    def test_local_call_with_system_prompt(self, mock_providers):
        """System prompt is passed through to ollama."""
        mock_mod = _mock_ollama_chat("response")
        with patch.dict("sys.modules", {"ollama": mock_mod}):
            import importlib
            import core.escalation
            importlib.reload(core.escalation)
            from core.escalation import llm_call
            llm_call(prompt="user msg", system_prompt="You are helpful", model="m")

        call_kwargs = mock_mod.chat.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "user msg"

    @patch("core.cloud_router.get_available_providers", return_value=[])
    def test_local_call_without_system_prompt(self, mock_providers):
        """No system prompt → only user message."""
        mock_mod = _mock_ollama_chat("response")
        with patch.dict("sys.modules", {"ollama": mock_mod}):
            import importlib
            import core.escalation
            importlib.reload(core.escalation)
            from core.escalation import llm_call
            llm_call(prompt="user msg", model="m")

        call_kwargs = mock_mod.chat.call_args
        messages = call_kwargs.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    @patch("core.cloud_router.get_available_providers", return_value=[])
    def test_temperature_passed_through(self, mock_providers):
        """Temperature parameter is forwarded to Ollama."""
        mock_mod = _mock_ollama_chat("r")
        with patch.dict("sys.modules", {"ollama": mock_mod}):
            import importlib
            import core.escalation
            importlib.reload(core.escalation)
            from core.escalation import llm_call
            llm_call(prompt="test", model="m", temperature=0.7)

        call_kwargs = mock_mod.chat.call_args
        assert call_kwargs.kwargs["options"]["temperature"] == 0.7

    @patch("core.cloud_router.get_available_providers", return_value=[])
    def test_sensitive_data_stays_local_even_with_high_tokens(self, mock_providers):
        """Sensitive data blocks escalation even with huge context."""
        mock_mod = _mock_ollama_chat("safe")
        with patch.dict("sys.modules", {"ollama": mock_mod}):
            import importlib
            import core.escalation
            importlib.reload(core.escalation)
            from core.escalation import llm_call
            result = llm_call(
                prompt="my password is secret123",
                model="m",
                local_attempts_failed=5,
            )

        # Should have gone local (not cloud)
        assert result == "safe"
        mock_mod.chat.assert_called_once()

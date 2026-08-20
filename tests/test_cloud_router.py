"""Tests for core/cloud_router.py — multi-provider failover, auth, rate limiting."""

import os
from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest

from core.cloud_router import (
    PROVIDERS,
    _PROVIDER_CONFIG,
    CloudRouterError,
    ProviderAuthError,
    RateLimitError,
    escalate,
    get_available_providers,
)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

class TestProviderRegistry:
    """Provider configuration and discovery."""

    def test_all_providers_registered(self):
        assert PROVIDERS == ["groq", "cerebras", "openrouter_free", "google_ai_studio"]

    def test_all_providers_have_config(self):
        for provider in PROVIDERS:
            assert provider in _PROVIDER_CONFIG, f"Missing config for {provider}"

    def test_all_configs_have_required_keys(self):
        required = {"endpoint", "model", "key_env", "timeout"}
        for name, config in _PROVIDER_CONFIG.items():
            missing = required - set(config.keys())
            assert not missing, f"{name} missing keys: {missing}"

    def test_all_key_envs_are_strings(self):
        for name, config in _PROVIDER_CONFIG.items():
            assert isinstance(config["key_env"], str), f"{name} key_env not a string"

    def test_timeouts_are_positive(self):
        for name, config in _PROVIDER_CONFIG.items():
            assert config["timeout"] > 0, f"{name} timeout not positive"

    def test_google_has_google_format(self):
        assert _PROVIDER_CONFIG["google_ai_studio"].get("format") == "google"

    def test_others_have_no_format(self):
        for name in ("groq", "cerebras", "openrouter_free"):
            assert _PROVIDER_CONFIG[name].get("format") is None


# ---------------------------------------------------------------------------
# get_available_providers
# ---------------------------------------------------------------------------

class TestGetAvailableProviders:
    """Provider availability based on env vars."""

    def test_no_keys_returns_empty(self):
        env_keys = [c["key_env"] for c in _PROVIDER_CONFIG.values()]
        with patch.dict(os.environ, {k: "" for k in env_keys}, clear=False):
            # Remove the keys entirely
            env = {k: os.environ.pop(k, "") for k in env_keys}
            try:
                result = get_available_providers()
                assert result == []
            finally:
                os.environ.update(env)

    def test_one_key_returns_one_provider(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key-123"}, clear=False):
            result = get_available_providers()
            assert "groq" in result

    def test_multiple_keys(self):
        keys = {"GROQ_API_KEY": "g", "CEREBRAS_API_KEY": "c"}
        with patch.dict(os.environ, keys, clear=False):
            result = get_available_providers()
            assert "groq" in result
            assert "cerebras" in result

    def test_empty_string_not_counted(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
            result = get_available_providers()
            assert "groq" not in result


# ---------------------------------------------------------------------------
# escalate — failover logic
# ---------------------------------------------------------------------------

class TestEscalate:
    """Escalation with failover across providers."""

    def test_unknown_provider_raises(self):
        with pytest.raises(CloudRouterError, match="Unknown provider"):
            escalate("test", preferred="nonexistent")

    def test_preferred_provider_called_first(self):
        with patch("core.cloud_router._call_provider") as mock_call:
            mock_call.return_value = "success"
            result = escalate("test prompt", preferred="groq")
            assert result == "success"
            mock_call.assert_called_once_with("groq", "test prompt", "")

    def test_failover_to_next_provider(self):
        with patch("core.cloud_router._call_provider") as mock_call:
            # First call (groq) fails, second (cerebras) succeeds
            mock_call.side_effect = [RateLimitError("rate limited"), "success"]
            result = escalate("test", preferred="groq")
            assert result == "success"
            assert mock_call.call_count == 2

    def test_all_providers_fail_raises(self):
        with patch("core.cloud_router._call_provider") as mock_call:
            mock_call.side_effect = RateLimitError("rate limited")
            with pytest.raises(CloudRouterError, match="All cloud providers exhausted"):
                escalate("test", preferred="groq")
            assert mock_call.call_count == len(PROVIDERS)

    def test_auth_error_skips_to_next(self):
        with patch("core.cloud_router._call_provider") as mock_call:
            mock_call.side_effect = [ProviderAuthError("bad key"), "ok"]
            result = escalate("test", preferred="groq")
            assert result == "ok"

    def test_system_prompt_passed_through(self):
        with patch("core.cloud_router._call_provider") as mock_call:
            mock_call.return_value = "response"
            escalate("prompt", preferred="groq", system_prompt="Be helpful")
            mock_call.assert_called_once_with("groq", "prompt", "Be helpful")

    def test_failover_order_preferred_first(self):
        """Failover order: preferred first, then remaining in PROVIDERS order."""
        with patch("core.cloud_router._call_provider") as mock_call:
            # All fail so we can see the full order
            mock_call.side_effect = [RateLimitError("r1"), RateLimitError("r2"), RateLimitError("r3"), RateLimitError("r4")]
            with pytest.raises(CloudRouterError):
                escalate("test", preferred="cerebras")
            call_order = [call.args[0] for call in mock_call.call_args_list]
            assert call_order[0] == "cerebras"
            # Rest follow PROVIDERS order (minus cerebras)
            remaining = [p for p in PROVIDERS if p != "cerebras"]
            assert call_order[1:] == remaining


# ---------------------------------------------------------------------------
# _call_openai_compatible
# ---------------------------------------------------------------------------

class TestCallOpenAICompatible:
    """OpenAI-compatible API calls (Groq, Cerebras, OpenRouter)."""

    @patch("core.cloud_router.httpx.Client")
    def test_missing_api_key_raises_auth_error(self, mock_client_cls):
        from core.cloud_router import _call_openai_compatible
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
            os.environ.pop("GROQ_API_KEY", None)
            with pytest.raises(ProviderAuthError, match="GROQ_API_KEY not set"):
                _call_openai_compatible("groq", "test")

    @patch("core.cloud_router.httpx.Client")
    def test_success_response(self, mock_client_cls):
        from core.cloud_router import _call_openai_compatible
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hello from cloud"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            result = _call_openai_compatible("groq", "say hello")
            assert result == "hello from cloud"

    @patch("core.cloud_router.httpx.Client")
    def test_rate_limit_429(self, mock_client_cls):
        from core.cloud_router import _call_openai_compatible
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with pytest.raises(RateLimitError, match="rate limited"):
                _call_openai_compatible("groq", "test")

    @patch("core.cloud_router.httpx.Client")
    def test_auth_failure_401(self, mock_client_cls):
        from core.cloud_router import _call_openai_compatible
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.dict(os.environ, {"GROQ_API_KEY": "bad-key"}):
            with pytest.raises(ProviderAuthError, match="authentication failed"):
                _call_openai_compatible("groq", "test")

    @patch("core.cloud_router.httpx.Client")
    def test_timeout_raises_rate_limit(self, mock_client_cls):
        from core.cloud_router import _call_openai_compatible
        mock_client_cls.return_value.__enter__.return_value.post.side_effect = httpx.TimeoutException("timeout")

        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            with pytest.raises(RateLimitError, match="timed out"):
                _call_openai_compatible("groq", "test")

    @patch("core.cloud_router.httpx.Client")
    def test_openrouter_specific_headers(self, mock_client_cls):
        """OpenRouter requires HTTP-Referer and X-Title headers."""
        from core.cloud_router import _call_openai_compatible
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        mock_response.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            _call_openai_compatible("openrouter_free", "test")
            call_kwargs = mock_client_cls.return_value.__enter__.return_value.post.call_args
            headers = call_kwargs.kwargs["headers"]
            assert "HTTP-Referer" in headers
            assert "X-Title" in headers


# ---------------------------------------------------------------------------
# _call_google_ai_studio
# ---------------------------------------------------------------------------

class TestCallGoogleAIStudio:
    """Google AI Studio (Gemini) API calls."""

    @patch("core.cloud_router.httpx.Client")
    def test_missing_api_key_raises_auth_error(self, mock_client_cls):
        from core.cloud_router import _call_google_ai_studio
        os.environ.pop("GOOGLE_AI_STUDIO_KEY", None)
        with pytest.raises(ProviderAuthError, match="GOOGLE_AI_STUDIO_KEY not set"):
            _call_google_ai_studio("test")

    @patch("core.cloud_router.httpx.Client")
    def test_success_response(self, mock_client_cls):
        from core.cloud_router import _call_google_ai_studio
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "gemini response"}]}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.dict(os.environ, {"GOOGLE_AI_STUDIO_KEY": "test-key"}):
            result = _call_google_ai_studio("test prompt")
            assert result == "gemini response"

    @patch("core.cloud_router.httpx.Client")
    def test_system_prompt_converted_to_contents(self, mock_client_cls):
        from core.cloud_router import _call_google_ai_studio
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.dict(os.environ, {"GOOGLE_AI_STUDIO_KEY": "test-key"}):
            _call_google_ai_studio("user msg", system_prompt="Be helpful")
            call_kwargs = mock_client_cls.return_value.__enter__.return_value.post.call_args
            payload = call_kwargs.kwargs["json"]
            # Should have system + model + user = 3 contents
            assert len(payload["contents"]) == 3
            assert payload["contents"][0]["role"] == "user"  # system as user
            assert payload["contents"][1]["role"] == "model"  # ack
            assert payload["contents"][2]["role"] == "user"  # actual prompt

    @patch("core.cloud_router.httpx.Client")
    def test_rate_limit_429(self, mock_client_cls):
        from core.cloud_router import _call_google_ai_studio
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_response

        with patch.dict(os.environ, {"GOOGLE_AI_STUDIO_KEY": "test-key"}):
            with pytest.raises(RateLimitError, match="rate limited"):
                _call_google_ai_studio("test")


# ---------------------------------------------------------------------------
# _call_provider routing
# ---------------------------------------------------------------------------

class TestCallProvider:
    """Provider routing — correct function called per provider type."""

    @patch("core.cloud_router._call_openai_compatible")
    def test_groq_routes_to_openai_compatible(self, mock_openai):
        from core.cloud_router import _call_provider
        mock_openai.return_value = "result"
        result = _call_provider("groq", "test")
        assert result == "result"
        mock_openai.assert_called_once_with("groq", "test", "")

    @patch("core.cloud_router._call_openai_compatible")
    def test_cerebras_routes_to_openai_compatible(self, mock_openai):
        from core.cloud_router import _call_provider
        mock_openai.return_value = "result"
        result = _call_provider("cerebras", "test")
        assert result == "result"

    @patch("core.cloud_router._call_openai_compatible")
    def test_openrouter_routes_to_openai_compatible(self, mock_openai):
        from core.cloud_router import _call_provider
        mock_openai.return_value = "result"
        result = _call_provider("openrouter_free", "test")
        assert result == "result"

    @patch("core.cloud_router._call_google_ai_studio")
    def test_google_routes_to_google_function(self, mock_google):
        from core.cloud_router import _call_provider
        mock_google.return_value = "result"
        result = _call_provider("google_ai_studio", "test")
        assert result == "result"
        mock_google.assert_called_once_with("test", "")

    def test_unknown_provider_raises(self):
        from core.cloud_router import _call_provider
        with pytest.raises(CloudRouterError, match="Unknown provider"):
            _call_provider("nonexistent", "test")

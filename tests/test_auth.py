"""Tests for core/auth.py — timing-safe token comparison, rate limiting.

Security-critical: every path must be exercised to prevent regressions.
"""

import hmac
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# _check_rate_limit
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    """Rate limiting — sliding window per client IP."""

    def setup_method(self):
        """Clear rate limits between tests."""
        from core.auth import _rate_limits
        _rate_limits.clear()

    def test_allows_requests_under_limit(self):
        from core.auth import _check_rate_limit
        # Should not raise for requests under the limit
        for _ in range(59):
            _check_rate_limit("test-client")
        # 60th request should still be allowed (at limit, not over)
        _check_rate_limit("test-client")

    def test_blocks_requests_over_limit(self):
        from core.auth import _check_rate_limit, RATE_LIMIT_MAX_REQUESTS
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            _check_rate_limit("test-client")
        # Next request should be blocked
        with pytest.raises(HTTPException) as exc_info:
            _check_rate_limit("test-client")
        assert exc_info.value.status_code == 429

    def test_different_clients_are_independent(self):
        from core.auth import _check_rate_limit, RATE_LIMIT_MAX_REQUESTS
        # Fill up client A
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            _check_rate_limit("client-a")
        # Client B should still be allowed
        _check_rate_limit("client-b")  # should not raise

    def test_window_expiry_allows_new_requests(self):
        from core.auth import _check_rate_limit, RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS
        # Fill up the limit
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            _check_rate_limit("test-client")
        # Simulate time passing by manipulating the stored timestamps
        from core.auth import _rate_limits
        old_time = time.monotonic() - RATE_LIMIT_WINDOW_SECONDS - 1
        _rate_limits["test-client"] = [old_time] * RATE_LIMIT_MAX_REQUESTS
        # Should allow new requests now
        _check_rate_limit("test-client")  # should not raise


# ---------------------------------------------------------------------------
# require_token
# ---------------------------------------------------------------------------


class TestRequireToken:
    """Token validation — timing-safe comparison."""

    def _make_request(self, client_host: str = "127.0.0.1"):
        """Create a mock Request object."""
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = client_host
        return request

    def setup_method(self):
        """Clear rate limits between tests."""
        from core.auth import _rate_limits
        _rate_limits.clear()

    def test_no_token_configured_allows_access(self):
        """When ADHD_COPILOT_TOKEN is empty, access is allowed (localhost mode)."""
        from core.auth import require_token
        with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": ""}, clear=False):
            result = require_token(self._make_request(), x_api_token=None)
            assert result == ""

    def test_valid_token_passes(self):
        from core.auth import require_token
        with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": "secret-token-123"}, clear=False):
            result = require_token(self._make_request(), x_api_token="secret-token-123")
            assert result == "secret-token-123"

    def test_invalid_token_raises_401(self):
        from core.auth import require_token
        with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": "correct-token"}, clear=False):
            with pytest.raises(HTTPException) as exc_info:
                require_token(self._make_request(), x_api_token="wrong-token")
            assert exc_info.value.status_code == 401

    def test_missing_token_raises_401(self):
        from core.auth import require_token
        with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": "required-token"}, clear=False):
            with pytest.raises(HTTPException) as exc_info:
                require_token(self._make_request(), x_api_token=None)
            assert exc_info.value.status_code == 401

    def test_empty_token_when_required_raises_401(self):
        from core.auth import require_token
        with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": "required"}, clear=False):
            with pytest.raises(HTTPException) as exc_info:
                require_token(self._make_request(), x_api_token="")
            assert exc_info.value.status_code == 401

    def test_timing_safe_comparison_used(self):
        """Verify hmac.compare_digest is used (not == operator)."""
        from core.auth import require_token
        with patch("core.auth.hmac.compare_digest") as mock_cmp:
            mock_cmp.return_value = True
            with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": "test"}, clear=False):
                require_token(self._make_request(), x_api_token="test")
            mock_cmp.assert_called_once()

    def test_returns_token_string_on_success(self):
        from core.auth import require_token
        with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": "abc"}, clear=False):
            result = require_token(self._make_request(), x_api_token="abc")
            assert isinstance(result, str)

    def test_rate_limit_applied_to_request(self):
        """require_token should check rate limit by client IP."""
        from core.auth import require_token, _rate_limits, RATE_LIMIT_MAX_REQUESTS
        with patch.dict(os.environ, {"ADHD_COPILOT_TOKEN": ""}, clear=False):
            # Flood with requests from the same IP
            for _ in range(RATE_LIMIT_MAX_REQUESTS):
                require_token(self._make_request("10.0.0.1"), x_api_token=None)
            # Next should be rate limited
            with pytest.raises(HTTPException) as exc_info:
                require_token(self._make_request("10.0.0.1"), x_api_token=None)
            assert exc_info.value.status_code == 429

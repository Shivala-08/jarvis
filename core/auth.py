"""Authentication — timing-safe token validation with rate limiting."""
import hmac
import os
import time
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

API_TOKEN = os.environ.get("ADHD_COPILOT_TOKEN", "")
API_KEY_HEADER = APIKeyHeader(name="X-API-Token", auto_error=False)

# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory sliding window)
# ---------------------------------------------------------------------------

_rate_limits: Dict[str, list] = defaultdict(list)
RATE_LIMIT_MAX_REQUESTS = 60  # per window
RATE_LIMIT_WINDOW_SECONDS = 60  # 1 minute window


def _check_rate_limit(client_id: str) -> None:
    """Check and enforce rate limiting for a client ID.

    Raises HTTPException 429 if the client exceeds the rate limit.
    """
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    # Prune old entries
    _rate_limits[client_id] = [
        t for t in _rate_limits[client_id] if t > window_start
    ]

    if len(_rate_limits[client_id]) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please slow down.",
        )

    _rate_limits[client_id].append(now)


def require_token(
    request: Request,
    x_api_token: str = Security(API_KEY_HEADER),
) -> str:
    """FastAPI dependency to validate X-API-Token against ADHD_COPILOT_TOKEN.

    Uses timing-safe comparison to prevent side-channel attacks.
    Enforces rate limiting per client IP.
    """
    # Rate limit by client IP
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    # Dynamically fetch the token to support setting it in tests or via env reloading
    configured_token = os.environ.get("ADHD_COPILOT_TOKEN", API_TOKEN)

    if configured_token:
        # Timing-safe comparison to prevent timing attacks
        token_bytes = (x_api_token or "").encode("utf-8")
        expected_bytes = configured_token.encode("utf-8")
        if not hmac.compare_digest(token_bytes, expected_bytes):
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API token",
            )
    # If no token is configured in the environment, we allow access
    # (default localhost isolation mode)
    return x_api_token or ""


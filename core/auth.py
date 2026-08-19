import os
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

API_TOKEN = os.environ.get("ADHD_COPILOT_TOKEN", "")
API_KEY_HEADER = APIKeyHeader(name="X-API-Token", auto_error=False)

def require_token(x_api_token: str = Security(API_KEY_HEADER)):
    """FastAPI dependency to validate X-API-Token against ADHD_COPILOT_TOKEN."""
    # We dynamically fetch the token to support setting it in tests or via env reloading
    configured_token = os.environ.get("ADHD_COPILOT_TOKEN", API_TOKEN)
    
    if configured_token:
        if not x_api_token or x_api_token != configured_token:
            raise HTTPException(status_code=401, detail="Invalid or missing API token")
    # If no token is configured in the environment, we allow access (default localhost isolation mode)
    return x_api_token

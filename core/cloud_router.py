"""Cloud Router — multi-provider failover for free-tier cloud inference.

Tries providers in order, falling through on rate limits or errors.
All providers here are genuinely free (no credit card required).

Providers:
    - Groq: fast inference, generous free tier
    - Cerebras: fast inference, free tier available
    - OpenRouter: routes to free models (Llama, Mixtral, etc.)
    - Google AI Studio: Gemini free tier, 1M token context

Usage:
    from core.cloud_router import escalate, CloudRouterError

    try:
        response = escalate(prompt="Explain this architecture...", preferred="groq")
    except CloudRouterError:
        # All providers exhausted — fall back to local
        response = ollama.chat(model="qwen3.5:9b", ...)
"""
import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = ["groq", "cerebras", "openrouter_free", "google_ai_studio"]

# Provider details — endpoint, model, and env var for API key
_PROVIDER_CONFIG: Dict[str, Dict[str, Any]] = {
    "groq": {
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
        "timeout": 30,
    },
    "cerebras": {
        "endpoint": "https://api.cerebras.ai/v1/chat/completions",
        "model": "llama-3.3-70b",
        "key_env": "CEREBRAS_API_KEY",
        "timeout": 30,
    },
    "openrouter_free": {
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "key_env": "OPENROUTER_API_KEY",
        "timeout": 60,
    },
    "google_ai_studio": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "model": "gemini-2.0-flash",
        "key_env": "GOOGLE_AI_STUDIO_KEY",
        "timeout": 60,
        "format": "google",
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CloudRouterError(Exception):
    """All providers exhausted — caller should fall back to local model."""
    pass


class RateLimitError(Exception):
    """Provider rate-limited — try next provider."""
    pass


class ProviderAuthError(Exception):
    """Provider API key missing or invalid."""
    pass


# ---------------------------------------------------------------------------
# Provider calls
# ---------------------------------------------------------------------------

def _call_openai_compatible(
    provider_name: str,
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """Call an OpenAI-compatible API (Groq, Cerebras, OpenRouter)."""
    config = _PROVIDER_CONFIG[provider_name]
    api_key = os.environ.get(config["key_env"], "")

    if not api_key:
        raise ProviderAuthError(
            f"{config['key_env']} not set — skipping {provider_name}"
        )

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": config["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # OpenRouter requires this header
    if provider_name == "openrouter_free":
        headers["HTTP-Referer"] = "https://github.com/adhd-copilot"
        headers["X-Title"] = "ADHD Co-Processor"

    try:
        with httpx.Client(timeout=config["timeout"]) as client:
            response = client.post(
                config["endpoint"],
                json=payload,
                headers=headers,
            )

        if response.status_code == 429:
            raise RateLimitError(f"{provider_name} rate limited")

        if response.status_code == 401:
            raise ProviderAuthError(f"{provider_name} authentication failed")

        response.raise_for_status()
        data = response.json()

        return data["choices"][0]["message"]["content"]

    except httpx.TimeoutException:
        raise RateLimitError(f"{provider_name} timed out")
    except (KeyError, IndexError) as e:
        raise CloudRouterError(f"{provider_name} returned unexpected response: {e}")


def _call_google_ai_studio(
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> str:
    """Call Google AI Studio (Gemini) API."""
    config = _PROVIDER_CONFIG["google_ai_studio"]
    api_key = os.environ.get(config["key_env"], "")

    if not api_key:
        raise ProviderAuthError(
            f"{config['key_env']} not set — skipping google_ai_studio"
        )

    contents = []
    if system_prompt:
        contents.append({
            "role": "user",
            "parts": [{"text": system_prompt}],
        })
        contents.append({
            "role": "model",
            "parts": [{"text": "Understood."}],
        })
    contents.append({
        "role": "user",
        "parts": [{"text": prompt}],
    })

    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }

    url = f"{config['endpoint']}?key={api_key}"

    try:
        with httpx.Client(timeout=config["timeout"]) as client:
            response = client.post(url, json=payload)

        if response.status_code == 429:
            raise RateLimitError("google_ai_studio rate limited")

        if response.status_code == 401:
            raise ProviderAuthError("google_ai_studio authentication failed")

        response.raise_for_status()
        data = response.json()

        return data["candidates"][0]["content"]["parts"][0]["text"]

    except httpx.TimeoutException:
        raise RateLimitError("google_ai_studio timed out")
    except (KeyError, IndexError) as e:
        raise CloudRouterError(f"google_ai_studio returned unexpected response: {e}")


def _call_provider(provider_name: str, prompt: str, system_prompt: str = "") -> str:
    """Route to the correct provider call."""
    config = _PROVIDER_CONFIG.get(provider_name)
    if not config:
        raise CloudRouterError(f"Unknown provider: {provider_name}")

    if config.get("format") == "google":
        return _call_google_ai_studio(prompt, system_prompt)

    return _call_openai_compatible(provider_name, prompt, system_prompt)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def escalate(
    prompt: str,
    preferred: str,
    system_prompt: str = "",
    max_tokens: int = 2048,
) -> str:
    """Escalate a prompt to cloud providers with failover.

    Tries the preferred provider first, then falls through to others
    on rate limits or errors. Raises CloudRouterError if all fail.

    Args:
        prompt: The user prompt to send.
        preferred: Provider name to try first.
        system_prompt: Optional system prompt.
        max_tokens: Max response tokens.

    Returns:
        The model's text response.

    Raises:
        CloudRouterError: All providers failed.
    """
    if preferred not in PROVIDERS:
        raise CloudRouterError(f"Unknown provider: {preferred}")

    order = [preferred] + [p for p in PROVIDERS if p != preferred]
    errors = []

    for provider in order:
        try:
            logger.info(f"Trying cloud provider: {provider}")
            result = _call_provider(provider, prompt, system_prompt)
            logger.info(f"Success via {provider}")
            return result
        except RateLimitError as e:
            logger.warning(f"Rate limited: {e}")
            errors.append(f"{provider}: rate limited")
            continue
        except ProviderAuthError as e:
            logger.warning(f"Auth error: {e}")
            errors.append(f"{provider}: {e}")
            continue
        except CloudRouterError as e:
            logger.warning(f"Provider error: {e}")
            errors.append(f"{provider}: {e}")
            continue

    raise CloudRouterError(
        f"All cloud providers exhausted. Tried: {', '.join(errors)}"
    )


def get_available_providers() -> list:
    """List providers that have API keys configured."""
    available = []
    for name, config in _PROVIDER_CONFIG.items():
        key = os.environ.get(config["key_env"], "")
        if key:
            available.append(name)
    return available

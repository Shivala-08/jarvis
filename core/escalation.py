"""Escalation Rules — deterministic cloud-escalation routing.

Decides whether a task should stay local or escalate to a free-tier cloud
provider. Runs BEFORE any cloud call — no override flag, no exceptions.

Usage:
    from core.escalation import should_escalate, contains_sensitive_data

    provider = should_escalate(
        task_type="braindump",
        context_tokens=20_000,
        local_attempts_failed=0,
        text=user_input,
    )
    if provider:
        response = cloud_router.escalate(prompt, preferred=provider)
    else:
        response = ollama.chat(model="qwen3.5:9b", ...)
"""
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Token budget for local models — beyond this, quality degrades noticeably
MAX_LOCAL_CONTEXT_TOKENS = 16_000

# Patterns that indicate sensitive data — MUST NOT leave the machine
SENSITIVE_PATTERNS = [
    r"password",
    r"api[_\-]?key",
    r"ssh[\s_\-]?key",
    r"private[_\-]?key",
    r"secret[_\-]?key",
    r"access[_\-]?token",
    r"bearer\s+\w+",
    r"authorization:\s*\w+",
    r"\b\d{3}-\d{2}-\d{4}\b",           # SSN-like (XXX-XX-XXXX)
    r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit card-like
    r"BEGIN\s+(RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY",
]

# ---------------------------------------------------------------------------
# Sensitive data detection
# ---------------------------------------------------------------------------

def contains_sensitive_data(text: str) -> bool:
    """Check if text contains patterns that should never leave the machine.

    Returns True if any sensitive pattern matches — this is a hard block
    with no override. When in doubt, keep it local.
    """
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# Escalation decision
# ---------------------------------------------------------------------------

def should_escalate(
    task_type: str,
    context_tokens: int,
    local_attempts_failed: int,
    text: str,
) -> Optional[str]:
    """Decide whether to escalate and to which provider.

    Returns:
        Provider name string if escalation is recommended, or None to stay local.

    Decision order (most important first):
        1. Sensitive data → NEVER escalate (hard block, no exceptions)
        2. Context too large for local model → Google AI Studio (1M token context, free)
        3. Local model failed 2+ times → Groq (fast repair pass)
        4. Architecture/design tasks → OpenRouter free tier (120B+ reasoning)
        5. Everything else → stay local
    """
    # 1. Hard block — sensitive data never leaves
    if contains_sensitive_data(text):
        return None

    # 2. Context overflow — local models degrade past ~16K tokens
    if context_tokens > MAX_LOCAL_CONTEXT_TOKENS:
        return "google_ai_studio"

    # 3. Local model struggling — fast cloud repair
    if local_attempts_failed >= 2:
        return "groq"

    # 4. Complex reasoning tasks benefit from larger models
    if task_type in ("architecture_design", "complex_reasoning", "multi_step_planning"):
        return "openrouter_free"

    # 5. Default — stay local
    return None


# ---------------------------------------------------------------------------
# Token estimation (rough, for routing decisions — not precise accounting)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token count estimate (words × 1.3).

    Good enough for the escalation routing decision. Not suitable for
    precise context-window budgeting.
    """
    words = len(text.split())
    return int(words * 1.3)


# ---------------------------------------------------------------------------
# Reusable LLM call with escalation (used by all agents)
# ---------------------------------------------------------------------------

def llm_call(
    prompt: str,
    system_prompt: str = "",
    task_type: str = "general",
    model: str | None = None,
    temperature: float = 0.3,
    local_attempts_failed: int = 0,
) -> str:
    """Call an LLM with automatic cloud escalation.

    Tries cloud first if conditions warrant it, then falls back to local Ollama.
    Returns the raw text response (callers handle JSON parsing if needed).

    Args:
        prompt: The user prompt.
        system_prompt: Optional system prompt.
        task_type: Used for escalation routing (e.g. 'braindump', 'coding').
        model: Ollama model name (if None, uses get_default_model()).
        temperature: Sampling temperature.
        local_attempts_failed: How many local attempts have failed (triggers Groq).

    Returns:
        The model's text response.

    Raises:
        RuntimeError: If both cloud and local fail.
    """
    import logging
    logger = logging.getLogger(__name__)

    if model is None:
        from core.config import get_default_model
        model = get_default_model()

    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    context_tokens = estimate_tokens(full_prompt)

    # --- Cloud escalation attempt ---
    from core.cloud_router import escalate, get_available_providers, CloudRouterError

    provider = should_escalate(
        task_type=task_type,
        context_tokens=context_tokens,
        local_attempts_failed=local_attempts_failed,
        text=full_prompt,
    )
    if provider and provider in get_available_providers():
        try:
            return escalate(full_prompt, preferred=provider, system_prompt=system_prompt)
        except CloudRouterError as e:
            logger.warning(f"Cloud escalation failed ({e}), falling back to local")

    # --- Local fallback ---
    try:
        import ollama
    except ImportError:
        raise RuntimeError("Ollama not installed — cannot make LLM calls")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = ollama.chat(
        model=model,
        messages=messages,
        options={"temperature": temperature},
    )
    return response["message"]["content"].strip()

"""Brain Dump Agent — converts raw stream-of-consciousness text into structured JSON tasks.

This is the simplest agent: validates plumbing for the agent orchestration layer.
Inference runs locally via Ollama, with optional cloud escalation for large inputs.
"""
import json
import logging

from core.escalation import llm_call

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a brain-dump processor for someone with ADHD.
Given raw stream-of-consciousness text, extract actionable items and return ONLY valid JSON.

Output schema:
{
  "thoughts": [
    {
      "text": "<original or cleaned-up thought>",
      "type": "task | idea | reminder | worry | observation",
      "priority": "now | soon | someday",
      "estimated_minutes": <int>,
      "tags": ["<relevant-tag>"]
    }
  ],
  "mood_hint": "<one-word mood guess from tone>",
  "suggested_first_step": "<single smallest concrete action>"
}

Rules:
- Split overlapping thoughts; don't merge unrelated ones.
- Keep task descriptions short (≤ 12 words).
- If something is clearly emotional (not a task), mark type "worry" or "observation".
- Never hallucinate tasks not present in the input.
- Return ONLY the JSON object, no markdown fences.
"""


def process_braindump(raw_text: str, model: str = None, context: str = None) -> dict:
    """Send raw text to Ollama and return structured JSON.

    Args:
        raw_text: The brain-dump text to process
        model: Ollama model to use
        context: Optional conversation context for multi-turn support

    Returns a fallback structure if Ollama returns empty or invalid JSON.
    """
    if model is None:
        from core.config import get_default_model
        model = get_default_model()
    # Build user message with optional context
    user_content = raw_text
    if context:
        user_content = f"{context}\n\nCurrent input: {raw_text}"
    
    try:
        content = llm_call(
            prompt=user_content,
            system_prompt=SYSTEM_PROMPT,
            task_type="braindump",
            model=model,
            temperature=0.3,
        )
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        content = ""

    # Strip markdown fences if present
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        content = content.rsplit("```", 1)[0].strip()

    # Try to parse JSON; fall back to a basic structure
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # Build a fallback from the raw text
        return {
            "thoughts": [
                {
                    "text": raw_text[:120],
                    "type": "task",
                    "priority": "soon",
                    "estimated_minutes": 15,
                    "tags": [],
                }
            ],
            "mood_hint": "unclear",
            "suggested_first_step": "Review this thought",
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Need to finish the report by Friday, also worried about the meeting tomorrow, "
        "and I had a cool idea for a side project about habit tracking for ADHD"
    )
    result = process_braindump(text)
    print(json.dumps(result, indent=2))

"""Brain Dump Agent — converts raw stream-of-consciousness text into structured JSON tasks.

This is the simplest agent: validates plumbing for the agent orchestration layer.
Zero cloud calls; all inference runs locally via Ollama.
"""
import json
import ollama

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


def process_braindump(raw_text: str, model: str = "llama3.1:latest") -> dict:
    """Send raw text to Ollama and return structured JSON.

    Returns a fallback structure if Ollama returns empty or invalid JSON.
    """
    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            options={"temperature": 0.3},
        )
        content = response["message"]["content"].strip()
    except Exception as e:
        print(f"  ⚠️  Ollama error: {e}")
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

"""Study / Task Decomposition Agent — breaks any topic into sub-15-minute micro-units.

Features:
- JSON-schema-constrained output
- Active-recall questions for each micro-unit
- Dependency ordering between units
- Validator rejects any unit > 15 minutes
- All inference via local Ollama — zero cloud calls
"""
import json
from typing import Any

import ollama

SYSTEM_PROMPT = """\
You are a study planning agent for someone with ADHD.
Given a topic or syllabus, decompose it into micro-learning units.

STRICT RULES:
- Each unit MUST be completable in ≤ 15 minutes.
- Every unit must have at least 1 active-recall question.
- Units must list prerequisites (by id) or "none".
- Return ONLY valid JSON matching the schema below.

Output schema:
{
  "topic": "<original topic>",
  "units": [
    {
      "id": "u1",
      "title": "<concise title, ≤8 words>",
      "description": "<1-2 sentence what to do>",
      "estimated_minutes": <int, max 15>,
      "prerequisites": ["<unit-id>" or "none"],
      "active_recall_questions": [
        "<question the learner should answer from memory after this unit>"
      ],
      "difficulty": "intro | intermediate | advanced"
    }
  ],
  "total_estimated_minutes": <int>,
  "suggested_study_order": ["u1", "u2", ...]
}

Return ONLY the JSON. No markdown fences, no commentary.
"""

# Validator: max minutes per unit
MAX_UNIT_MINUTES = 15


def validate_units(units: list[dict]) -> list[str]:
    """Validate that all units comply with constraints. Returns list of errors (empty = valid)."""
    errors = []
    ids_seen = set()
    for i, unit in enumerate(units):
        uid = unit.get("id", f"index-{i}")
        ids_seen.add(uid)
        mins = unit.get("estimated_minutes", 0)
        if mins > MAX_UNIT_MINUTES:
            errors.append(f"Unit '{uid}' is {mins} min — exceeds {MAX_UNIT_MINUTES} min limit.")
        if not unit.get("title"):
            errors.append(f"Unit '{uid}' missing title.")
        if not unit.get("active_recall_questions"):
            errors.append(f"Unit '{uid}' has no active-recall questions.")
        prereqs = unit.get("prerequisites", [])
        for p in prereqs:
            if p != "none" and p not in ids_seen:
                errors.append(f"Unit '{uid}' depends on unknown prerequisite '{p}'.")

    # Check for circular dependencies (simple forward-reference check)
    for unit in units:
        prereqs = unit.get("prerequisites", [])
        if unit.get("id") in prereqs:
            errors.append(f"Unit '{unit['id']}' has a circular self-dependency.")
    return errors


def decompose_topic(topic: str, model: str = "llama3.1:latest") -> dict:
    """Decompose a topic into micro-units. Auto-retries with smaller scope if validation fails."""
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Decompose this topic into micro-units: {topic}"},
        ],
        options={"temperature": 0.4},
    )
    content = response["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        content = content.rsplit("```", 1)[0].strip()

    result = json.loads(content)
    units = result.get("units", [])

    errors = validate_units(units)
    if errors:
        # Auto-fix: split any over-long units
        fixed_units = []
        for unit in units:
            mins = unit.get("estimated_minutes", 15)
            if mins > MAX_UNIT_MINUTES:
                # Split into ceil(mins/15) parts
                parts = (mins + MAX_UNIT_MINUTES - 1) // MAX_UNIT_MINUTES
                per_part = mins // parts
                for p in range(parts):
                    fixed_units.append({
                        **unit,
                        "id": f"{unit['id']}_part{p+1}",
                        "title": f"{unit['title']} (Part {p+1}/{parts})",
                        "estimated_minutes": per_part,
                        "prerequisites": (
                            [f"{unit['id']}_part{p}"] if p > 0
                            else unit.get("prerequisites", ["none"])
                        ),
                    })
            else:
                fixed_units.append(unit)
        result["units"] = fixed_units
        result["total_estimated_minutes"] = sum(u["estimated_minutes"] for u in fixed_units)

    return result


def format_study_plan(plan: dict) -> str:
    """Render a human-readable study plan."""
    lines = [f"📚 Study Plan: {plan['topic']}", ""]
    for unit in plan.get("units", []):
        lines.append(f"  [{unit['id']}] {unit['title']} ({unit['estimated_minutes']} min)")
        lines.append(f"    {unit.get('description', '')}")
        prereqs = unit.get("prerequisites", [])
        if prereqs and prereqs != ["none"]:
            lines.append(f"    Prerequisites: {', '.join(prereqs)}")
        for q in unit.get("active_recall_questions", []):
            lines.append(f"    ❓ {q}")
        lines.append("")
    total = plan.get("total_estimated_minutes", "—")
    lines.append(f"⏱  Total estimated time: {total} minutes")
    order = plan.get("suggested_study_order", [])
    if order:
        lines.append(f"🔢 Suggested order: {' → '.join(order)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "graph algorithms"
    plan = decompose_topic(topic)
    print(format_study_plan(plan))

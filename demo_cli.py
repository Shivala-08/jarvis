"""Quick demo of scheduler + study planning."""
from agents.scheduler_agent import build_schedule, rebalance, _estimate_alpha, generate_micro_sprint

tasks = [
    {"text": "Finish quarterly report", "estimated_minutes": 90, "priority": "now"},
    {"text": "Call dentist", "estimated_minutes": 15, "priority": "now"},
    {"text": "Pick up groceries", "estimated_minutes": 30, "priority": "now"},
    {"text": "Habit tracking side project", "estimated_minutes": 45, "priority": "someday"},
]

alpha = _estimate_alpha([])
schedule = build_schedule(tasks, alpha)

print("=== Today's Schedule ===")
for block in schedule:
    marker = "🔖" if block["type"] == "task" else "☕"
    label = block.get("label", block["type"])
    start = block["start"][11:16]
    end = block["end"][11:16]
    mins = block.get("scaled_minutes", "—")
    print(f"  {marker} {start}→{end}  {label}  ({mins} min)")

print()
print("=== After missing block 0 (simulating overrun) ===")
remaining, suggestion = rebalance(schedule, missed_block_id=0)
print(f"  💬 {suggestion}")
for block in remaining:
    print(f"  🔖 {block['label']}  {block['start'][11:16]}→{block['end'][11:16]}")

print()
sprint = generate_micro_sprint("Finish quarterly report")
print(f"  🎯 Micro-sprint: {sprint}")

# Study planning
print()
print("=" * 50)
from agents.study_agent import decompose_topic, format_study_plan
plan = decompose_topic("graph algorithms")
print(format_study_plan(plan))

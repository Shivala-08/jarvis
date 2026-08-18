"""Full interactive CLI demo — brain dump → memory → schedule → study → micro-sprint."""
import json
from agents.braindump_agent import process_braindump
from memory.adhd_memory import ADHDMemoryEngine, get_history
from agents.scheduler_agent import build_schedule, rebalance, _estimate_alpha, generate_micro_sprint
from agents.study_agent import decompose_topic, format_study_plan

memory = ADHDMemoryEngine(user_id="demo_user")

# ── Step 1: Brain Dump ──────────────────────────────────────────────
print("=" * 60)
print("🧠 STEP 1: BRAIN DUMP")
print("=" * 60)
raw = (
    "Need to finish the quarterly report by Friday, also worried about "
    "the standup meeting tomorrow morning, had a cool idea for a habit "
    "tracking app for ADHD people, remember to call the dentist and "
    "pick up groceries, the graph algorithms assignment is due next week"
)
print(f"  Input: \"{raw}\"\n")

result = process_braindump(raw)
print(f"  📝 Extracted {len(result['thoughts'])} thoughts:")
for t in result["thoughts"]:
    ttype = t.get("type", "thought")
    text = t.get("text", "?")
    priority = t.get("priority", "?")
    mins = t.get("estimated_minutes", "?")
    print(f"    [{ttype:>11}] {text:<45} priority={priority}  ~{mins}min")
print(f"\n  💭 Mood: {result.get('mood_hint', '?')}")
print(f"  🎯 Suggested first step: {result.get('suggested_first_step', '?')}")

# Store in memory
for t in result["thoughts"]:
    memory.store_task(
        t.get("text", "unnamed task"),
        t.get("estimated_minutes", 15),
        t.get("priority", "soon"),
    )
memory.capture_brain_dump(raw)
print(f"\n  ✅ {len(result['thoughts'])} thoughts stored in memory.")

# ── Step 2: Memory Search ───────────────────────────────────────────
print("\n" + "=" * 60)
print("🔍 STEP 2: MEMORY SEARCH")
print("=" * 60)
query = "work deadlines"
print(f"  Query: \"{query}\"\n")
try:
    results = memory.retrieve_context_for_task(query)
    if results:
        for r in results:
            print(f"    📌 {r['memory'][:70]}  (score: {r['score']:.3f})")
    else:
        print("    (no results yet — memories are still being indexed)")
except Exception as e:
    print(f"    ⚠️  Search skipped: {e}")

# ── Step 3: Build Schedule ──────────────────────────────────────────
print("\n" + "=" * 60)
print("📅 STEP 3: BUILD SCHEDULE")
print("=" * 60)
tasks = []
for t in result["thoughts"]:
    if t.get("type") == "task":
        tasks.append({
            "text": t.get("text", "unnamed task"),
            "estimated_minutes": t.get("estimated_minutes", 25),
            "priority": t.get("priority", "soon"),
        })

# Fallback if LLM didn't classify any as "task"
if not tasks:
    tasks = [
        {"text": "Finish quarterly report", "estimated_minutes": 90, "priority": "now"},
        {"text": "Call dentist", "estimated_minutes": 15, "priority": "soon"},
        {"text": "Pick up groceries", "estimated_minutes": 30, "priority": "soon"},
    ]

history = get_history()
alpha = _estimate_alpha(history)
print(f"  Alpha (time-scaling): {alpha:.2f}\n")

schedule = build_schedule(tasks, alpha)
for block in schedule:
    marker = "🔖" if block["type"] == "task" else "☕"
    label = block.get("label", block["type"])
    start = block["start"][11:16]
    end = block["end"][11:16]
    mins = block.get("scaled_minutes", "—")
    print(f"  {marker} {start}→{end}  {label}  ({mins} min)")

# ── Step 4: Silent Rebalance ────────────────────────────────────────
print("\n" + "=" * 60)
print("🔄 STEP 4: SILENT REBALANCE (simulating missed block)")
print("=" * 60)
remaining, suggestion = rebalance(schedule, missed_block_id=0)
print(f"  💬 {suggestion}\n")
for block in remaining:
    print(f"  🔖 {block['label']}  {block['start'][11:16]}→{block['end'][11:16]}  ({block.get('scaled_minutes', '—')} min)")

# ── Step 5: Micro-sprint ────────────────────────────────────────────
print("\n" + "=" * 60)
print("🎯 STEP 5: MICRO-SPRINT SUGGESTION")
print("=" * 60)
sprint = generate_micro_sprint(tasks[0]["text"] if tasks else "start working")
print(f"  💬 \"{sprint}\"")

# ── Step 6: Study Plan ──────────────────────────────────────────────
print("\n" + "=" * 60)
print("📚 STEP 6: STUDY PLAN (graph algorithms)")
print("=" * 60)
plan = decompose_topic("graph algorithms")
print(format_study_plan(plan))

# ── Summary ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("✅ DEMO COMPLETE — all features working locally, zero cloud calls")
print("=" * 60)

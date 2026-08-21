# 🔍 DETAILED TECHNICAL AUDIT — ADHD Cognitive Co-Processor

**Date:** August 22, 2026
**Auditor:** Buffy (Codebuff)
**Modules audited:** 34 Python modules across 6 layers

---

## Executive Summary

I audited all **34 Python modules** across 6 layers (core, routes, agents, memory, speech, remote), ran the full test suite, and checked every import. Here's every system failure, ranked by severity.

---

## 🔴 CRITICAL FAILURES (System is broken)

### 1. MISSING DEPENDENCIES — 5 packages not installed
```
❌ ollama        → study_agent, body_double_agent, coding_agent, memory_service ALL FAIL
❌ psutil        → body_double_agent crashes on import → FocusMonitor broken
❌ sounddevice   → speech_pipeline fails → voice WebSocket dead
❌ qdrant-client → memory purge/status endpoints fail
❌ mem0ai        → ADHDMemoryEngine fails → memories, braindump storage, search ALL fail
```
**Impact:** Brain dump can't store to memory. Voice system can't transcribe. Focus monitoring crashes. Memory search broken. Scheduler can't read task history.

**Fix:** `pip install ollama psutil sounddevice qdrant-client mem0ai`

### 2. `study_agent.py` — Direct `ollama.chat()` blocks event loop
```python
# routes/agents.py line 34-43 (FIXED)
@router.post("/study")
def api_study(topic: str, ...):  # ← SYNCHRONOUS handler
    plan = decompose_topic(topic)  # ← calls ollama.chat() directly (~30-40s)
```
**Impact:** Single study request freezes the ENTIRE server for 30-40s. All other requests queue up and time out. Voice WebSocket dies. Health checks fail.

**Status:** ✅ FIXED (made async with `asyncio.to_thread`)

### 3. `routes/braindump.py` line 84 — Same sync Ollama call in `/api/conversation`
```python
@router.post("/conversation")
def api_conversation(req: ConversationRequest):  # ← SYNCHRONOUS
    result = process_braindump(req.message, ...)  # ← blocks for 30-40s
```
**Impact:** Same event-loop freeze as study.

**Status:** ✅ FIXED (made async with `asyncio.to_thread`)

### 4. `routes/schedule.py` line 62 — `/api/sprint` also sync-blocks
```python
@router.post("/sprint")
def api_sprint(req: SprintRequest):
    suggestion = generate_micro_sprint(req.task)  # ← calls llm_call() → ollama.chat()
```
**Impact:** Sprint endpoint freezes server for 30-40s.

**Status:** ❌ NOT YET FIXED

### 5. `core/proactive.py` — `register_proactive_triggers()` breaks test suite
The function subscribes to `SCHEDULE_UPDATED` and `TASK_COMPLETED` but the test patches `subscribe` AFTER the module has already imported the real one. Test assertion fails:
```
FAILED tests/test_proactive.py::TestEventWiring::test_register_subscribes_to_events
assert 0 >= 2  ← subscribe was never called because patch target is wrong
```
**Impact:** Pre-existing test failure. The function works but tests don't verify it.

---

## 🟠 HIGH SEVERITY (Feature failures)

### 6. Body Double Daemon — `No module named 'psutil'`
```python
# main.py: _run_body_double_daemon()
import psutil  # ← ImportError kills the daemon silently
```
**Impact:** Focus monitoring completely non-functional. No drift detection. No nudges. Server logs show warning but continues.

### 7. Voice WebSocket — Token auth mismatch
The WebSocket handler checks `os.environ.get("ADHD_COPILOT_TOKEN")` but the UI stores the token in `localStorage`. If the browser hasn't been told the token, every WS connection gets:
```
"Unauthorized: Invalid or missing API token"
```
**Impact:** Voice interface completely broken until user manually pastes token.

### 8. Obsidian — Server unreachable
`ObsidianClient.is_available()` connects to `localhost:27124`. If the Obsidian REST API plugin isn't running, `server_reachable: false`. The code handles this gracefully (falls back to file writes), but the dashboard shows "Offline".

**Impact:** Notes are written to disk but Obsidian doesn't see them in real-time.

### 9. `agents/monitor_operative.py` — `FocusMonitor` and `TaskMonitor` instantiations fail
`routes/system.py` line 135:
```python
from agents.monitor_operative import FocusMonitor, TaskMonitor
focus_monitor = FocusMonitor()  # ← These classes DON'T EXIST in monitor_operative.py
```
**Impact:** `GET /api/monitor/stats` always returns 500 error.

### 10. `agents/scheduler_agent.py` — Hard crash if config missing
```python
CONFIG = toml.load("config/config.toml")
SCHEDULER_CFG = CONFIG["scheduler"]  # ← KeyError if config.toml missing
```
**Impact:** Server won't start if `config/config.toml` is missing or malformed.

---

## 🟡 MEDIUM SEVERITY (Reliability / Data issues)

### 11. `core/auth.py` — Rate limiter uses in-memory dict (not persistent)
`_rate_limits` resets every restart. During rate limit testing, rapid restarts bypass it. Also, `RATE_LIMIT_MAX_REQUESTS = 60` per minute may be too aggressive for the UI which fires 5+ API calls on page load.

### 12. `core/cron_scheduler.py` — Scheduler runs tasks synchronously
`_run_task()` calls `process_braindump()` or `build_schedule()` directly in the scheduler thread. These are 30-40s Ollama calls. While the scheduler is running a task, no other tasks are checked. Missed executions possible.

### 13. `core/memory_service.py` — `MemoryService.start()` never called from `run_ui_server()`
The memory service is created lazily via `get_memory_svc()` but `start()` is never explicitly called — the service starts only if something triggers `build_memory_service()` AND the memory engine is available. If Qdrant isn't running, the service silently fails.

### 14. `memory/adhd_memory.py` — `ObsidianClient._server_available` is cached forever
```python
def is_available(self) -> bool:
    if self._server_available is not None:
        return self._server_available  # ← cached, never rechecked
```
If Obsidian is started after the first check, it stays "Offline" until server restart.

### 15. `agents/web_task_agent.py` — Scrapling not in dependencies
```python
from scrapling.fetchers import StealthyFetcher  # ← ImportError if not installed
```
**Impact:** Web scraping/search features silently fail with import error.

### 16. `agents/vision_agent.py` — `mlx-vlm` only works on Apple Silicon
The vision agent assumes Apple Silicon (`mlx.core.metal`). On Linux/Windows, `analyze_screen()` always falls back to cloud (which doesn't actually have image data).

### 17. `core/sovereignty.py` — `purge_all_memory()` uses `QdrantClient` directly
It bypasses Mem0 and deletes the collection directly. If Qdrant isn't running on port 6333, it fails silently. No connection check before attempting.

---

## 🟢 LOW SEVERITY (Code quality / Minor bugs)

### 18. `core/event_bus.py` — Thread safety gap in `publish()`
```python
def publish(self, event: Event) -> None:
    with self._lock:
        self._event_history.append(event)
    subscribers = list(self._subscribers.get(event.event_type, []))
    # ← subscribers list is captured outside lock, but new subscribers
    #   could be added between lock release and list capture
```
Race condition: a handler subscribing during publish may miss the current event.

### 19. `core/proactive.py` — `_on_task_completed` uses function attributes as state
```python
if not hasattr(_on_task_completed, "_completions"):
    _on_task_completed._completions = []  # ← function-level mutable state
```
This works but is fragile — not thread-safe, not testable, not restartable.

### 20. `core/config.py` — Config loaded once at import, never reloaded
```python
CONFIG = toml.load(str(CONFIG_PATH))  # ← runs once when module is imported
```
If the user edits `config.toml` while the server is running, changes are never picked up.

### 21. `routes/system.py` — `api_health()` calls `get_memory_svc()` which may block
If Qdrant isn't running, `_get_memory_client()` in `adhd_memory.py` will block/fail during health check. The health endpoint should be fast and independent.

### 22. Multiple routes use `from memory.adhd_memory import get_history`
This creates a new `ADHDMemoryEngine` instance each time. Not a bug, but wasteful — should use the singleton from `core/dependencies.py`.

### 23. `agents/scheduler_agent.py` — `build_schedule()` doesn't handle empty `start_time` timezone
`start_time` defaults to `datetime.now(timezone.utc)` but calendar operations use local time. Schedule blocks may have wrong times if user is in a non-UTC timezone.

---

## 📊 Test Suite Results

```
✅ 180 passed
❌ 1 failed (test_proactive.py — wrong mock target)
⏱ 0.61s
```

**Passing systems (tested):**
- Braindump agent ✅
- Scheduler agent ✅
- Study agent validator ✅
- Event bus ✅
- Config loading ✅
- Skill manager ✅
- Cron scheduler ✅
- FastAPI health ✅
- FastAPI braindump ✅
- FastAPI schedule ✅
- Path traversal protection ✅
- Token auth security ✅

---

## 🏗️ Architecture Issues

| Issue | Description |
|-------|------------|
| **No request timeouts** | Ollama calls (30-40s) have no timeout. A single slow request blocks the server indefinitely. |
| **No circuit breaker** | If Ollama is down, every request retries and hangs for 30-40s instead of failing fast. |
| **Global mutable state** | Singletons, function attributes, module-level globals — hard to test and isolate. |
| **No background task queue** | All LLM calls happen in request handlers or fire-and-forget threads. No retry, no backpressure. |
| **Mixed sync/async** | Some routes are `async def`, some are `def`. The sync ones calling Ollama block the event loop. |
| **Health check depends on everything** | `/api/health` initializes memory service, which connects to Qdrant. If Qdrant is down, health check hangs. |

---

## 🎯 Priority Fix List

| Priority | Fix | Effort |
|----------|-----|--------|
| P0 | Install missing pip packages | 1 min |
| P0 | Make `/api/sprint` async (same as study fix) | 5 min |
| P1 | Fix `monitor_operative.py` missing classes | 10 min |
| P1 | Install `psutil` for body double agent | 1 min |
| P1 | Add timeout to Ollama calls in `llm_call()` | 15 min |
| P2 | Fix proactive test mock target | 10 min |
| P2 | Make health check independent of Qdrant | 15 min |
| P2 | Cache-bust `ObsidianClient._server_available` | 5 min |
| P3 | Add circuit breaker for Ollama | 30 min |

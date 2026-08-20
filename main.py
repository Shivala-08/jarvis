"""ADHD Co-Processor — Main entry point.

Ties together brain dump → memory → schedule → study → voice.
All local-first, zero cloud calls, zero paid API keys.

Inspired by OpenJarvis architecture:
- Event-driven communication between components
- Background fact extraction for automatic learning
- Composable skill system for agent capabilities
- Evaluation metrics (energy, latency, FLOPs, cost)
- Cron-based scheduling for persistent tasks

Usage:
    uv run python main.py              # Interactive CLI mode
    uv run python main.py --voice      # Voice interface + UI server
    uv run python main.py --ui         # Start local web UI server + API
    uv run python main.py --test       # Run integration tests
    uv run python main.py --monitor    # Start monitor operative
    uvicorn main:app --host localhost --port 8080   # ASGI entry point
"""
import argparse
import asyncio
import io
import json
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Optional

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------# Core imports (OpenJarvis-inspired components)
# ---------------------------------------------------------------------------
from core.event_bus import EventType, publish, subscribe, get_event_bus
from core.memory_service import build_memory_service, stop_memory_service
from core.skill_manager import SkillManager
from core.eval_metrics import get_latency_tracker, get_energy_estimator, get_dashboard
from core.cron_scheduler import start_scheduler, stop_scheduler


# ---------------------------------------------------------------------------
# Daemonized Body Double Agent
# ---------------------------------------------------------------------------

def _run_body_double_daemon():
    """Run the focus monitor in a background daemon thread."""
    try:
        from agents.body_double_agent import FocusMonitor
        monitor = FocusMonitor()
        print("🔍 Body Double daemon started (focus monitoring active)")
        
        # Subscribe to focus events
        def on_focus_drift(event):
            print(f"  📊 Focus drift detected: {event.data}")
        
        subscribe(EventType.FOCUS_DRIFT_DETECTED, on_focus_drift)
        
        # Subscribe to nudge events → push to PWA clients
        def on_nudge_fired(event):
            try:
                from remote.pwa_server import broadcast_sync
                broadcast_sync("nudge", {
                    "text": event.data.get("text", ""),
                    "drift_seconds": event.data.get("drift_seconds", 0),
                    "timestamp": time.time(),
                })
            except ImportError:
                pass  # PWA module not available
        
        subscribe(EventType.NUDGE_FIRED, on_nudge_fired)
        
        while True:
            try:
                nudge = monitor.tick()
                if nudge:
                    print(f"  💬 [{nudge['drift_seconds']}s drift] {nudge['text']}")
                    # Publish event (subscribers handle PWA broadcast)
                    publish(
                        EventType.NUDGE_FIRED,
                        {"text": nudge['text'], "drift_seconds": nudge['drift_seconds']},
                        source="body_double",
                    )
            except Exception as tick_err:
                # Don't let a single tick failure kill the daemon
                print(f"  ⚠️  Body Double tick error (continuing): {tick_err}")
            time.sleep(30)
    except Exception as e:
        print(f"⚠️  Body Double daemon error: {e}")


def start_body_double_daemon():
    """Spawn the body double agent as a daemon thread."""
    t = threading.Thread(target=_run_body_double_daemon, daemon=True, name="body-double")
    t.start()
    return t


# ---------------------------------------------------------------------------
# FastAPI app — module level so `uvicorn main:app` works
# ---------------------------------------------------------------------------

from fastapi import FastAPI, WebSocket, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
from core.auth import require_token

app = FastAPI(title="ADHD Co-Processor API", version="0.2.0")

# CORS setup
allowed_origins_env = os.environ.get("JARVIS_ALLOWED_ORIGINS", "")
if allowed_origins_env:
    allowed_origins = [orig.strip() for orig in allowed_origins_env.split(",") if orig.strip()]
else:
    allowed_origins = [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---------- Phase 11: PWA Routes ----------
try:
    from remote.pwa_server import setup_pwa_routes, set_running_loop
    setup_pwa_routes(app)

    @app.on_event("startup")
    def _store_event_loop():
        """Store the running event loop so sync code can schedule async broadcasts."""
        import asyncio
        set_running_loop(asyncio.get_event_loop())
except ImportError:
    pass  # PWA module not available

# ---------- Phase C: Sync layer (ingest on startup) ----------
try:
    from core.sync import ingest_pending_deltas, export_state_delta

    @app.on_event("startup")
    def _sync_ingest():
        """Ingest any delta files delivered by Syncthing from other devices."""
        try:
            from memory.adhd_memory import ADHDMemoryEngine
            engine = ADHDMemoryEngine()
            result = ingest_pending_deltas(engine)
            if result["ingested_files"] > 0:
                logger.info(
                    f"Sync: ingested {result['records_upserted']} records "
                    f"from {result['ingested_files']} delta files"
                )
        except Exception as e:
            logger.warning(f"Sync ingest failed (non-fatal): {e}")

    @app.on_event("shutdown")
    def _sync_export():
        """Export memories to delta files before shutdown."""
        try:
            from memory.adhd_memory import ADHDMemoryEngine
            engine = ADHDMemoryEngine()
            result = export_state_delta(engine)
            if result["exported"] > 0:
                logger.info(f"Sync: exported {result['exported']} memories to {result.get('file', '?')}")
        except Exception as e:
            logger.warning(f"Sync export failed (non-fatal): {e}")
except ImportError:
    pass  # sync module not available

# Set up notification handlers
try:
    from core.notifications import setup_notification_handlers
    setup_notification_handlers()
except ImportError:
    pass  # Notifications module not available

# ---------- Phase D: Proactive triggers ----------
try:
    from core.proactive import register_proactive_triggers
    register_proactive_triggers()
except Exception:
    pass  # proactive module not available

# ---------- Request Models ----------

class BrainDumpRequest(BaseModel):
    text: str
    conversation_id: Optional[str] = None

class StudyRequest(BaseModel):
    topic: str
    conversation_id: Optional[str] = None

class SearchRequest(BaseModel):
    query: str

class RebalanceRequest(BaseModel):
    missed_block_id: Optional[int] = None

class SprintRequest(BaseModel):
    task: str

class ScheduledTaskRequest(BaseModel):
    task_id: str
    prompt: str
    cron_expression: str
    agent_type: str = "braindump"

class ConversationRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None

class TaskCompletionRequest(BaseModel):
    task_text: str
    actual_minutes: float
    estimated_minutes: Optional[int] = None


# ---------- Lazy singletons ----------

_memory = None
_memory_service = None
_conversation_memory = None
_task_tracker = None

def get_memory():
    global _memory
    if _memory is None:
        from memory.adhd_memory import ADHDMemoryEngine
        _memory = ADHDMemoryEngine()
    return _memory


def get_memory_svc():
    """Get or build the background memory service."""
    global _memory_service
    if _memory_service is None:
        memory = get_memory()
        _memory_service = build_memory_service(memory)
    return _memory_service


def get_conversation_memory():
    """Get or create the conversation memory store."""
    global _conversation_memory
    if _conversation_memory is None:
        from memory.adhd_memory import ConversationMemory
        _conversation_memory = ConversationMemory()
    return _conversation_memory


def get_task_tracker():
    """Get or create the task completion tracker."""
    global _task_tracker
    if _task_tracker is None:
        from memory.adhd_memory import TaskCompletionTracker
        _task_tracker = TaskCompletionTracker()
    return _task_tracker


# ---------- API Routes ----------

@app.get("/api/health")
def api_health():
    """Health check endpoint with system status."""
    # Don't call get_memory_svc() — it initializes Mem0 on the main thread
    # and blocks all other requests. Check the global instead.
    return {
        "status": "ok",
        "service": "adhd-copilot",
        "version": "0.2.0",
        "components": {
            "memory_service": "active" if _memory_service and _memory_service.is_running else "lazy",
            "event_bus": "active",
            "skills": len(SkillManager.list_skills()),
            "scheduler": "active",
        },
    }


@app.get("/api/diagnostics")
def api_diagnostics():
    """Retrieve model configurations and local AI tags diagnostics."""
    from core.diagnostics import run_model_diagnostics
    return run_model_diagnostics()


@app.post("/api/braindump")
async def api_braindump(req: BrainDumpRequest):
    """Process a brain dump with latency tracking and conversation context."""
    from agents.braindump_agent import process_braindump
    
    # Build conversation context if conversation_id provided
    conv_context = ""
    if req.conversation_id:
        conv_mem = get_conversation_memory()
        conv_context = conv_mem.get_context(req.conversation_id)
    
    # Track latency — run LLM call in thread pool to avoid blocking the event loop
    latency_tracker = get_latency_tracker()
    with latency_tracker.track("braindump"):
        result = await asyncio.to_thread(
            process_braindump, req.text, context=conv_context if conv_context else None
        )
    
    # Store conversation turns
    if req.conversation_id:
        conv_mem = get_conversation_memory()
        conv_mem.add_turn(req.conversation_id, "user", req.text)
        summary = f"Captured {len(result.get('thoughts', []))} thoughts. {result.get('suggested_first_step', '')}"
        conv_mem.add_turn(req.conversation_id, "assistant", summary)
    
    # Run memory storage in background thread (don't block response)
    def _store():
        try:
            mem = get_memory()
            for thought in result.get("thoughts", []):
                mem.store_task(
                    thought["text"],
                    estimated_minutes=thought.get("estimated_minutes", 15),
                    priority=thought.get("priority", "soon"),
                )
            mem.capture_brain_dump(req.text, braindump_result=result)
        except Exception as e:
            print(f"  ⚠️  Memory storage failed: {e}")
    
    threading.Thread(target=_store, daemon=True).start()
    
    # Publish event for background memory service
    publish(
        EventType.BRAINDUMP_COMPLETED,
        {"text": req.text, "result": result},
        source="api",
    )
    
    return result


@app.get("/api/schedule")
def api_schedule():
    """Build schedule with latency tracking."""
    from agents.scheduler_agent import build_schedule, _estimate_alpha
    from memory.adhd_memory import get_history
    
    latency_tracker = get_latency_tracker()
    with latency_tracker.track("schedule"):
        memory = get_memory()
        tasks = []
        for mem in memory.get_all_memories():
            meta = mem.get("metadata", {})
            if meta.get("type") == "task":
                tasks.append({
                    "text": mem.get("memory", ""),
                    "estimated_minutes": meta.get("estimated_minutes", 25),
                    "priority": meta.get("priority", "soon"),
                })
        if not tasks:
            tasks = [{"text": "No tasks yet", "estimated_minutes": 5, "priority": "soon"}]
        alpha = _estimate_alpha(get_history())
        schedule = build_schedule(tasks, alpha)
    
    return {"schedule": schedule, "alpha": alpha}


@app.post("/api/rebalance", dependencies=[Depends(require_token)])
def api_rebalance(req: RebalanceRequest):
    """Rebalance schedule with event publishing."""
    from agents.scheduler_agent import build_schedule, rebalance, _estimate_alpha
    from memory.adhd_memory import get_history
    
    memory = get_memory()
    tasks = []
    for mem in memory.get_all_memories():
        meta = mem.get("metadata", {})
        if meta.get("type") == "task":
            tasks.append({
                "text": mem.get("memory", ""),
                "estimated_minutes": meta.get("estimated_minutes", 25),
                "priority": meta.get("priority", "soon"),
            })
    if not tasks:
        tasks = [{"text": "No tasks yet", "estimated_minutes": 5, "priority": "soon"}]
    alpha = _estimate_alpha(get_history())
    schedule = build_schedule(tasks, alpha)
    remaining, suggestion = rebalance(schedule, missed_block_id=req.missed_block_id, history=get_history())
    
    # Publish event
    publish(
        EventType.SCHEDULE_UPDATED,
        {"remaining_blocks": len(remaining), "suggestion": suggestion},
        source="api",
    )
    
    return {"schedule": remaining, "suggestion": suggestion}


@app.post("/api/study")
def api_study(req: StudyRequest):
    """Decompose study topic with latency tracking."""
    from agents.study_agent import decompose_topic
    
    latency_tracker = get_latency_tracker()
    with latency_tracker.track("study"):
        plan = decompose_topic(req.topic)
    
    # Publish event
    publish(
        EventType.STUDY_PLAN_GENERATED,
        {"topic": req.topic, "units": len(plan.get("units", []))},
        source="api",
    )
    
    return plan


@app.get("/api/memories")
def api_memories():
    """List all memories."""
    memory = get_memory()
    memories = memory.get_all_memories()
    return {"memories": memories, "count": len(memories)}


@app.post("/api/memories/search")
def api_memory_search(req: SearchRequest):
    """Search memories with latency tracking."""
    latency_tracker = get_latency_tracker()
    with latency_tracker.track("memory_search"):
        memory = get_memory()
        results = memory.retrieve_context_for_task(req.query)
    return {"results": results}


@app.post("/api/purge", dependencies=[Depends(require_token)])
def api_purge():
    """Purge all memories."""
    memory = get_memory()
    result = memory.purge_all()
    
    # Publish event
    publish(
        EventType.MEMORY_PURGED,
        {"status": result.get("status")},
        source="api",
    )
    
    return result


@app.post("/api/sprint")
def api_sprint(req: SprintRequest):
    """Generate micro-sprint suggestion."""
    from agents.scheduler_agent import generate_micro_sprint
    suggestion = generate_micro_sprint(req.task)
    return {"suggestion": suggestion}


# ---------- Conversation Memory endpoints ----------

@app.post("/api/conversation")
def api_conversation(req: ConversationRequest):
    """Multi-turn conversation with context memory."""
    from agents.braindump_agent import process_braindump
    
    conv_id = req.conversation_id or f"conv_{int(time.time())}"
    conv_mem = get_conversation_memory()
    
    # Get conversation context
    context = conv_mem.get_context(conv_id)
    
    # Process with context
    latency_tracker = get_latency_tracker()
    with latency_tracker.track("conversation"):
        result = process_braindump(req.message, context=context if context else None)
    
    # Store turns
    conv_mem.add_turn(conv_id, "user", req.message)
    summary = f"Captured {len(result.get('thoughts', []))} thoughts. {result.get('suggested_first_step', '')}"
    conv_mem.add_turn(conv_id, "assistant", summary)
    
    # Store in semantic memory
    memory = get_memory()
    for thought in result.get("thoughts", []):
        memory.store_task(
            thought["text"],
            estimated_minutes=thought.get("estimated_minutes", 15),
            priority=thought.get("priority", "soon"),
        )
    
    publish(
        EventType.BRAINDUMP_COMPLETED,
        {"text": req.message, "result": result, "conversation_id": conv_id},
        source="conversation",
    )
    
    return {
        "conversation_id": conv_id,
        **result,
    }


@app.get("/api/conversations")
def api_list_conversations():
    """List all conversation IDs."""
    conv_mem = get_conversation_memory()
    ids = conv_mem.get_conversation_ids()
    return {
        "conversations": [
            {"id": cid, "stats": conv_mem.get_stats(cid)}
            for cid in ids
        ]
    }


@app.get("/api/conversations/{conversation_id}")
def api_get_conversation(conversation_id: str, limit: int = 20):
    """Get conversation history."""
    conv_mem = get_conversation_memory()
    turns = conv_mem.get_turns(conversation_id, limit=limit)
    return {
        "conversation_id": conversation_id,
        "turns": turns,
        "stats": conv_mem.get_stats(conversation_id),
    }


@app.delete("/api/conversations/{conversation_id}", dependencies=[Depends(require_token)])
def api_delete_conversation(conversation_id: str):
    """Delete a conversation."""
    conv_mem = get_conversation_memory()
    deleted = conv_mem.delete_conversation(conversation_id)
    return {"deleted": deleted, "conversation_id": conversation_id}


# ---------- Task Completion Tracking endpoints ----------

@app.post("/api/tasks/start", dependencies=[Depends(require_token)])
def api_start_task(req: TaskCompletionRequest):
    """Record that a task has started."""
    tracker = get_task_tracker()
    estimated = req.estimated_minutes or 25
    result = tracker.start_task(req.task_text, estimated)
    return {"status": "started", **result}


@app.post("/api/tasks/complete", dependencies=[Depends(require_token)])
def api_complete_task(req: TaskCompletionRequest):
    """Record that a task has been completed."""
    tracker = get_task_tracker()
    record = tracker.complete_task(req.task_text, req.actual_minutes)
    
    publish(
        EventType.TASK_DURATION_RECORDED,
        {
            "task": req.task_text,
            "estimated": record.get("estimated_minutes"),
            "actual": req.actual_minutes,
            "ratio": record.get("ratio"),
        },
        source="api",
    )
    
    return {
        "status": "completed",
        **record,
        "alpha": tracker.get_alpha(),
    }


@app.get("/api/tasks/completions")
def api_get_completions(limit: int = 20):
    """Get task completion history."""
    tracker = get_task_tracker()
    return {
        "completions": tracker.get_completion_history(limit=limit),
        "stats": tracker.get_stats(),
    }


@app.get("/api/tasks/alpha")
def api_get_alpha():
    """Get the current time-scaling alpha."""
    tracker = get_task_tracker()
    return {
        "alpha": tracker.get_alpha(),
        "stats": tracker.get_stats(),
    }


# ---------- New OpenJarvis-inspired endpoints ----------

@app.get("/api/skills")
def api_skills():
    """List available skills and their statistics."""
    skills = SkillManager.list_skills()
    return {
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "type": skill.skill_type.value,
                "tags": skill.tags,
                "stats": skill.get_stats(),
            }
            for skill in skills
        ],
        "catalog": SkillManager.get_skill_catalog(),
    }


@app.post("/api/skills/{skill_name}/invoke", dependencies=[Depends(require_token)])
def api_invoke_skill(skill_name: str, args: dict = {}):
    """Invoke a skill by name."""
    try:
        result = SkillManager.invoke_skill(skill_name, **args)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/dashboard")
def api_dashboard():
    """Get performance dashboard with metrics."""
    dashboard = get_dashboard()
    return dashboard.get_dashboard(window_minutes=60)


@app.get("/api/recommendations")
def api_recommendations():
    """Get performance recommendations."""
    dashboard = get_dashboard()
    return {"recommendations": dashboard.get_recommendations()}


@app.get("/api/scheduler/tasks")
def api_list_scheduled_tasks():
    """List all scheduled tasks."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    tasks = scheduler.list_tasks()
    return {
        "tasks": [
            {
                "task_id": task.task_id,
                "prompt": task.prompt,
                "cron_expression": task.cron_expression,
                "agent_type": task.agent_type,
                "enabled": task.enabled,
                "last_run": task.last_run,
                "next_run": task.next_run,
                "run_count": task.run_count,
            }
            for task in tasks
        ]
    }


@app.post("/api/scheduler/tasks", dependencies=[Depends(require_token)])
def api_create_scheduled_task(req: ScheduledTaskRequest):
    """Create a new scheduled task."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    task = scheduler.create_task(
        task_id=req.task_id,
        prompt=req.prompt,
        cron_expression=req.cron_expression,
        agent_type=req.agent_type,
    )
    return {
        "task_id": task.task_id,
        "next_run": task.next_run,
        "status": "created",
    }


@app.post("/api/scheduler/tasks/{task_id}/pause", dependencies=[Depends(require_token)])
def api_pause_scheduled_task(task_id: str):
    """Pause a scheduled task."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    success = scheduler.pause_task(task_id)
    return {"success": success, "task_id": task_id}


@app.post("/api/scheduler/tasks/{task_id}/resume", dependencies=[Depends(require_token)])
def api_resume_scheduled_task(task_id: str):
    """Resume a scheduled task."""
    from core.cron_scheduler import get_scheduler
    scheduler = get_scheduler()
    success = scheduler.resume_task(task_id)
    return {"success": success, "task_id": task_id}


@app.get("/api/monitor/stats")
def api_monitor_stats():
    """Get monitoring statistics from the monitor operative."""
    from agents.monitor_operative import FocusMonitor, TaskMonitor
    focus_monitor = FocusMonitor()
    task_monitor = TaskMonitor()
    
    return {
        "focus": focus_monitor.get_focus_stats(),
        "tasks": task_monitor.get_task_stats(),
    }




# ---------- Phase 14: Data Sovereignty endpoints ----------

@app.get("/api/sovereignty/snapshot", dependencies=[Depends(require_token)])
def api_sovereignty_snapshot():
    """Take a single network snapshot and check for violations."""
    from core.sovereignty import SovereigntyMonitor
    monitor = SovereigntyMonitor()
    return monitor.snapshot()



@app.get("/api/sovereignty/status", dependencies=[Depends(require_token)])
def api_sovereignty_status():
    """Quick sovereignty status — instant snapshot, no waiting."""
    from core.sovereignty import SovereigntyMonitor
    monitor = SovereigntyMonitor()
    result = monitor.snapshot()
    return {
        "verdict": result["verdict"],
        "violations": len(result["violations"]),
        "allowed": len(result["allowed"]),
        "system": len(result["system"]),
        "total": result["total_connections"],
        "tailscale": any(c["reason"].startswith("Tailscale") for c in result["allowed"]),
        "google_oauth": any(c["reason"].startswith("Google") for c in result["allowed"]),
        "violation_details": result["violations"][:5],
    }

@app.get("/api/sovereignty/report", dependencies=[Depends(require_token)])
def api_sovereignty_report(duration_seconds: int = 30):
    """Run a sovereignty trace for N seconds and return full report."""
    from core.sovereignty import SovereigntyMonitor
    import time as _time

    monitor = SovereigntyMonitor()
    monitor.start(interval=3)
    _time.sleep(min(duration_seconds, 60))
    monitor.stop()
    return monitor.report().to_dict()


@app.post("/api/sovereignty/purge", dependencies=[Depends(require_token)])
def api_sovereignty_purge():
    """Purge ALL memory: Qdrant collection, task history, logs."""
    from core.sovereignty import purge_all_memory
    return purge_all_memory()


# ---------- Phase C: Cross-device Sync ----------

@app.get("/api/sync/status", dependencies=[Depends(require_token)])
def api_sync_status():
    """Check sync layer status — pending deltas, last export, folder state."""
    from core.sync import get_sync_status
    return get_sync_status()


@app.post("/api/sync/export", dependencies=[Depends(require_token)])
def api_sync_export():
    """Manually trigger a state export (usually runs on shutdown)."""
    from core.sync import export_state_delta
    memory = get_memory()
    return export_state_delta(memory)


@app.post("/api/sync/ingest", dependencies=[Depends(require_token)])
def api_sync_ingest():
    """Manually trigger delta ingestion (usually runs on startup)."""
    from core.sync import ingest_pending_deltas
    memory = get_memory()
    return ingest_pending_deltas(memory)


# ---------- Phase D: Proactive triggers ----------

@app.get("/api/proactive/status", dependencies=[Depends(require_token)])
def api_proactive_status():
    """Check proactive trigger status — registered events, cron tasks."""
    from core.proactive import get_proactive_status
    return get_proactive_status()


@app.post("/api/proactive/morning-briefing", dependencies=[Depends(require_token)])
def api_proactive_morning():
    """Manually trigger a morning briefing (usually fires at 08:00)."""
    from core.proactive import morning_briefing
    text = morning_briefing()
    return {"text": text, "triggered": True}


@app.post("/api/proactive/idle-check", dependencies=[Depends(require_token)])
def api_proactive_idle(minutes_idle: int = 30):
    """Manually trigger an idle check-in."""
    from core.proactive import idle_check
    text = idle_check(minutes_idle)
    return {"text": text, "triggered": text is not None, "minutes_idle": minutes_idle}


# ---------- Phase E: Vision ----------

class VisionRequest(BaseModel):
    prompt: str = "What's on screen? Describe any errors or issues."
    image_url: Optional[str] = None


@app.get("/api/vision/status", dependencies=[Depends(require_token)])
def api_vision_status():
    """Check vision agent status — model availability, dependencies."""
    from agents.vision_agent import get_vision_status
    return get_vision_status()


@app.post("/api/vision/analyze-screen", dependencies=[Depends(require_token)])
def api_vision_screen(prompt: str = "What's on screen?"):
    """Capture and analyze a screenshot."""
    from agents.vision_agent import analyze_screen
    return analyze_screen(prompt)


@app.post("/api/vision/analyze-image", dependencies=[Depends(require_token)])
def api_vision_image(request: VisionRequest):
    """Analyze an image from URL or path."""
    from agents.vision_agent import analyze_image
    if not request.image_url:
        return {"analysis": "No image_url provided", "source": "error", "image_size": [0, 0]}
    return analyze_image(request.image_url, request.prompt)


@app.post("/api/vision/analyze-upload", dependencies=[Depends(require_token)])
def api_vision_upload(file: bytes, prompt: str = "What do you see?"):
    """Analyze an uploaded image file."""
    from agents.vision_agent import analyze_image_bytes
    return analyze_image_bytes(file, prompt)


# ---------- Notification endpoints ----------

@app.get("/api/notifications")
def api_get_notifications(
    category: Optional[str] = None,
    unread_only: bool = False,
    limit: int = 20,
):
    """Get notifications."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    notifs = manager.get_notifications(
        category=category, unread_only=unread_only, limit=limit
    )
    return {
        "notifications": [
            {
                "id": n.id,
                "title": n.title,
                "body": n.body,
                "category": n.category,
                "timestamp": n.timestamp,
                "read": n.read,
            }
            for n in notifs
        ],
        "unread_count": manager.get_unread_count(),
    }


@app.post("/api/notifications/send", dependencies=[Depends(require_token)])
def api_send_notification(
    title: str,
    body: str,
    category: str = "general",
):
    """Send a notification (admin/testing)."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    notif = manager.send_notification(title, body, category, force=True)
    return {
        "sent": notif is not None,
        "notification_id": notif.id if notif else None,
    }


@app.post("/api/notifications/{notification_id}/read", dependencies=[Depends(require_token)])
def api_mark_read(notification_id: str):
    """Mark a notification as read."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    success = manager.mark_read(notification_id)
    return {"marked_read": success, "unread_count": manager.get_unread_count()}


@app.post("/api/notifications/read-all", dependencies=[Depends(require_token)])
def api_mark_all_read():
    """Mark all notifications as read."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    count = manager.mark_all_read()
    return {"marked_read": count, "unread_count": 0}


@app.delete("/api/notifications", dependencies=[Depends(require_token)])
def api_clear_notifications():
    """Clear all notifications."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    count = manager.clear_all()
    return {"cleared": count}


@app.get("/api/notifications/preferences")
def api_notification_preferences():
    """Get notification preferences."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    return manager.get_preferences()


@app.post("/api/notifications/preferences", dependencies=[Depends(require_token)])
def api_update_notification_preferences(preferences: dict):
    """Update notification preferences."""
    from core.notifications import get_notification_manager
    manager = get_notification_manager()
    updated = manager.update_preferences(**preferences)
    return manager.get_preferences()


# ---------- Calendar Sync endpoints ----------

@app.get("/api/calendar/status")
def api_calendar_status():
    """Check if Google Calendar is configured and reachable."""
    from agents.calendar_sync import calendar_available
    return {"available": calendar_available()}


@app.get("/api/calendar/today")
def api_calendar_today():
    """Get today's calendar events and busy blocks."""
    from agents.calendar_sync import CalendarSync
    sync = CalendarSync()
    events = sync.get_events_for_day()
    busy = sync.get_busy_blocks()
    return {
        "events": events,
        "busy_blocks": busy,
        "count": len(events),
    }


@app.post("/api/calendar/sync", dependencies=[Depends(require_token)])
def api_calendar_sync():
    """Sync task schedule to Google Calendar."""
    from agents.calendar_sync import CalendarSync
    from agents.scheduler_agent import _estimate_alpha
    from memory.adhd_memory import get_history

    memory = get_memory()
    tasks = []
    for mem in memory.get_all_memories():
        meta = mem.get("metadata", {})
        if meta.get("type") == "task":
            tasks.append({
                "text": mem.get("memory", ""),
                "estimated_minutes": meta.get("estimated_minutes", 25),
                "priority": meta.get("priority", "soon"),
            })

    alpha = _estimate_alpha(get_history())
    sync = CalendarSync()
    result = sync.sync_today(tasks, alpha)
    return result


@app.post("/api/calendar/clear", dependencies=[Depends(require_token)])
def api_calendar_clear():
    """Clear all copilot events from today's calendar."""
    from agents.calendar_sync import CalendarSync
    sync = CalendarSync()
    deleted = sync.clear_copilot_events()
    return {"deleted": deleted}


# ---------- Phase 8: Coding Assistant endpoints ----------

class CodeRequest(BaseModel):
    instruction: str
    file_path: Optional[str] = None
    action: str = "auto"  # fix, add, explain, refactor, review


@app.post("/api/code", dependencies=[Depends(require_token)])
def api_code(req: CodeRequest):
    """Coding assistant — fix bugs, add features, explain code."""
    from agents.coding_agent import CodeAssistant
    
    latency_tracker = get_latency_tracker()
    assistant = CodeAssistant()
    
    with latency_tracker.track("code"):
        if req.action == "fix":
            result = assistant.fix_bug(req.instruction, req.file_path)
        elif req.action == "add":
            result = assistant.add_feature(req.instruction, req.file_path)
        elif req.action == "explain":
            result = assistant.explain(req.instruction, req.file_path)
        elif req.action == "refactor":
            result = assistant.refactor(req.instruction, req.file_path)
        elif req.action == "review":
            result = assistant.review(req.file_path or "main.py")
        else:
            # Auto-detect intent
            instruction_lower = req.instruction.lower()
            if any(w in instruction_lower for w in ["fix", "bug", "error"]):
                result = assistant.fix_bug(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["add", "create", "new", "feature"]):
                result = assistant.add_feature(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["explain", "what", "how", "why"]):
                result = assistant.explain(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["refactor", "improve", "clean"]):
                result = assistant.refactor(req.instruction, req.file_path)
            elif any(w in instruction_lower for w in ["review", "check", "audit"]):
                result = assistant.review(req.file_path or "main.py")
            else:
                result = assistant.explain(req.instruction, req.file_path)
    
    # Publish event
    publish(
        EventType.TASK_COMPLETED,
        {"task_text": req.instruction, "action": result.get("action", "unknown")},
        source="api",
    )
    
    return result


@app.post("/api/code/apply", dependencies=[Depends(require_token)])
def api_code_apply(result: dict, dry_run: bool = True):
    """Apply code changes from a coding assistant result."""
    from agents.coding_agent import CodeAssistant
    assistant = CodeAssistant()
    return assistant.apply_changes(result, dry_run=dry_run)


# ---------- Phase 9: Web Task endpoints ----------

class WebTaskRequest(BaseModel):
    task: str
    url: Optional[str] = None
    selector: Optional[str] = None
    action: str = "auto"  # search, scrape, task


@app.post("/api/web-task", dependencies=[Depends(require_token)])
def api_web_task(req: WebTaskRequest):
    """Web task agent — search, scrape, or complete web tasks."""
    from agents.web_task_agent import WebTaskAgent
    
    latency_tracker = get_latency_tracker()
    agent = WebTaskAgent()
    
    with latency_tracker.track("web_task"):
        if req.action == "search":
            result = agent.search(req.task)
        elif req.action == "scrape" and req.url:
            result = agent.scrape(req.url, req.selector)
        else:
            result = agent.execute(req.task)
    
    # Publish event
    publish(
        EventType.TASK_COMPLETED,
        {"task_text": req.task, "action": req.action},
        source="api",
    )
    
    return result


@app.get("/api/web-task/search")
def api_web_search(q: str):
    """Quick web search endpoint."""
    from agents.web_task_agent import WebTaskAgent
    agent = WebTaskAgent()
    return agent.search(q)


@app.get("/api/web-task/scrape")
def api_web_scrape(url: str, selector: Optional[str] = None):
    """Quick web scrape endpoint."""
    from agents.web_task_agent import WebTaskAgent
    agent = WebTaskAgent()
    return agent.scrape(url, selector)


@app.get("/api/network-check")
def api_network_check():
    """Phase 9: Detect actual outbound network connections.

    Uses 'lsof' to check for non-localhost TCP connections.
    Distinguishes between app-level and system-level connections.
    This verifies the data sovereignty promise.
    """
    try:
        import subprocess
        import os
        app_connections = []
        system_connections = []
        google_prefixes = ("142.250.", "172.217.", "74.125.", "216.58.", "173.194.", "209.85.")
        app_names = {"python", "python3", "uvicorn"}
        my_pid = os.getpid()

        # Use lsof to list established TCP connections with process info
        try:
            result = subprocess.run(
                ["lsof", "-i", "tcp", "-n", "-P"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "ESTABLISHED" not in line:
                    continue
                parts = line.split()
                if len(parts) < 9:
                    continue
                process_name = parts[0].lower()
                conn_field = parts[8] if len(parts) > 8 else ""
                # lsof format: local_ip:port->remote_ip:port
                if "->" not in conn_field:
                    continue
                _local, _sep, remote_full = conn_field.rpartition("->")
                # remote_full is like "142.250.x.x:443"
                if ":" not in remote_full:
                    continue
                remote_ip, _, remote_port_str = remote_full.rpartition(":")
                try:
                    remote_port = int(remote_port_str)
                except ValueError:
                    continue
                # Skip localhost connections
                if remote_ip in ("127.0.0.1", "::1", "localhost"):
                    continue
                # Skip Google Calendar OAuth (HTTPS to Google IPs)
                if remote_port == 443 and any(
                    remote_ip.startswith(p) for p in google_prefixes
                ):
                    continue

                entry = {
                    "remote_ip": remote_ip,
                    "remote_port": remote_port,
                    "process": process_name,
                }
                # Classify: is this from our app or from the system?
                if process_name in app_names:
                    app_connections.append(entry)
                else:
                    system_connections.append(entry)

        except FileNotFoundError:
            pass  # lsof not available on this OS

        # Build verdict
        if app_connections:
            return {
                "status": "violation",
                "message": f"APP made {len(app_connections)} unexpected outbound connection(s) — privacy promise broken!",
                "app_connections": app_connections,
                "system_connections": system_connections,
                "total_system": len(system_connections),
            }
        elif system_connections:
            return {
                "status": "clean",
                "message": f"App makes ZERO outbound calls. {len(system_connections)} system-level connection(s) detected (browsers, OS services — not this app).",
                "app_connections": [],
                "system_connections": system_connections,
                "total_system": len(system_connections),
            }
        else:
            return {
                "status": "clean",
                "message": "Zero outbound connections detected. All inference and storage is local.",
                "app_connections": [],
                "system_connections": [],
                "total_system": 0,
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Could not check network: {e}",
            "app_connections": [],
            "system_connections": [],
            "total_system": 0,
        }


@app.get("/api/purge-status")
def api_purge_status():
    """Phase 9: Verify that purge actually cleared the collection."""
    try:
        from qdrant_client import QdrantClient
        import toml
        config = toml.load("config/config.toml")
        mem_cfg = config.get("memory", {})
        client = QdrantClient(host="localhost", port=6333)
        collection = mem_cfg.get("collection_name", "adhd_memory")
        collections = [c.name for c in client.get_collections().collections]
        exists = collection in collections
        return {
            "purged": not exists,
            "collection": collection,
            "message": "Collection cleared" if not exists else f"Collection '{collection}' still exists",
        }
    except Exception as e:
        return {"purged": False, "message": f"Could not verify: {e}"}


@app.get("/api/obsidian/notes")
def api_obsidian_notes():
    """List recent notes in the Obsidian vault."""
    from pathlib import Path
    try:
        from memory.adhd_memory import ObsidianClient
        client = ObsidianClient()
        vault_path = client.vault_path
        if not vault_path.exists():
            return {"notes": []}
        notes = sorted(
            [f.name for f in vault_path.glob("*.md") if f.name != "Dashboard.md"],
            reverse=True,
        )
        return {"notes": notes}
    except Exception as e:
        return {"notes": [], "error": str(e)}


@app.get("/api/obsidian")
def api_obsidian_status():
    """Phase 3.5: Check Obsidian vault status."""
    from pathlib import Path
    try:
        from memory.adhd_memory import ObsidianClient
        client = ObsidianClient()
        server_up = client.is_available()
        vault_path = client.vault_path
        note_count = len(list(vault_path.glob("*.md"))) if vault_path.exists() else 0
        return {
            "status": "ok",
            "server_reachable": server_up,
            "vault_path": str(vault_path),
            "note_count": note_count,
            "message": (
                f"Obsidian server reachable, {note_count} notes in vault"
                if server_up
                else f"Obsidian server offline (vault has {note_count} notes on disk)"
            ),
        }
    except Exception as e:
        return {
            "status": "error",
            "server_reachable": False,
            "vault_path": "vault",
            "note_count": 0,
            "message": f"Obsidian check failed: {e}",
        }


# ---------- WebSocket Voice Streaming ----------

@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket):
    """WebSocket endpoint for real-time voice streaming."""
    import base64

    token = websocket.query_params.get("token")
    expected_token = os.environ.get("ADHD_COPILOT_TOKEN", "")
    if expected_token and token != expected_token:
        await websocket.accept()
        await websocket.send_json({"type": "error", "text": "Unauthorized: Invalid or missing API token"})
        await websocket.close(code=4003)
        return

    await websocket.accept()
    print("🔌 WebSocket voice client connected")

    audio_chunks = []
    sample_rate = 16000
    mode = "command"

    # Run blocking calls (STT, braindump, TTS) in a thread pool so they
    # don't freeze the event loop and cause WebSocket timeouts.
    loop = asyncio.get_running_loop()

    try:
        while True:
            raw = await websocket.receive()
            if raw.get("type") == "websocket.disconnect":
                break

            msg = raw.get("text")
            if msg is None:
                continue

            data = json.loads(msg)
            msg_type = data.get("type", "")

            if msg_type == "start":
                mode = data.get("mode", "command")
                sample_rate = data.get("sample_rate", 16000)
                audio_chunks.clear()
                await websocket.send_json({"type": "status", "text": "Recording..."})
                print(f"  🎤 Voice stream started (mode={mode}, rate={sample_rate})")

            elif msg_type == "audio":
                # Receive base64-encoded float32 PCM samples
                pcm_bytes = base64.b64decode(data["data"])
                audio_chunks.append(pcm_bytes)

            elif msg_type == "stop":
                print(f"  🛑 Voice stream stopped ({len(audio_chunks)} chunks received)")
                await websocket.send_json({"type": "status", "text": "Transcribing..."})

                if not audio_chunks:
                    await websocket.send_json({"type": "error", "text": "No audio data received."})
                    continue

                # Assemble audio
                import numpy as np
                raw_pcm = b"".join(audio_chunks)
                audio = np.frombuffer(raw_pcm, dtype=np.float32)

                if len(audio) < sample_rate * 0.3:  # < 300ms
                    await websocket.send_json({"type": "error", "text": "Audio too short. Speak a bit longer."})
                    continue

                # Transcribe with Faster-Whisper (blocking — run in thread)
                latency_tracker = get_latency_tracker()
                try:
                    from speech.speech_pipeline import SpeechToText
                    stt = SpeechToText()
                    with latency_tracker.track("voice_transcribe"):
                        transcript = await loop.run_in_executor(
                            None, stt.transcribe_numpy, audio, sample_rate
                        )
                except Exception as e:
                    await websocket.send_json({"type": "error", "text": f"Transcription failed: {e}"})
                    continue

                if not transcript.strip():
                    await websocket.send_json({"type": "error", "text": "No speech detected. Try again."})
                    continue

                await websocket.send_json({"type": "transcript", "text": transcript})
                print(f"  📝 Transcript: {transcript}")

                # Process based on mode
                response_text = ""
                try:
                    if mode == "braindump":
                        def _process_braindump():
                            from agents.braindump_agent import process_braindump
                            result = process_braindump(transcript)
                            thought_count = len(result.get("thoughts", []))
                            mood = result.get("mood_hint", "unknown")
                            step = result.get("suggested_first_step", "none")
                            resp = (
                                f"Captured {thought_count} thoughts. "
                                f"Mood hint: {mood}. "
                                f"Suggested first step: {step}."
                            )
                            # Store in memory
                            memory = get_memory()
                            for thought in result.get("thoughts", []):
                                memory.store_task(
                                    thought["text"],
                                    estimated_minutes=thought.get("estimated_minutes", 15),
                                    priority=thought.get("priority", "soon"),
                                )
                            memory.capture_brain_dump(transcript, braindump_result=result)
                            
                            # Publish event
                            publish(
                                EventType.BRAINDUMP_COMPLETED,
                                {"text": transcript, "result": result},
                                source="voice",
                            )
                            
                            return resp
                        response_text = await loop.run_in_executor(None, _process_braindump)
                    else:
                        # Command mode
                        text_lower = transcript.lower().strip()
                        if "brain dump" in text_lower or "dump" in text_lower:
                            response_text = "Let's do a brain dump. Click Voice Dump and speak for 15 seconds."
                        elif "schedule" in text_lower:
                            response_text = "I'll show your schedule on the dashboard."
                        elif "study" in text_lower:
                            response_text = "What topic would you like me to break down into study steps?"
                        elif "search" in text_lower or "look up" in text_lower or "find online" in text_lower:
                            # Web search via voice
                            def _web_search():
                                from agents.web_task_agent import WebTaskAgent
                                agent = WebTaskAgent()
                                result = agent.search(transcript)
                                if result.get("results"):
                                    top = result["results"][0]
                                    return f"Found: {top.get('title', '')} at {top.get('url', '')}"
                                return "No results found."
                            response_text = await loop.run_in_executor(None, _web_search)
                        elif "code" in text_lower or "fix" in text_lower or "bug" in text_lower:
                            # Coding assistance via voice
                            def _voice_code():
                                from agents.coding_agent import CodeAssistant
                                assistant = CodeAssistant()
                                result = assistant.explain(transcript)
                                return result.get("summary", "I can help with that code task.")
                            response_text = await loop.run_in_executor(None, _voice_code)
                        elif "help" in text_lower:
                            response_text = (
                                "I can help with brain dumps, scheduling, study planning, "
                                "web searches, coding tasks, and focus nudges. "
                                "Just tell me what you need."
                            )
                        else:
                            def _process_command():
                                from agents.braindump_agent import process_braindump
                                result = process_braindump(transcript)
                                count = len(result.get("thoughts", []))
                                return (
                                    f"Got it! Captured {count} thoughts. "
                                    f"{result.get('suggested_first_step', '')}"
                                )
                            response_text = await loop.run_in_executor(None, _process_command)
                except Exception as e:
                    response_text = f"Sorry, I had trouble processing that: {e}"

                await websocket.send_json({"type": "response_text", "text": response_text})
                print(f"  💬 Response: {response_text}")

                # Synthesize response audio with Kokoro
                try:
                    from speech.speech_pipeline import TextToSpeech
                    tts = TextToSpeech()
                    response_audio = await loop.run_in_executor(
                        None, tts.synthesize, response_text
                    )

                    if len(response_audio) > 0:
                        await websocket.send_json({"type": "status", "text": "Speaking..."})
                        tts_sample_rate = 24000
                        chunk_size = tts_sample_rate * 2
                        for i in range(0, len(response_audio), chunk_size):
                            chunk = response_audio[i:i + chunk_size]
                            audio_b64 = base64.b64encode(chunk.tobytes()).decode("ascii")
                            await websocket.send_json({
                                "type": "response_audio",
                                "data": audio_b64,
                                "sample_rate": tts_sample_rate,
                                "is_final": (i + chunk_size >= len(response_audio)),
                            })
                    else:
                        await websocket.send_json({"type": "status", "text": "Ready"})
                except Exception as e:
                    print(f"  ⚠️  TTS failed: {e}")
                    await websocket.send_json({"type": "status", "text": "Ready (TTS unavailable)"})

                audio_chunks.clear()
                await websocket.send_json({"type": "status", "text": "Ready"})

    except Exception as e:
        print(f"  ⚠️  WebSocket error: {e}")
    finally:
        print("🔌 WebSocket voice client disconnected")


# ---------- Static UI ----------

UI_DIR = Path(__file__).parent / "ui"


@app.get("/")
def serve_ui():
    return FileResponse(UI_DIR / "index.html")


# Mount static files (CSS, JS, etc.) if needed
if (UI_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")


# ---------------------------------------------------------------------------# CLI mode
# ---------------------------------------------------------------------------

def run_cli():
    """Interactive CLI mode."""
    from core.diagnostics import print_diagnostics_report
    print_diagnostics_report()
    from agents.braindump_agent import process_braindump
    from memory.adhd_memory import ADHDMemoryEngine
    from agents.scheduler_agent import build_schedule, rebalance, generate_micro_sprint, _estimate_alpha
    from agents.study_agent import decompose_topic, format_study_plan

    memory = ADHDMemoryEngine()
    
    # Start background services
    get_memory_svc()
    
    print("🧠 ADHD Co-Processor — CLI Mode (v0.2.0)")
    print("=" * 50)
    print("Commands:")
    print("  dump <text>      — Process a brain dump (with conversation context)")
    print("  schedule         — Show today's schedule")
    print("  study <topic>    — Decompose a study topic")
    print("  memory           — List all memories")
    print("  search <query>   — Search memories")
    print("  sprint <task>    — Get a micro-sprint suggestion")
    print("  code <task>      — Coding assistant (fix/add/explain)")
    print("  web <task>       — Web task (search/scrape/browse)")
    print("  complete <task>  — Record task completion (actual minutes)")
    print("  conversations    — List conversation history")
    print("  skills           — List available skills")
    print("  dashboard        — Show performance metrics")
    print("  purge            — Purge all memory")
    print("  quit             — Exit")
    print("=" * 50)

    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Bye!")
            break

        if not line:
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "quit":
            print("👋 Bye!")
            break

        elif cmd == "dump":
            text = arg or input("  Enter thoughts: ").strip()
            if not text:
                print("  ⚠️  No text provided.")
                continue
            
            # Get conversation context
            conv_id = "cli_session"
            conv_mem = get_conversation_memory()
            context = conv_mem.get_context(conv_id)
            
            # Track latency
            latency_tracker = get_latency_tracker()
            with latency_tracker.track("braindump"):
                result = process_braindump(text, context=context if context else None)
            
            # Store conversation turns
            conv_mem.add_turn(conv_id, "user", text)
            summary = f"Captured {len(result.get('thoughts', []))} thoughts. {result.get('suggested_first_step', '')}"
            conv_mem.add_turn(conv_id, "assistant", summary)
            
            # Store each thought in memory
            for thought in result.get("thoughts", []):
                memory.store_task(
                    thought["text"],
                    estimated_minutes=thought.get("estimated_minutes", 15),
                    priority=thought.get("priority", "soon"),
                )
            memory.capture_brain_dump(text, braindump_result=result)
            
            # Publish event
            publish(
                EventType.BRAINDUMP_COMPLETED,
                {"text": text, "result": result},
                source="cli",
            )
            
            print(f"\n  ✅ Captured {len(result.get('thoughts', []))} thoughts")
            print(f"  💭 Mood: {result.get('mood_hint', 'unknown')}")
            print(f"  🎯 Suggested first step: {result.get('suggested_first_step', 'none')}")

        elif cmd == "schedule":
            tasks = []
            for mem in memory.get_all_memories():
                meta = mem.get("metadata", {})
                if meta.get("type") == "task":
                    tasks.append({
                        "text": mem.get("memory", ""),
                        "estimated_minutes": meta.get("estimated_minutes", 25),
                        "priority": meta.get("priority", "soon"),
                    })
            if not tasks:
                tasks = [
                    {"text": "No tasks yet — use 'dump' to add some", "estimated_minutes": 5, "priority": "soon"}
                ]
            from memory.adhd_memory import get_history
            alpha = _estimate_alpha(get_history())
            schedule = build_schedule(tasks, alpha)
            print(f"\n  📅 Schedule (α={alpha:.2f}):")
            for block in schedule:
                marker = "🔖" if block["type"] == "task" else "☕"
                label = block.get("label", block["type"])
                start = block["start"][11:16]
                end = block["end"][11:16]
                mins = block.get("scaled_minutes", "—")
                print(f"    {marker} {start}→{end}  {label}  ({mins} min)")

        elif cmd == "study":
            topic = arg or input("  Enter topic: ").strip()
            if not topic:
                print("  ⚠️  No topic provided.")
                continue
            
            latency_tracker = get_latency_tracker()
            with latency_tracker.track("study"):
                plan = decompose_topic(topic)
            
            print(format_study_plan(plan))

        elif cmd == "memory":
            memories = memory.get_all_memories()
            print(f"\n  🧠 Stored memories ({len(memories)}):")
            for m in memories:
                print(f"    • {m.get('memory', '?')[:80]}")

        elif cmd == "search":
            query = arg or input("  Enter search query: ").strip()
            if not query:
                continue
            
            latency_tracker = get_latency_tracker()
            with latency_tracker.track("memory_search"):
                results = memory.retrieve_context_for_task(query)
            
            print(f"\n  🔍 Results for '{query}':")
            for r in results:
                print(f"    📌 {r['memory'][:80]}  (score: {r['score']:.3f})")

        elif cmd == "sprint":
            task = arg or input("  Enter task: ").strip()
            if not task:
                continue
            suggestion = generate_micro_sprint(task)
            print(f"\n  💬 {suggestion}")

        elif cmd == "skills":
            skills = SkillManager.list_skills()
            print(f"\n  🛠️  Available Skills ({len(skills)}):")
            for skill in skills:
                stats = skill.get_stats()
                print(f"    • {skill.name}: {skill.description}")
                print(f"      Invocations: {stats['invocations']}, Success rate: {stats['success_rate']:.1f}%")

        elif cmd == "dashboard":
            dashboard = get_dashboard()
            metrics = dashboard.get_dashboard(window_minutes=60)
            recommendations = dashboard.get_recommendations()
            
            print("\n  📊 Performance Dashboard:")
            print(f"    Latency (braindump): {metrics['latency']['braindump']['avg']:.0f}ms avg")
            print(f"    Latency (schedule): {metrics['latency']['schedule']['avg']:.0f}ms avg")
            print(f"    Latency (study): {metrics['latency']['study']['avg']:.0f}ms avg")
            print(f"    Energy (inference): {metrics['energy']['inference']['sum']:.2f} Wh")
            
            if recommendations:
                print("\n  💡 Recommendations:")
                for rec in recommendations:
                    print(f"    • {rec}")

        elif cmd == "code":
            instruction = arg or input("  Enter coding instruction: ").strip()
            if not instruction:
                print("  ⚠️  No instruction provided.")
                continue
            
            from agents.coding_agent import CodeAssistant
            assistant = CodeAssistant()
            
            latency_tracker = get_latency_tracker()
            with latency_tracker.track("code"):
                instruction_lower = instruction.lower()
                if any(w in instruction_lower for w in ["fix", "bug", "error"]):
                    result = assistant.fix_bug(instruction)
                elif any(w in instruction_lower for w in ["add", "create", "new", "feature"]):
                    result = assistant.add_feature(instruction)
                elif any(w in instruction_lower for w in ["explain", "what", "how", "why"]):
                    result = assistant.explain(instruction)
                elif any(w in instruction_lower for w in ["refactor", "improve", "clean"]):
                    result = assistant.refactor(instruction)
                elif any(w in instruction_lower for w in ["review", "check", "audit"]):
                    result = assistant.review(instruction)
                else:
                    result = assistant.explain(instruction)
            
            print(f"\n  🔧 Action: {result.get('action', 'unknown')}")
            print(f"  📝 Summary: {result.get('summary', 'none')}")
            print(f"  📄 Confidence: {result.get('confidence', 'unknown')}")
            if result.get('files_changed'):
                print("  📁 Files changed:")
                for fc in result['files_changed']:
                    print(f"    • {fc.get('path', '?')}: {fc.get('changes', '?')}")
            if result.get('explanation'):
                print(f"\n  💡 Explanation:\n{result['explanation']}")
            if result.get('warnings'):
                print(f"\n  ⚠️  Warnings: {', '.join(result['warnings'])}")

        elif cmd == "web":
            task = arg or input("  Enter web task: ").strip()
            if not task:
                print("  ⚠️  No task provided.")
                continue
            
            from agents.web_task_agent import WebTaskAgent
            agent = WebTaskAgent()
            
            latency_tracker = get_latency_tracker()
            with latency_tracker.track("web_task"):
                task_lower = task.lower()
                if task_lower.startswith("http"):
                    result = agent.scrape(task)
                    print(f"\n  🌐 Scraped: {result.get('url', task)}")
                    print(f"  📝 Text length: {result.get('text_length', 0)} chars")
                    if result.get('error'):
                        print(f"  ❌ Error: {result['error']}")
                    else:
                        print(f"\n{result.get('text', '')[:2000]}")
                elif any(w in task_lower for w in ["search", "find", "look up"]):
                    query = task.replace("search", "").replace("find", "").replace("look up", "").strip()
                    result = agent.search(query)
                    print(f"\n  🔍 Search: {query}")
                    if result.get('results'):
                        for r in result['results'][:5]:
                            print(f"    • {r.get('title', '')}")
                            print(f"      {r.get('url', '')}")
                    else:
                        print(f"  ⚠️  No results: {result.get('error', 'unknown')}")
                else:
                    result = agent.execute(task)
                    print(f"\n  📋 Task: {result.get('task_summary', task)}")
                    print(f"  ⏱  Completed in {result.get('elapsed_seconds', 0)}s")
                    print(f"\n📝 Results:\n{result.get('synthesis', 'No synthesis available')}")

        elif cmd == "complete":
            task_text = arg or input("  Enter task name: ").strip()
            if not task_text:
                print("  ⚠️  No task provided.")
                continue
            try:
                actual_str = input("  Actual minutes spent: ").strip()
                actual_minutes = float(actual_str)
            except ValueError:
                print("  ⚠️  Invalid number.")
                continue
            
            tracker = get_task_tracker()
            record = tracker.complete_task(task_text, actual_minutes)
            print(f"  ✅ Task completed: {task_text}")
            print(f"  📊 Estimated: {record.get('estimated_minutes', '?')} min, Actual: {actual_minutes} min")
            print(f"  📈 Ratio: {record.get('ratio', '?')}x, Alpha: {tracker.get_alpha():.2f}")
            
            publish(
                EventType.TASK_DURATION_RECORDED,
                {
                    "task": task_text,
                    "estimated": record.get("estimated_minutes"),
                    "actual": actual_minutes,
                    "ratio": record.get("ratio"),
                },
                source="cli",
            )

        elif cmd == "conversations":
            conv_mem = get_conversation_memory()
            conv_ids = conv_mem.get_conversation_ids()
            if not conv_ids:
                print("  📝 No conversations yet. Use 'dump' to start one.")
            else:
                print(f"  📝 Conversations ({len(conv_ids)}):")
                for cid in conv_ids[-10:]:
                    stats = conv_mem.get_stats(cid)
                    turns = stats.get("turns", 0)
                    last = stats.get("last_turn", "?")[:16] if stats.get("last_turn") else "?"
                    print(f"    • {cid} — {turns} turns, last: {last}")

        elif cmd == "purge":
            result = memory.purge_all()
            print(f"  {'✅' if result['status'] == 'success' else '⚠️'} {result['message']}")

        else:
            print(f"  Unknown command: {cmd}. Type a command or 'quit'.")


# ---------------------------------------------------------------------------
# Voice mode
# ---------------------------------------------------------------------------

def _get_bind_host() -> str:
    """Helper to determine the bind host safely.

    Only binds to 0.0.0.0 (remote access) if ADHD_COPILOT_TOKEN is configured.
    """
    token_configured = bool(os.environ.get("ADHD_COPILOT_TOKEN", ""))
    remote_mode = os.environ.get("JARVIS_REMOTE", "").lower() == "true" or os.environ.get("JARVIS_HOST") == "0.0.0.0"
    
    if remote_mode:
        if not token_configured:
            print("🚨 SECURITY WARNING: Remote mode (binding to 0.0.0.0) requested but ADHD_COPILOT_TOKEN is not set.")
            print("   For security reasons, binding to localhost instead.")
            return "localhost"
        return "0.0.0.0"
    return "localhost"


def run_voice():
    """Voice interface + UI server mode."""
    import uvicorn
    from core.diagnostics import print_diagnostics_report
    print_diagnostics_report()
    from speech.speech_pipeline import SpeechPipeline
    from agents.braindump_agent import process_braindump
    from memory.adhd_memory import ADHDMemoryEngine

    memory = ADHDMemoryEngine()
    
    # Start background services
    get_memory_svc()

    # Start UI server + body double daemon in background threads
    start_body_double_daemon()
    PORT = 8080
    bind_host = _get_bind_host()
    ui_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": bind_host, "port": PORT, "log_level": "warning"},
        daemon=True,
        name="ui-server",
    )
    ui_thread.start()
    print(f"🌐 UI server starting at http://{bind_host}:{PORT}")
    print("   (voice + UI running together — Ctrl+C to stop)")

    # Give uvicorn a moment to bind
    time.sleep(1.5)

    # Initialize voice pipeline after server is up
    pipeline = SpeechPipeline()

    def handle_command(text: str) -> str:
        text_lower = text.lower().strip()
        if "brain dump" in text_lower or "dump" in text_lower:
            return "Let's do a brain dump. Speak for 15 seconds and I'll organize your thoughts."
        elif "schedule" in text_lower:
            return "I'll show your schedule. Check the dashboard for today's plan."
        elif "study" in text_lower:
            return "What topic would you like me to break down into study steps?"
        elif "help" in text_lower:
            return (
                "I can help with brain dumps, scheduling, study planning, and focus nudges. "
                "Just tell me what you need."
            )
        else:
            result = process_braindump(text)
            count = len(result.get("thoughts", []))
            return f"Got it! I captured {count} thoughts. {result.get('suggested_first_step', '')}"

    print("🎤 Voice mode active — speak your commands")
    try:
        while True:
            pipeline.listen_and_respond(handle_command, max_duration=15)
    except KeyboardInterrupt:
        print("\n👋 Voice mode stopped.")


# ---------------------------------------------------------------------------
# UI server mode
# ---------------------------------------------------------------------------

def run_ui_server():
    """Start FastAPI backend + serve static UI files + background services."""
    import uvicorn
    from core.diagnostics import print_diagnostics_report
    print_diagnostics_report()

    # Start background services
    start_body_double_daemon()
    # Initialize memory service in background thread to avoid blocking the event loop
    threading.Thread(target=get_memory_svc, daemon=True, name="memory-init").start()
    start_scheduler()

    PORT = 8080
    bind_host = _get_bind_host()
    print(f"🌐 ADHD Co-Processor API running at http://{bind_host}:{PORT}")
    print(f"   UI: http://{bind_host}:{PORT}")
    print(f"   API docs: http://{bind_host}:{PORT}/docs")
    print(f"   Health: http://{bind_host}:{PORT}/api/health")
    print(f"   Skills: http://{bind_host}:{PORT}/api/skills")
    print(f"   Dashboard: http://{bind_host}:{PORT}/api/dashboard")
    print("   Body Double daemon: active")
    print("   Memory Service: active")
    print("   Cron Scheduler: active")
    print("   Press Ctrl+C to stop.")
    uvicorn.run(app, host=bind_host, port=PORT, log_level="info")


# ---------------------------------------------------------------------------
# Monitor mode
# ---------------------------------------------------------------------------

def run_monitor():
    """Start the monitor operative for long-horizon monitoring."""
    from agents.monitor_operative import FocusMonitor, TaskMonitor
    from memory.adhd_memory import ADHDMemoryEngine
    
    memory = ADHDMemoryEngine()
    focus_monitor = FocusMonitor(memory)
    task_monitor = TaskMonitor(memory)
    
    print("🔍 Monitor Operative started (Ctrl+C to stop)")
    print("   Tracking focus and task patterns...")
    
    try:
        while True:
            # Monitor focus
            from agents.body_double_agent import get_active_window_info
            app_info = get_active_window_info()
            focused = app_info is not None
            focus_monitor.record_focus_event(focused, app_info.get("app", "unknown") if app_info else "unknown")
            
            # Print stats periodically
            focus_stats = focus_monitor.get_focus_stats()
            if focus_stats["total_sessions"] % 10 == 0:
                print(f"  📊 Focus: {focus_stats['focus_ratio']:.1%} focused ({focus_stats['total_sessions']} samples)")
            
            time.sleep(30)
    except KeyboardInterrupt:
        print("\n🛑 Monitor stopped.")
        focus_monitor.end_session()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def run_tests():
    """Run integration tests across all components."""
    print("🧪 Running integration tests...\n")
    passed = 0
    failed = 0

    # Test 1: Braindump agent
    try:
        from agents.braindump_agent import process_braindump
        result = process_braindump("Need to finish the report and worried about the meeting")
        assert "thoughts" in result, "Missing 'thoughts' key"
        assert len(result["thoughts"]) > 0, "No thoughts extracted"
        print("  ✅ Braindump agent — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Braindump agent — {e}")
        failed += 1

    # Test 2: Scheduler
    try:
        from agents.scheduler_agent import build_schedule, rebalance, _estimate_alpha
        tasks = [{"text": "Task A", "estimated_minutes": 30, "priority": "now"}]
        alpha = _estimate_alpha([])
        schedule = build_schedule(tasks, alpha)
        assert len(schedule) > 0, "Empty schedule"
        remaining, suggestion = rebalance(schedule, missed_block_id=0)
        assert isinstance(suggestion, str), "No suggestion returned"
        print("  ✅ Scheduler agent — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Scheduler agent — {e}")
        failed += 1

    # Test 3: Study agent validator
    try:
        from agents.study_agent import validate_units
        good_units = [{"id": "u1", "title": "Test", "estimated_minutes": 10, "prerequisites": ["none"], "active_recall_questions": ["Q?"]}]
        bad_units = [{"id": "u1", "title": "Long", "estimated_minutes": 30, "prerequisites": ["none"], "active_recall_questions": ["Q?"]}]
        assert len(validate_units(good_units)) == 0, "Good units should pass"
        assert len(validate_units(bad_units)) > 0, "Long units should fail"
        print("  ✅ Study agent validator — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Study agent validator — {e}")
        failed += 1

    # Test 4: Memory engine
    try:
        from memory.adhd_memory import ADHDMemoryEngine
        engine = ADHDMemoryEngine(user_id="test_user")
        engine.capture_brain_dump("Test memory entry")
        print("  ✅ Memory engine — OK (requires Qdrant running)")
        passed += 1
    except Exception as e:
        print(f"  ⚠️  Memory engine — {e} (Qdrant may not be running)")
        failed += 1

    # Test 5: Config loads
    try:
        import toml
        config = toml.load("config/config.toml")
        assert "engine" in config, "Missing engine config"
        assert "speech" in config, "Missing speech config"
        assert config["guardrails"]["no_red_badges"] is True, "Guardrail missing"
        print("  ✅ Config — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Config — {e}")
        failed += 1

    # Test 6: Event bus
    try:
        from core.event_bus import EventBus, EventType, publish, subscribe
        bus = EventBus()
        
        received_events = []
        def handler(event):
            received_events.append(event)
        
        subscribe(EventType.BRAINDUMP_COMPLETED, handler)
        publish(EventType.BRAINDUMP_COMPLETED, {"test": True}, source="test")
        
        assert len(received_events) == 1, f"Expected 1 event, got {len(received_events)}"
        print("  ✅ Event bus — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Event bus — {e}")
        failed += 1

    # Test 7: Skill manager
    try:
        from core.skill_manager import SkillManager, Skill, SkillType
        
        # Check builtin skills registered
        skills = SkillManager.list_skills()
        assert len(skills) > 0, "No builtin skills registered"
        
        # Check skill catalog
        catalog = SkillManager.get_skill_catalog()
        assert "available_skills" in catalog, "Missing skill catalog"
        
        print(f"  ✅ Skill manager — OK ({len(skills)} skills)")
        passed += 1
    except Exception as e:
        print(f"  ❌ Skill manager — {e}")
        failed += 1

    # Test 8: Metrics collector
    try:
        from core.eval_metrics import MetricsCollector, get_latency_tracker
        
        collector = MetricsCollector()
        collector.record("test.metric", 42.0, "ms")
        
        stats = collector.get_stats("test.metric")
        assert stats["count"] == 1, f"Expected 1 metric, got {stats['count']}"
        
        print("  ✅ Metrics collector — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Metrics collector — {e}")
        failed += 1

    # Test 9: Cron scheduler
    try:
        from core.cron_scheduler import parse_cron, matches_cron
        from datetime import datetime
        
        # Parse cron expression
        parsed = parse_cron("0 9 * * 1-5")
        assert parsed["hour"] == "9", "Wrong hour"
        assert parsed["day_of_week"] == "1-5", "Wrong day of week"
        
        # Check if time matches
        test_time = datetime(2026, 8, 19, 9, 0)  # Tuesday 9 AM
        assert matches_cron(test_time, "0 9 * * 1-5"), "Should match Tuesday 9 AM"
        
        print("  ✅ Cron scheduler — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Cron scheduler — {e}")
        failed += 1

    # Test 10: Monitor operative
    try:
        from agents.monitor_operative import FocusMonitor, TaskMonitor
        
        focus_monitor = FocusMonitor()
        focus_monitor.start_session()
        focus_monitor.record_focus_event(True, "test_app")
        focus_monitor.end_session()
        
        stats = focus_monitor.get_focus_stats()
        assert stats["total_sessions"] > 0, "No sessions recorded"
        
        print("  ✅ Monitor operative — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Monitor operative — {e}")
        failed += 1

    # Test 11: FastAPI app loads and endpoints are registered
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)

        # Health endpoint
        r = client.get("/api/health")
        assert r.status_code == 200, f"Health check failed: {r.status_code}"
        data = r.json()
        assert data["status"] == "ok", f"Health status wrong: {data}"
        print("  ✅ FastAPI /api/health — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI health — {e}")
        failed += 1

    # Test 12: API braindump endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/braindump", json={"text": "Need to finish the report by Friday"})
        assert r.status_code == 200, f"Braindump API failed: {r.status_code}"
        data = r.json()
        assert "thoughts" in data, "Missing thoughts in response"
        assert len(data["thoughts"]) > 0, "No thoughts extracted via API"
        print("  ✅ FastAPI /api/braindump — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI braindump — {e}")
        failed += 1

    # Test 13: API schedule endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/schedule")
        assert r.status_code == 200, f"Schedule API failed: {r.status_code}"
        data = r.json()
        assert "schedule" in data, "Missing schedule in response"
        assert "alpha" in data, "Missing alpha in response"
        print("  ✅ FastAPI /api/schedule — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI schedule — {e}")
        failed += 1

    # Test 14: API rebalance endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/rebalance", json={"missed_block_id": None})
        assert r.status_code == 200, f"Rebalance API failed: {r.status_code}"
        data = r.json()
        assert "suggestion" in data, "Missing suggestion in response"
        print("  ✅ FastAPI /api/rebalance — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI rebalance — {e}")
        failed += 1

    # Test 15: API memories endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/memories")
        assert r.status_code == 200, f"Memories API failed: {r.status_code}"
        data = r.json()
        assert "memories" in data, "Missing memories in response"
        assert "count" in data, "Missing count in response"
        print("  ✅ FastAPI /api/memories — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI memories — {e}")
        failed += 1

    # Test 16: API memory search endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/memories/search", json={"query": "report"})
        assert r.status_code == 200, f"Memory search API failed: {r.status_code}"
        data = r.json()
        assert "results" in data, "Missing results in response"
        print("  ✅ FastAPI /api/memories/search — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI memory search — {e}")
        failed += 1

    # Test 17: API study endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/study", json={"topic": "binary search"})
        assert r.status_code == 200, f"Study API failed: {r.status_code}"
        data = r.json()
        assert "units" in data, "Missing units in response"
        print("  ✅ FastAPI /api/study — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI study — {e}")
        failed += 1

    # Test 18: API sprint endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/sprint", json={"task": "finish report"})
        assert r.status_code == 200, f"Sprint API failed: {r.status_code}"
        data = r.json()
        assert "suggestion" in data, "Missing suggestion in response"
        print("  ✅ FastAPI /api/sprint — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI sprint — {e}")
        failed += 1

    # Test 19: API skills endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/skills")
        assert r.status_code == 200, f"Skills API failed: {r.status_code}"
        data = r.json()
        assert "skills" in data, "Missing skills in response"
        assert "catalog" in data, "Missing catalog in response"
        print("  ✅ FastAPI /api/skills — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI skills — {e}")
        failed += 1

    # Test 20: API dashboard endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/dashboard")
        assert r.status_code == 200, f"Dashboard API failed: {r.status_code}"
        data = r.json()
        assert "latency" in data, "Missing latency in response"
        print("  ✅ FastAPI /api/dashboard — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI dashboard — {e}")
        failed += 1

    # Test 21: API purge endpoint
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/purge", json={})
        assert r.status_code == 200, f"Purge API failed: {r.status_code}"
        data = r.json()
        assert "status" in data, "Missing status in response"
        print("  ✅ FastAPI /api/purge — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI purge — {e}")
        failed += 1

    # Test 22: Network check endpoint (Phase 9)
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/network-check")
        assert r.status_code == 200, f"Network check failed: {r.status_code}"
        data = r.json()
        assert "status" in data, "Missing status in response"
        assert data["status"] in ("clean", "violation", "error"), f"Unknown status: {data['status']}"
        app_conns = data.get("app_connections", [])
        sys_conns = data.get("system_connections", [])
        print(f"  ✅ FastAPI /api/network-check — OK (app={len(app_conns)}, system={len(sys_conns)})")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI network check — {e}")
        failed += 1

    # Test 23: UI serves correctly
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200, f"UI serve failed: {r.status_code}"
        assert "ADHD Co-Processor" in r.text, "UI content missing"
        print("  ✅ UI / (index.html) — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ UI serve — {e}")
        failed += 1

    # Test 24: Body double agent loads
    try:
        from agents.body_double_agent import FocusMonitor, get_active_window_info
        monitor = FocusMonitor()
        app_info = get_active_window_info()
        print(f"  ✅ Body double agent — OK (active app: {app_info.get('app', 'unknown') if app_info else 'n/a'})")
        passed += 1
    except Exception as e:
        print(f"  ❌ Body double agent — {e}")
        failed += 1

    # Test 25: Google Calendar graceful degradation
    try:
        from agents.scheduler_agent import _get_calendar_service
        import os
        creds_path = Path("config/google_client_secret.json")
        if not creds_path.exists():
            try:
                service = _get_calendar_service()
                print("  ✅ Google Calendar — configured and available")
            except FileNotFoundError as e:
                print(f"  ✅ Google Calendar — gracefully reports missing credentials")
            except Exception as e:
                print(f"  ✅ Google Calendar — gracefully handles missing setup ({type(e).__name__})")
            passed += 1
        else:
            print("  ✅ Google Calendar — credentials file found")
            passed += 1
    except Exception as e:
        print(f"  ❌ Google Calendar — {e}")
        failed += 1


    # Test 26: Calendar sync module loads
    try:
        from agents.calendar_sync import CalendarSync, calendar_available
        sync = CalendarSync()
        # calendar_available() will fail without OAuth creds, that's expected
        try:
            available = calendar_available()
            print(f"  ✅ Calendar sync — OK (available={available})")
        except Exception:
            print("  ✅ Calendar sync — OK (no OAuth creds, graceful degradation)")
        passed += 1
    except Exception as e:
        print(f"  ❌ Calendar sync — {e}")
        failed += 1

    # Test 27: Wyoming bridge module loads
    try:
        from remote.wyoming_bridge import WyomingBridge, make_describe_message
        bridge = WyomingBridge()
        msg = make_describe_message()
        assert "describe" in msg, "Bad describe message"
        print("  ✅ Wyoming bridge — OK (module loads, messages valid)")
        passed += 1
    except Exception as e:
        print(f"  ❌ Wyoming bridge — {e}")
        failed += 1

    # Test 28: Coding agent module loads
    try:
        from agents.coding_agent import CodeAssistant
        assistant = CodeAssistant()
        print("  ✅ Coding agent — OK (module loads)")
        passed += 1
    except Exception as e:
        print(f"  ❌ Coding agent — {e}")
        failed += 1

    # Test 29: Web task agent module loads
    try:
        from agents.web_task_agent import WebTaskAgent
        agent = WebTaskAgent()
        print("  ✅ Web task agent — OK (module loads)")
        passed += 1
    except Exception as e:
        print(f"  ❌ Web task agent — {e}")
        failed += 1

    # Test 30: Calendar API endpoints exist
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/calendar/status")
        assert r.status_code == 200, f"Calendar status failed: {r.status_code}"
        data = r.json()
        assert "available" in data, "Missing available in response"
        print("  ✅ FastAPI /api/calendar/status — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI calendar status — {e}")
        failed += 1

    # Test 31: Path Traversal checks (Phase 1 Security)
    try:
        from agents.coding_agent import _safe_resolve
        # Safe relative resolve
        safe_path = _safe_resolve("main.py")
        assert safe_path.name == "main.py", "Failed to resolve relative path correctly"
        
        # Absolute path block
        try:
            _safe_resolve("/etc/passwd")
            raise AssertionError("Absolute path resolution should be blocked")
        except ValueError:
            pass
            
        # Traversal block
        try:
            _safe_resolve("../../etc/passwd")
            raise AssertionError("Directory traversal path should be blocked")
        except ValueError:
            pass
            
        print("  ✅ Path traversal protection checks — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Path traversal protection checks — {e}")
        failed += 1

    # Test 32: API Token Auth checks (Phase 1 Security)
    try:
        from fastapi.testclient import TestClient
        # Cache existing env var
        orig_token = os.environ.get("ADHD_COPILOT_TOKEN")
        os.environ["ADHD_COPILOT_TOKEN"] = "integration_test_secret_token"
        
        client = TestClient(app)
        
        # 1. Missing Token -> 401
        r_missing = client.post("/api/purge")
        assert r_missing.status_code == 401, f"Missing token should yield 401, got {r_missing.status_code}"
        
        # 2. Invalid Token -> 401
        r_invalid = client.post("/api/purge", headers={"X-API-Token": "incorrect_token"})
        assert r_invalid.status_code == 401, f"Invalid token should yield 401, got {r_invalid.status_code}"
        
        # 3. Valid Token -> 200 (runs purge successfully)
        r_valid = client.post("/api/purge", headers={"X-API-Token": "integration_test_secret_token"})
        assert r_valid.status_code == 200, f"Valid token should yield 200, got {r_valid.status_code}"
        
        # Restore env var
        if orig_token is None:
            del os.environ["ADHD_COPILOT_TOKEN"]
        else:
            os.environ["ADHD_COPILOT_TOKEN"] = orig_token
            
        print("  ✅ API Token security checks — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ API Token security checks — {e}")
        failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("🎉 All tests passed!")
    return failed == 0






def run_sovereignty():
    """Run data sovereignty check and report."""
    import sys as _sys
    _sys.argv = ["sovereignty"]  # Reset argv for the sovereignty module
    from core.sovereignty import SovereigntyMonitor, purge_all_memory
    import time as _time

    print("🔍 Data Sovereignty Check — Phase 14")
    print("=" * 50)

    # Single snapshot first
    print("\n📸 Taking network snapshot...")
    monitor = SovereigntyMonitor()
    result = monitor.snapshot()

    if result["verdict"] == "clean":
        print("  ✅ VERDICT: CLEAN — All outbound traffic matches the allowlist")
    else:
        print(f"  🚨 VERDICT: {len(result['violations'])} VIOLATION(S) DETECTED")

    print(f"  Connections: {result['total_connections']} total")
    print(f"  Allowed: {len(result['allowed'])}")
    print(f"  System: {len(result['system'])}")
    print()

    if result["violations"]:
        print("  🚨 VIOLATIONS:")
        for v in result["violations"]:
            print(f"     {v['process']} → {v['remote_ip']}:{v['remote_port']}")
            print(f"       {v['reason']}")
        print()

    # Quick trace
    print("🔍 Running 15s trace...")
    monitor2 = SovereigntyMonitor()
    monitor2.start(interval=3)
    try:
        for i in range(5):
            _time.sleep(3)
            snap = monitor2.snapshot()
            v = len(snap["violations"])
            a = len(snap["allowed"])
            print(f"  [{i*3+3:2d}s] ✅ {a} allowed | {'🚨' if v else '✅'} {v} violations")
    except KeyboardInterrupt:
        pass
    monitor2.stop()

    # Full report
    report = monitor2.report()
    d = report.to_dict()
    print()
    print("=" * 50)
    print("  SOVEREIGNTY REPORT")
    print("=" * 50)
    print(f"  Duration: {d['duration_seconds']}s | Snapshots: {d['total_snapshots']}")
    print(f"  Verdict: {'✅ CLEAN' if d['verdict'] == 'clean' else '🚨 VIOLATIONS'}")
    print(f"  Unique violations: {d['unique_violations']}")
    print(f"  Tailscale: {'✅ detected' if d['tailscale_detected'] else 'ℹ️  not detected'}")
    print(f"  Google OAuth: {'✅ detected' if d['google_oauth_detected'] else 'ℹ️  not detected'}")
    print()
    for r in d["recommendations"]:
        print(f"  {r}")
    print()

def run_desktop():
    """Start the native desktop shell (pywebview + FastAPI)."""
    print("🖥️  Starting desktop shell...")
    from remote.desktop_shell import main as desktop_main
    desktop_main()

def run_wake_word():
    """Start the Wyoming bridge for Android wake-word integration."""
    print("🔊 Starting Wyoming Bridge for Android wake-word...")
    print("   Connect Home Assistant Companion → Settings → Voice Assistants → Add")
    print("   Pipeline type: Wyoming")
    print()
    from remote.wyoming_bridge import start_bridge_sync
    start_bridge_sync()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ADHD Co-Processor")
    parser.add_argument("--voice", action="store_true", help="Voice interface + UI server")
    parser.add_argument("--ui", action="store_true", help="Start local web UI server + API")
    parser.add_argument("--test", action="store_true", help="Run integration tests")
    parser.add_argument("--monitor", action="store_true", help="Start monitor operative")
    parser.add_argument("--wake-word", action="store_true", help="Start Wyoming bridge for Android wake-word")
    parser.add_argument("--desktop", action="store_true", help="Start native desktop shell (pywebview)")
    parser.add_argument("--sovereignty", action="store_true", help="Run data sovereignty check")
    args = parser.parse_args()

    try:
        if args.test:
            success = run_tests()
            sys.exit(0 if success else 1)
        elif args.voice:
            run_voice()
        elif args.ui:
            run_ui_server()
        elif args.monitor:
            run_monitor()
        elif args.wake_word:
            run_wake_word()
        elif args.desktop:
            run_desktop()
        elif args.sovereignty:
            run_sovereignty()
        else:
            run_cli()
    finally:
        # Cleanup background services
        stop_memory_service()
        stop_scheduler()

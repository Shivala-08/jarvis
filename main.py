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
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------
# Core imports (OpenJarvis-inspired components)
# ---------------------------------------------------------------------------
from core.event_bus import EventType, publish, subscribe
from core.dependencies import (
    get_conversation_memory,
    get_memory,
    get_memory_svc,
    get_task_tracker,
    stop_memory_services,
)
from core.skill_manager import SkillManager
from core.eval_metrics import get_latency_tracker, get_dashboard
from core.cron_scheduler import start_scheduler, stop_scheduler

import logging

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("adhd-copilot")


# ---------------------------------------------------------------------------
# FastAPI app — module level so `uvicorn main:app` works
# ---------------------------------------------------------------------------

from fastapi import FastAPI, WebSocket, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from core.auth import require_token

app = FastAPI(title="ADHD Co-Processor API", version="0.2.0")

# CORS setup — restrictive by default, explicit origins only
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
    allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH"],
    allow_headers=["X-API-Token", "Content-Type", "Authorization"],
    allow_credentials=True,
)


# ---------------------------------------------------------------------------
# Global exception handler — consistent error responses
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler for unhandled errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": "An unexpected error occurred."},
    )


# ---------------------------------------------------------------------------
# Register route modules (extracted from this file for debuggability)
# ---------------------------------------------------------------------------

from routes import register_routes
register_routes(app)


# ---------------------------------------------------------------------------
# Startup / Shutdown events
# ---------------------------------------------------------------------------

@app.on_event("startup")
def _on_startup():
    """Initialize background services on startup."""
    # PWA routes
    try:
        from remote.pwa_server import setup_pwa_routes, set_running_loop
        setup_pwa_routes(app)
        import asyncio
        set_running_loop(asyncio.get_event_loop())
    except ImportError:
        pass

    # Sync layer
    try:
        from core.sync import ingest_pending_deltas
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

    # Notification handlers
    try:
        from core.notifications import setup_notification_handlers
        setup_notification_handlers()
    except ImportError:
        pass

    # Proactive triggers
    try:
        from core.proactive import register_proactive_triggers
        register_proactive_triggers()
    except Exception:
        pass


@app.on_event("shutdown")
def _on_shutdown():
    """Cleanup on shutdown."""
    try:
        from core.sync import export_state_delta
        from memory.adhd_memory import ADHDMemoryEngine
        engine = ADHDMemoryEngine()
        result = export_state_delta(engine)
        if result["exported"] > 0:
            logger.info(f"Sync: exported {result['exported']} memories to {result.get('file', '?')}")
    except Exception as e:
        logger.warning(f"Sync export failed (non-fatal): {e}")

    stop_memory_services()
    stop_scheduler()


# ---------------------------------------------------------------------------
# WebSocket Voice Streaming
# ---------------------------------------------------------------------------

@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket):
    """WebSocket endpoint for real-time voice streaming."""
    import base64
    import json

    token = websocket.query_params.get("token")
    expected_token = os.environ.get("ADHD_COPILOT_TOKEN", "")
    if expected_token and token != expected_token:
        await websocket.accept()
        await websocket.send_json({"type": "error", "text": "Unauthorized: Invalid or missing API token"})
        await websocket.close(code=4003)
        return

    await websocket.accept()
    logger.info("WebSocket voice client connected")

    audio_chunks = []
    sample_rate = 16000
    mode = "command"
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
                logger.info(f"Voice stream started (mode={mode}, rate={sample_rate})")

            elif msg_type == "audio":
                pcm_bytes = base64.b64decode(data["data"])
                audio_chunks.append(pcm_bytes)

            elif msg_type == "stop":
                logger.info(f"Voice stream stopped ({len(audio_chunks)} chunks received)")
                await websocket.send_json({"type": "status", "text": "Transcribing..."})

                if not audio_chunks:
                    await websocket.send_json({"type": "error", "text": "No audio data received."})
                    continue

                import numpy as np
                raw_pcm = b"".join(audio_chunks)
                audio = np.frombuffer(raw_pcm, dtype=np.float32)

                if len(audio) < sample_rate * 0.3:
                    await websocket.send_json({"type": "error", "text": "Audio too short. Speak a bit longer."})
                    continue

                # Transcribe
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
                logger.info(f"Transcript: {transcript}")

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
                            resp = f"Captured {thought_count} thoughts. Mood hint: {mood}. Suggested first step: {step}."
                            memory = get_memory()
                            for thought in result.get("thoughts", []):
                                memory.store_task(
                                    thought["text"],
                                    estimated_minutes=thought.get("estimated_minutes", 15),
                                    priority=thought.get("priority", "soon"),
                                )
                            memory.capture_brain_dump(transcript, braindump_result=result)
                            publish(EventType.BRAINDUMP_COMPLETED, {"text": transcript, "result": result}, source="voice")
                            return resp
                        response_text = await loop.run_in_executor(None, _process_braindump)
                    else:
                        text_lower = transcript.lower().strip()
                        if "brain dump" in text_lower or "dump" in text_lower:
                            response_text = "Let's do a brain dump. Click Voice Dump and speak for 15 seconds."
                        elif "schedule" in text_lower:
                            response_text = "I'll show your schedule on the dashboard."
                        elif "study" in text_lower:
                            response_text = "What topic would you like me to break down into study steps?"
                        elif "search" in text_lower or "look up" in text_lower or "find online" in text_lower:
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
                                return f"Got it! Captured {count} thoughts. {result.get('suggested_first_step', '')}"
                            response_text = await loop.run_in_executor(None, _process_command)
                except Exception as e:
                    response_text = f"Sorry, I had trouble processing that: {e}"

                await websocket.send_json({"type": "response_text", "text": response_text})

                # TTS
                try:
                    from speech.speech_pipeline import TextToSpeech
                    tts = TextToSpeech()
                    response_audio = await loop.run_in_executor(None, tts.synthesize, response_text)
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
                    logger.warning(f"TTS failed: {e}")
                    await websocket.send_json({"type": "status", "text": "Ready (TTS unavailable)"})

                audio_chunks.clear()
                await websocket.send_json({"type": "status", "text": "Ready"})

    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        logger.info("WebSocket voice client disconnected")


# ---------------------------------------------------------------------------
# Body Double daemon
# ---------------------------------------------------------------------------

def _run_body_double_daemon():
    """Run the focus monitor in a background daemon thread."""
    try:
        from agents.body_double_agent import FocusMonitor
        monitor = FocusMonitor()
        logger.info("Body Double daemon started (focus monitoring active)")

        def on_focus_drift(event):
            logger.info(f"Focus drift detected: {event.data}")

        subscribe(EventType.FOCUS_DRIFT_DETECTED, on_focus_drift)

        def on_nudge_fired(event):
            try:
                from remote.pwa_server import broadcast_sync
                broadcast_sync("nudge", {
                    "text": event.data.get("text", ""),
                    "drift_seconds": event.data.get("drift_seconds", 0),
                    "timestamp": time.time(),
                })
            except ImportError:
                pass

        subscribe(EventType.NUDGE_FIRED, on_nudge_fired)

        while True:
            try:
                nudge = monitor.tick()
                if nudge:
                    logger.info(f"[{nudge['drift_seconds']}s drift] {nudge['text']}")
                    publish(
                        EventType.NUDGE_FIRED,
                        {"text": nudge["text"], "drift_seconds": nudge["drift_seconds"]},
                        source="body_double",
                    )
            except Exception as tick_err:
                logger.warning(f"Body Double tick error (continuing): {tick_err}")
            time.sleep(30)
    except Exception as e:
        logger.warning(f"Body Double daemon error: {e}")


def start_body_double_daemon():
    """Spawn the body double agent as a daemon thread."""
    t = threading.Thread(target=_run_body_double_daemon, daemon=True, name="body-double")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

UI_DIR = Path(__file__).parent / "ui"


@app.get("/")
def serve_ui():
    return FileResponse(UI_DIR / "index.html")


if (UI_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Helper: determine bind host safely
# ---------------------------------------------------------------------------

def _get_bind_host() -> str:
    """Only binds to 0.0.0.0 if ADHD_COPILOT_TOKEN is configured."""
    token_configured = bool(os.environ.get("ADHD_COPILOT_TOKEN", ""))
    remote_mode = os.environ.get("JARVIS_REMOTE", "").lower() == "true" or os.environ.get("JARVIS_HOST") == "0.0.0.0"
    if remote_mode:
        if not token_configured:
            logger.warning("Remote mode requested but ADHD_COPILOT_TOKEN not set. Binding to localhost.")
            return "localhost"
        return "0.0.0.0"
    return "localhost"


# ---------------------------------------------------------------------------
# CLI mode
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
    get_memory_svc()

    print("🧠 ADHD Co-Processor — CLI Mode (v0.2.0)")
    print("=" * 50)
    print("Commands:")
    print("  dump <text>      — Process a brain dump")
    print("  schedule         — Show today's schedule")
    print("  study <topic>    — Decompose a study topic")
    print("  memory           — List all memories")
    print("  search <query>   — Search memories")
    print("  sprint <task>    — Get a micro-sprint suggestion")
    print("  code <task>      — Coding assistant")
    print("  web <task>       — Web task")
    print("  complete <task>  — Record task completion")
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
            conv_id = "cli_session"
            conv_mem = get_conversation_memory()
            context = conv_mem.get_context(conv_id)
            latency_tracker = get_latency_tracker()
            with latency_tracker.track("braindump"):
                result = process_braindump(text, context=context if context else None)
            conv_mem.add_turn(conv_id, "user", text)
            summary = f"Captured {len(result.get('thoughts', []))} thoughts. {result.get('suggested_first_step', '')}"
            conv_mem.add_turn(conv_id, "assistant", summary)
            for thought in result.get("thoughts", []):
                memory.store_task(thought["text"], estimated_minutes=thought.get("estimated_minutes", 15), priority=thought.get("priority", "soon"))
            memory.capture_brain_dump(text, braindump_result=result)
            publish(EventType.BRAINDUMP_COMPLETED, {"text": text, "result": result}, source="cli")
            print(f"\n  ✅ Captured {len(result.get('thoughts', []))} thoughts")
            print(f"  💭 Mood: {result.get('mood_hint', 'unknown')}")
            print(f"  🎯 Suggested first step: {result.get('suggested_first_step', 'none')}")
        elif cmd == "schedule":
            from core.dependencies import get_task_list
            tasks = get_task_list()
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
        elif cmd == "web":
            task = arg or input("  Enter web task: ").strip()
            if not task:
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
                elif any(w in task_lower for w in ["search", "find", "look up"]):
                    query = task.replace("search", "").replace("find", "").replace("look up", "").strip()
                    result = agent.search(query)
                    print(f"\n  🔍 Search: {query}")
                    if result.get('results'):
                        for r in result['results'][:5]:
                            print(f"    • {r.get('title', '')}")
                            print(f"      {r.get('url', '')}")
                else:
                    result = agent.execute(task)
                    print(f"\n  📋 Task: {result.get('task_summary', task)}")
        elif cmd == "complete":
            task_text = arg or input("  Enter task name: ").strip()
            if not task_text:
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
            publish(EventType.TASK_DURATION_RECORDED, {"task": task_text, "estimated": record.get("estimated_minutes"), "actual": actual_minutes, "ratio": record.get("ratio")}, source="cli")
        elif cmd == "conversations":
            conv_mem = get_conversation_memory()
            conv_ids = conv_mem.get_conversation_ids()
            if not conv_ids:
                print("  📝 No conversations yet.")
            else:
                print(f"  📝 Conversations ({len(conv_ids)}):")
                for cid in conv_ids[-10:]:
                    stats = conv_mem.get_stats(cid)
                    turns = stats.get("turns", 0)
                    print(f"    • {cid} — {turns} turns")
        elif cmd == "purge":
            result = memory.purge_all()
            print(f"  {'✅' if result['status'] == 'success' else '⚠️'} {result['message']}")
        else:
            print(f"  Unknown command: {cmd}. Type a command or 'quit'.")


# ---------------------------------------------------------------------------
# Voice mode
# ---------------------------------------------------------------------------

def run_voice():
    """Voice interface + UI server mode."""
    import uvicorn
    from core.diagnostics import print_diagnostics_report
    print_diagnostics_report()
    from speech.speech_pipeline import SpeechPipeline
    from memory.adhd_memory import ADHDMemoryEngine

    memory = ADHDMemoryEngine()
    get_memory_svc()
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
    logger.info(f"UI server starting at http://{bind_host}:{PORT}")
    time.sleep(1.5)

    pipeline = SpeechPipeline()

    def handle_command(text: str) -> str:
        text_lower = text.lower().strip()
        if "brain dump" in text_lower or "dump" in text_lower:
            return "Let's do a brain dump. Speak for 15 seconds."
        elif "schedule" in text_lower:
            return "I'll show your schedule on the dashboard."
        elif "study" in text_lower:
            return "What topic would you like me to break down?"
        elif "help" in text_lower:
            return "I can help with brain dumps, scheduling, study planning, and focus nudges."
        else:
            from agents.braindump_agent import process_braindump
            result = process_braindump(text)
            count = len(result.get("thoughts", []))
            return f"Got it! Captured {count} thoughts. {result.get('suggested_first_step', '')}"

    logger.info("Voice mode active")
    try:
        while True:
            pipeline.listen_and_respond(handle_command, max_duration=15)
    except KeyboardInterrupt:
        logger.info("Voice mode stopped.")


# ---------------------------------------------------------------------------
# UI server mode
# ---------------------------------------------------------------------------

def run_ui_server():
    """Start FastAPI backend + serve static UI files + background services."""
    import uvicorn
    from core.diagnostics import print_diagnostics_report
    print_diagnostics_report()

    start_body_double_daemon()
    threading.Thread(target=get_memory_svc, daemon=True, name="memory-init").start()
    start_scheduler()

    PORT = 8080
    bind_host = _get_bind_host()
    logger.info(f"ADHD Co-Processor API running at http://{bind_host}:{PORT}")
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

    logger.info("Monitor Operative started")
    try:
        while True:
            from agents.body_double_agent import get_active_window_info
            app_info = get_active_window_info()
            focused = app_info is not None
            focus_monitor.record_focus_event(focused, app_info.get("app", "unknown") if app_info else "unknown")
            focus_stats = focus_monitor.get_focus_stats()
            if focus_stats["total_sessions"] % 10 == 0:
                logger.info(f"Focus: {focus_stats['focus_ratio']:.1%} ({focus_stats['total_sessions']} samples)")
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Monitor stopped.")
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
        assert "thoughts" in result and len(result["thoughts"]) > 0
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
        remaining, suggestion = rebalance(schedule, missed_block_id=0)
        assert len(schedule) > 0 and isinstance(suggestion, str)
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
        assert len(validate_units(good_units)) == 0 and len(validate_units(bad_units)) > 0
        print("  ✅ Study agent validator — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Study agent validator — {e}")
        failed += 1

    # Test 4: Event bus
    try:
        from core.event_bus import EventBus, EventType, publish, subscribe
        bus = EventBus()
        received = []
        def handler(event):
            received.append(event)
        subscribe(EventType.BRAINDUMP_COMPLETED, handler)
        publish(EventType.BRAINDUMP_COMPLETED, {"test": True}, source="test")
        assert len(received) == 1
        print("  ✅ Event bus — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Event bus — {e}")
        failed += 1

    # Test 5: Config loads
    try:
        import toml
        config = toml.load("config/config.toml")
        assert "engine" in config and "speech" in config and config["guardrails"]["no_red_badges"] is True
        print("  ✅ Config — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Config — {e}")
        failed += 1

    # Test 6: Skill manager
    try:
        from core.skill_manager import SkillManager
        skills = SkillManager.list_skills()
        assert len(skills) > 0
        print(f"  ✅ Skill manager — OK ({len(skills)} skills)")
        passed += 1
    except Exception as e:
        print(f"  ❌ Skill manager — {e}")
        failed += 1

    # Test 7: Cron scheduler
    try:
        from core.cron_scheduler import parse_cron, matches_cron
        from datetime import datetime
        parsed = parse_cron("0 9 * * 1-5")
        assert parsed["hour"] == "9"
        test_time = datetime(2026, 8, 19, 9, 0)
        assert matches_cron(test_time, "0 9 * * 1-5")
        print("  ✅ Cron scheduler — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Cron scheduler — {e}")
        failed += 1

    # Test 8: FastAPI health
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        print("  ✅ FastAPI /api/health — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI health — {e}")
        failed += 1

    # Test 9: API braindump
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.post("/api/braindump", json={"text": "Need to finish the report by Friday"})
        assert r.status_code == 200 and len(r.json()["thoughts"]) > 0
        print("  ✅ FastAPI /api/braindump — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI braindump — {e}")
        failed += 1

    # Test 10: API schedule
    try:
        from fastapi.testclient import TestClient
        client = TestClient(app)
        r = client.get("/api/schedule")
        assert r.status_code == 200 and "schedule" in r.json() and "alpha" in r.json()
        print("  ✅ FastAPI /api/schedule — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ FastAPI schedule — {e}")
        failed += 1

    # Test 11: Path traversal protection
    try:
        from agents.coding_agent import _safe_resolve
        safe_path = _safe_resolve("main.py")
        assert safe_path.name == "main.py"
        try:
            _safe_resolve("/etc/passwd")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        try:
            _safe_resolve("../../etc/passwd")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        print("  ✅ Path traversal protection — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ Path traversal protection — {e}")
        failed += 1

    # Test 12: API token auth
    try:
        from fastapi.testclient import TestClient
        orig_token = os.environ.get("ADHD_COPILOT_TOKEN")
        os.environ["ADHD_COPILOT_TOKEN"] = "integration_test_secret_token"
        client = TestClient(app)
        r_missing = client.post("/api/purge")
        assert r_missing.status_code == 401
        r_invalid = client.post("/api/purge", headers={"X-API-Token": "wrong"})
        assert r_invalid.status_code == 401
        r_valid = client.post("/api/purge", headers={"X-API-Token": "integration_test_secret_token"})
        assert r_valid.status_code == 200
        if orig_token is None:
            del os.environ["ADHD_COPILOT_TOKEN"]
        else:
            os.environ["ADHD_COPILOT_TOKEN"] = orig_token
        print("  ✅ API Token security — OK")
        passed += 1
    except Exception as e:
        print(f"  ❌ API Token security — {e}")
        failed += 1

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("🎉 All tests passed!")
    return failed == 0


# ---------------------------------------------------------------------------
# Desktop / Wake-word modes
# ---------------------------------------------------------------------------

def run_desktop():
    """Start the native desktop shell (pywebview + FastAPI)."""
    logger.info("Starting desktop shell...")
    from remote.desktop_shell import main as desktop_main
    desktop_main()


def run_wake_word():
    """Start the Wyoming bridge for Android wake-word integration."""
    logger.info("Starting Wyoming Bridge for Android wake-word...")
    from remote.wyoming_bridge import start_bridge_sync
    start_bridge_sync()


# ---------------------------------------------------------------------------
# Sovereignty mode
# ---------------------------------------------------------------------------

def run_sovereignty():
    """Run data sovereignty check and report."""
    from core.sovereignty import SovereigntyMonitor
    import time as _time

    print("🔍 Data Sovereignty Check")
    print("=" * 50)

    monitor = SovereigntyMonitor()
    result = monitor.snapshot()

    if result["verdict"] == "clean":
        print("  ✅ VERDICT: CLEAN")
    else:
        print(f"  🚨 VERDICT: {len(result['violations'])} VIOLATION(S)")

    print(f"  Connections: {result['total_connections']} total")
    print(f"  Allowed: {len(result['allowed'])}, System: {len(result['system'])}")

    if result["violations"]:
        print("\n  🚨 VIOLATIONS:")
        for v in result["violations"]:
            print(f"     {v['process']} → {v['remote_ip']}:{v['remote_port']}")
            print(f"       {v['reason']}")

    print("\n🔍 Running 15s trace...")
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

    report = monitor2.report()
    d = report.to_dict()
    print(f"\n  Verdict: {'✅ CLEAN' if d['verdict'] == 'clean' else '🚨 VIOLATIONS'}")
    for r in d["recommendations"]:
        print(f"  {r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ADHD Co-Processor")
    parser.add_argument("--voice", action="store_true", help="Voice interface + UI server")
    parser.add_argument("--ui", action="store_true", help="Start local web UI server + API")
    parser.add_argument("--test", action="store_true", help="Run integration tests")
    parser.add_argument("--monitor", action="store_true", help="Start monitor operative")
    parser.add_argument("--wake-word", action="store_true", help="Start Wyoming bridge")
    parser.add_argument("--desktop", action="store_true", help="Start native desktop shell")
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
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

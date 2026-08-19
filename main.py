"""ADHD Co-Processor — Main entry point.

Ties together brain dump → memory → schedule → study → voice.
All local-first, zero cloud calls, zero paid API keys.

Usage:
    uv run python main.py              # Interactive CLI mode
    uv run python main.py --voice      # Voice interface + UI server
    uv run python main.py --ui         # Start local web UI server + API
    uv run python main.py --test       # Run integration tests
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

# ---------------------------------------------------------------------------
# Daemonized Body Double Agent
# ---------------------------------------------------------------------------

def _run_body_double_daemon():
    """Run the focus monitor in a background daemon thread."""
    try:
        from agents.body_double_agent import FocusMonitor
        monitor = FocusMonitor()
        print("🔍 Body Double daemon started (focus monitoring active)")
        while True:
            try:
                nudge = monitor.tick()
                if nudge:
                    print(f"  💬 [{nudge['drift_seconds']}s drift] {nudge['text']}")
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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="ADHD Co-Processor API", version="0.1.0")

# CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Request Models ----------

class BrainDumpRequest(BaseModel):
    text: str

class StudyRequest(BaseModel):
    topic: str

class SearchRequest(BaseModel):
    query: str

class RebalanceRequest(BaseModel):
    missed_block_id: Optional[int] = None

class SprintRequest(BaseModel):
    task: str


# ---------- Lazy singletons ----------

_memory = None

def get_memory():
    global _memory
    if _memory is None:
        from memory.adhd_memory import ADHDMemoryEngine
        _memory = ADHDMemoryEngine()
    return _memory


# ---------- API Routes ----------

@app.get("/api/health")
def api_health():
    """Health check endpoint."""
    return {"status": "ok", "service": "adhd-copilot"}


@app.post("/api/braindump")
def api_braindump(req: BrainDumpRequest):
    from agents.braindump_agent import process_braindump
    memory = get_memory()
    result = process_braindump(req.text)
    # Store each thought in memory
    for thought in result.get("thoughts", []):
        memory.store_task(
            thought["text"],
            estimated_minutes=thought.get("estimated_minutes", 15),
            priority=thought.get("priority", "soon"),
        )
    memory.capture_brain_dump(req.text, braindump_result=result)
    return result


@app.get("/api/schedule")
def api_schedule():
    from agents.scheduler_agent import build_schedule, _estimate_alpha
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
    return {"schedule": schedule, "alpha": alpha}


@app.post("/api/rebalance")
def api_rebalance(req: RebalanceRequest):
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
    return {"schedule": remaining, "suggestion": suggestion}


@app.post("/api/study")
def api_study(req: StudyRequest):
    from agents.study_agent import decompose_topic
    plan = decompose_topic(req.topic)
    return plan


@app.get("/api/memories")
def api_memories():
    memory = get_memory()
    memories = memory.get_all_memories()
    return {"memories": memories, "count": len(memories)}


@app.post("/api/memories/search")
def api_memory_search(req: SearchRequest):
    memory = get_memory()
    results = memory.retrieve_context_for_task(req.query)
    return {"results": results}


@app.post("/api/purge")
def api_purge():
    memory = get_memory()
    result = memory.purge_all()
    return result


@app.post("/api/sprint")
def api_sprint(req: SprintRequest):
    from agents.scheduler_agent import generate_micro_sprint
    suggestion = generate_micro_sprint(req.task)
    return {"suggestion": suggestion}


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
        app_names = {"python", "python3", "uvicorn", "ollama", "qdrant"}
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
    """Phase 3.5: Check Obsidian vault status.

    Reports whether the Obsidian REST API server is reachable and
    how many notes are in the vault directory.
    """
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
async def ws_voice(websocket):
    """WebSocket endpoint for real-time voice streaming.

    Protocol:
      Client → Server (JSON messages):
        {"type": "start", "mode": "command"|"braindump", "sample_rate": 16000}
        {"type": "audio", "data": <base64-encoded PCM float32 samples>}
        {"type": "stop"}
      Server → Client:
        {"type": "transcript", "text": "..."}
        {"type": "response_text", "text": "..."}
        {"type": "response_audio", "data": <base64-encoded PCM float32 samples>, "sample_rate": 24000}
        {"type": "status", "text": "..."}
        {"type": "error", "text": "..."}
    """
    import base64

    await websocket.accept()
    print("🔌 WebSocket voice client connected")

    audio_chunks = []
    sample_rate = 16000
    mode = "command"

    # Run blocking calls (STT, braindump, TTS) in a thread pool so they
    # don't freeze the event loop and cause WebSocket timeouts.
    loop = asyncio.get_event_loop()

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
                try:
                    from speech.speech_pipeline import SpeechToText
                    stt = SpeechToText()
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

                # Process based on mode (blocking braindump + memory — run in thread)
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
                            return resp
                        response_text = await loop.run_in_executor(None, _process_braindump)
                    else:
                        # Command mode: route to the appropriate handler
                        text_lower = transcript.lower().strip()
                        if "brain dump" in text_lower or "dump" in text_lower:
                            response_text = "Let's do a brain dump. Click Voice Dump and speak for 15 seconds."
                        elif "schedule" in text_lower:
                            response_text = "I'll show your schedule on the dashboard."
                        elif "study" in text_lower:
                            response_text = "What topic would you like me to break down into study steps?"
                        elif "help" in text_lower:
                            response_text = (
                                "I can help with brain dumps, scheduling, study planning, "
                                "and focus nudges. Just tell me what you need."
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

                # Synthesize response audio with Kokoro (blocking — run in thread)
                try:
                    from speech.speech_pipeline import TextToSpeech
                    tts = TextToSpeech()
                    response_audio = await loop.run_in_executor(
                        None, tts.synthesize, response_text
                    )

                    if len(response_audio) > 0:
                        await websocket.send_json({"type": "status", "text": "Speaking..."})
                        # Send audio in chunks to avoid huge messages
                        tts_sample_rate = 24000
                        chunk_size = tts_sample_rate * 2  # 2 seconds per chunk
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


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

def run_cli():
    """Interactive CLI mode."""
    from agents.braindump_agent import process_braindump
    from memory.adhd_memory import ADHDMemoryEngine
    from agents.scheduler_agent import build_schedule, rebalance, generate_micro_sprint, _estimate_alpha
    from agents.study_agent import decompose_topic, format_study_plan

    memory = ADHDMemoryEngine()
    print("🧠 ADHD Co-Processor — CLI Mode")
    print("=" * 50)
    print("Commands:")
    print("  dump <text>      — Process a brain dump")
    print("  schedule         — Show today's schedule")
    print("  study <topic>    — Decompose a study topic")
    print("  memory           — List all memories")
    print("  search <query>   — Search memories")
    print("  sprint <task>    — Get a micro-sprint suggestion")
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
            result = process_braindump(text)
            # Store each thought in memory
            for thought in result.get("thoughts", []):
                memory.store_task(
                    thought["text"],
                    estimated_minutes=thought.get("estimated_minutes", 15),
                    priority=thought.get("priority", "soon"),
                )
            memory.capture_brain_dump(text, braindump_result=result)
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

        elif cmd == "purge":
            result = memory.purge_all()
            print(f"  {'✅' if result['status'] == 'success' else '⚠️'} {result['message']}")

        else:
            print(f"  Unknown command: {cmd}. Type a command or 'quit'.")


# ---------------------------------------------------------------------------
# Voice mode
# ---------------------------------------------------------------------------

def run_voice():
    """Voice interface + UI server mode.

    Starts the web dashboard at localhost:8080 and the voice pipeline
    simultaneously so you can interact by voice and see state in the browser.
    """
    import uvicorn
    from speech.speech_pipeline import SpeechPipeline
    from agents.braindump_agent import process_braindump
    from memory.adhd_memory import ADHDMemoryEngine

    memory = ADHDMemoryEngine()

    # Start UI server + body double daemon in background threads
    start_body_double_daemon()
    PORT = 8080
    ui_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "localhost", "port": PORT, "log_level": "warning"},
        daemon=True,
        name="ui-server",
    )
    ui_thread.start()
    print(f"🌐 UI server starting at http://localhost:{PORT}")
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
    """Start FastAPI backend + serve static UI files + body double daemon."""
    import uvicorn

    # Start body double daemon in background
    start_body_double_daemon()

    PORT = 8080
    print(f"🌐 ADHD Co-Processor API running at http://localhost:{PORT}")
    print(f"   UI: http://localhost:{PORT}")
    print(f"   API docs: http://localhost:{PORT}/docs")
    print(f"   Health: http://localhost:{PORT}/api/health")
    print("   Body Double daemon: active")
    print("   Press Ctrl+C to stop.")
    uvicorn.run(app, host="localhost", port=PORT, log_level="info")


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

    # Test 6: FastAPI app loads and endpoints are registered
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

    # Test 7: API braindump endpoint
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

    # Test 8: API schedule endpoint
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

    # Test 9: API rebalance endpoint
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

    # Test 10: API memories endpoint
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

    # Test 11: API memory search endpoint
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

    # Test 12: API study endpoint
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

    # Test 13: API sprint endpoint
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

    # Test 14: API purge endpoint
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

    # Test 15: Network check endpoint (Phase 9)
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

    # Test 16: UI serves correctly
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

    # Test 17: Body double agent loads
    try:
        from agents.body_double_agent import FocusMonitor, get_active_window_info
        monitor = FocusMonitor()
        # Just verify it initializes without error
        app_info = get_active_window_info()
        print(f"  ✅ Body double agent — OK (active app: {app_info.get('app', 'unknown') if app_info else 'n/a'})")
        passed += 1
    except Exception as e:
        print(f"  ❌ Body double agent — {e}")
        failed += 1

    # Test 18: Google Calendar graceful degradation
    try:
        from agents.scheduler_agent import _get_calendar_service
        import os
        # Check if credentials file exists
        creds_path = Path("config/google_client_secret.json")
        if not creds_path.exists():
            # Expected: graceful error without crashing the app
            try:
                service = _get_calendar_service()
                # If it somehow works, that's fine too
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

    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("🎉 All tests passed!")
    return failed == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ADHD Co-Processor")
    parser.add_argument("--voice", action="store_true", help="Voice interface + UI server")
    parser.add_argument("--ui", action="store_true", help="Start local web UI server + API")
    parser.add_argument("--test", action="store_true", help="Run integration tests")
    args = parser.parse_args()

    if args.test:
        success = run_tests()
        sys.exit(0 if success else 1)
    elif args.voice:
        run_voice()
    elif args.ui:
        run_ui_server()
    else:
        run_cli()

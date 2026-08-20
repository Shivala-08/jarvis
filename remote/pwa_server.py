"""PWA Server — WebSocket endpoint for the custom home-screen app.

Features:
- WebSocket connection for real-time voice streaming
- Push-to-talk audio capture from browser MediaRecorder API
- REST endpoint for audio blob submission
- Web Push subscription management
- Serves the PWA static files (manifest, icons, app.js)

Usage:
    from remote.pwa_server import setup_pwa_routes
    setup_pwa_routes(app)

    # Or run standalone:
    python remote/pwa_server.py
"""
import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import logging

logger = logging.getLogger(__name__)

# PWA directory
PWA_DIR = Path(__file__).parent.parent / "ui" / "pwa"


# ---------------------------------------------------------------------------
# Connection manager for WebSocket clients
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = None

    def _get_lock(self):
        if self._lock is None:
            import threading
            self._lock = threading.Lock()
        return self._lock

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and track a new WebSocket connection."""
        await websocket.accept()
        with self._get_lock():
            self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected ({len(self.active_connections)} active)")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        with self._get_lock():
            self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected ({len(self.active_connections)} active)")

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Send a message to all connected clients."""
        with self._get_lock():
            connections = list(self.active_connections)

        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

    async def send_to(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        """Send a message to a specific client."""
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)


# Global connection manager
_manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PushSubscription(BaseModel):
    endpoint: str
    keys: Dict[str, str]


class WebTaskRequest(BaseModel):
    task: str
    mode: str = "auto"  # search, scrape, task


# ---------------------------------------------------------------------------
# Setup function for integrating with the main FastAPI app
# ---------------------------------------------------------------------------

def setup_pwa_routes(app: FastAPI) -> None:
    """Add PWA routes to an existing FastAPI app."""

    # ---------- PWA static files ----------

    @app.get("/pwa/manifest.json")
    def pwa_manifest():
        """Serve the PWA manifest."""
        manifest_path = PWA_DIR / "manifest.json"
        if manifest_path.exists():
            return FileResponse(str(manifest_path), media_type="application/json")
        return JSONResponse({"error": "Manifest not found"}, status_code=404)

    @app.get("/pwa/icons/{icon_name}")
    def pwa_icon(icon_name: str):
        """Serve PWA icons."""
        icon_path = PWA_DIR / "icons" / icon_name
        if icon_path.exists():
            return FileResponse(str(icon_path))
        return JSONResponse({"error": "Icon not found"}, status_code=404)

    @app.get("/app")
    def serve_pwa():
        """Serve the PWA main page."""
        index_path = PWA_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse({"error": "PWA not found"}, status_code=404)

    # Mount PWA static files (app.js, sw.js, etc.)
    if PWA_DIR.exists():
        app.mount("/pwa/static", StaticFiles(directory=str(PWA_DIR)), name="pwa-static")

    # ---------- WebSocket for real-time voice ----------

    @app.websocket("/ws/pwa")
    async def ws_pwa(websocket: WebSocket):
        token = websocket.query_params.get("token")
        import os
        expected_token = os.environ.get("ADHD_COPILOT_TOKEN", "")
        if expected_token and token != expected_token:
            await websocket.accept()
            await websocket.send_json({"type": "error", "text": "Unauthorized: Invalid or missing API token"})
            await websocket.close(code=4003)
            return

        await _manager.connect(websocket)

        audio_chunks = []
        sample_rate = 16000

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
                    sample_rate = data.get("sample_rate", 16000)
                    audio_chunks.clear()
                    await _manager.send_to(websocket, {"type": "status", "text": "Recording..."})

                elif msg_type == "audio":
                    pcm_bytes = base64.b64decode(data["data"])
                    audio_chunks.append(pcm_bytes)

                elif msg_type == "stop":
                    await _manager.send_to(websocket, {"type": "status", "text": "Processing..."})

                    if not audio_chunks:
                        await _manager.send_to(websocket, {"type": "error", "text": "No audio data"})
                        continue

                    # Assemble and process audio
                    import numpy as np
                    raw_pcm = b"".join(audio_chunks)
                    audio = np.frombuffer(raw_pcm, dtype=np.float32)

                    if len(audio) < sample_rate * 0.3:
                        await _manager.send_to(websocket, {"type": "error", "text": "Audio too short"})
                        continue

                    # Transcribe
                    try:
                        from speech.speech_pipeline import SpeechToText
                        stt = SpeechToText()
                        loop = asyncio.get_event_loop()
                        transcript = await loop.run_in_executor(
                            None, stt.transcribe_numpy, audio, sample_rate
                        )
                    except Exception as e:
                        await _manager.send_to(websocket, {"type": "error", "text": f"STT failed: {e}"})
                        continue

                    if not transcript.strip():
                        await _manager.send_to(websocket, {"type": "error", "text": "No speech detected"})
                        continue

                    await _manager.send_to(websocket, {"type": "transcript", "text": transcript})

                    # Process the command
                    response_text = await _process_pwa_command(transcript)
                    await _manager.send_to(websocket, {"type": "response", "text": response_text})

                    # Synthesize response
                    try:
                        from speech.speech_pipeline import TextToSpeech
                        tts = TextToSpeech()
                        loop = asyncio.get_event_loop()
                        response_audio = await loop.run_in_executor(
                            None, tts.synthesize, response_text
                        )
                        if len(response_audio) > 0:
                            chunk_size = 24000 * 2
                            for i in range(0, len(response_audio), chunk_size):
                                chunk = response_audio[i:i + chunk_size]
                                audio_b64 = base64.b64encode(chunk.tobytes()).decode("ascii")
                                await _manager.send_to(websocket, {
                                    "type": "response_audio",
                                    "data": audio_b64,
                                    "sample_rate": 24000,
                                })
                    except Exception as e:
                        logger.debug(f"TTS failed: {e}")

                    audio_chunks.clear()
                    await _manager.send_to(websocket, {"type": "status", "text": "Ready"})

                elif msg_type == "command":
                    # Text command (no audio)
                    text = data.get("text", "")
                    if text:
                        response_text = await _process_pwa_command(text)
                        await _manager.send_to(websocket, {"type": "response", "text": response_text})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"PWA WebSocket error: {e}")
        finally:
            _manager.disconnect(websocket)

    # ---------- REST endpoints ----------

    @app.post("/api/pwa/audio")
    async def pwa_audio_upload(audio_base64: str, sample_rate: int = 16000, mode: str = "command"):
        """Accept recorded audio blobs from the browser MediaRecorder API."""
        try:
            import numpy as np
            from speech.speech_pipeline import SpeechToText

            pcm_bytes = base64.b64decode(audio_base64)
            audio = np.frombuffer(pcm_bytes, dtype=np.float32)

            stt = SpeechToText()
            loop = asyncio.get_event_loop()
            transcript = await loop.run_in_executor(
                None, stt.transcribe_numpy, audio, sample_rate
            )

            if not transcript.strip():
                return {"error": "No speech detected"}

            response_text = await _process_pwa_command(transcript)

            return {
                "transcript": transcript,
                "response": response_text,
            }

        except Exception as e:
            return {"error": str(e)}

    @app.post("/api/pwa/subscribe")
    async def pwa_subscribe(subscription: PushSubscription):
        """Handle Web Push subscription from the PWA."""
        # Store subscription for later use
        # In production, you'd store this in a database
        return {"status": "subscribed", "endpoint": subscription.endpoint[:50] + "..."}

    @app.get("/api/pwa/status")
    async def pwa_status():
        """Get PWA connection status."""
        return {
            "active_connections": len(_manager.active_connections),
            "pwa_dir": str(PWA_DIR),
            "pwa_exists": PWA_DIR.exists(),
        }


# ---------------------------------------------------------------------------
# Command processing
# ---------------------------------------------------------------------------

async def _process_pwa_command(text: str) -> str:
    """Process a voice/text command from the PWA."""
    text_lower = text.lower().strip()

    # Brain dump
    if "brain dump" in text_lower or "dump" in text_lower:
        return "Let's do a brain dump. Tap the record button and speak for 15 seconds."

    # Schedule
    if "schedule" in text_lower:
        return "I'll show your schedule on the dashboard."

    # Study
    if "study" in text_lower:
        return "What topic would you like me to break down into study steps?"

    # Web search
    if "search" in text_lower or "look up" in text_lower or "find online" in text_lower:
        def _search():
            from agents.web_task_agent import WebTaskAgent
            agent = WebTaskAgent()
            result = agent.search(text)
            if result.get("results"):
                top = result["results"][0]
                return f"Found: {top.get('title', '')} at {top.get('url', '')}"
            return "No results found."
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _search)

    # Coding
    if "code" in text_lower or "fix" in text_lower or "bug" in text_lower:
        def _code():
            from agents.coding_agent import CodeAssistant
            assistant = CodeAssistant()
            result = assistant.explain(text)
            return result.get("summary", "I can help with that code task.")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _code)

    # Help
    if "help" in text_lower:
        return (
            "I can help with brain dumps, scheduling, study planning, "
            "web searches, coding tasks, and focus nudges. "
            "Just tell me what you need."
        )

    # Default: treat as brain dump
    def _braindump():
        from agents.braindump_agent import process_braindump
        result = process_braindump(text)
        count = len(result.get("thoughts", []))
        return f"Got it! Captured {count} thoughts. {result.get('suggested_first_step', '')}"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _braindump)


# ---------------------------------------------------------------------------
# Broadcast helper (for other modules to push updates to PWA)
# ---------------------------------------------------------------------------

# Reference to the running event loop, set when the app starts
_running_loop: Optional[asyncio.AbstractEventLoop] = None


def set_running_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the running event loop so sync code can schedule async work."""
    global _running_loop
    _running_loop = loop


async def broadcast_update(update_type: str, data: Dict[str, Any]) -> None:
    """Broadcast an update to all connected PWA clients.

    Call this from other parts of the app to push real-time updates:
        await broadcast_update("schedule_updated", {"blocks": 5})
        await broadcast_update("nudge", {"text": "Time to refocus"})
    """
    await _manager.broadcast({"type": update_type, "data": data})


def broadcast_sync(update_type: str, data: Dict[str, Any]) -> None:
    """Thread-safe broadcast for use from synchronous code (e.g., daemon threads).

    Schedules the async broadcast on the running event loop. If the loop isn't
    available yet (app still starting), the message is silently dropped.
    """
    if _running_loop is None or _running_loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(
            broadcast_update(update_type, data),
            _running_loop,
        )
    except RuntimeError:
        pass  # Loop not running or closed


# ---------------------------------------------------------------------------
# Standalone server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    standalone_app = FastAPI(title="PWA Server")
    standalone_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    setup_pwa_routes(standalone_app)

    print("🌐 PWA Server starting at http://localhost:8081")
    print("   PWA: http://localhost:8081/app")
    print("   WebSocket: ws://localhost:8081/ws/pwa")
    print("   Press Ctrl+C to stop.")
    uvicorn.run(standalone_app, host="localhost", port=8081, log_level="info")

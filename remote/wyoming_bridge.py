"""Wyoming Bridge — Android wake-word integration via Home Assistant Companion.

The Home Assistant Companion app (2026.3+) has a built-in "Hey Jarvis" wake word
that runs fully on-device via microWakeWord. When triggered, it sends audio to a
Wyoming-protocol server. This module implements that server.

Flow:
  1. Android: User says "Hey Jarvis" → HA Companion detects wake word (on-device)
  2. Android: HA Companion records command audio → sends via Wyoming protocol
  3. Host: This bridge receives audio → transcribes via Faster-Whisper
  4. Host: Routes transcript to braindump_agent (or other handler)
  5. Host: Synthesizes reply via Kokoro TTS
  6. Host: Streams audio back via Wyoming protocol
  7. Android: HA Companion plays the spoken reply

Usage:
    # Run standalone
    uv run python -m remote.wyoming_bridge --host 0.0.0.0 --port 10700

    # Or import and use programmatically
    from remote.wyoming_bridge import WyomingBridge
    bridge = WyomingBridge()
    bridge.start()
"""
import argparse
import asyncio
import json
import logging
import struct
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wyoming protocol message types
# ---------------------------------------------------------------------------

# Wyoming protocol is a simple JSON-lines protocol over TCP.
# Each message is a JSON object followed by a newline.
# Audio is sent as raw PCM bytes with a header.

WYOMING_VERSION = "1.0"

MSG_DESCRIBE = "describe"
MSG_AUDIO_START = "audio-start"
MSG_AUDIO_CHUNK = "audio-chunk"
MSG_AUDIO_STOP = "audio-stop"
MSG_TRANSCRIPT = "transcript"
MSG_INFER = "infer"
MSG_INTENT = "intent"
MSG_TEXT = "text"
MSG_ERROR = "error"
MSG_READY = "ready"


# ---------------------------------------------------------------------------
# Wyoming message helpers
# ---------------------------------------------------------------------------

def make_describe_message(sample_rate: int = 16000, channels: int = 1, encoding: str = "pcm_s16le") -> str:
    """Create a describe message advertising our audio capabilities."""
    msg = {
        "type": MSG_DESCRIBE,
        "version": WYOMING_VERSION,
        "payload": {
            "audio": {
                "rate": sample_rate,
                "width": 16,  # bits
                "channels": channels,
                "encoding": encoding,
            },
            "asr": {
                "name": "faster-whisper",
                "languages": ["en"],
            },
            "tts": {
                "name": "kokoro",
                "languages": ["en"],
                "voices": ["af_heart"],
            },
            "wake": {
                "name": "micro-wakeword",
            },
        },
    }
    return json.dumps(msg) + "\n"


def make_text_message(text: str) -> str:
    """Create a text message."""
    return json.dumps({"type": MSG_TEXT, "text": text}) + "\n"


def make_error_message(error: str) -> str:
    """Create an error message."""
    return json.dumps({"type": MSG_ERROR, "error": error}) + "\n"


def make_ready_message() -> str:
    """Create a ready message."""
    return json.dumps({"type": MSG_READY}) + "\n"


# ---------------------------------------------------------------------------
# Wyoming Bridge
# ---------------------------------------------------------------------------

class WyomingBridge:
    """Bridges Android wake-word audio to the ADHD Co-Processor agents.

    Handles the Wyoming protocol to receive audio from Home Assistant Companion,
    process it, and return spoken responses.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 10700,
        sample_rate: int = 16000,
        command_handler: Optional[Callable[[str], str]] = None,
    ):
        self.host = host
        self.port = port
        self.sample_rate = sample_rate
        self._server = None
        self._command_handler = command_handler or self._default_handler

        # Lazy imports for speech pipeline
        self._stt = None
        self._tts = None

    @property
    def stt(self):
        if self._stt is None:
            from speech.speech_pipeline import SpeechToText
            self._stt = SpeechToText()
        return self._stt

    @property
    def tts(self):
        if self._tts is None:
            from speech.speech_pipeline import TextToSpeech
            self._tts = TextToSpeech()
        return self._tts

    def _default_handler(self, text: str) -> str:
        """Default command handler — routes to braindump agent."""
        text_lower = text.lower().strip()

        # Brain dump
        if "brain dump" in text_lower or "dump" in text_lower:
            from agents.braindump_agent import process_braindump
            result = process_braindump(text)
            count = len(result.get("thoughts", []))
            return f"Captured {count} thoughts. {result.get('suggested_first_step', '')}"

        # Schedule
        if "schedule" in text_lower or "what should i do" in text_lower:
            from agents.scheduler_agent import generate_micro_sprint
            return generate_micro_sprint(text)

        # Study
        if "study" in text_lower or "learn" in text_lower or "teach me" in text_lower:
            from agents.study_agent import decompose_topic, format_study_plan
            plan = decompose_topic(text)
            units = plan.get("units", [])
            if units:
                first = units[0]
                return f"Study plan created with {len(units)} units. Start with: {first.get('title', 'Unit 1')}."
            return "I couldn't break that down. Try a more specific topic."

        # Web search
        if "search" in text_lower or "look up" in text_lower or "find online" in text_lower:
            from agents.web_task_agent import WebTaskAgent
            agent = WebTaskAgent()
            result = agent.search(text)
            if result.get("results"):
                top = result["results"][0]
                return f"Found: {top.get('title', '')} at {top.get('url', '')}"
            return "No results found."

        # Help
        if "help" in text_lower:
            return (
                "I can help with brain dumps, scheduling, study planning, "
                "web searches, and coding tasks. Just tell me what you need."
            )

        # Default: treat as brain dump
        from agents.braindump_agent import process_braindump
        result = process_braindump(text)
        count = len(result.get("thoughts", []))
        return f"Got it! Captured {count} thoughts. {result.get('suggested_first_step', '')}"

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a single Wyoming protocol client connection."""
        addr = writer.get_extra_info("peername")
        logger.info(f"Client connected: {addr}")

        try:
            # Send describe message
            writer.write(make_describe_message(self.sample_rate).encode())
            await writer.drain()

            # Wait for client's describe
            audio_chunks = []
            is_recording = False

            while True:
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == MSG_DESCRIBE:
                    # Client described itself — send ready
                    writer.write(make_ready_message().encode())
                    await writer.drain()

                elif msg_type == MSG_AUDIO_START:
                    is_recording = True
                    audio_chunks.clear()
                    logger.info(f"Recording started from {addr}")

                elif msg_type == MSG_AUDIO_CHUNK:
                    # Read raw audio bytes (sent as length-prefixed)
                    # Wyoming sends audio as raw bytes after the JSON line
                    # For simplicity, we read the audio from the message payload
                    audio_data = msg.get("data", "")
                    if audio_data:
                        import base64
                        audio_bytes = base64.b64decode(audio_data)
                        audio_chunks.append(audio_bytes)

                elif msg_type == MSG_AUDIO_STOP:
                    is_recording = False
                    logger.info(f"Recording stopped from {addr} ({len(audio_chunks)} chunks)")

                    if not audio_chunks:
                        writer.write(make_error_message("No audio received").encode())
                        await writer.drain()
                        continue

                    # Process the audio
                    response_text = await self._process_audio(audio_chunks)

                    # Send transcript back
                    writer.write(make_text_message(response_text).encode())
                    await writer.drain()

                    # Synthesize and send audio response
                    await self._send_tts_response(writer, response_text)

                elif msg_type == MSG_TEXT:
                    # Direct text command (no audio)
                    text = msg.get("text", "")
                    if text:
                        logger.info(f"Text command from {addr}: {text}")
                        response = self._command_handler(text)
                        writer.write(make_text_message(response).encode())
                        await writer.drain()
                        await self._send_tts_response(writer, response)

                elif msg_type == MSG_INFER:
                    # ASR inference request
                    # Client wants us to transcribe audio they send
                    pass  # Handled via audio chunks

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Client error: {e}")
            try:
                writer.write(make_error_message(str(e)).encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            logger.info(f"Client disconnected: {addr}")

    async def _process_audio(self, audio_chunks: list) -> str:
        """Transcribe audio chunks and run through command handler."""
        import numpy as np

        # Concatenate all audio chunks
        raw_pcm = b"".join(audio_chunks)

        # Try to interpret as int16 PCM (Wyoming standard)
        try:
            audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            # Fallback: try float32
            audio = np.frombuffer(raw_pcm, dtype=np.float32)

        if len(audio) < self.sample_rate * 0.3:
            return "Audio too short to process."

        # Transcribe in executor (blocking)
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None, self.stt.transcribe_numpy, audio, self.sample_rate
        )

        if not transcript.strip():
            return "I didn't catch that. Could you say it again?"

        logger.info(f"Transcript: {transcript}")

        # Process command in executor (blocking)
        response = await loop.run_in_executor(
            None, self._command_handler, transcript
        )

        return response

    async def _send_tts_response(self, writer: asyncio.StreamWriter, text: str):
        """Synthesize text and send audio back to client."""
        import numpy as np
        import base64

        try:
            loop = asyncio.get_event_loop()
            audio = await loop.run_in_executor(
                None, self.tts.synthesize, text
            )

            if len(audio) == 0:
                return

            # Convert float32 to int16 for Wyoming protocol
            audio_int16 = (audio * 32767).astype(np.int16)

            # Send audio start
            writer.write(json.dumps({
                "type": MSG_AUDIO_START,
                "rate": self.tts.SAMPLE_RATE,
                "width": 16,
                "channels": 1,
            }).encode() + b"\n")
            await writer.drain()

            # Send audio in chunks
            chunk_size = self.tts.SAMPLE_RATE * 2  # 1 second chunks
            raw_bytes = audio_int16.tobytes()
            for i in range(0, len(raw_bytes), chunk_size):
                chunk = raw_bytes[i:i + chunk_size]
                writer.write(json.dumps({
                    "type": MSG_AUDIO_CHUNK,
                    "data": base64.b64encode(chunk).decode("ascii"),
                }).encode() + b"\n")
                await writer.drain()

            # Send audio stop
            writer.write(json.dumps({"type": MSG_AUDIO_STOP}).encode() + b"\n")
            await writer.drain()

        except Exception as e:
            logger.error(f"TTS failed: {e}")

    async def start(self):
        """Start the Wyoming bridge server."""
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )

        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        logger.info(f"Wyoming Bridge listening on {addrs}")
        print(f"🔊 Wyoming Bridge listening on {addrs}")
        print(f"   Connect Home Assistant Companion → Settings → Voice Assistants → Add")
        print(f"   Pipeline type: Wyoming")
        print(f"   Host: {self.host}:{self.port}")

        async with self._server:
            await self._server.serve_forever()

    def stop(self):
        """Stop the server."""
        if self._server:
            self._server.close()


# ---------------------------------------------------------------------------
# Sync wrapper for non-async usage
# ---------------------------------------------------------------------------

def start_bridge_sync(host: str = "0.0.0.0", port: int = 10700):
    """Start the bridge in a sync context (e.g., from main.py)."""
    bridge = WyomingBridge(host=host, port=port)
    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        print("\n🔇 Wyoming Bridge stopped.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wyoming Bridge for Android wake-word")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=10700, help="Bind port (default: 10700)")
    args = parser.parse_args()

    print("🔊 Starting Wyoming Bridge...")
    print(f"   Host: {args.host}:{args.port}")
    print("   This bridges Android Home Assistant 'Hey Jarvis' wake word")
    print("   to the ADHD Co-Processor agents.")
    print()

    start_bridge_sync(host=args.host, port=args.port)

"""Speech Pipeline — speak in, hear back, no typing required.

Features:
- Faster-Whisper STT (small.en model, int8 on CPU)
- Kokoro-82M TTS (af_heart voice, 1.0–1.1× speed for calm tone)
- Bidirectional voice interface for brain dumps and interactions
- All processing local — zero cloud calls
"""
import io
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
import torch

try:
    import toml
    CONFIG = toml.load("config/config.toml")
except (FileNotFoundError, Exception):
    CONFIG = {}

SPEECH_CFG = CONFIG.get("speech", {})
WHISPER_MODEL = SPEECH_CFG.get("whisper_model", "small.en")
WHISPER_COMPUTE = SPEECH_CFG.get("whisper_compute_type", "int8")
TTS_VOICE = SPEECH_CFG.get("tts_voice", "af_heart")
TTS_SPEED = SPEECH_CFG.get("tts_speed", 1.05)
TTS_SILENCE_SEC = float(SPEECH_CFG.get("tts_silence_sec", 0.25))


# ---------------------------------------------------------------------------
# Speech-to-Text (Faster-Whisper)
# ---------------------------------------------------------------------------

class SpeechToText:
    """Wrapper around Faster-Whisper for local speech recognition."""

    def __init__(self, model_size: str = WHISPER_MODEL, compute_type: str = WHISPER_COMPUTE):
        self.model_size = model_size
        self.compute_type = compute_type
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            print(f"🎤 Loading Whisper model '{self.model_size}' (compute: {self.compute_type})...")
            self._model = WhisperModel(
                self.model_size,
                compute_type=self.compute_type,
            )
            print("  ✅ Whisper model loaded.")
        return self._model

    def transcribe_audio_file(self, audio_path: str) -> str:
        """Transcribe a WAV/FLAC/MP3 file."""
        segments, info = self.model.transcribe(audio_path)
        text_parts = [segment.text for segment in segments]
        return " ".join(text_parts).strip()

    def transcribe_numpy(self, audio: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe a numpy array of audio samples."""
        # Faster-whisper expects a float32 array normalized to [-1, 1]
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.max() > 1.0:
            audio = audio / (np.abs(audio).max() + 1e-8)
        segments, info = self.model.transcribe(audio, beam_size=5)
        text_parts = [segment.text for segment in segments]
        return " ".join(text_parts).strip()

    def record_and_transcribe(self, duration: Optional[float] = None, sample_rate: int = 16000) -> str:
        """Record from microphone and transcribe. If duration is None, records until silence."""
        print("🎤 Listening... (speak now)")

        if duration is not None:
            audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype="float32")
            sd.wait()
        else:
            # Record with a timeout
            audio = self._record_with_silence_detection(sample_rate)

        audio = audio.flatten()
        print(f"  📝 Transcribing {len(audio)/sample_rate:.1f}s of audio...")
        return self.transcribe_numpy(audio, sample_rate)

    def _record_with_silence_detection(
        self, sample_rate: int = 16000, silence_threshold: float = 0.01,
        silence_duration: float = 2.0, max_duration: float = 30.0,
    ) -> np.ndarray:
        """Record until silence is detected or max duration is reached."""
        frames = []
        block_size = int(sample_rate * 0.1)  # 100ms blocks
        silent_blocks = 0
        max_silent_blocks = int(silence_duration / 0.1)

        with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32") as stream:
            start = time.time()
            while (time.time() - start) < max_duration:
                data, _ = stream.read(block_size)
                frames.append(data.copy())
                rms = np.sqrt(np.mean(data ** 2))
                if rms < silence_threshold:
                    silent_blocks += 1
                    if silent_blocks >= max_silent_blocks:
                        break
                else:
                    silent_blocks = 0

        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames, axis=0)


# ---------------------------------------------------------------------------
# Text-to-Speech (Kokoro)
# ---------------------------------------------------------------------------

class TextToSpeech:
    """Wrapper around Kokoro-82M for local speech synthesis."""

    def __init__(self, voice: str = TTS_VOICE, speed: float = TTS_SPEED, silence_sec: float = TTS_SILENCE_SEC):
        self.voice = voice
        self.speed = speed
        self.silence_sec = silence_sec
        self._pipeline = None

    @property
    def pipeline(self):
        if self._pipeline is None:
            from kokoro import KPipeline
            print(f"🔊 Loading Kokoro TTS (voice: {self.voice})...")
            self._pipeline = KPipeline(lang_code="a")  # American English
            print("  ✅ Kokoro TTS loaded.")
        return self._pipeline

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences on sentence-ending punctuation.

        Keeps the punctuation attached to the preceding sentence so Kokoro
        produces natural prosody (falling intonation at period, rising at '?').
        Falls back to the full text when no sentence boundaries are found.
        """
        parts = re.split(r'(?<=[.!?])\s+', text.strip())
        return [p for p in parts if p] or [text.strip()]

    SAMPLE_RATE = 24000  # Kokoro-82M native sample rate

    def synthesize(self, text: str, silence_sec: Optional[float] = None) -> np.ndarray:
        """Convert text to audio, synthesizing sentence-by-sentence.

        Each sentence is generated independently so Kokoro applies proper
        prosody (falling/rising intonation).  A short silence gap is
        inserted between sentences to sound natural and give the listener
        a brief cognitive pause — important for the calm, non-urgent tone
        the ADHD co-processor aims for.

        Args:
            text: The text to synthesize.
            silence_sec: Seconds of silence between sentences.
                Defaults to the value from config.toml (tts_silence_sec).
        """
        if silence_sec is None:
            silence_sec = self.silence_sec
        sentences = self._split_sentences(text)
        sr = self.SAMPLE_RATE
        silence = np.zeros(int(sr * silence_sec), dtype=np.float32)

        pieces: list[np.ndarray] = []
        for i, sentence in enumerate(sentences):
            for result in self.pipeline(sentence, voice=self.voice, speed=self.speed):
                audio = result.audio
                if isinstance(audio, torch.Tensor):
                    audio = audio.cpu().numpy()
                pieces.append(audio)
            # Add silence gap between sentences (not after the last one)
            if i < len(sentences) - 1:
                pieces.append(silence)

        if not pieces:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(pieces)

    def speak(self, text: str):
        """Speak text aloud immediately."""
        print(f"  🔊 Speaking: {text[:80]}...")
        audio = self.synthesize(text)
        if len(audio) > 0:
            sd.play(audio, samplerate=self.SAMPLE_RATE)
            sd.wait()

    def synthesize_to_file(self, text: str, output_path: str):
        """Save synthesized speech to a WAV file."""
        audio = self.synthesize(text)
        sf.write(output_path, audio, self.SAMPLE_RATE)
        print(f"  💾 Saved to {output_path}")


# ---------------------------------------------------------------------------
# Full Speech Pipeline
# ---------------------------------------------------------------------------

class SpeechPipeline:
    """Bidirectional voice interface: speak in → get response → hear back."""

    def __init__(self):
        self.stt = SpeechToText()
        self.tts = TextToSpeech()

    def listen_and_respond(self, response_fn, max_duration: float = 30.0) -> str:
        """Listen for speech, process with response_fn, and speak the answer.

        Args:
            response_fn: callable(text) -> str that processes input and returns response
            max_duration: max recording duration in seconds
        Returns:
            The spoken response text
        """
        # Listen
        transcript = self.stt.record_and_transcribe(duration=max_duration)
        if not transcript.strip():
            print("  ⚠️  No speech detected.")
            return ""

        print(f"  📝 Heard: {transcript}")

        # Process
        response = response_fn(transcript)
        print(f"  💬 Response: {response}")

        # Speak
        self.tts.speak(response)
        return response

    def brain_dump_session(self, process_fn, duration: float = 15.0):
        """Record a timed brain dump, process it, and speak the summary."""
        print(f"🧠 Brain dump mode — speak for {duration} seconds...")
        transcript = self.stt.record_and_transcribe(duration=duration)
        if not transcript.strip():
            print("  ⚠️  No speech detected.")
            return

        result = process_fn(transcript)
        summary = (
            f"I captured {result.get('memories_stored', 0)} thoughts. "
            f"Mood hint: {result.get('mood_hint', 'unknown')}. "
            f"Suggested first step: {result.get('suggested_first_step', 'none')}."
        )
        self.tts.speak(summary)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pipeline = SpeechPipeline()
    print("🎤 Voice interface ready.")
    print("  Commands: 'talk' = listen & respond, 'dump' = brain dump, 'quit' = exit")

    def dummy_responder(text: str) -> str:
        return f"You said: {text}. I heard you clearly."

    while True:
        try:
            cmd = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if cmd == "quit":
            break
        elif cmd == "talk":
            pipeline.listen_and_respond(dummy_responder)
        elif cmd == "dump":
            pipeline.brain_dump_session(lambda text: {"memories_stored": 3, "mood_hint": "focused", "suggested_first_step": "Take a breath"})
        else:
            print("  Unknown command. Try 'talk', 'dump', or 'quit'.")

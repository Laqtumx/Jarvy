from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from jarvy.brain.agent import Agent

_WAKE_PREFIX = re.compile(r"^\s*jarvy\b[\s,:;.!?—-]*", re.IGNORECASE)


def extract_wake_command(transcript: str) -> str | None:
    """Return text after a leading Jarvy wake word, otherwise ignore the transcript."""
    match = _WAKE_PREFIX.match(transcript)
    if not match:
        return None
    return transcript[match.end() :].strip()


class VoiceInterface:
    """Offline Vosk speech recognition and local pyttsx3 speech output."""

    def __init__(self, agent: Agent, model_dir: Path) -> None:
        self.agent = agent
        self.model_dir = model_dir.expanduser().resolve()
        self._pending: tuple[str, str, float] | None = None

    def _speak(self, engine: Any, text: str) -> None:
        if not text:
            return
        engine.say(text[:1200])
        engine.runAndWait()

    def _handle_command(self, command: str, engine: Any) -> bool:
        normalized = command.casefold().strip()
        if normalized in {"ultron stop", "stop jarvy", "shutdown"}:
            self._speak(engine, "Voice assistant stopped.")
            return False
        if self._pending and time.monotonic() - self._pending[2] > 60:
            self._pending = None
        if self._pending and normalized in {"confirm", "approve", "yes"}:
            original, approval, _ = self._pending
            self._pending = None
            result = asyncio.run(self.agent.run(original, approval=approval))
            if result.pending_approval:
                self._speak(engine, result.response.splitlines()[0])
                self._pending = (original, result.pending_approval, time.monotonic())
                self._speak(
                    engine, "This action still needs confirmation. Say Jarvy confirm to approve it."
                )
            else:
                self._speak(engine, result.response)
            return True
        if self._pending and normalized in {"cancel", "no"}:
            self._pending = None
            self._speak(engine, "Cancelled.")
            return True
        self._pending = None
        if not command:
            self._speak(engine, "I'm listening.")
            return True
        result = asyncio.run(self.agent.run(command))
        if result.pending_approval:
            self._speak(engine, result.response.splitlines()[0])
            self._pending = (command, result.pending_approval, time.monotonic())
            self._speak(engine, "Say Jarvy confirm to approve this exact action, or Jarvy cancel.")
        else:
            self._speak(engine, result.response)
        return True

    def run(self) -> None:
        try:
            import pyttsx3
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model
        except ImportError as exc:
            raise RuntimeError("Voice mode needs: pip install -e '.[voice]'") from exc
        if not self.model_dir.is_dir():
            raise RuntimeError(
                "Set JARVY_VOSK_MODEL_DIR to a downloaded offline Vosk speech model directory."
            )
        engine = pyttsx3.init()
        model = Model(str(self.model_dir))
        recognizer = KaldiRecognizer(model, 16_000)
        print(
            "Jarvy voice mode listens locally. Start a request with 'Jarvy'. "
            "Say 'Ultron stop' to exit."
        )
        self._speak(engine, "Voice mode ready. Say Jarvy, then your request.")
        with sd.RawInputStream(
            samplerate=16_000,
            blocksize=8_000,
            dtype="int16",
            channels=1,
        ) as microphone:
            while True:
                audio, _ = microphone.read(4_000)
                if not recognizer.AcceptWaveform(bytes(audio)):
                    continue
                try:
                    transcript = json.loads(recognizer.Result()).get("text", "")
                except (json.JSONDecodeError, TypeError):
                    continue
                command = extract_wake_command(str(transcript))
                if command is None:
                    continue
                print(f"Jarvy heard: {command or '[wake word]'}")
                if not self._handle_command(command, engine):
                    return

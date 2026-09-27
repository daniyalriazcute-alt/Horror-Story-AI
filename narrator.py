"""
Agent 2: The Narrator.
Converts the Storyteller's text into eerie narration using Edge-TTS
(free, no API key required). Enforces a hard 40-second audio cap by
trimming the TEXT before synthesis (never cuts the finished audio
file, which would clip mid-word).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import edge_tts

from agents.prompts import TTS_VOICE_MAP

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
MAX_AUDIO_SECONDS = 40
WORDS_PER_SECOND = 2.5  # ~150 wpm average narration pace


@dataclass
class NarrationResult:
    audio_path: str
    voice: str
    duration_seconds: float
    success: bool
    error: str = ""


def _trim_to_duration(text: str, max_seconds: int = MAX_AUDIO_SECONDS) -> str:
    """Trim text at a sentence boundary so narration stays under the cap."""
    max_words = int(max_seconds * WORDS_PER_SECOND)
    words = text.split()
    if len(words) <= max_words:
        return text

    truncated = " ".join(words[:max_words])
    # snap back to the last full sentence so we never cut mid-thought
    for sep in [".", "!", "?"]:
        idx = truncated.rfind(sep)
        if idx != -1 and idx > len(truncated) * 0.5:
            return truncated[: idx + 1]
    return truncated + "..."


async def _synthesize(text: str, voice: str, out_path: Path) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def _estimate_duration(text: str) -> float:
    word_count = len(text.split())
    return round(word_count / WORDS_PER_SECOND, 1)


def narrate_story(story_id: int, story_text: str, language: str = "en") -> NarrationResult:
    """Main entry point for Agent 2. Trims text to the 40s cap, then
    synthesizes audio with a single retry on failure."""

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    voice = TTS_VOICE_MAP.get(language, TTS_VOICE_MAP["en"])
    trimmed_text = _trim_to_duration(story_text)
    estimated_duration = min(_estimate_duration(trimmed_text), MAX_AUDIO_SECONDS)

    out_path = AUDIO_DIR / f"story_{story_id}_{uuid.uuid4().hex[:8]}.mp3"

    attempts = 0
    last_error = ""
    while attempts <= 1:  # initial attempt + max 1 retry
        attempts += 1
        try:
            asyncio.run(_synthesize(trimmed_text, voice, out_path))
            if out_path.exists() and out_path.stat().st_size > 0:
                return NarrationResult(
                    audio_path=str(out_path),
                    voice=voice,
                    duration_seconds=estimated_duration,
                    success=True,
                )
            last_error = "Audio file was empty after synthesis"
        except Exception as exc:  # noqa: BLE001 -- retry once, then surface it
            last_error = str(exc)
            continue

    return NarrationResult(
        audio_path="",
        voice=voice,
        duration_seconds=0.0,
        success=False,
        error=last_error,
    )

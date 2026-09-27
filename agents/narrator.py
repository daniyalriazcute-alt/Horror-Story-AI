"""
Agent 2: The Narrator.
Converts the Storyteller's text into eerie narration.

Primary engine: Edge-TTS (free, richer/deeper voices) -- but Microsoft's
backend blocks many cloud/datacenter IPs (including Streamlit Community
Cloud), so it can 403 in production even though it works locally.

Fallback engine: gTTS (Google Translate TTS, free, no API key) -- fewer
voice options but reliably reachable from cloud hosts. This fallback
doubles as the project's required "max 1 retry" behavior: attempt 1 is
Edge-TTS, the retry is gTTS with a different (still free) backend.

Enforces a hard 40-second audio cap by trimming the TEXT before
synthesis (never cuts the finished audio file, which would clip
mid-word).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import edge_tts
from gtts import gTTS

from agents.prompts import GTTS_LANG_MAP, TTS_VOICE_MAP

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
MAX_AUDIO_SECONDS = 40
WORDS_PER_SECOND = 2.5  # ~150 wpm average narration pace


@dataclass
class NarrationResult:
    audio_path: str
    voice: str
    duration_seconds: float
    success: bool
    engine_used: str = ""
    error: str = ""


def _trim_to_duration(text: str, max_seconds: int = MAX_AUDIO_SECONDS) -> str:
    """Trim text at a sentence boundary so narration stays under the cap."""
    max_words = int(max_seconds * WORDS_PER_SECOND)
    words = text.split()
    if len(words) <= max_words:
        return text

    truncated = " ".join(words[:max_words])
    for sep in [".", "!", "?"]:
        idx = truncated.rfind(sep)
        if idx != -1 and idx > len(truncated) * 0.5:
            return truncated[: idx + 1]
    return truncated + "..."


async def _synthesize_edge(text: str, voice: str, out_path: Path) -> None:
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def _synthesize_gtts(text: str, lang: str, out_path: Path) -> None:
    tts = gTTS(text=text, lang=lang, slow=False)
    tts.save(str(out_path))


def _estimate_duration(text: str) -> float:
    word_count = len(text.split())
    return round(word_count / WORDS_PER_SECOND, 1)


def narrate_story(story_id: int, story_text: str, language: str = "en") -> NarrationResult:
    """Main entry point for Agent 2. Trims text to the 40s cap, then tries
    Edge-TTS first; if that fails, falls back to gTTS (counts as the
    project's single allowed retry)."""

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    edge_voice = TTS_VOICE_MAP.get(language, TTS_VOICE_MAP["en"])
    gtts_lang = GTTS_LANG_MAP.get(language, GTTS_LANG_MAP["en"])
    trimmed_text = _trim_to_duration(story_text)
    estimated_duration = min(_estimate_duration(trimmed_text), MAX_AUDIO_SECONDS)

    out_path = AUDIO_DIR / f"story_{story_id}_{uuid.uuid4().hex[:8]}.mp3"
    last_error = ""

    # --- Attempt 1: Edge-TTS ---
    try:
        asyncio.run(_synthesize_edge(trimmed_text, edge_voice, out_path))
        if out_path.exists() and out_path.stat().st_size > 0:
            return NarrationResult(
                audio_path=str(out_path),
                voice=edge_voice,
                duration_seconds=estimated_duration,
                success=True,
                engine_used="edge-tts",
            )
        last_error = "Edge-TTS produced an empty file"
    except Exception as exc:  # noqa: BLE001 -- fall through to retry engine
        last_error = f"Edge-TTS failed: {exc}"

    # --- Retry (1 of 1): gTTS fallback, different engine/backend ---
    try:
        _synthesize_gtts(trimmed_text, gtts_lang, out_path)
        if out_path.exists() and out_path.stat().st_size > 0:
            return NarrationResult(
                audio_path=str(out_path),
                voice=f"gTTS ({gtts_lang})",
                duration_seconds=estimated_duration,
                success=True,
                engine_used="gtts",
            )
        last_error = f"{last_error} | gTTS also produced an empty file"
    except Exception as exc:  # noqa: BLE001 -- both engines failed, surface it
        last_error = f"{last_error} | gTTS also failed: {exc}"

    return NarrationResult(
        audio_path="",
        voice="",
        duration_seconds=0.0,
        success=False,
        error=last_error,
    )

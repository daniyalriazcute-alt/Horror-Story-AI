"""
Agent 1: The Storyteller.
Calls Groq's free-tier API (model: openai/gpt-oss-120b) to generate a
short horror story, with OWASP guardrails applied before AND after
the LLM call, and a single retry on failure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from groq import Groq

from agents.prompts import (
    STORYTELLER_SYSTEM_PROMPT,
    get_generation_failed_message,
    get_refusal_message,
)
from utils.security import check_prompt_injection, sanitize_output, check_system_leakage

MODEL_NAME = "openai/gpt-oss-120b"
MAX_STORY_WORDS = 100  # keeps narration under the 40s audio cap


@dataclass
class StoryResult:
    text: str
    language: str
    was_refused: bool
    tokens_used: int
    guard_reason: str = ""


def _get_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your environment or .env file."
        )
    return Groq(api_key=api_key)


def _call_groq(client: Groq, theme: str, tone: str, length: str) -> tuple[str, int]:
    user_message = (
        f"Theme: {theme}\nTone: {tone}\nLength: {length} "
        f"(hard limit {MAX_STORY_WORDS} words)"
    )
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": STORYTELLER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_tokens=300,
        temperature=0.85,
    )
    text = response.choices[0].message.content or ""
    tokens_used = getattr(response.usage, "total_tokens", 0) if response.usage else 0
    return text.strip(), tokens_used


def generate_story(
    theme: str,
    tone: str = "psychological",
    length: str = "short",
    language: str = "en",
) -> StoryResult:
    """Main entry point for Agent 1. Returns a StoryResult whether the
    generation succeeded, was refused for security reasons, or failed."""

    # --- LLM01 guard: check the raw user input BEFORE calling the LLM ---
    injection_check = check_prompt_injection(theme)
    if not injection_check.safe:
        return StoryResult(
            text=get_refusal_message(language),
            language=language,
            was_refused=True,
            tokens_used=0,
            guard_reason=injection_check.reason,
        )

    client = _get_client()
    attempts = 0
    last_error: Exception | None = None

    while attempts <= 1:  # initial attempt + max 1 retry
        attempts += 1
        try:
            raw_text, tokens_used = _call_groq(client, theme, tone, length)

            if not raw_text:
                last_error = RuntimeError("Empty response from model")
                continue

            # --- LLM05 guard: sanitize model output ---
            cleaned_text, output_check = sanitize_output(raw_text)
            if not output_check.safe:
                last_error = RuntimeError(f"Unsafe output: {output_check.matched_pattern}")
                continue

            # --- LLM07 guard: make sure the model didn't leak its prompt ---
            leakage_check = check_system_leakage(cleaned_text)
            if not leakage_check.safe:
                last_error = RuntimeError("Possible system prompt leakage detected")
                continue

            # Enforce the word cap defensively even if the model overshoots
            words = cleaned_text.split()
            if len(words) > MAX_STORY_WORDS:
                cleaned_text = " ".join(words[:MAX_STORY_WORDS]).rsplit(".", 1)[0] + "."

            return StoryResult(
                text=cleaned_text,
                language=language,
                was_refused=False,
                tokens_used=tokens_used,
            )

        except Exception as exc:  # noqa: BLE001 -- deliberately broad, we retry once
            last_error = exc
            continue

    # Both attempts failed -- this is an API/infra failure, NOT a security
    # refusal, so it must use a different message (avoid implying the user's
    # prompt was blocked when it was actually a Groq call that failed).
    return StoryResult(
        text=get_generation_failed_message(language),
        language=language,
        was_refused=True,
        tokens_used=0,
        guard_reason=f"generation_failed: {last_error}",
    )

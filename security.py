"""
OWASP LLM Top 10 (2025) guardrails for Nightlore.
Covers exactly three categories, as scoped for this project:
  - LLM01: Prompt Injection
  - LLM07: System Prompt Leakage
  - LLM05: Improper Output Handling

This module does code-level (regex/keyword) filtering BEFORE the prompt
ever reaches the LLM. It is deliberately simple and fast (no ML model)
so it can run on every keystroke/submission with near-zero latency.
Pair this with the LLM's own instruction-following (see agents/prompts.py)
for defense in depth -- never rely on the system prompt alone.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# LLM01 -- Prompt Injection patterns (multilingual: EN, ES, DE, UR, Roman UR)
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = [
    # English
    r"ignore (all|any|the)? ?previous instructions",
    r"ignore (all|any|the)? ?prior instructions",
    r"disregard (all|any|the)? ?(previous|prior|above)",
    r"you are now",
    r"act as if",
    r"bypass (the|your|all)? ?(rules|filters|guardrails|restrictions)",
    r"jailbreak",
    r"reveal (your|the) (system prompt|instructions)",
    r"repeat (your|the) (system prompt|instructions)",
    r"what (is|are) your (system prompt|instructions)",
    r"new instructions?:",
    r"override (your|the)? ?(rules|settings|config)",
    # Spanish
    r"ignora las instrucciones anteriores",
    r"olvida las instrucciones",
    r"actúa como si",
    r"revela tu (mensaje del sistema|instrucciones)",
    # German
    r"ignoriere (die|alle) vorherigen anweisungen",
    r"vergiss die anweisungen",
    r"verhalte dich als ob",
    r"zeig(e)? mir dein(e)? (systemprompt|anweisungen)",
    # Urdu (Arabic script) -- common phrasing for "ignore previous instructions"
    r"پچھل[ای] ہدایات .*نظر ?انداز",
    r"اپنا سسٹم پرامپٹ .*بتا",
    # Roman Urdu
    r"pichli hidayat.*nazar ?andaz",
    r"system prompt.*bata ?do",
    r"tum ab .*ho jao",
]

_COMPILED_INJECTION = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# ---------------------------------------------------------------------------
# LLM05 -- Improper Output Handling: things the model output must never
# contain before it's rendered to the UI or sent to the TTS engine.
# ---------------------------------------------------------------------------
UNSAFE_OUTPUT_PATTERNS = [
    r"<script",
    r"</script",
    r"<iframe",
    r"javascript:",
    r"```",  # code fences have no place in a narrated story
    r"\bDROP TABLE\b",
    r"\bDELETE FROM\b",
]

_COMPILED_OUTPUT = [re.compile(p, re.IGNORECASE) for p in UNSAFE_OUTPUT_PATTERNS]


@dataclass
class GuardResult:
    safe: bool
    reason: str = ""
    matched_pattern: str = ""


def check_prompt_injection(user_text: str) -> GuardResult:
    """LLM01 check. Run on every user-supplied theme/prompt BEFORE it is
    sent to the Storyteller agent."""
    if not user_text or not user_text.strip():
        return GuardResult(safe=True)

    for pattern in _COMPILED_INJECTION:
        match = pattern.search(user_text)
        if match:
            return GuardResult(
                safe=False,
                reason="prompt_injection",
                matched_pattern=match.group(0),
            )
    return GuardResult(safe=True)


def sanitize_output(model_text: str) -> tuple[str, GuardResult]:
    """LLM05 check. Run on every LLM response BEFORE it is rendered or
    passed to the TTS engine. Strips unsafe fragments and flags the result."""
    if not model_text:
        return "", GuardResult(safe=True)

    flagged = False
    matched = ""
    cleaned = model_text
    for pattern in _COMPILED_OUTPUT:
        if pattern.search(cleaned):
            flagged = True
            matched = pattern.pattern
            cleaned = pattern.sub("", cleaned)

    result = GuardResult(
        safe=not flagged,
        reason="improper_output_handling" if flagged else "",
        matched_pattern=matched,
    )
    return cleaned.strip(), result


# ---------------------------------------------------------------------------
# LLM07 -- System Prompt Leakage: guard the OTHER direction too. If the
# model's own output accidentally echoes system-prompt-looking text back
# (e.g. because it was tricked), catch it here as a second layer.
# ---------------------------------------------------------------------------
LEAKAGE_MARKERS = [
    "you are \"the storyteller\"",
    "you are \"the narrator\"",
    "owasp llm top 10",
    "security -- follow these rules",
    "retry rule",
]


def check_system_leakage(model_text: str) -> GuardResult:
    lowered = model_text.lower()
    for marker in LEAKAGE_MARKERS:
        if marker in lowered:
            return GuardResult(safe=False, reason="system_prompt_leakage", matched_pattern=marker)
    return GuardResult(safe=True)


def run_all_guards(text: str, direction: str = "input") -> GuardResult:
    """Convenience wrapper. direction = 'input' checks LLM01 on user text;
    direction = 'output' checks LLM05 + LLM07 on model text."""
    if direction == "input":
        return check_prompt_injection(text)
    else:
        cleaned, output_result = sanitize_output(text)
        if not output_result.safe:
            return output_result
        return check_system_leakage(cleaned)


# ---------------------------------------------------------------------------
# Token / request rate limiter (free-tier Groq protection)
# ---------------------------------------------------------------------------
@dataclass
class RateLimiter:
    max_requests_per_minute: int = 8
    max_tokens_per_minute: int = 6000
    _request_log: list = field(default_factory=list)  # list[float] timestamps
    _token_log: list = field(default_factory=list)  # list[tuple[float, int]]

    def _prune(self, now: float) -> None:
        window_start = now - 60
        self._request_log = [t for t in self._request_log if t >= window_start]
        self._token_log = [(t, n) for (t, n) in self._token_log if t >= window_start]

    def allow_request(self, estimated_tokens: int = 300) -> tuple[bool, str]:
        now = time.time()
        self._prune(now)

        if len(self._request_log) >= self.max_requests_per_minute:
            wait = 60 - (now - self._request_log[0])
            return False, f"Rate limit reached. Try again in {int(wait)}s."

        tokens_used = sum(n for _, n in self._token_log)
        if tokens_used + estimated_tokens > self.max_tokens_per_minute:
            return False, "Token budget for this minute is exhausted. Please wait."

        return True, ""

    def record(self, tokens_used: int) -> None:
        now = time.time()
        self._request_log.append(now)
        self._token_log.append((now, tokens_used))

"""
Nightlore -- Horror Story + AI Narration Reader
Main Streamlit entry point.

Run with:  streamlit run app.py
Requires:  GROQ_API_KEY set in environment or .env
"""

from __future__ import annotations

import uuid
from pathlib import Path

import streamlit as st

from agents.narrator import narrate_story
from agents.prompts import SUPPORTED_LANGUAGES
from agents.storyteller import generate_story
from utils import database as db
from utils.security import RateLimiter, check_prompt_injection

# --------------------------------------------------------------------------
# Page config + session bootstrap
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="Nightlore -- Horror Story + AI Narration Reader",
    page_icon="💀",
    layout="centered",
)

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
    db.init_db()
    db.start_session(st.session_state.session_id)

if "rate_limiter" not in st.session_state:
    st.session_state.rate_limiter = RateLimiter(
        max_requests_per_minute=8, max_tokens_per_minute=6000
    )

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = True

if "current_story" not in st.session_state:
    st.session_state.current_story = None  # dict: text, story_id, language, was_refused

if "current_audio" not in st.session_state:
    st.session_state.current_audio = None  # dict: path, duration


# --------------------------------------------------------------------------
# Theme CSS -- red/black horror palette, dark & light variants
# --------------------------------------------------------------------------
def load_css(dark: bool) -> str:
    if dark:
        bg = "#0A0810"
        surface = "#150C10"
        surface2 = "#1A0D12"
        border = "#3A1520"
        text = "#F5E9EC"
        text_dim = "#8A7580"
        accent = "#E8637A"
        accent_bg = "#7A2530"
        safe = "#5FCB8D"
    else:
        bg = "#FAF6F7"
        surface = "#FFFFFF"
        surface2 = "#F3E9EB"
        border = "#E3CFD3"
        text = "#241419"
        text_dim = "#7A6469"
        accent = "#C43A54"
        accent_bg = "#F0D6DB"
        safe = "#1D9E75"

    return f"""
    <style>
    .stApp {{
        background-color: {bg};
        color: {text};
    }}
    .nl-card {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 14px;
    }}
    .nl-header {{
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 4px;
    }}
    .nl-title {{
        font-size: 20px;
        font-weight: 600;
        color: {text};
    }}
    .nl-accent {{
        color: {accent};
    }}
    .nl-badge {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background-color: {surface2};
        border: 1px solid {accent_bg};
        border-radius: 20px;
        padding: 4px 12px;
        font-size: 12px;
        color: {accent};
    }}
    .nl-badge-safe {{
        border-color: {safe};
        color: {safe};
    }}
    .nl-dim {{
        color: {text_dim};
        font-size: 12px;
    }}
    .nl-mono {{
        font-family: 'Courier New', monospace;
        font-size: 13px;
        line-height: 1.7;
        color: {text};
    }}
    .stButton>button {{
        border-radius: 8px;
        border: 1px solid {border};
    }}
    .nl-refusal {{
        background-color: {surface2};
        border: 1px solid {accent_bg};
        border-radius: 8px;
        padding: 12px;
        color: {accent};
        font-size: 13px;
    }}
    </style>
    """


st.markdown(load_css(st.session_state.dark_mode), unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------
header_col1, header_col2 = st.columns([3, 1])
with header_col1:
    st.markdown(
        '<div class="nl-header">💀 <span class="nl-title">Horror Story '
        '<span class="nl-accent">+ AI Narration Reader</span></span></div>',
        unsafe_allow_html=True,
    )
with header_col2:
    st.markdown(
        '<div style="text-align:right;" class="nl-badge">🟢 LIVE</div>',
        unsafe_allow_html=True,
    )

st.markdown(
    '<span class="nl-badge nl-badge-safe">🛡️ Guarded &middot; OWASP LLM Top 10</span>',
    unsafe_allow_html=True,
)
st.write("")


# --------------------------------------------------------------------------
# Settings panel (sidebar): New chat / End chat / Chat history / theme
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Settings")

    st.session_state.dark_mode = st.toggle("Dark mode", value=st.session_state.dark_mode)

    if st.button("➕ New chat", use_container_width=True):
        db.delete_session_history(st.session_state.session_id)
        st.session_state.current_story = None
        st.session_state.current_audio = None
        st.rerun()

    if st.button("🔌 End chat", use_container_width=True):
        db.end_session(st.session_state.session_id)
        st.session_state.session_id = str(uuid.uuid4())
        db.start_session(st.session_state.session_id)
        st.session_state.current_story = None
        st.session_state.current_audio = None
        st.rerun()

    st.markdown("---")
    st.markdown("#### Chat history")
    history = db.get_session_history(st.session_state.session_id)
    if not history:
        st.caption("No stories yet this session.")
    else:
        for row in history:
            label = row["theme"][:40] + ("..." if len(row["theme"]) > 40 else "")
            with st.expander(label):
                st.write(row["story_text"])
                if row["audio_path"] and Path(row["audio_path"]).exists():
                    st.audio(row["audio_path"])


# --------------------------------------------------------------------------
# Agent 1 -- Storyteller panel
# --------------------------------------------------------------------------
st.markdown('<div class="nl-card">', unsafe_allow_html=True)
st.markdown("#### 🤖 Agent 1 — Storyteller")

language_code = st.selectbox(
    "Language",
    options=list(SUPPORTED_LANGUAGES.keys()),
    format_func=lambda k: SUPPORTED_LANGUAGES[k],
    index=0,
)

theme = st.text_area(
    "Story theme",
    placeholder="e.g. an abandoned lighthouse where the light hasn't turned in three nights",
    height=80,
)

col_a, col_b = st.columns(2)
with col_a:
    tone = st.selectbox("Tone", ["psychological", "slasher", "lovecraftian"])
with col_b:
    length = st.selectbox("Length", ["short"], index=0, help="Fixed to fit the 40s narration cap")

# live guardrail preview as the user types
if theme:
    injection_check = check_prompt_injection(theme)
    if not injection_check.safe:
        st.markdown(
            '<span class="nl-badge">🔴 Blocked -- possible prompt injection detected</span>',
            unsafe_allow_html=True,
        )

generate_clicked = st.button("🪶 Generate story", type="primary", use_container_width=True)

if generate_clicked:
    if not theme.strip():
        st.warning("Enter a theme first.")
    else:
        allowed, msg = st.session_state.rate_limiter.allow_request(estimated_tokens=300)
        if not allowed:
            st.error(msg)
        else:
            with st.spinner("The Storyteller is writing..."):
                result = generate_story(
                    theme=theme, tone=tone, length=length, language=language_code
                )
                st.session_state.rate_limiter.record(result.tokens_used)

                story_id = db.save_story(
                    session_id=st.session_state.session_id,
                    theme=theme,
                    language=language_code,
                    tone=tone,
                    story_text=result.text,
                    tokens_used=result.tokens_used,
                    was_refused=result.was_refused,
                )
                st.session_state.current_story = {
                    "id": story_id,
                    "text": result.text,
                    "language": language_code,
                    "was_refused": result.was_refused,
                    "guard_reason": result.guard_reason,
                }
                st.session_state.current_audio = None

# Display the result
if st.session_state.current_story:
    story = st.session_state.current_story
    if story["was_refused"]:
        is_security_block = story.get("guard_reason", "") == "prompt_injection"
        icon = "🛡️" if is_security_block else "⚠️"
        label = "Blocked (security)" if is_security_block else "Generation error"
        st.markdown(f"**{label}**")
        st.markdown(f'<div class="nl-refusal">{icon} {story["text"]}</div>', unsafe_allow_html=True)
    else:
        st.markdown("**Generated story**")
        st.markdown(f'<div class="nl-mono">{story["text"]}</div>', unsafe_allow_html=True)
        st.caption(f"~{len(story['text'].split())} words")

st.markdown("</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Agent 2 -- Narrator panel
# --------------------------------------------------------------------------
st.markdown('<div class="nl-card">', unsafe_allow_html=True)
st.markdown("#### 🎙️ Agent 2 — Narrator")

can_narrate = (
    st.session_state.current_story is not None
    and not st.session_state.current_story["was_refused"]
)

if not can_narrate:
    st.caption("Waiting for a story from Agent 1...")
else:
    narrate_clicked = st.button("🔊 Narrate this story (max 40s)", use_container_width=True)
    if narrate_clicked:
        with st.spinner("The Narrator is recording..."):
            story = st.session_state.current_story
            narration = narrate_story(
                story_id=story["id"],
                story_text=story["text"],
                language=story["language"],
            )
            if narration.success:
                db.save_narration(
                    story_id=story["id"],
                    audio_path=narration.audio_path,
                    voice=narration.voice,
                    duration_seconds=narration.duration_seconds,
                )
                st.session_state.current_audio = {
                    "path": narration.audio_path,
                    "duration": narration.duration_seconds,
                    "voice": narration.voice,
                    "engine": narration.engine_used,
                }
            else:
                st.error(f"Narration failed: {narration.error}")

    if st.session_state.current_audio:
        audio = st.session_state.current_audio
        st.audio(audio["path"])
        st.caption(f"Voice: {audio['voice']} ({audio['engine']}) · {audio['duration']}s / 40s cap")

st.markdown("</div>", unsafe_allow_html=True)

st.markdown(
    '<div style="text-align:center;" class="nl-dim">Powered by ⚡ Groq · 🕸️ CrewAI</div>',
    unsafe_allow_html=True,
)

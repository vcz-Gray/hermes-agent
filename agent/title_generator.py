"""Auto-generate short session titles from the first user/assistant exchange.

Runs asynchronously after the first response is delivered so it never
adds latency to the user-facing reply.
"""

import logging
import threading
import unicodedata
from typing import Callable, Optional

from agent.auxiliary_client import call_llm

logger = logging.getLogger(__name__)

# Callback signature: (task_name, exception) -> None. Used to surface
# auxiliary failures to the user through AIAgent._emit_auxiliary_failure
# so silent-drops (e.g. OpenRouter 402 exhausting the fallback chain)
# become visible instead of piling up as NULL session titles.
FailureCallback = Callable[[str, BaseException], None]
TitleCallback = Callable[[str], None]

_TITLE_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation that starts with the "
    "following exchange. The title should capture the main topic or intent. "
    "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
)


def _strip_leading_symbol_prefix(text: str) -> str:
    """Remove decorative leading emoji/symbol prefixes from generated titles."""
    cleaned = (text or "").strip()
    while cleaned:
        first = cleaned[0]
        category = unicodedata.category(first)
        if category.startswith(("S", "M")) or first in {"-", "–", "—", ":", ";", ",", ".", "!", "?", "~"}:
            cleaned = cleaned[1:].lstrip()
            continue
        break
    return cleaned


def _select_discord_thread_title_emoji(text: str) -> str:
    """Pick one compact emoji for a Discord thread title."""
    lowered = (text or "").lower()
    if any(token in text for token in ("중단", "타임아웃", "장애")):
        return "⚠️"
    if any(token in text for token in ("버그", "오류", "에러", "실패", "로그인 문제")):
        return "🐛"
    if any(token in text for token in ("배포", "릴리즈", "출시", "런칭")):
        return "🚀"
    if any(token in text for token in ("스레드", "쓰레드", "제목")) or "thread" in lowered:
        return "🧵"
    if any(token in text for token in ("문서", "가이드", "정리", "요약")):
        return "📝"
    if any(token in text for token in ("설정", "구성", "인증서")) or any(
        token in lowered for token in ("config", "configuration", "ssl", "cert")
    ):
        return "⚙️"
    if any(token in text for token in ("보안", "권한", "인증", "인가")) or "oauth" in lowered:
        return "🔐"
    return "💬"


_DISCORD_THREAD_TITLE_PROMPT = (
    "Generate a Discord thread title for the user's opening message. "
    "Hermes is creating or renaming the thread before replying. "
    "Return ONLY the final thread title, nothing else. "
    "Use exactly one relevant emoji at the start, then one space, then a short specific title. "
    "Capture the actual task or problem, not a generic decorative label. "
    "Prefer Korean when the user wrote in Korean. Avoid request-style wording like 해줘/부탁해/봐줘. "
    "Do not echo raw mentions, bot names, markdown, or quotes. "
    "Be as compact as possible while preserving the key noun. Aim for 2-5 words or 8-24 Korean characters after the emoji. "
    "Good examples: ⚠️ 스트리밍 중단, 🧵 스레드 제목 개선, ⚙️ SSL 인증서 갱신"
)


def generate_title(
    user_message: str,
    assistant_response: str,
    timeout: float = 30.0,
    failure_callback: Optional[FailureCallback] = None,
    main_runtime: dict = None,
) -> Optional[str]:
    """Generate a session title from the first exchange.

    Uses the main runtime's model when available, falling back to the
    auxiliary LLM client (cheapest/fastest available model).
    Returns the title string or None on failure.

    ``failure_callback`` is invoked with ``(task, exception)`` when the
    auxiliary call raises — the caller typically wires this to
    ``AIAgent._emit_auxiliary_failure`` so the user sees a warning instead
    of silently accumulating untitled sessions.
    """
    # Truncate long messages to keep the request small
    user_snippet = user_message[:500] if user_message else ""
    assistant_snippet = assistant_response[:500] if assistant_response else ""

    messages = [
        {"role": "system", "content": _TITLE_PROMPT},
        {"role": "user", "content": f"User: {user_snippet}\n\nAssistant: {assistant_snippet}"},
    ]

    try:
        response = call_llm(
            task="title_generation",
            messages=messages,
            max_tokens=500,
            temperature=0.3,
            timeout=timeout,
            main_runtime=main_runtime,
        )
        title = (response.choices[0].message.content or "").strip()
        # Clean up: remove quotes, trailing punctuation, prefixes like "Title: "
        title = title.strip('"\'')
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        # Enforce reasonable length
        if len(title) > 80:
            title = title[:77] + "..."
        return title if title else None
    except Exception as e:
        # Log at WARNING so this shows up in agent.log without debug mode.
        # Full detail at debug level for operators who need the stack.
        logger.warning("Title generation failed: %s", e)
        logger.debug("Title generation traceback", exc_info=True)
        if failure_callback is not None:
            try:
                failure_callback("title generation", e)
            except Exception:
                logger.debug("Title generation failure_callback raised", exc_info=True)
        return None


def generate_discord_thread_title(
    user_message: str,
    timeout: float = 20.0,
    failure_callback: Optional[FailureCallback] = None,
    main_runtime: dict = None,
) -> Optional[str]:
    """Generate a Discord thread title from the user's opening message.

    This is a synchronous helper intended for gateway paths that must decide the
    thread title *before* the first response is sent. The caller may wrap it in
    ``asyncio.to_thread`` from async code.
    """
    user_snippet = user_message[:500] if user_message else ""
    messages = [
        {"role": "system", "content": _DISCORD_THREAD_TITLE_PROMPT},
        {"role": "user", "content": user_snippet},
    ]

    try:
        response = call_llm(
            task="title_generation",
            messages=messages,
            max_tokens=120,
            temperature=0.2,
            timeout=timeout,
            main_runtime=main_runtime,
        )
        title = (response.choices[0].message.content or "").strip()
        title = title.strip('"\'')
        if title.lower().startswith("title:"):
            title = title[6:].strip()
        title = " ".join(title.split())
        compact_title = _strip_leading_symbol_prefix(title)
        if not compact_title:
            return None
        emoji = _select_discord_thread_title_emoji(f"{user_snippet} {compact_title}")
        max_title_len = 30
        prefix = f"{emoji} "
        allowed = max_title_len - len(prefix)
        if len(compact_title) > allowed:
            compact_title = compact_title[: allowed - 1].rstrip() + "…"
        return f"{prefix}{compact_title}"
    except Exception as e:
        logger.warning("Discord thread title generation failed: %s", e)
        logger.debug("Discord thread title generation traceback", exc_info=True)
        if failure_callback is not None:
            try:
                failure_callback("discord thread title generation", e)
            except Exception:
                logger.debug("Discord thread title failure_callback raised", exc_info=True)
        return None


def auto_title_session(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
    failure_callback: Optional[FailureCallback] = None,
    main_runtime: dict = None,
    title_callback: Optional[TitleCallback] = None,
) -> None:
    """Generate and set a session title if one doesn't already exist.

    Called in a background thread after the first exchange completes.
    Silently skips if:
    - session_db is None
    - session already has a title (user-set or previously auto-generated)
    - title generation fails
    """
    if not session_db or not session_id:
        return

    # Check if title already exists (user may have set one via /title before first response)
    try:
        existing = session_db.get_session_title(session_id)
        if existing:
            return
    except Exception:
        return

    title = generate_title(
        user_message, assistant_response, failure_callback=failure_callback, main_runtime=main_runtime
    )
    if not title:
        return

    try:
        session_db.set_session_title(session_id, title)
        logger.debug("Auto-generated session title: %s", title)
        if title_callback is not None:
            try:
                title_callback(title)
            except Exception:
                logger.debug("Auto-title callback failed", exc_info=True)
    except Exception as e:
        logger.debug("Failed to set auto-generated title: %s", e)


def maybe_auto_title(
    session_db,
    session_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list,
    failure_callback: Optional[FailureCallback] = None,
    main_runtime: dict = None,
    title_callback: Optional[TitleCallback] = None,
) -> None:
    """Fire-and-forget title generation after the first exchange.

    Only generates a title when:
    - This appears to be the first user→assistant exchange
    - No title is already set
    """
    if not session_db or not session_id or not user_message or not assistant_response:
        return

    # Count user messages in history to detect first exchange.
    # conversation_history includes the exchange that just happened,
    # so for a first exchange we expect exactly 1 user message
    # (or 2 counting system). Be generous: generate on first 2 exchanges.
    user_msg_count = sum(1 for m in (conversation_history or []) if m.get("role") == "user")
    if user_msg_count > 2:
        return

    thread = threading.Thread(
        target=auto_title_session,
        args=(session_db, session_id, user_message, assistant_response),
        kwargs={
            "failure_callback": failure_callback,
            "main_runtime": main_runtime,
            "title_callback": title_callback,
        },
        daemon=True,
        name="auto-title",
    )
    thread.start()

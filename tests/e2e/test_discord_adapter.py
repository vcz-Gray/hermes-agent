"""Minimal e2e tests for Discord mention stripping + /command detection.

Covers the fix for slash commands not being recognized when sent via
@mention in a channel, especially after auto-threading.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.platforms.discord.adapter import (
    _apply_discord_thread_title_prefix,
    _derive_auto_thread_title,
    _extract_bracketed_discord_thread_title_prefix,
    _normalize_discord_thread_title_prefix,
    _strip_leading_bracketed_discord_thread_title_prefix,
    discord,
)
from tests.e2e.conftest import (
    BOT_USER_ID,
    E2E_MESSAGE_SETTLE_DELAY,
    get_response_text,
    make_discord_message,
    make_fake_dm_channel,
    make_fake_text_channel,
    make_fake_thread,
)

pytestmark = pytest.mark.asyncio


async def dispatch(adapter, msg):
    await adapter._handle_message(msg)
    await asyncio.sleep(E2E_MESSAGE_SETTLE_DELAY)


class TestMentionStrippedCommandDispatch:
    async def test_mention_then_command(self, discord_adapter, bot_user):
        """<@BOT> /help → mention stripped, /help dispatched."""
        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> /help",
            mentions=[bot_user],
        )
        await dispatch(discord_adapter, msg)
        response = get_response_text(discord_adapter)
        assert response is not None
        assert "/new" in response

    async def test_nickname_mention_then_command(self, discord_adapter, bot_user):
        """<@!BOT> /help → nickname mention also stripped, /help works."""
        msg = make_discord_message(
            content=f"<@!{BOT_USER_ID}> /help",
            mentions=[bot_user],
        )
        await dispatch(discord_adapter, msg)
        response = get_response_text(discord_adapter)
        assert response is not None
        assert "/new" in response

    async def test_text_before_command_not_detected(self, discord_adapter, bot_user):
        """'<@BOT> something else /help' → mention stripped, but 'something else /help'
        doesn't start with / so it's treated as text, not a command."""
        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> something else /help",
            mentions=[bot_user],
        )
        await dispatch(discord_adapter, msg)
        # Message is accepted (not dropped by mention gate), but since it doesn't
        # start with / it's routed as text — no command output, and no agent in this
        # mock setup means no send call either.
        response = get_response_text(discord_adapter)
        assert response is None or "/new" not in response

    async def test_no_mention_in_channel_dropped(self, discord_adapter):
        """Message without @mention in server channel → silently dropped."""
        msg = make_discord_message(content="/help", mentions=[])
        await dispatch(discord_adapter, msg)
        assert get_response_text(discord_adapter) is None

    async def test_dm_no_mention_needed(self, discord_adapter):
        """DMs don't require @mention — /help works directly."""
        dm = make_fake_dm_channel()
        msg = make_discord_message(content="/help", channel=dm, mentions=[])
        await dispatch(discord_adapter, msg)
        response = get_response_text(discord_adapter)
        assert response is not None
        assert "/new" in response


class TestAutoThreadTitleCleanup:
    def test_strips_bot_invocation_noise(self):
        title = _derive_auto_thread_title(
            f"<@{BOT_USER_ID}> 헤르메스야, 이 디스코드 채널에서 새로운 쓰레드 제목 자연스럽게 정리해줘"
        )
        assert title == "🧵 제목 정리"

    def test_uses_first_line_and_humanizes_custom_emoji(self):
        title = _derive_auto_thread_title(
            f"<@{BOT_USER_ID}> <:sparkles:12345> Hermes, 배포 체크 부탁해\n상세 내용은 아래 참고"
        )
        assert title == "🚀 배포 체크"

    def test_strips_request_ending_for_bug_report_style_titles(self):
        title = _derive_auto_thread_title("여기서 로그인 버그 봐줘")
        assert title == "🐛 로그인 버그"


class TestThreadTitlePrefixHelpers:
    def test_normalizes_bare_or_bracketed_project_prefix(self):
        assert _normalize_discord_thread_title_prefix("프로젝트A") == "[프로젝트A]"
        assert _normalize_discord_thread_title_prefix(" [프로젝트A] ") == "[프로젝트A]"

    def test_extracts_bracketed_prefix_from_existing_title_or_message(self):
        assert _extract_bracketed_discord_thread_title_prefix("[프로젝트A] 🐛 로그인 버그") == "[프로젝트A]"
        assert _extract_bracketed_discord_thread_title_prefix("", "[프로젝트B] 배포 체크 부탁해") == "[프로젝트B]"

    def test_strips_leading_bracketed_prefix_before_generating_suffix(self):
        assert _strip_leading_bracketed_discord_thread_title_prefix("[프로젝트A] 로그인 버그 봐줘") == "로그인 버그 봐줘"

    def test_applies_project_prefix_without_duplication(self):
        assert _apply_discord_thread_title_prefix("🐛 로그인 버그", "프로젝트A") == "[프로젝트A] 🐛 로그인 버그"
        assert _apply_discord_thread_title_prefix("[프로젝트A] 🐛 로그인 버그", "[프로젝트A]") == "[프로젝트A] 🐛 로그인 버그"


class TestAutoThreadingPreservesCommand:
    async def test_command_detected_after_auto_thread(self, discord_adapter, bot_user, monkeypatch):
        """@mention /help in channel with auto-thread → thread created AND command dispatched."""
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        fake_thread = make_fake_thread(thread_id=90001, name="help")
        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> /help",
            mentions=[bot_user],
        )

        # Simulate discord.py restoring the original raw content (with mention)
        # after create_thread(), which undoes any prior mention stripping.
        original_content = msg.content

        async def clobber_content(**kwargs):
            msg.content = original_content
            return fake_thread

        msg.create_thread = AsyncMock(side_effect=clobber_content)
        discord_adapter._generate_thread_title = AsyncMock(return_value="help")
        await dispatch(discord_adapter, msg)

        msg.create_thread.assert_awaited_once()
        response = get_response_text(discord_adapter)
        assert response is not None
        assert "/new" in response

    async def test_auto_thread_uses_cleaned_human_title(self, discord_adapter, bot_user, monkeypatch):
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        fake_thread = make_fake_thread(thread_id=90003, name="clean-title")
        msg = make_discord_message(
            content=(
                f"<@{BOT_USER_ID}> 헤르메스야, <:sparkles:12345> 이 채널에서 새 쓰레드 제목 좀 자연스럽게 정리해줘"
            ),
            mentions=[bot_user],
        )
        msg.create_thread = AsyncMock(return_value=fake_thread)
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 스레드 제목 개선")

        await dispatch(discord_adapter, msg)

        msg.create_thread.assert_awaited_once_with(
            name="🧵 스레드 제목 개선",
            auto_archive_duration=1440,
        )


class TestAutoThreadingFromReplyReference:
    async def test_reply_mention_threads_from_referenced_original(self, discord_adapter, bot_user, monkeypatch):
        """Reply + @mention in channel → thread is created from the referenced original message."""
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        channel = make_fake_text_channel()
        fake_thread = make_fake_thread(thread_id=90002, name="source-thread", parent=channel)

        original = make_discord_message(content="원문 질문", channel=channel)
        original.thread = None
        original.create_thread = AsyncMock(return_value=fake_thread)

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> 이 원문 기준으로 이어서 봐줘",
            channel=channel,
            mentions=[bot_user],
        )
        msg.type = discord.MessageType.reply
        msg.reference = SimpleNamespace(message_id=original.id, resolved=original)
        msg.create_thread = AsyncMock()
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 원문 질문")

        await dispatch(discord_adapter, msg)

        original.create_thread.assert_awaited_once()
        msg.create_thread.assert_not_called()
        assert str(fake_thread.id) in discord_adapter._threads


class TestExistingThreadRetitle:
    async def test_first_message_in_existing_thread_retitles_before_response(self, discord_adapter, bot_user, monkeypatch):
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        thread = make_fake_thread(thread_id=90004, name="long generic thread")
        thread.edit = AsyncMock()

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> 헤르메스야, 이 디스코드 채널에서 새로운 쓰레드 제목 자연스럽게 정리해줘",
            channel=thread,
            mentions=[bot_user],
        )
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 스레드 제목 개선")

        await dispatch(discord_adapter, msg)

        thread.edit.assert_awaited_once_with(
            name="🧵 스레드 제목 개선",
            reason="Hermes normalized thread title from first message",
        )
        assert str(thread.id) in discord_adapter._threads

    async def test_followup_message_in_existing_thread_does_not_retitle_again(self, discord_adapter, bot_user, monkeypatch):
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        thread = make_fake_thread(thread_id=90014, name="🧵 스레드 제목 개선")
        thread.edit = AsyncMock()
        discord_adapter._threads.mark(str(thread.id))

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> 후속 질문이야",
            channel=thread,
            mentions=[bot_user],
        )
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 다른 제목")

        await dispatch(discord_adapter, msg)

        thread.edit.assert_not_awaited()
        discord_adapter._generate_thread_title.assert_not_awaited()

    async def test_existing_participated_thread_still_retitles_when_name_is_stale(self, discord_adapter, bot_user, monkeypatch):
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        thread = make_fake_thread(thread_id=90015, name="old thread name")
        thread.edit = AsyncMock()
        discord_adapter._threads.mark(str(thread.id))

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> 헤르메스야, 이 디스코드 채널에서 새로운 쓰레드 제목 자연스럽게 정리해줘",
            channel=thread,
            mentions=[bot_user],
        )
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 스레드 제목 개선")

        await dispatch(discord_adapter, msg)

        thread.edit.assert_awaited_once_with(
            name="🧵 스레드 제목 개선",
            reason="Hermes normalized thread title from first message",
        )

    async def test_existing_participated_stale_thread_respects_retitle_cooldown(self, discord_adapter, bot_user, monkeypatch):
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        monkeypatch.setenv("DISCORD_EXISTING_THREAD_RETITLE_COOLDOWN_SECONDS", "21600")
        thread = make_fake_thread(thread_id=90016, name="old thread name")
        thread.edit = AsyncMock()
        discord_adapter._threads.mark(str(thread.id))
        discord_adapter._thread_retitle_timestamps.set(str(thread.id), str(time.time()))

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> 후속 질문이야",
            channel=thread,
            mentions=[bot_user],
        )
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 다른 제목")

        await dispatch(discord_adapter, msg)

        thread.edit.assert_not_awaited()
        discord_adapter._generate_thread_title.assert_not_awaited()


class TestProjectCommand:
    async def test_sets_thread_local_project_prefix_and_retitles_thread(self, discord_adapter, bot_user):
        thread = make_fake_thread(thread_id=90005, name="🐛 로그인 버그")
        thread.edit = AsyncMock()
        discord_adapter._threads.mark(str(thread.id))

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> /project 프로젝트A",
            channel=thread,
            mentions=[bot_user],
        )

        await dispatch(discord_adapter, msg)

        thread.edit.assert_awaited_once_with(
            name="[프로젝트A] 🐛 로그인 버그",
            reason="Hermes set thread-local project prefix",
        )
        assert get_response_text(discord_adapter) == "thread project prefix 설정됨 → [프로젝트A]"

    async def test_shows_current_thread_local_project_prefix(self, discord_adapter, bot_user):
        thread = make_fake_thread(thread_id=90006, name="[프로젝트A] 🐛 로그인 버그")
        discord_adapter._threads.mark(str(thread.id))
        discord_adapter._thread_title_prefixes.set(str(thread.id), "[프로젝트A]")

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> /project",
            channel=thread,
            mentions=[bot_user],
        )

        await dispatch(discord_adapter, msg)

        assert get_response_text(discord_adapter) == "현재 thread project prefix: [프로젝트A]"

    async def test_clears_thread_local_project_prefix_and_keeps_suffix(self, discord_adapter, bot_user):
        thread = make_fake_thread(thread_id=90007, name="[프로젝트A] 🐛 로그인 버그")
        thread.edit = AsyncMock()
        discord_adapter._threads.mark(str(thread.id))
        discord_adapter._thread_title_prefixes.set(str(thread.id), "[프로젝트A]")

        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> /project off",
            channel=thread,
            mentions=[bot_user],
        )

        await dispatch(discord_adapter, msg)

        thread.edit.assert_awaited_once_with(
            name="🐛 로그인 버그",
            reason="Hermes cleared thread-local project prefix",
        )
        assert get_response_text(discord_adapter) == "thread project prefix 해제됨 → 🐛 로그인 버그"

    async def test_opening_message_prefix_is_used_for_new_auto_thread(self, discord_adapter, bot_user, monkeypatch):
        monkeypatch.setenv("DISCORD_AUTO_THREAD", "true")
        fake_thread = make_fake_thread(thread_id=90008, name="[프로젝트B] 🚀 배포 체크")
        msg = make_discord_message(
            content=f"<@{BOT_USER_ID}> [프로젝트B] 배포 체크 부탁해",
            mentions=[bot_user],
        )
        msg.create_thread = AsyncMock(return_value=fake_thread)

        await dispatch(discord_adapter, msg)

        msg.create_thread.assert_awaited_once_with(
            name="[프로젝트B] 🚀 배포 체크",
            auto_archive_duration=1440,
        )

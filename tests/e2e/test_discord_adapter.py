"""Minimal e2e tests for Discord mention stripping + /command detection.

Covers the fix for slash commands not being recognized when sent via
@mention in a channel, especially after auto-threading.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.platforms.discord import _derive_auto_thread_title, discord
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
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 제목 정리")

        await dispatch(discord_adapter, msg)

        msg.create_thread.assert_awaited_once_with(
            name="🧵 제목 정리",
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
        discord_adapter._generate_thread_title = AsyncMock(return_value="🧵 제목 정리")

        await dispatch(discord_adapter, msg)

        thread.edit.assert_awaited_once_with(
            name="🧵 제목 정리",
            reason="Hermes normalized thread title from first message",
        )
        assert str(thread.id) in discord_adapter._threads

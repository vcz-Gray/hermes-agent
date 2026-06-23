"""Gateway /sync command tests."""

from unittest.mock import patch, MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/sync", platform=Platform.TELEGRAM, user_id="12345", chat_id="67890"):
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    return runner


def test_sync_exposed_to_gateway_help():
    from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, gateway_help_lines

    assert "sync" in GATEWAY_KNOWN_COMMANDS
    assert any("/sync" in line for line in gateway_help_lines())


class TestHandleSyncCommand:
    @pytest.mark.asyncio
    async def test_managed_install_returns_package_manager_guidance(self, monkeypatch):
        runner = _make_runner()
        event = _make_event()
        monkeypatch.setenv("HERMES_MANAGED", "homebrew")

        result = await runner._handle_sync_command(event)

        assert "managed by Homebrew" in result
        assert "sync Hermes Agent" in result

    @pytest.mark.asyncio
    async def test_no_git_directory(self, tmp_path):
        runner = _make_runner()
        event = _make_event()
        fake_root = tmp_path / "project"
        fake_root.mkdir()
        (fake_root / "gateway").mkdir(parents=True)
        (fake_root / "gateway" / "run.py").touch()
        fake_file = str(fake_root / "gateway" / "run.py")

        with patch("gateway.run.__file__", fake_file):
            result = await runner._handle_sync_command(event)

        assert "Not a git repository" in result

    @pytest.mark.asyncio
    async def test_runs_hermes_sync_and_returns_output(self, tmp_path):
        runner = _make_runner()
        event = _make_event()
        fake_root = tmp_path / "project"
        fake_root.mkdir()
        (fake_root / ".git").mkdir()
        (fake_root / "gateway").mkdir()
        (fake_root / "gateway" / "run.py").touch()
        fake_file = str(fake_root / "gateway" / "run.py")

        cp = MagicMock(returncode=0, stdout="Synced branch 'viewcommz-main' from upstream/main.\nNext step: run 'hermes update'.\n", stderr="")

        with patch("gateway.run.__file__", fake_file), \
             patch("gateway.run._resolve_hermes_bin", return_value=["/usr/bin/hermes"]), \
             patch("subprocess.run", return_value=cp) as mock_run:
            result = await runner._handle_sync_command(event)

        argv = mock_run.call_args.args[0]
        assert argv == ["/usr/bin/hermes", "sync"]
        assert "Synced branch 'viewcommz-main'" in result
        assert "run 'hermes update'" in result

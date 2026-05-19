from __future__ import annotations

import subprocess
from argparse import Namespace

import pytest


def _cp(cmd: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def test_cmd_sync_happy_path(monkeypatch, capsys):
    from hermes_cli import sync_cmd as mod

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _cp(cmd, stdout="/repo\n")
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return _cp(cmd, stdout="")
        if cmd[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
            return _cp(cmd, stdout="viewcommz-main\n")
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return _cp(cmd, stdout="git@github.com:vcz-Gray/hermes-agent.git\n")
        if cmd[:4] == ["git", "remote", "get-url", "upstream"]:
            return _cp(cmd, stdout="git@github.com:NousResearch/hermes-agent.git\n")
        if cmd[:4] == ["git", "symbolic-ref", "refs/remotes/upstream/HEAD"]:
            return _cp(cmd, stdout="refs/remotes/upstream/main\n")
        if cmd[:3] == ["git", "fetch", "upstream"]:
            return _cp(cmd)
        if cmd[:2] == ["git", "rebase"]:
            return _cp(cmd)
        if cmd[:2] == ["git", "push"]:
            return _cp(cmd)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    mod.cmd_sync(Namespace())

    assert ["git", "fetch", "upstream"] in calls
    assert ["git", "rebase", "upstream/main"] in calls
    assert ["git", "push", "origin", "viewcommz-main"] in calls
    out = capsys.readouterr().out
    assert "Synced branch 'viewcommz-main' from upstream/main" in out
    assert "run 'hermes update'" in out


def test_cmd_sync_refuses_dirty_worktree(monkeypatch):
    from hermes_cli import sync_cmd as mod

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _cp(cmd, stdout="/repo\n")
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return _cp(cmd, stdout=" M hermes_cli/main.py\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="working tree is not clean"):
        mod.cmd_sync(Namespace())


def test_cmd_sync_refuses_missing_upstream(monkeypatch):
    from hermes_cli import sync_cmd as mod

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _cp(cmd, stdout="/repo\n")
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return _cp(cmd, stdout="")
        if cmd[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
            return _cp(cmd, stdout="viewcommz-main\n")
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return _cp(cmd, stdout="git@github.com:vcz-Gray/hermes-agent.git\n")
        if cmd[:4] == ["git", "remote", "get-url", "upstream"]:
            return _cp(cmd, returncode=2, stderr="No such remote 'upstream'\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="missing git remote 'upstream'"):
        mod.cmd_sync(Namespace())


def test_cmd_sync_refuses_detached_head(monkeypatch):
    from hermes_cli import sync_cmd as mod

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _cp(cmd, stdout="/repo\n")
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return _cp(cmd, stdout="")
        if cmd[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
            return _cp(cmd, returncode=1, stderr="detached HEAD\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="detached HEAD"):
        mod.cmd_sync(Namespace())


def test_detect_upstream_default_branch_falls_back_to_main(monkeypatch):
    from hermes_cli import sync_cmd as mod

    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["git", "symbolic-ref", "refs/remotes/upstream/HEAD"]:
            return _cp(cmd, returncode=1, stderr="fatal: ref refs/remotes/upstream/HEAD is not a symbolic ref\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert mod.detect_upstream_default_branch() == "main"


def test_sync_registered_in_command_metadata():
    from hermes_cli.commands import COMMAND_REGISTRY

    assert any(cmd.name == "sync" for cmd in COMMAND_REGISTRY)



def test_sync_reserved_as_top_level_profile_subcommand():
    from hermes_cli.profiles import _HERMES_SUBCOMMANDS

    assert "sync" in _HERMES_SUBCOMMANDS

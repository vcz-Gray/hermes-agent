from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SyncError(RuntimeError):
    """Recoverable sync failure surfaced to the CLI as SystemExit."""


def run_git(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command inside the Hermes repo and return the completed process."""
    cmd = ["git", *args]
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"git {' '.join(args)} failed"
        raise SyncError(detail)
    return proc


def ensure_git_repo() -> None:
    try:
        run_git(["rev-parse", "--show-toplevel"])
    except SyncError as exc:
        raise SyncError("Refusing to sync: Hermes checkout is not a git repository.") from exc


def assert_clean_worktree() -> None:
    # Allow purely local untracked outputs (reports, state files, scratch docs)
    # so fork sync is not blocked by repo-adjacent runtime noise. We still
    # require tracked files and the index to be clean before merging/pushing.
    proc = run_git(["status", "--porcelain", "--untracked-files=no"])
    if proc.stdout.strip():
        raise SyncError(
            "Refusing to sync: tracked changes are present. Commit, stash, or discard them first."
        )


def get_current_branch() -> str:
    proc = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    branch = (proc.stdout or "").strip()
    if proc.returncode != 0 or not branch:
        raise SyncError("Refusing to sync: detached HEAD. Check out a branch first.")
    return branch


def assert_remote_exists(name: str) -> str:
    proc = run_git(["remote", "get-url", name], check=False)
    if proc.returncode != 0:
        raise SyncError(
            f"Refusing to sync: missing git remote '{name}'. Configure {name} before running 'hermes sync'."
        )
    return (proc.stdout or "").strip()


def detect_upstream_default_branch() -> str:
    proc = run_git(["symbolic-ref", "refs/remotes/upstream/HEAD"], check=False)
    ref = (proc.stdout or "").strip()
    if proc.returncode == 0 and ref.startswith("refs/remotes/upstream/"):
        return ref.rsplit("/", 1)[-1]
    return "main"


def perform_sync() -> tuple[str, str]:
    ensure_git_repo()
    assert_clean_worktree()
    branch = get_current_branch()
    assert_remote_exists("origin")
    assert_remote_exists("upstream")
    run_git(["fetch", "upstream"])
    upstream_branch = detect_upstream_default_branch()
    try:
        run_git(["merge", "--no-edit", f"upstream/{upstream_branch}"])
    except SyncError as exc:
        raise SyncError(
            f"Sync stopped: merge from upstream/{upstream_branch} failed. Resolve conflicts manually, then rerun 'hermes sync'."
        ) from exc
    try:
        run_git(["push", "origin", branch])
    except SyncError as exc:
        raise SyncError(
            f"Sync stopped: push to origin/{branch} failed. Fix the remote state, then rerun 'hermes sync'."
        ) from exc
    return branch, upstream_branch


def cmd_sync(_args) -> None:
    """Sync the current Hermes fork branch from upstream and push it to origin."""
    try:
        branch, upstream_branch = perform_sync()
    except SyncError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Synced branch '{branch}' from upstream/{upstream_branch}.")
    print("Next step: run 'hermes update'.")


__all__ = [
    "PROJECT_ROOT",
    "SyncError",
    "assert_clean_worktree",
    "assert_remote_exists",
    "cmd_sync",
    "detect_upstream_default_branch",
    "ensure_git_repo",
    "get_current_branch",
    "perform_sync",
    "run_git",
]

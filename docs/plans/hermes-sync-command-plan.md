# Hermes Sync Command Plan

> For Hermes: implement `hermes sync` as a fork-aware upstream sync command, then use `hermes sync && hermes update` as the operator workflow.

**Goal:** Add a first-class `hermes sync` CLI command that updates the current Hermes checkout from an upstream repository into the user's fork-tracking branch, so operators can run `hermes sync` before `hermes update`.

**Architecture:** Keep sync and update separate. `hermes sync` performs git remote/branch validation plus upstream fetch + rebase/merge + optional push to fork. `hermes update` remains a pure updater that consumes the already-synced branch.

**Tech Stack:** Python CLI (`hermes_cli/main.py`, `cli.py`, `hermes_cli/commands.py`), subprocess/git integration, pytest.

---

## Product decisions locked in

1. User-facing command name is **`hermes sync`**.
2. Primary workflow is **manual sequential execution**:
   - `hermes sync`
   - `hermes update`
3. `hermes sync` should target a **fork workflow**, not generic repo mirroring.
4. `hermes update` should **not** absorb sync logic.
5. First version should optimize for **clarity and safety** over full automation.

---

## Scope for v1

### In scope
- New top-level CLI subcommand: `hermes sync`
- Detect and validate git remotes/branch assumptions
- Fetch upstream
- Sync current branch from upstream default branch (prefer rebase, or documented merge fallback)
- Optional push to origin/fork after successful sync
- Clear operator-facing output describing what changed
- Tests for clean sync, missing remotes, dirty worktree, and divergence/failure paths
- Docs/help text updates

### Out of scope for v1
- Gateway slash command `/sync`
- Automatic invocation from `hermes update`
- Background/cron sync automation
- Multi-branch policy engine
- Interactive conflict resolution UX beyond surfacing the git failure clearly

---

## Assumed operator workflow

### Happy path
1. Operator works in fork-backed Hermes checkout.
2. Operator runs `hermes sync`.
3. Command validates:
   - git repo exists
   - `origin` exists
   - `upstream` exists
   - current branch is an allowed sync branch (for example `main` or `viewcommz-main`)
   - working tree is clean or policy allows safe continuation
4. Command fetches upstream and rebases local branch onto upstream default branch.
5. Command pushes synced branch to fork (`origin`).
6. Operator runs `hermes update`.

### Failure path
- If repo is dirty, required remotes are missing, branch policy is violated, or git sync fails, `hermes sync` exits non-zero with an actionable message and **does not** continue into update.

---

## Recommended sync semantics

### Default behavior
Use:
- `git fetch upstream`
- `git rebase upstream/<default-branch>`
- `git push origin <current-branch>`

### Why rebase by default
- Cleaner history on a personal fork branch
- Easier to reason about ahead/behind relative to upstream
- Keeps `hermes update` consuming a linear branch

### Merge fallback
If rebase proves too painful in practice, allow a follow-up enhancement for:
- `hermes sync --merge`

Do **not** add both modes in v1 unless implementation cost is trivial. Keep v1 simple.

---

## Config / policy decisions

v1 should avoid over-design. Use this policy order:

1. **Current branch** is the sync target.
2. Require both remotes:
   - `origin` = fork
   - `upstream` = canonical source
3. Infer upstream default branch from git metadata if possible; otherwise default to `main`.
4. Refuse to run on dirty worktrees unless an explicit force flag is provided in a later version.

Optional future config knobs (not required for v1):
- `sync.upstream_remote`
- `sync.origin_remote`
- `sync.default_branch`
- `sync.push_after_sync`

---

## Files likely to change

### CLI registration
- `hermes_cli/main.py`
  - add argparse subcommand for `sync`
- `hermes_cli/profiles.py`
  - include `sync` in `_HERMES_SUBCOMMANDS` so profile wrappers understand it

### Slash/help registry
- `hermes_cli/commands.py`
  - add command metadata so `/help` / command registry stays consistent if CLI slash support is desired later

### Command implementation
- likely new file: `hermes_cli/sync_cmd.py`
  - keep sync logic isolated from update logic
  - helper functions for git command execution, remote validation, branch detection, cleanliness checks

### CLI dispatcher
- `cli.py`
  - wire command handler if interactive slash-style CLI access is desired
  - if `hermes sync` is only a top-level subcommand, keep slash handling minimal or skip in v1

### Docs
- `README.md`
- Hermes docs page(s) for CLI commands / operator workflows
- possibly `RELEASE_*.md` only if bundled in a release note later

### Tests
- likely new tests under:
  - `tests/hermes_cli/test_sync_cmd.py`
  - plus parser/registration coverage if needed

---

## Implementation plan

### Task 1: Confirm command surface and UX text
**Objective:** Freeze v1 behavior before code changes.

**Decisions to encode:**
- command name: `hermes sync`
- no implicit update chaining
- success output should tell the user to run `hermes update` next
- failure output should explain exactly which git prerequisite is missing

**Verification:**
- brief spec note added to plan/doc before implementation starts

---

### Task 2: Add argparse subcommand
**Objective:** Make `hermes sync` parse as a real top-level CLI command.

**Files:**
- Modify: `hermes_cli/main.py`
- Modify: `hermes_cli/profiles.py`
- Test: parser-related tests if present

**Implementation notes:**
- Add `sync` subparser with short description like:
  - `Sync current Hermes fork branch from upstream before running update`
- Decide minimal flags for v1; ideally none unless needed
- Ensure profile wrapper recognizes `sync`

**Verification:**
- `hermes sync --help` shows expected help text
- profile wrapper accepts `hermes --profile <name> sync`

---

### Task 3: Implement sync command module
**Objective:** Encapsulate git sync logic in a dedicated module.

**Files:**
- Create: `hermes_cli/sync_cmd.py`

**Functions to include:**
- `run_git(...)`
- `ensure_git_repo(...)`
- `get_current_branch(...)`
- `assert_clean_worktree(...)`
- `assert_remote_exists(name)`
- `detect_upstream_default_branch(...)`
- `perform_sync(...)`
- `cmd_sync(args)`

**Behavior:**
- verify repo
- verify remotes
- verify clean worktree
- fetch upstream
- rebase current branch onto `upstream/<default-branch>`
- push current branch to origin
- print concise summary

**Verification:**
- unit tests with mocked subprocesses for each branch of behavior

---

### Task 4: Wire subcommand dispatch
**Objective:** Connect parser output to command implementation.

**Files:**
- Modify: `hermes_cli/main.py`
- Possibly modify: any shared command router used by other top-level subcommands

**Behavior:**
- dispatch `sync` to `cmd_sync(args)`
- preserve existing exit-code conventions

**Verification:**
- `hermes sync` calls the sync module in test harness

---

### Task 5: Add safety/error handling
**Objective:** Make failures obvious and non-destructive.

**Cases to handle:**
- not in a git repo
- missing `origin`
- missing `upstream`
- detached HEAD
- dirty worktree
- upstream fetch failure
- rebase conflict/failure
- push failure

**Output principles:**
- say what failed
- say what the user should do next
- do not silently continue

**Verification:**
- test each failure path with exact message assertions where practical

---

### Task 6: Add tests
**Objective:** Lock the workflow down.

**Files:**
- Create: `tests/hermes_cli/test_sync_cmd.py`

**Minimum test cases:**
1. clean happy path: fetch + rebase + push
2. missing upstream remote
3. missing origin remote
4. dirty worktree refusal
5. detached HEAD refusal
6. upstream default branch fallback to `main`
7. rebase failure returns non-zero/actionable error
8. push failure returns non-zero/actionable error

**Verification command:**
- `python -m pytest tests/hermes_cli/test_sync_cmd.py -v -o 'addopts='`

---

### Task 7: Update docs
**Objective:** Teach the intended operator workflow.

**Files:**
- Modify: `README.md`
- Modify: CLI docs/reference pages as appropriate

**Doc text to add:**
- explain fork-based workflow
- explain required remotes
- show canonical usage:
  - `hermes sync`
  - `hermes update`
- explain why sync and update are separate

**Verification:**
- docs mention `hermes sync` and sequential workflow clearly

---

### Task 8: Optional polish after v1 passes
**Objective:** Only after core behavior is stable.

**Possible follow-ups:**
- `hermes refresh` wrapper alias for `sync && update`
- `--merge` mode
- configurable allowed branch names
- `--no-push` flag
- richer status summary (`ahead/behind before/after`)

These are explicitly **post-v1**.

---

## Acceptance criteria

1. `hermes sync` exists as a top-level CLI command.
2. Command fails fast when git prerequisites are missing.
3. Command fetches from `upstream`, syncs the current branch, and pushes to `origin` on success.
4. Command does not run `hermes update` automatically.
5. Docs clearly instruct users to run:
   - `hermes sync`
   - `hermes update`
6. Tests cover both happy path and core failure modes.

---

## Suggested operator messaging

### Success
- `Hermes branch synced from upstream. Next step: run 'hermes update'.`

### Dirty worktree
- `Refusing to sync: working tree is not clean. Commit, stash, or discard changes first.`

### Missing upstream
- `Refusing to sync: missing git remote 'upstream'. Configure upstream to point at the canonical Hermes repository.`

### Rebase conflict
- `Sync stopped: rebase onto upstream/main failed. Resolve conflicts manually, then rerun 'hermes sync'.`

---

## Recommended implementation order

1. parser + dispatch
2. sync module happy path
3. safety checks
4. tests
5. docs
6. optional polish

---

## Final recommendation

Implement **`hermes sync` as a narrow, explicit, fork-aware git sync command** and keep **`hermes update` unchanged**. The supported human workflow should be:

```bash
hermes sync
hermes update
```

This gives the user clear control, avoids overloading the updater, and matches the chosen operating model.
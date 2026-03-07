# Git & Workflow Steering

## Commit Style

Use gitmoji prefixes on ALL commits:

| Emoji | When |
|-------|------|
| ✨ | New feature |
| 🐛 | Bug fix |
| ♻️ | Refactor |
| 📝 | Docs |
| 🔧 | Config/infra |
| ⚡ | Performance |
| ✅ | Tests |
| 📦 | Dependencies |
| 🗑️ | Remove code |

Example: `🐛 fix vision server timeout on cold start`

## Branch Naming

`feat/<short-description>` or `fix/<issue-number>-<short-description>`

Examples:
- `feat/vision-launchd`
- `fix/5-threading-instrumentor-stall`

## Work Queue

GitHub issues are the task list. Check open issues before starting:
```bash
gh issue list --repo bagg3rs/naboo
```

When picking up an issue:
1. Create a branch: `git checkout -b fix/5-description`
2. Read the issue in full — acceptance criteria are your definition of done
3. Implement, test against the acceptance criteria
4. Commit with gitmoji

## Testing

Always run before considering a task done:
```bash
# Basic sanity
uv run python3 scripts/test_e2e.py "what is 2 plus 2?"
# → should complete in ≤5s

# Vision (requires Naboo powered on, camera at 192.168.0.163)
uv run python3 scripts/test_e2e.py "what do you see right now?"
# → should complete in ≤15s with a room description
```

## Handoff to OpenClaw Agent

When a task is complete:
1. Commit all changes with a clear gitmoji commit message
2. Open a PR with:
   - Issue reference (`Closes #N`)
   - What you changed and why
   - Test output (copy the timing from `time uv run python3 scripts/test_e2e.py ...`)
3. Post to Discord #naboo (channel `1476135433900396680`) if webhook is configured:

```
✅ Issue #N complete — PR #X ready for review
What I changed: [one sentence]
Test result: [timing + pass/fail]
```

The OpenClaw agent reviews PRs, handles cherry-picking to the public repo, and merges.

## Two-Repo Architecture

### Repos

| Repo | Visibility | Purpose |
|------|-----------|---------|
| `bagg3rs/naboo-dev` | **Private** | Your workspace. WIP, specs, Kiro tasks, messy history welcome |
| `bagg3rs/naboo` | **Public** | Portfolio + GitHub Pages. Clean history only. Managed by OpenClaw |

### Local remotes (on Mac mini .50 at `~/naboo/`)

```bash
origin  git@github.com:bagg3rs/naboo.git      # public
dev     git@github.com:bagg3rs/naboo-dev.git  # private
```

### Your Workflow

1. All work goes to `naboo-dev` (private)
2. Create branches from `dev/main`, push to `dev`
3. Open PRs against `naboo-dev/main`
4. OpenClaw reviews, cherry-picks or squash-merges clean commits to `naboo/main`
5. **Never push directly to `origin` (public repo)** — OpenClaw owns that

### Specs

`.kiro/specs/` lives in `naboo-dev` only. Specs are your work queue, not public content.  
Spec status:
- `production-deploy.md` — launchd auto-start + vision E2E test
- `auto-mode.md` — port vision-guided autonomous exploration
- `chapter-4.md` — Eyes Open build log chapter (draft here, publish after Rich review)

### Git hygiene on .50

Before starting any work, sync from remote to avoid conflicts:
```bash
git fetch dev && git reset --hard dev/main
```

### Do NOT push secrets

`infra/.env` is gitignored — update `infra/.env.example` instead.

## What OpenClaw Handles (Don't Duplicate)

- Cherry-picking / squash-merging to the public `naboo` repo
- GitHub issue management
- Memory in `MEMORY.md` / `memory/YYYY-MM-DD.md`
- Publishing GitHub Pages (docs go live when merged to public main)

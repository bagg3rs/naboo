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

## Public vs Private Repo

- This may be a private dev repo — push freely
- The public repo (`bagg3rs/naboo`) is managed by OpenClaw
- Do NOT push secrets — `infra/.env` is gitignored; update `infra/.env.example` instead
- Chapter docs in `docs/` get published; keep them personal-info-free

## What OpenClaw Handles (Don't Duplicate)

- Publishing chapters to the public portfolio repo
- Writing GitHub issue specs
- Merging PRs to public `main`
- Memory in `MEMORY.md` / `memory/YYYY-MM-DD.md`

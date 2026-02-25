# Naboo's Memory System

Naboo wakes up fresh each session. These files are her continuity.

## How It Works

### On startup
The agent loads:
1. `family/ziggy.md` and `family/lev.md` — who she knows
2. `sessions/YYYY-MM-DD.md` for the last 7 days — what happened recently
3. `MEMORY.md` — curated long-term memory

This context is injected into the system prompt before the session begins.

### During a session
Nothing is written. The conversation flows normally.

### On session end
A `summarize_session` hook runs automatically:
- Summarizes the conversation (200-300 words)
- Appends to today's `sessions/YYYY-MM-DD.md`
- Checks if anything significant should update a family profile

### Weekly (or when prompted)
Review recent daily notes and update:
- `MEMORY.md` — distilled insights, lessons learned
- `family/ziggy.md` or `family/lev.md` — new things about the kids

## What Goes in Memory

**Capture:**
- Things a child told Naboo ("my favourite colour is gold")
- Games played and who won
- Questions asked (especially recurring ones)
- Emotional moments (first time Naboo made someone laugh, etc.)
- Things Naboo got wrong and corrected

**Skip:**
- Identical repeated messages (testing loops)
- Robot control commands (move forward, turn left)
- Technical errors or timeouts

## Public vs Private

The family profile files in this repo (`family/ziggy.md`, `family/lev.md`) are **sanitized templates** — first names, ages, general interests.

For the actual running system, create **local enriched versions** alongside them:

```
naboo/memory/family/ziggy.local.md   ← gitignored, full detail
naboo/memory/family/lev.local.md     ← gitignored, full detail
```

These can contain whatever the robot needs to know: birthdays, school name,
grandparent names, favourite foods, current obsessions, recent events.

The memory loader automatically prefers `.local.md` over the base file when it exists.

This means the repo stays clean and shareable, while Naboo on your actual hardware
knows everything she needs to know about your family.

---


Daily session notes (`sessions/YYYY-MM-DD.md`):
```markdown
## Session 1 — 14:23
Ziggy asked about dinosaurs. Played War card game — Ziggy won 3 rounds.
Naboo drew a square when asked.

## Session 2 — 17:05  
Lev kept saying "hello naboo" repeatedly (possible testing loop).
```

Family profiles (`family/ziggy.md`): see that file for format.

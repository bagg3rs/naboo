# Spec: Chapter 4 — Eyes Open

**Status:** Ready  
**Repo:** naboo-dev (private draft)  
**Delivers to:** naboo (public) after Rich review

## Goal

Write Chapter 4 of the Naboo GitHub Pages build log: the story of giving Naboo vision. Personal, honest, build-log style — not dry technical docs.

## Source material

- `docs/03-faster-brain.md` — previous chapter (MLX migration) for tone reference
- `infra/vision_server.py` — the vision server code
- `naboo/strands_tools.py` → `get_camera_view` tool
- The OTel stall debugging story (the 30-second freeze mystery)
- Commit history: `bb9804a` (vision stage 1), `1c0b926` (OTel fix), `603e7e2` (Kiro steering)

## Narrative Arc

The chapter should follow this story shape:

1. **The idea** — Ziggy asks "can you see me?" and Naboo can't. That's the seed.
2. **The hardware** — ESP32S3 camera already on the robot (`/capture` endpoint, JPEG on demand). The easy part.
3. **Vision server** — mlx-vlm on Mac mini, FastAPI wrapper, `Qwen2-VL-2B` model. Getting it working the first time.
4. **The freeze** — everything working, then suddenly every response takes 30 seconds. The debugging journey. OTel Collector unreachable, `force_flush()` blocking, `OTEL_SDK_DISABLED` fix. Make this human and frustrating.
5. **The payoff** — "what do you see?" → 8 seconds → description of the room.
6. **What's next** — auto mode (Naboo roaming and looking where it's going).

## Tone & Style Constraints

- First person, Rich's voice (not Naboo's, not corporate)
- Personal, honest — include the moments that didn't work
- No decorative emojis in body text
- Functional ✅/🔄 in status tables only, single 🤖 in title max
- Same length and depth as Chapter 3 (~800-1200 words)
- Code snippets only where they add to the story, not for completeness

## Output

`docs/04-eyes-open.md` — same format as existing chapters

## Review Process

**Before publishing:** Rich reads it and answers 5 questions:
1. Does the frustration feel real or performed?
2. Is the Ziggy moment genuine or forced?
3. Would a stranger reading this want to build something similar?
4. Anything in here that shouldn't be public?
5. One thing you'd cut?

Only publish to public repo after Rich is happy with answers.

## Acceptance Criteria

- [ ] Draft committed to naboo-dev
- [ ] Rich has answered the 5 review questions
- [ ] No personal identifiers (surnames, school, addresses)
- [ ] GitHub Pages renders correctly
- [ ] Chapter linked from README.md

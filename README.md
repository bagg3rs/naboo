# 🤖 Naboo

> *"If I only had a brain…"*

Naboo is a family AI robot. They started life as a stock [mBot2](https://www.makeblock.com/pages/mbot2-steam-educational-robot-kit) — plastic wheels, ultrasonic sensors, a bit of pre-programmed wiggling. Then we gave them a brain.

This repo documents the journey from **stock robot** to **physical AI agent**: natural language understanding, voice responses, camera vision, autonomous navigation, and a personality the kids genuinely love.

---

## What Naboo Can Do

- **Talk back** — voice commands via Home Assistant wake word ("hey Naboo"), responses via HA TTS using the Ryan Cheerful voice
- **Think** — [Strands agents](https://github.com/strands-agents/sdk-python) powering 3-tier LLM routing: fast local responses, smart local responses, cloud fallback
- **See** — camera with real-time scene analysis and object detection
- **Move intelligently** — autonomous exploration, obstacle avoidance, shape drawing, room mapping
- **Remember** — persistent family context: knows who Ziggy and Lev are, their interests, their bedtime

## The Stack

| Layer | Technology |
|-------|-----------|
| Robot body | mBot2 (CyberPi / ESP32) |
| Agent framework | [Strands Agents](https://github.com/strands-agents/sdk-python) |
| Local LLM | MLX / Qwen 2.5 7B (Mac mini M4, ~3s) |
| Cloud LLM | AWS Bedrock / Claude (fallback) |
| Voice in | Home Assistant wake word ("hey Naboo") |
| Voice out | Home Assistant TTS — Ryan Cheerful (edge TTS) |
| Vision | Camera + Claude Vision |
| Messaging | MQTT (Mosquitto on Mac mini) |
| Home automation | Home Assistant |

## The Story

Read the full build log: **[bagg3rs.github.io/naboo](https://bagg3rs.github.io/naboo)**

- [Chapter 1 — Stock Robot](docs/01-stock-robot.md) — what we started with
- [Chapter 2 — Adding a Brain](docs/02-adding-a-brain.md) — Strands agents, dual-LLM routing, and why memory matters
- [Chapter 3 — A Faster Brain](docs/03-faster-brain.md) — MLX vs Ollama: 3x speedup on a Mac mini M4

## Quick Start

```bash
git clone git@github.com:bagg3rs/naboo.git
cd naboo
cp infra/.env.example infra/.env
# Edit .env with your MQTT + (optionally) AWS config
uv run python3 -m naboo
```

## Project Structure

```
naboo/
├── naboo/          # Core agent — Strands, tools, prompts, memory
├── infra/          # MQTT config, env files, Docker
├── docs/           # GitHub Pages build log
└── scripts/        # Test utilities
```

## Status

| Component | Status |
|-----------|--------|
| Strands agent | ✅ Running |
| Voice pipeline (HA wake word + TTS) | ✅ Running |
| 3-tier LLM routing | ✅ Running |
| MLX inference (Mac mini M4) | ✅ Running |
| Persistent family memory | ✅ Running |
| User identification ("I'm Ziggy") | ✅ Running |
| Camera / vision | 🔄 In progress |
| Autonomous navigation | 🔄 In progress |

---

*Named after the Home Assistant wake word. Ziggy (6) picked it. Non-negotiable.*

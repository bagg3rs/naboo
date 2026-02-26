# 🤖 Naboo

> *"If I only had a brain…"*

Naboo is a family AI robot. They started life as a stock [mBot2](https://www.makeblock.com/pages/mbot2-steam-educational-robot-kit) — plastic wheels, ultrasonic sensors, a bit of pre-programmed wiggling. Then we gave them a brain.

This repo documents the journey from **stock robot** to **physical AI agent**: natural language understanding, voice responses, camera vision, autonomous navigation, and a personality the kids genuinely love.

---

## What Naboo Can Do

- **Talk back** — voice commands via Home Assistant wake word ("hey Naboo"), responses via HA TTS using the Ryan Cheerful voice
- **Think** — [Strands agents](https://github.com/strands-agents/sdk-python) powering dual-LLM routing: fast local responses for simple commands, cloud reasoning for complex ones
- **See** — camera with real-time scene analysis, object detection, ArUco marker navigation
- **Move intelligently** — autonomous exploration, obstacle avoidance, shape drawing, room mapping
- **Play** — card games (War via camera vision), Q&A, jokes, Arsenal scores, drawing letters

## The Stack

| Layer | Technology |
|-------|-----------|
| Robot body | mBot2 (CyberPi / ESP32) |
| Agent framework | [Strands Agents](https://github.com/strands-agents/sdk-python) |
| Fast LLM (local) | Ollama / Qwen 2.5:3b |
| Smart LLM (cloud) | AWS Bedrock / Claude |
| Voice in | Home Assistant wake word ("hey Naboo") |
| Voice out | Home Assistant TTS — Ryan Cheerful (edge TTS) |
| Vision | Camera + Claude Vision |
| Messaging | MQTT / AWS IoT Core |
| Home automation | Home Assistant |

## The Story

Read the full build log: **[bagg3rs.github.io/naboo](https://bagg3rs.github.io/naboo)**

- [Chapter 1 — Stock Robot](docs/01-stock-robot.md)
- [Chapter 2 — Adding a Brain](docs/02-adding-a-brain.md)
- [Chapter 3 — Finding Her Voice](docs/03-finding-her-voice.md)
- [Chapter 4 — Eyes Open](docs/04-eyes-open.md)
- [Chapter 5 — What's Next](docs/05-whats-next.md)

## Quick Start

```bash
git clone git@github.com:bagg3rs/naboo.git
cd naboo
cp infra/.env.example infra/.env
# Edit .env with your AWS + MQTT config
docker compose -f infra/docker-compose.yml up -d
```

See [docs/setup.md](docs/setup.md) for the full setup guide.

## Project Structure

```
naboo/
├── naboo/          # Core agent — Strands, tools, prompts, memory
├── firmware/       # mBot2 MicroPython firmware
├── vision/         # Camera, scene analysis, navigation
├── voice/          # TTS routing, pre-recorded audio clips
├── infra/          # Docker, Terraform, MQTT config
├── docs/           # GitHub Pages build log
└── scripts/        # Utilities
```

## Status

| Component | Status |
|-----------|--------|
| Strands agent | ✅ Running |
| Voice (wake word + TTS) | ✅ Running |
| Dual-LLM routing | ✅ Running |
| Camera / vision | ✅ Running |
| Autonomous navigation | 🔄 In progress |
| Mac mini migration | 🔄 In progress |

---

*Named after the Home Assistant wake word. Ziggy (6) picked it. Non-negotiable.*

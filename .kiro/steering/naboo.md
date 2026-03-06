# Naboo — Project Steering

## What Is This

Naboo is a family MBot2 robot with a conversational AI brain. It lives in the living room
and talks to the kids (Ziggy, age 6; Lev, age 2) via Home Assistant voice pipelines.
This is a personal/portfolio project — written as a build log, not corporate docs.

## Architecture

```
HA voice pipeline → MQTT (192.168.0.50:1883)
                         ↓
             NabooAgent (Strands, on Mac mini .50)
             ├── MLX Qwen2.5-7B (port 11435) — fast responses ~3s
             ├── Vision server mlx-vlm (port 11436) — camera descriptions ~4s
             └── Bedrock Claude Haiku — fallback for complex/current-info queries
```

Key facts:
- Agent runs at `~/naboo/` on Mac mini (192.168.0.50)
- MQTT broker also on Mac mini
- ESP32 camera at `http://192.168.0.163/capture` (on Naboo the robot)
- Bedrock credentials in `infra/.env` (never commit)

## Stack

- Python 3.12 (via uv)
- `strands-agents` — agent framework
- `paho-mqtt` — MQTT client
- `httpx` — HTTP client (sync in tools, async in warmup)
- `mlx-vlm==0.1.15` + `transformers==4.51.3` — PINNED, do not upgrade
- FastAPI — vision server

## Key Files

| File | Purpose |
|------|---------|
| `naboo/__main__.py` | Entry point — early env loading, OTel disable, pidfile |
| `naboo/agent.py` | `NabooAgent` — MQTT listener, question routing, warmup |
| `naboo/router/model_router.py` | Routes queries to MLX/Bedrock by complexity |
| `naboo/router/query_classifier.py` | Classifies query complexity (SIMPLE/MODERATE/COMPLEX) |
| `naboo/tools/strands_tools.py` | All Strands tools — `get_camera_view`, `robot_speak`, etc. |
| `naboo/memory/` | Family profiles, session memory |
| `infra/vision_server.py` | FastAPI mlx-vlm server (deployed to `~/vision_server.py` on Mac mini) |
| `infra/.env` | Local secrets — NEVER commit |
| `infra/.env.example` | Template — keep updated |
| `scripts/test_e2e.py` | End-to-end test via MQTT |

## Critical Constraints

1. **mlx-vlm pinned**: `mlx-vlm==0.1.15` + `transformers==4.51.3`. Do NOT upgrade.
   Reason: 0.3.12+ breaks Qwen2-VL processor loading (`TypeError: argument of type 'NoneType'`)
2. **OTel disabled by default**: `NABOO_OTEL_ENABLED=false`. Do NOT call `trace.set_tracer_provider()` globally.
   Reason: Strands picks up global providers and calls `force_flush()` causing 30s stalls.
3. **No emojis in robot speech**: `robot_speak()` output goes to HA TTS (Ryan Cheerful voice).
   Emojis get read aloud as "robot face" etc.
4. **They/them pronouns for Naboo**: Naboo is an alien robot from Zephyria.
5. **Public repo — no personal data**: No surnames, school names, birth dates, addresses.
   First names (Ziggy, Lev) and ages are fine. Enriched local data must be gitignored.

## Running Locally (Mac mini .50)

```bash
# Start agent
cd ~/naboo
source ~/.local/bin/env  # adds uv to PATH
PYTHONUNBUFFERED=1 nohup uv run python3 -u -m naboo > /tmp/naboo.log 2>&1 &

# Test
uv run python3 scripts/test_e2e.py "what is 2 plus 2?"
uv run python3 scripts/test_e2e.py "what do you see right now?"  # requires Naboo powered on

# Check logs
tail -f /tmp/naboo.log

# Vision server health
curl http://localhost:11436/health
```

## Naboo's Personality

Naboo speaks simply (5–6 year old level), warmly, and without emoji. Short answers.
They know family members by name once introduced. They have a sense of playfulness.
See `naboo/memory/family.md` for family profiles.

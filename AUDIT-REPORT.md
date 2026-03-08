# Naboo Project Audit Report

**Date:** 2026-03-08  
**Auditor:** Automated code audit (senior engineer review)  
**Scope:** All source, docs, config, prompts, and memory files in the naboo repo

---

## 1. Documentation Accuracy Check

### README.md

| Issue | Detail |
|-------|--------|
| **Vision says "Camera + Claude Vision"** | `README.md:23` — The vision stack actually uses `mlx-vlm` with `Qwen2-VL-2B-Instruct-4bit` locally (`infra/vision_server.py:15`), NOT Claude Vision. Claude (Bedrock) has `supports_vision=True` in `config.py:83` but there's no code path that actually sends images to Bedrock. The `get_camera_view` tool (`strands_tools.py:490-530`) calls the local mlx-vlm vision server. **Misleading.** |
| **Stack table says "Voice in: Home Assistant wake word"** | `README.md:17` — No HA integration code exists in this repo. The voice pipeline is outside the repo scope (HA config), but the README implies it's part of this project. Minor — could add a note. |
| **Stack table says "Voice out: Home Assistant TTS — Ryan Cheerful"** | `README.md:18` — Same issue. No HA TTS code in this repo. `robot_speak` publishes to MQTT `mbot2/speak`; the HA side is external. |
| **Status table: "Camera / vision 🔄 In progress"** | `README.md:40` — `get_camera_view` and `query_vision` are fully implemented in `strands_tools.py`. The vision server exists at `infra/vision_server.py`. This should be ✅ Running. |
| **Status table: "Autonomous navigation 🔄 In progress"** | `README.md:41` — `auto_mode` tool is fully implemented in `strands_tools.py:647-740`. Should be ✅ Running or at least mention the tool exists. |
| **Project structure shows `infra/` as "MQTT config, env files, Docker"** | `README.md:33` — There is no Docker anything in this repo. Should say "env files, launchd plists, vision server, scripts". |

### docs/02-adding-a-brain.md

| Issue | Detail |
|-------|--------|
| **"System 2 (smart, cloud): AWS Bedrock, Claude Haiku 4.5"** | `02-adding-a-brain.md:54-55` — The current code has 3 tiers, not 2. S2 is now MLX/Ollama local 7B, not Bedrock. Bedrock is Tier 3 (COMPLEX/CURRENT_INFO). The doc describes the original architecture, not the current one. |
| **Architecture diagram shows only "System 1" and "System 2"** | `02-adding-a-brain.md:25-40` — Missing MLX tier. Missing vision server. The actual architecture (per `config.py`) is: S1=MLX 3B/7B, S2=MLX 7B, S3=Bedrock. |
| **"Naboo's agent sits on a small edge computer (Linux box, home network)"** | `02-adding-a-brain.md:20` — It runs on a Mac mini M4 (per `.kiro/steering/naboo.md` and launchd plists). Not a Linux box. |
| **Code snippet `classify_message()` is simplified to 2 tiers** | `02-adding-a-brain.md:62-72` — The actual classifier (`query_classifier.py`) uses 4 complexity levels (SIMPLE, MODERATE, COMPLEX, CURRENT_INFO) with regex patterns, caching, and thread-safety. The doc snippet is misleading as a representation of the current code. |
| **"the machine running Naboo has `.local.md` files"** | `02-adding-a-brain.md:90` — Accurate, the code supports this (`memory_loader.py:27-33`). ✅ |
| **Footer link: "Chapter 3 — Finding Her Voice"** | `02-adding-a-brain.md:107` — Wrong link text AND wrong pronouns. Chapter 3 is "A Faster Brain", not "Finding Her Voice". Also uses "Her" which contradicts the they/them pronouns established in the system prompt and `.kiro/steering/naboo.md`. |
| **Memory loader code snippet** | `02-adding-a-brain.md:76-91` — Slightly simplified but structurally matches `memory_loader.py:16-49`. The real code has additional `seen` set deduplication and header formatting. Minor discrepancy. |

### docs/03-faster-brain.md

| Issue | Detail |
|-------|--------|
| **"One model, one server"** | `03-faster-brain.md:68` — The docs explain why they settled on 7B for everything. But `infra/.env:25-26` confirms `MLX_MODEL_S1=MLX_MODEL_S2=Qwen2.5-7B-Instruct-4bit`. The *config.py defaults* still have S1=3B, S2=7B (`config.py:10-11`), which is inconsistent with the .env and the lesson learned. |
| **Mostly accurate** | This chapter accurately describes the MLX migration, the timing improvements, and the operational lessons learned. ✅ |

### docs/01-stock-robot.md

| Issue | Detail |
|-------|--------|
| **Accurate** | Describes the mBot2 hardware. No code claims to verify. ✅ |

---

## 2. Coverage Gaps

### Code features NOT documented

| Feature | Location | Notes |
|---------|----------|-------|
| **Bird feeder tools** | `strands_tools.py:137-370` | 4 bird feeder tools (`get_bird_stats`, `get_bird_patterns`, `get_busiest_bird_days`, `get_hourly_bird_activity`). ~230 lines of code. Zero mention in any doc. |
| **Music/tune system** | `strands_tools.py:485-640` | Full music library (15 tunes, MIDI notes, aliases), `play_tune` and `list_tunes` tools. Not mentioned in any chapter doc. |
| **Vision server** | `infra/vision_server.py` | Fully working FastAPI mlx-vlm server. Only mentioned in passing in Chapter 3 (warmup). No dedicated documentation. |
| **Query pre-enrichment** | `agent.py:72-119` | Weather and football fixture pre-fetching with tool bypass. Undocumented optimization. |
| **Response cleaning** | `agent.py:38-95` | Sophisticated response cleaning pipeline for stripping tool narration from LLM output. Undocumented. |
| **User identification** | `agent.py:130-157` | Detects "I am Ziggy" style introductions, maps names to family identities, persists per conversation_id. Documented in system prompt but not in any chapter. |
| **MLX warmup** | `agent.py:210-250` | Warms text + vision models at startup (active hours only, 08:00-22:00). Undocumented. |
| **Telemetry system** | `telemetry.py` | Full OTel integration with Strands delegation, context manager spans, NoOp fallback. Only mentioned as a debugging story in Chapter 3. |
| **OTel stall workaround** | `__main__.py:18-50` | Extensive monkey-patching of Strands Tracer + ThreadingInstrumentor. Documented in issue #5 but not in project docs. |
| **MLX batch script** | `infra/scripts/mlx-batch.sh` | Smart start/stop wrapper for batch jobs. Undocumented. |
| **Two-repo workflow** | `.kiro/steering/workflow.md` | naboo-dev (private) + naboo (public) cherry-pick workflow. Not in README or docs. |
| **Duplicate vision tools** | `strands_tools.py` | Both `get_camera_view` (~line 490) and `query_vision` (~line 545) exist. See Code Quality section. |
| **PirateWeather integration** | `strands_tools.py:410-460` | Full PirateWeather API integration with city coordinate lookup. Not documented. |

### Docs promise things code doesn't deliver

| Promise | Location | Reality |
|---------|----------|---------|
| **`card_game` tool** | `system_prompt.txt:82-96` | **Does not exist.** The system prompt describes `card_game` with `start_game`, `play_turn`, `get_status`, `end_game` actions. No implementation in `strands_tools.py`. Not in `ALL_TOOLS` in `agent.py`. |
| **`query_vision` available to agent** | `system_prompt.txt:42` | `query_vision` is defined in `strands_tools.py` but is **NOT in `ALL_TOOLS`** in `agent.py:43-50`. Only `get_camera_view` is registered. The system prompt tells the agent to use `query_vision` but the agent can't access it. |
| **Ring camera support** | `strands_tools.py:565-570` | `query_vision` has camera_map entries for `living_room` (Ring) and `garden` (Ring) but these use MQTT-based vision query/response, not the HTTP vision server. There's no evidence this works — the MQTT topics `mbot2/vision/query` and `mbot2/vision/description` are never published to by the vision server. |
| **"Go Fish" and "Memory" card games** | `system_prompt.txt:84-85` | Listed as "coming soon" in the system prompt. Not implemented. |

---

## 3. Code Quality

### Dead Code

| File:Line | Issue |
|-----------|-------|
| `strands_tools.py:42-43` | `_vision_cache` global and `set_vision_cache()` function. The vision cache was intentionally removed per `.kiro/specs/auto-mode.md` R4, but the injection points remain. `_vision_cache` is only read in `query_vision()` which isn't registered with the agent. |
| `strands_tools.py:66-69` | `update_telemetry()` function. Called from nowhere in the codebase. Meant for auto_mode telemetry updates but no caller exists. |
| `strands_tools.py:71-99` | `_compute_scene_key()` — Only used by `query_vision()` cache path which is dead code (vision cache removed). |
| `strands_tools.py:102-126` | `_is_scene_similar()` — Same as above. Dead code. |
| `strands_tools.py:545-640` | `query_vision()` tool — Fully implemented MQTT-based vision but NOT registered in `ALL_TOOLS`. Uses a completely different mechanism (MQTT pub/sub) from `get_camera_view` (HTTP). The MQTT topics it uses (`mbot2/vision/query`, `mbot2/vision/description`) don't appear to have any subscriber. |
| `strands_tools.py:297-370` | `get_busiest_bird_days()` and `get_hourly_bird_activity()` — Defined but NOT in `ALL_TOOLS` (`agent.py:43-50`). They're exported from `tools/__init__.py` but never registered with the agent. |
| `strands_tools.py:4-5` | `import uuid` — Imported but never used in this file. |
| `agent.py:7` | `from pathlib import Path` — Also imported in the function body of `_connect_mqtt` (`agent.py:163`). The module-level import is used by `_load_system_prompt`. Not strictly dead but redundant import in the method. |
| `config.py:54` | `CAMERA_ENTITY` — Defined but never used anywhere in the codebase. |
| `model_router.py:67-73` | `NOVA2_MODEL_PATTERNS` and `is_nova2_model()` — Nova 2 web grounding detection. Never called from anywhere. Future-proofing for a feature that doesn't exist yet. |
| `model_router.py:39-40` | `web_grounding_config` parameter — Accepted but web grounding is never configured in `config.py`. Only used if you call `select_model` with `CURRENT_INFO` and web grounding is enabled, which never happens with current config. |

### Missing Error Handling

| File:Line | Issue |
|-----------|-------|
| `agent.py:184` | `_on_message` catches `Exception` broadly but doesn't handle the case where `self._loop` is None (race condition during startup). If a message arrives before `start()` sets `self._loop`, it will throw `AttributeError`. |
| `agent.py:213-233` | `_warmup_mlx()` uses `httpx.AsyncClient` but `httpx` is not in `pyproject.toml` dependencies. It's likely installed as a transitive dependency, but this is fragile. |
| `strands_tools.py:400-410` | `get_weather()` uses `httpx.get()` (sync) but `httpx` not in declared dependencies. |
| `strands_tools.py:582-600` | `query_vision()` subscribes to MQTT topic but never unsubscribes on timeout path. The `message_callback_remove` at line 615 only runs if the loop completes normally, but not in the `except` block at line 636. |
| `execute_movement_sequence` | `strands_tools.py:435-476` — No rate limiting between moves. The main `robot_control` tool has rate limiting (`MIN_COMMAND_INTERVAL`), but `execute_movement_sequence` bypasses it entirely, publishing rapid-fire MQTT commands. |
| `__main__.py:82-83` | `_shutdown()` handler creates a task with `loop.create_task(agent.stop())` but this runs inside a signal handler where the loop might not be running. This can silently fail. |

### Inconsistencies

| Issue | Detail |
|-------|--------|
| **Two vision tools, different mechanisms** | `get_camera_view` (HTTP to vision server, registered) vs `query_vision` (MQTT pub/sub, unregistered). Both do the same thing (describe what the camera sees) but via completely different paths. |
| **S1 model config mismatch** | `config.py:10` defaults S1 to `qwen2.5:3b`, `config.py:16` defaults MLX S1 to `Qwen2.5-3B-Instruct-4bit`. But `infra/.env:25` sets both MLX_MODEL_S1 and S2 to `Qwen2.5-7B-Instruct-4bit`. The "two different models" design is effectively unused in production. |
| **Bedrock model ID mismatch** | `infra/.env:12` has `eu.anthropic.claude-opus-4-6-20250514-v1:0` (Opus!), `.env.example:17` has `eu.anthropic.claude-haiku-4-5-20251001-v1:0` (Haiku), `config.py:22` defaults to Haiku, plist has Haiku. The live `.env` uses Opus which is ~100x more expensive than Haiku for a kid's robot. Likely a dev oversight. |
| **`robot_speak` topic** | `robot_speak()` publishes to `mbot2/speak` (`strands_tools.py:384`), but `robot_control()` publishes to `mbot2/command` (`strands_tools.py:430`). The MQTT topic scheme is inconsistent (`mbot2/speak` vs `mbot2/command` vs `mbot2/sound`). This is probably intentional (different firmware handlers) but there's no documentation of the MQTT topic contract. |
| **System prompt turn timing** | `system_prompt.txt:58-59` says "~120°/second at speed 35 (so 90° ≈ 0.75s)". But `execute_movement_sequence` docstring (`strands_tools.py:409`) says "~150 degrees/second at speed 35" and "90° turn ≈ 0.6s". These conflict. |

---

## 4. Architecture Map (Actual)

Based on reading all code:

```
┌─────────────────────────────────────────────────────────┐
│ Home Assistant                                          │
│  ├── Wake word detection ("hey Naboo")                  │
│  ├── STT (speech-to-text)                               │
│  ├── TTS (text-to-speech, Ryan Cheerful)                │
│  └── Publishes question → MQTT naboo/questions          │
│      Subscribes to → MQTT naboo/answers                 │
└────────────────────┬────────────────────────────────────┘
                     │ MQTT (Mosquitto on 192.168.0.50:1883)
                     │
┌────────────────────▼────────────────────────────────────┐
│ NabooAgent (naboo/agent.py)                             │
│  ├── MQTT listener (paho-mqtt)                          │
│  ├── Question queue (asyncio)                           │
│  ├── User identification ("I'm Ziggy" → family map)     │
│  ├── Question enrichment (pre-fetch weather/fixtures)   │
│  │                                                      │
│  ├── QueryClassifier (naboo/router/query_classifier.py) │
│  │   └── Regex-based: SIMPLE → MODERATE → COMPLEX →     │
│  │       CURRENT_INFO → TOOL_BACKED                     │
│  │                                                      │
│  ├── ModelRouter (naboo/router/model_router.py)         │
│  │   ├── SIMPLE     → MLX Qwen2.5-7B (port 11435)      │
│  │   ├── MODERATE   → MLX Qwen2.5-7B (port 11435)      │
│  │   ├── COMPLEX    → Bedrock Claude (eu-west-2)        │
│  │   └── CURRENT_INFO → Bedrock Claude (eu-west-2)      │
│  │   (MLX via OpenAI-compat API, Bedrock via strands)   │
│  │                                                      │
│  ├── Strands Agent (with tools)                         │
│  │   ├── robot_speak → MQTT mbot2/speak                 │
│  │   ├── robot_sound → MQTT mbot2/sound                 │
│  │   ├── robot_control → MQTT mbot2/command             │
│  │   ├── execute_movement_sequence → MQTT mbot2/command │
│  │   ├── get_weather → PirateWeather API / DuckDuckGo   │
│  │   ├── web_search → DuckDuckGo (ddgs)                 │
│  │   ├── get_camera_view → HTTP to vision server        │
│  │   ├── auto_mode → AutoModeController (injected)      │
│  │   ├── get_bird_stats → HTTP to bird feeder API       │
│  │   ├── get_bird_patterns → HTTP to bird feeder API    │
│  │   ├── play_tune → MQTT mbot2/command                 │
│  │   └── list_tunes → returns music library list        │
│  │                                                      │
│  ├── Memory loader (naboo/memory/memory_loader.py)      │
│  │   ├── MEMORY.md → long-term curated memory           │
│  │   ├── family/*.md → family profiles (prefer .local)  │
│  │   └── sessions/YYYY-MM-DD.md → last 7 days           │
│  │                                                      │
│  ├── Response cleaning (_clean_response)                 │
│  │   └── Strip tool narration, meta-commentary, prefixes│
│  │                                                      │
│  ├── Session summary → append to sessions/ on stop      │
│  │                                                      │
│  └── Telemetry (OTel, disabled by default)              │
│      └── Strands StrandsTelemetry delegation             │
└─────────────────────────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐  ┌─────────────────────────┐
│ MLX-LM Server    │  │ Vision Server (FastAPI)  │
│ :11435            │  │ :11436                   │
│ Qwen2.5-7B-4bit  │  │ Qwen2-VL-2B-4bit        │
│ OpenAI-compat API │  │ /vision → describe image │
└──────────────────┘  │ /health → status          │
                      └─────────────────────────┘
                               ▲
                               │ HTTP GET /capture
                      ┌────────┴────────┐
                      │ ESP32 Camera    │
                      │ 192.168.0.163   │
                      └─────────────────┘

┌──────────────────┐
│ mBot2 / CyberPi  │
│ (robot body)     │
│ Subscribes to:   │
│  mbot2/speak     │
│  mbot2/sound     │
│  mbot2/command   │
└──────────────────┘
```

**Key architectural notes:**
- The agent fetches camera frames directly from ESP32, sends them to the vision server, gets a text description back. No MQTT in the vision path (despite `query_vision` trying to use MQTT).
- Home Assistant is entirely external — no HA API calls from this codebase. `HA_URL` and `HA_TOKEN` are configured but never used in any code path.
- The `auto_mode` tool expects an `_auto_mode_controller` to be injected via `set_auto_mode_controller()`, but **no code in the repo calls `set_auto_mode_controller()`**. The auto_mode tool will always return "Auto mode is not available right now."

---

## 5. System Prompt vs Reality

### Tools claimed in system prompt (`naboo/prompts/system_prompt.txt`)

| Claimed Tool | In Code? | Registered in ALL_TOOLS? | Status |
|-------------|----------|--------------------------|--------|
| `robot_control` | ✅ `strands_tools.py:371` | ✅ `agent.py:47` | **Working** |
| `robot_speak` | ✅ `strands_tools.py:340` | ✅ `agent.py:45` | **Working** |
| `robot_sound` | ✅ `strands_tools.py:387` | ✅ `agent.py:45` | **Working** |
| `execute_movement_sequence` | ✅ `strands_tools.py:405` | ✅ `agent.py:47` | **Working** |
| `web_search` | ✅ `strands_tools.py:501` | ✅ `agent.py:48` | **Working** |
| `get_weather` | ✅ `strands_tools.py:410` | ✅ `agent.py:48` | **Working** |
| `play_tune` | ✅ `strands_tools.py:498` | ✅ `agent.py:51` | **Working** |
| `query_vision` | ✅ `strands_tools.py:545` | ❌ **NOT registered** | **BROKEN** — System prompt says to use it; agent can't |
| `auto_mode` | ✅ `strands_tools.py:647` | ✅ `agent.py:50` | **BROKEN** — No controller injected; always returns "not available" |
| `card_game` | ❌ **NOT IMPLEMENTED** | ❌ | **PHANTOM** — Full usage instructions in prompt, zero code |
| `get_camera_view` | ✅ `strands_tools.py:490` | ✅ `agent.py:49` | **Working** but NOT mentioned in system prompt tool list |

### System prompt issues

| Line(s) | Issue |
|---------|-------|
| `system_prompt.txt:42` | Lists `query_vision` — not registered with agent |
| `system_prompt.txt:43` | Lists `card_game` — doesn't exist |
| `system_prompt.txt:44-54` | "CAMERA VISION - CRITICAL INSTRUCTIONS" tell the agent to use `query_vision`. Since it's not registered, the agent will fail or hallucinate when asked "what can you see?". However, `get_camera_view` IS registered and does the same thing. The prompt should reference `get_camera_view` instead. |
| `system_prompt.txt:58-59` | Turn timing conflicts with `execute_movement_sequence` docstring (120°/s vs 150°/s) |
| `system_prompt.txt:82-96` | Full `card_game` usage documentation for a non-existent tool |
| `system_prompt.txt:40` | Lists `get_camera_view` in tool list — but CAMERA VISION instructions (line 44+) say to use `query_vision` instead. Contradictory. |

---

## 6. Dependency Audit

### pyproject.toml declared dependencies

| Package | Actually Imported? | Notes |
|---------|-------------------|-------|
| `strands-agents>=0.1.0` | ✅ `from strands import Agent, tool` | Core framework |
| `paho-mqtt>=2.0.0` | ✅ `import paho.mqtt.client as mqtt` | MQTT client |
| `python-dotenv>=1.0.0` | ✅ `from dotenv import load_dotenv` | Env loading |
| `boto3>=1.34.0` | ❌ **Never imported directly** | Likely transitive for `strands.models.BedrockModel`. Probably needed at runtime but never explicitly imported. |
| `requests>=2.31.0` | ❌ **Never imported** | **UNUSED.** All HTTP calls use `httpx` (not declared) or `ddgs`. |
| `ollama>=0.4.0` | ❌ **Never imported** | **UNUSED.** Ollama access goes through `strands.models.ollama.OllamaModel`, which may use this internally, but worth verifying. |
| `amqtt>=0.11.3` | ❌ **Never imported** | **UNUSED.** This is an MQTT broker library. Naboo uses paho-mqtt (client). Likely leftover from when an embedded broker was considered. |
| `ddgs>=9.10.0` | ✅ `from ddgs import DDGS` | DuckDuckGo search |
| `openai>=2.24.0` | ❌ **Never imported directly** | Used transitively by `strands.models.openai.OpenAIModel` for MLX. Likely needed. |
| `opentelemetry-api>=1.20.0` | ✅ `from opentelemetry import trace` | OTel API |
| `opentelemetry-sdk>=1.20.0` | ❌ Not imported directly | Transitive for OTel. Needed when enabled. |
| `opentelemetry-exporter-otlp-proto-grpc>=1.20.0` | ❌ Not imported directly | OTel is disabled by default. May not be needed. |
| `opentelemetry-exporter-otlp-proto-http>=1.20.0` | ❌ Not imported directly | Same as above. |

### Undeclared dependencies (used but not in pyproject.toml)

| Package | Where Used | Risk |
|---------|-----------|------|
| `httpx` | `strands_tools.py` (bird stats, weather, vision), `agent.py` (warmup) | **HIGH** — Core functionality depends on this. Currently works because `strands-agents` or another dep pulls it in transitively. Should be declared explicitly. |
| `fastapi` | `infra/vision_server.py` | LOW — Vision server is a separate deployment, not part of the naboo package. But should be documented. |
| `uvicorn` | `infra/vision_server.py` | Same as above. |
| `mlx_vlm` | `infra/vision_server.py` | Same as above. |
| `PIL` (Pillow) | `infra/vision_server.py` | Same as above. |

---

## 7. Security Audit

### Secrets committed to repo

| File | Issue | Severity |
|------|-------|----------|
| `infra/.env` | **PirateWeather API key committed**: `PIRATEWEATHER_API_KEY=HJj28HLuOxCODzmstXMZ7v09RD25Hf5d` | **MEDIUM** — `.env` is in `.gitignore` so it won't be pushed to git, BUT it exists in this workspace copy. The key is also in the launchd plist (see below). |
| `infra/launchd/ai.naboo.agent.plist` | **PirateWeather API key in plain text** (`line 38`). This file IS tracked in git. | **HIGH** — API key committed to git in a tracked file. Anyone with repo access can see it. |
| `infra/launchd/ai.naboo.agent.plist` | Contains all internal IP addresses (192.168.0.50, 192.168.0.163, 192.168.0.170) | **LOW** — Internal IPs, not externally accessible. |
| `naboo/memory/sessions/*.md` | Session logs with conversation content are present locally but gitignored. | **OK** — Properly gitignored. |
| `naboo/memory/family/*.md` | Base profiles with first names and ages are tracked. `.local.md` files are gitignored. | **OK** — As designed. First names/ages are acceptable per project constraints. |

### Other security observations

| Issue | Detail |
|-------|--------|
| **No MQTT authentication** | The MQTT broker at 192.168.0.50:1883 has no auth configured (no username/password in any connection code). Anyone on the local network can publish to `naboo/questions` and get the agent to respond, or publish to `mbot2/command` to move the robot. |
| **No rate limiting on questions** | The MQTT listener processes every message on `naboo/questions` with no rate limiting. A flood of messages would trigger unlimited LLM calls (including potentially expensive Bedrock calls). |
| **Vision server has no auth** | `infra/vision_server.py` exposes `/vision` endpoint on `0.0.0.0` with no authentication. Anyone on the network can send images for analysis. |
| **PID file race condition** | `__main__.py:67` writes PID to `/tmp/naboo-agent.pid` which is world-writable. A malicious process could write a different PID, causing `_kill_existing()` to kill an arbitrary process. Low risk in a home environment. |
| **Bedrock set to Opus** | `infra/.env:12` has `BEDROCK_MODEL_ID=eu.anthropic.claude-opus-4-6-20250514-v1:0`. Opus is the most expensive Claude model. If COMPLEX queries are routed to Bedrock, costs could be significant. Likely a dev mistake — `.env.example` and the plist both specify Haiku. |

---

## Summary of Critical Issues

1. **`card_game` tool referenced in system prompt but never implemented** — The agent will hallucinate or fail when kids ask to play cards.
2. **`query_vision` not registered with agent** — System prompt's "CRITICAL INSTRUCTIONS" for camera usage reference a tool the agent can't call. `get_camera_view` works but prompt says to use `query_vision`.
3. **`auto_mode` controller never injected** — Tool always returns "not available" despite being registered and extensively documented in the system prompt.
4. **PirateWeather API key committed in tracked plist file** — `infra/launchd/ai.naboo.agent.plist` is tracked in git.
5. **`httpx` not declared as dependency** — Used extensively but only available transitively. Could break on a clean install.
6. **`requests` and `amqtt` are unused dependencies** — Dead weight in pyproject.toml.
7. **Chapter 2 architecture is significantly out of date** — Describes 2-tier (Ollama + Bedrock) when reality is 3-tier (MLX + MLX + Bedrock).
8. **README claims Docker exists** — It doesn't.
9. **Vision documented as "Camera + Claude Vision"** — Actually uses local mlx-vlm/Qwen2-VL, not Claude.
10. **Bedrock model set to Opus in .env** — Almost certainly unintentional for a kid's robot. ~$15/M input tokens vs $0.80 for Haiku.

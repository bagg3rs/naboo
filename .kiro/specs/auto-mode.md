# Spec: Auto Mode — Vision-Guided Autonomous Exploration

**Status:** Ready  
**Repo:** naboo-dev (private)  
**Delivers to:** naboo (public) after review

## Goal

Port `auto_mode.py` from the old `mbot2-physical-ai` codebase into the new `naboo` repo as a clean, integrated feature. Naboo should be able to explore autonomously: move forward, detect obstacles with ultrasonic sensor, look with the camera, decide which way to turn.

## Source

`/Users/rb/apps/mbot2-physical-ai/src/naboo/auto_mode.py` on Mac mini (.50)

The core logic is solid — this is a port + cleanup, not a rewrite.

## Requirements

### R1 — Core Controller

`naboo/auto_mode.py`:
- `AutoModeConfig` dataclass (speeds, timeouts, thresholds)
- `AutoModeState` dataclass (runtime state)
- `AutoModeController` class with:
  - `start_auto_mode()` / `stop_auto_mode()`
  - `_auto_mode_loop()` — main asyncio loop
  - `_smart_bounce()` — stop → look → decide → turn
  - `_decide_turn()` — parse vision description for directional hints
  - `on_telemetry()` — update distance/battery from MQTT

### R2 — Vision Integration

- Uses existing `get_camera_view` tool's HTTP endpoint (vision server at `localhost:11436`)
- Direct HTTP call to vision server (not via MQTT query/response cycle like old code)
- Timeout: 10s per vision call
- Graceful degradation: if vision fails, fall back to random 90° turn

### R3 — Strands Tool Integration

Add to `naboo/strands_tools.py`:
- `start_auto_mode(duration_seconds: int = 300)` — starts exploration
- `stop_auto_mode()` — stops and returns stats
- Both wired to the agent's `AutoModeController` instance

### R4 — Cleanup vs Old Code

**Remove from old code:**
- `LocalVisionClient` / Moondream proactive checks (we don't have Moondream on .50)
- Vision caching (`CacheManager`) — premature optimisation, remove
- MQTT-based vision query/response cycle — replace with direct HTTP to vision server
- SQLite/cost tracking — not needed

**Keep:**
- `_decide_turn()` logic (parse left/right/wall/person from vision text)
- Stuck detection (`bounce_times` window)
- Sensor health monitoring (`consecutive_vision_timeouts`)
- Battery / telemetry timeout safety stops
- `_get_failure_message()` for voice feedback

### R5 — Config via .env

Add to `infra/.env.example`:
```
AUTO_MODE_OBSTACLE_THRESHOLD=20
AUTO_MODE_FORWARD_SPEED=40
AUTO_MODE_TIMEOUT_SECONDS=300
```

## MQTT Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `mbot2/command` | publish | Move forward/backward/turn/stop |
| `naboo/telemetry` | subscribe | Distance + battery readings |
| `naboo/responses` | publish | Voice feedback to HA |

## Acceptance Criteria

- [ ] `start_auto_mode()` tool triggers exploration via Strands agent
- [ ] Robot avoids obstacles (tested with Naboo powered on)
- [ ] Voice feedback fires on stop ("Explored for Xs, Y obstacles avoided")
- [ ] Auto-stops on timeout / low battery / telemetry loss
- [ ] Unit tests for `_decide_turn()` with various vision descriptions

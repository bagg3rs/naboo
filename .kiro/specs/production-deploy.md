# Spec: Production Deploy — Launchd Auto-Start + Vision E2E

**Status:** Ready  
**Repo:** naboo-dev (private)  
**Delivers to:** naboo (public) after review

## Goal

Make the Naboo stack fully production-ready on Mac mini (.50):
- Both services auto-start on boot via launchd
- Vision pipeline verified end-to-end
- No manual intervention needed after reboot

## Current State

- Naboo agent: started manually with `nohup uv run ...`
- Vision server: started manually, running under `~/mlx-test/.venv/`
- Both have no launchd plists, die on reboot

## Requirements

### R1 — Naboo Agent Launchd Service

Create `~/Library/LaunchAgents/naboo.agent.plist`:
- Label: `ai.naboo.agent`
- Working directory: `~/naboo/`
- Command: `uv run python3 -u -m naboo`
- Env vars: inherit from `infra/.env` (use `EnvironmentVariables` key)
- `PYTHONUNBUFFERED=1` set
- Stdout → `/tmp/naboo.log`, stderr → `/tmp/naboo-err.log`
- `KeepAlive: true` (auto-restart on crash)
- `RunAtLoad: true`

### R2 — Vision Server Launchd Service

Create `~/Library/LaunchAgents/naboo.vision.plist`:
- Label: `ai.naboo.vision`
- Working directory: `~/mlx-test/`
- Command: `.venv/bin/python3 vision_server.py`
- Stdout → `/tmp/naboo-vision.log`
- `KeepAlive: true`, `RunAtLoad: true`
- Must start before naboo agent (use `WaitForDependency` or just rely on agent retry)

### R3 — Vision E2E Test

Script `scripts/test_vision_e2e.sh` that:
1. Checks vision server is responding (`curl localhost:11436/health`)
2. Checks camera is reachable (`curl http://192.168.0.163/capture --max-time 5`)
3. Runs `uv run python3 scripts/test_e2e.py "what do you see right now?"`
4. Passes if response arrives in ≤15s and contains a description (not an error)

### R4 — Startup Verification Script

`scripts/naboo-status.sh`:
- Shows launchctl status for both services
- Shows last 10 lines of each log
- Shows MLX server status (port 11435)
- Shows MQTT broker status (port 1883)

## Out of Scope

- Docker containerisation (future spec)
- Remote monitoring / alerting
- Vision server migration to the naboo repo (separate work)

## Acceptance Criteria

- [ ] Reboot Mac mini → both services come back up automatically
- [ ] `scripts/test_vision_e2e.sh` passes when Naboo is powered on
- [ ] `scripts/naboo-status.sh` gives clean summary
- [ ] Launchd plists committed to `infra/launchd/` in repo

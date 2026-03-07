#!/bin/bash
# Stop MLX server (idempotent - safe to run if already stopped)
PLIST="$HOME/Library/LaunchAgents/ai.naboo.mlx-server.plist"
SERVICE="ai.naboo.mlx-server"

if ! launchctl list | grep -q "$SERVICE"; then
    echo "$(date): MLX server not running, skipping stop"
    exit 0
fi

launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
pkill -f mlx_lm.server 2>/dev/null || true
echo "$(date): MLX server stopped"

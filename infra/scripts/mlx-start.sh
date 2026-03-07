#!/bin/bash
# Start MLX server (idempotent - safe to run if already running)
PLIST="$HOME/Library/LaunchAgents/ai.naboo.mlx-server.plist"
SERVICE="ai.naboo.mlx-server"

if launchctl list | grep -q "$SERVICE"; then
    echo "$(date): MLX server already running, skipping start"
    exit 0
fi

launchctl bootstrap gui/$(id -u) "$PLIST"
echo "$(date): MLX server started"

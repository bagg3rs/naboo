#!/bin/bash
# Run a batch job against MLX, starting/stopping the server as needed.
# Usage: mlx-batch.sh <your command or script>
#
# Example:
#   mlx-batch.sh python3 ~/my-batch-job.py
#
# If MLX is already running (e.g. during daytime hours), it will be left
# running after the batch completes. If we had to start it, we stop it again.

PLIST="$HOME/Library/LaunchAgents/ai.naboo.mlx-server.plist"
SERVICE="ai.naboo.mlx-server"
WAIT_SECS=60  # Max seconds to wait for server to become ready

WE_STARTED=false

if ! launchctl list | grep -q "$SERVICE"; then
    echo "$(date): MLX not running — starting for batch job..."
    launchctl bootstrap gui/$(id -u) "$PLIST"
    WE_STARTED=true

    # Wait for server to become ready
    for i in $(seq 1 $WAIT_SECS); do
        if curl -s --max-time 2 http://localhost:11435/v1/models >/dev/null 2>&1; then
            echo "$(date): MLX ready after ${i}s"
            break
        fi
        sleep 1
        if [ $i -eq $WAIT_SECS ]; then
            echo "$(date): ERROR — MLX failed to start after ${WAIT_SECS}s"
            exit 1
        fi
    done
else
    echo "$(date): MLX already running — proceeding with batch"
fi

# Run the batch job
echo "$(date): Running: $@"
"$@"
EXIT_CODE=$?
echo "$(date): Batch job finished (exit $EXIT_CODE)"

# Only stop the server if we started it
if [ "$WE_STARTED" = true ]; then
    echo "$(date): Stopping MLX (we started it)"
    launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null
    pkill -f mlx_lm.server 2>/dev/null || true
fi

exit $EXIT_CODE

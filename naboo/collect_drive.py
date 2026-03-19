"""
Naboo Collect Mode — simple autonomous driving for data collection.

No VLM, no Haiku, zero tokens. Just ultrasonic sensor + random walk.
Designed to efficiently collect depth + motor training data.
"""

import asyncio
import json
import logging
import os
import random
import time
from collections import deque

log = logging.getLogger("naboo.collect_drive")

SPEED_FWD = 22
SPEED_TURN = 20
SPEED_BACK = 18
SAFETY_CM = 20
CAUTION_CM = 35
MAX_SECS = 600  # 10 minutes per run
BOUNCE_WINDOW = 15
BOUNCE_LIMIT = 3

# Vary behavior to get diverse training data
TURN_DURATION_MIN = 0.4
TURN_DURATION_MAX = 1.2
FORWARD_DURATION_MIN = 1.0
FORWARD_DURATION_MAX = 4.0
RANDOM_TURN_CHANCE = 0.15  # 15% chance of random turn each cycle


class CollectDriver:
    """Simple autonomous driver for training data collection."""

    def __init__(self, mqtt_client):
        self.mqtt = mqtt_client
        self._task = None  # asyncio.Task or None
        self._running = False
        self._distance: float = 999.0
        self._battery: float = 100.0
        self._last_telem: float = 0.0
        self._collision: bool = False
        self._bounces: deque[float] = deque()
        self._last_turn_dir = None  # Alternate turns
        self._pitch: float = 0.0
        self._accel_x: float = 0.0
        self._impact_cooldown: float = 0.0  # Prevent double-triggers

    async def start(self):
        if self._running:
            return
        self._running = True
        self._collision = False
        self._bounces.clear()
        self.mqtt.subscribe("mbot2/telemetry")
        self.mqtt.subscribe("mbot2/collision")
        self._task = asyncio.create_task(self._loop())
        log.info("🚀 Collect drive started (no VLM, no tokens)")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._motor("stop", 0)
        log.info("🛑 Collect drive stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def on_telemetry(self, data: dict):
        self._distance = float(data.get("d", 999))
        self._battery = float(data.get("b", 100))
        self._pitch = float(data.get("pitch", 0))
        self._accel_x = float(data.get("ax", 0))
        self._last_telem = time.monotonic()

        # IMU-based collision: sudden pitch change or deceleration spike
        now = time.monotonic()
        if now > self._impact_cooldown:
            if abs(self._pitch) > 15 or abs(self._accel_x) > 3:
                log.info("💥 IMU impact detected: pitch=%.1f accel_x=%.1f", self._pitch, self._accel_x)
                self._collision = True
                self._impact_cooldown = now + 2.0  # 2s cooldown

    def on_collision(self):
        self._collision = True

    def _pick_turn(self) -> str:
        """Alternate turn direction to avoid spinning in circles."""
        if self._last_turn_dir == "turn_left":
            self._last_turn_dir = "turn_right"
        elif self._last_turn_dir == "turn_right":
            self._last_turn_dir = "turn_left"
        else:
            self._last_turn_dir = random.choice(["turn_left", "turn_right"])
        return self._last_turn_dir

    async def _loop(self):
        t0 = time.monotonic()
        try:
            while self._running:
                elapsed = time.monotonic() - t0
                if elapsed > MAX_SECS:
                    log.info("⏱️ Time limit reached (%.0fs)", elapsed)
                    break
                if self._battery < 20:
                    log.info("🔋 Low battery (%.0f%%)", self._battery)
                    break

                # Collision recovery
                if self._collision:
                    self._collision = False
                    self._motor("stop", 0)
                    await asyncio.sleep(0.1)
                    self._motor("move_backward", SPEED_BACK)
                    await asyncio.sleep(0.6)
                    turn = self._pick_turn()
                    self._motor(turn, SPEED_TURN)
                    await asyncio.sleep(random.uniform(TURN_DURATION_MIN, TURN_DURATION_MAX))
                    self._record_bounce()
                    continue

                # Stuck detection — do a bigger turn
                if self._is_stuck():
                    log.info("🔄 Stuck — bigger turn")
                    self._motor("stop", 0)
                    await asyncio.sleep(0.1)
                    turn = self._pick_turn()
                    self._motor(turn, SPEED_TURN)
                    await asyncio.sleep(2.0)  # Bigger turn
                    self._bounces.clear()
                    continue

                # Too close — back up and turn
                if self._distance < SAFETY_CM:
                    self._motor("stop", 0)
                    await asyncio.sleep(0.1)
                    self._motor("move_backward", SPEED_BACK)
                    await asyncio.sleep(0.5)
                    turn = self._pick_turn()
                    self._motor(turn, SPEED_TURN)
                    dur = random.uniform(TURN_DURATION_MIN, TURN_DURATION_MAX)
                    await asyncio.sleep(dur)
                    self._record_bounce()
                    continue

                # Caution zone — slow turn
                if self._distance < CAUTION_CM:
                    turn = self._pick_turn()
                    self._motor(turn, SPEED_TURN)
                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    continue

                # Random turn for variety (gets more diverse training data)
                if random.random() < RANDOM_TURN_CHANCE:
                    turn = random.choice(["turn_left", "turn_right"])
                    self._last_turn_dir = turn
                    self._motor(turn, SPEED_TURN)
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                # Drive forward
                self._motor("move_forward", SPEED_FWD)
                await asyncio.sleep(random.uniform(FORWARD_DURATION_MIN, FORWARD_DURATION_MAX))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("❌ Collect drive crashed: %s", e, exc_info=True)
        finally:
            self._motor("stop", 0)
            self._running = False

    def _motor(self, cmd: str, speed: int):
        self.mqtt.publish("mbot2/command", json.dumps({
            "command_type": cmd, "parameters": {"speed": speed}
        }))

    def _record_bounce(self):
        now = time.monotonic()
        self._bounces.append(now)
        while self._bounces and (now - self._bounces[0]) > BOUNCE_WINDOW:
            self._bounces.popleft()

    def _is_stuck(self) -> bool:
        now = time.monotonic()
        while self._bounces and (now - self._bounces[0]) > BOUNCE_WINDOW:
            self._bounces.popleft()
        return len(self._bounces) >= BOUNCE_LIMIT

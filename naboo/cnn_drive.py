"""
Naboo CNN Navigation — TinyNav-powered autonomous driving.

The "fast brain": reactive navigation using depth → CNN → steering/throttle.
No cloud API calls. No tokens. Pure neural network instinct.

Runs on Mac mini (.50). Gets depth from collect_service, sends motor commands via MQTT.
"""

import asyncio
import glob
import json
import logging
import os
import time

import httpx
import numpy as np
import torch
from prometheus_client import Counter, Gauge, Histogram

log = logging.getLogger("naboo.cnn_drive")

# Import the model architecture
from naboo.train_tinynav import TinyNavCNN, WINDOW_SIZE, DEPTH_SIZE

COLLECT_URL = os.environ.get("COLLECT_URL", "http://localhost:8082")
SPEED_SCALE = 50  # Max motor speed
TURN_SPEED = 35
MIN_THROTTLE = 0.10  # Below this = stop
INFERENCE_HZ = 4  # Target inference rate
EMERGENCY_STOP_CM = 10  # Hard ultrasonic override — stop no matter what CNN says
CAUTION_CM = 20  # Force a turn if ultrasonic reads closer than this

# Prometheus metrics
cnn_inferences = Counter("naboo_cnn_inferences_total", "Total CNN inferences")
cnn_inference_ms = Histogram("naboo_cnn_inference_ms", "CNN inference time ms",
                              buckets=[1, 2, 5, 10, 20, 50])
cnn_steering = Gauge("naboo_cnn_steering", "CNN predicted steering")
cnn_throttle = Gauge("naboo_cnn_throttle", "CNN predicted throttle")
cnn_active = Gauge("naboo_cnn_active", "CNN drive active")
cnn_motor_cmd = Counter("naboo_cnn_motor_commands_total", "CNN motor commands", ["action"])
cnn_stops = Counter("naboo_cnn_stops_total", "CNN stop decisions (throttle below threshold)")


class CNNDriver:
    """TinyNav CNN-powered autonomous navigation."""

    def __init__(self, mqtt_client, model_path=None):
        self.mqtt = mqtt_client
        self._task = None
        self._running = False
        self._model = None
        self._device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._window = []  # Last N depth frames
        self._ultrasonic_cm = 999  # Latest ultrasonic reading
        self._stats = {"inferences": 0, "avg_ms": 0, "last_steer": 0, "last_throttle": 0}

        # Find latest model if not specified
        if model_path is None:
            model_dir = os.path.join(os.path.dirname(__file__), "..", "models")
            models = sorted(glob.glob(os.path.join(model_dir, "tinynav_*.pt")))
            if models:
                model_path = models[-1]

        if model_path and os.path.exists(model_path):
            self._load_model(model_path)
        else:
            log.error("No TinyNav model found!")

    def _load_model(self, path):
        log.info("Loading TinyNav model: %s", path)
        checkpoint = torch.load(path, map_location=self._device)
        self._model = TinyNavCNN().to(self._device)
        self._model.load_state_dict(checkpoint["model_state_dict"])
        self._model.eval()
        params = sum(p.numel() for p in self._model.parameters())
        log.info("Model loaded: %d params, device=%s", params, self._device)

    async def start(self):
        if self._running:
            return
        if not self._model:
            log.error("Cannot start — no model loaded")
            return
        self._running = True
        self._window = []
        self._stats = {"inferences": 0, "avg_ms": 0, "last_steer": 0, "last_throttle": 0}
        self._task = asyncio.create_task(self._loop())
        cnn_active.set(1)
        log.info("🧠 CNN navigation started")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        # Send stop immediately before waiting for task
        self._motor("stop", 0)
        self._motor("stop", 0)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Send stop again after task is dead
        self._motor("stop", 0)
        cnn_active.set(0)
        log.info("🛑 CNN navigation stopped (inferences: %d, avg: %.0fms)",
                 self._stats["inferences"], self._stats["avg_ms"])

    @property
    def is_running(self):
        return self._running

    def on_telemetry(self, data):
        """Update ultrasonic reading from mBot2 telemetry."""
        d = data.get("d", 999)
        if d is not None and d < 500:
            self._ultrasonic_cm = d

    @property
    def stats(self):
        return self._stats

    async def _get_depth(self):
        """Get current 24x24 depth from collection service."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{COLLECT_URL}/depth?raw=1&small")
                if r.status_code == 200:
                    data = r.json()
                    return np.array(data["depth"], dtype=np.float32)
        except Exception as e:
            log.error("Depth fetch error: %s", e)
        return None

    def _predict(self, window):
        """Run CNN inference on a window of depth frames."""
        # Stack and normalize
        stacked = np.array(window)  # (10, 24, 24)
        w_min, w_max = stacked.min(), stacked.max()
        if w_max - w_min > 0:
            stacked = (stacked - w_min) / (w_max - w_min)
        else:
            stacked = np.zeros_like(stacked)

        # To tensor: (1, 10, 24, 24)
        tensor = torch.FloatTensor(stacked).unsqueeze(0).to(self._device)

        t0 = time.time()
        with torch.no_grad():
            steer, throttle = self._model(tensor)
        ms = (time.time() - t0) * 1000

        return steer.item(), throttle.item(), ms

    async def _loop(self):
        try:
            while self._running:
                t0 = time.time()

                # Get depth frame
                depth = await self._get_depth()
                if depth is None:
                    await asyncio.sleep(0.5)
                    continue

                # Build sliding window
                self._window.append(depth)
                if len(self._window) > WINDOW_SIZE:
                    self._window.pop(0)
                if len(self._window) < WINDOW_SIZE:
                    # Not enough frames yet — keep collecting
                    await asyncio.sleep(0.1)
                    continue

                # Run inference
                steer, throttle, ms = self._predict(self._window)

                # Update stats + metrics
                n = self._stats["inferences"]
                self._stats["avg_ms"] = (self._stats["avg_ms"] * n + ms) / (n + 1)
                self._stats["inferences"] = n + 1
                self._stats["last_steer"] = round(steer, 2)
                self._stats["last_throttle"] = round(throttle, 2)

                cnn_inferences.inc()
                cnn_inference_ms.observe(ms)
                cnn_steering.set(steer)
                cnn_throttle.set(throttle)

                # Safety override: ultrasonic hard limits
                dist = self._ultrasonic_cm
                if dist < EMERGENCY_STOP_CM:
                    self._motor("stop", 0)
                    cnn_stops.inc()
                    if self._stats["inferences"] % 10 == 0:
                        log.info("🛑 Emergency stop: %dcm", dist)
                elif dist < CAUTION_CM and abs(steer) < 0.3:
                    # Too close and CNN says go straight — force a turn
                    cmd = "turn_left" if self._stats["inferences"] % 2 == 0 else "turn_right"
                    self._motor(cmd, TURN_SPEED)
                    cnn_motor_cmd.labels(action=cmd).inc()
                    if self._stats["inferences"] % 10 == 0:
                        log.info("⚠️ Caution override: %dcm, forcing turn", dist)
                # Normal CNN control
                elif throttle < MIN_THROTTLE:
                    self._motor("stop", 0)
                    cnn_stops.inc()
                elif abs(steer) > 0.3:
                    cmd = "turn_left" if steer < 0 else "turn_right"
                    speed = int(TURN_SPEED * min(abs(steer), 1.0))
                    self._motor(cmd, max(speed, 15))
                    cnn_motor_cmd.labels(action=cmd).inc()
                else:
                    speed = int(SPEED_SCALE * throttle)
                    self._motor("force_forward", max(speed, 15))
                    cnn_motor_cmd.labels(action="forward").inc()

                if self._stats["inferences"] % 20 == 0:
                    log.info("🧠 CNN: steer=%.2f throttle=%.2f (%.0fms)",
                             steer, throttle, ms)

                # Maintain target Hz
                elapsed = time.time() - t0
                sleep_time = max(0.05, (1.0 / INFERENCE_HZ) - elapsed)
                await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("CNN drive error: %s", e, exc_info=True)
        finally:
            self._motor("stop", 0)
            self._running = False

    def _motor(self, cmd, speed):
        self.mqtt.publish("mbot2/command", json.dumps({
            "command_type": cmd, "parameters": {"speed": speed}
        }))

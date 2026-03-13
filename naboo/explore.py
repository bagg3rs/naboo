"""
Naboo Explore Module — VLM-in-the-loop autonomous exploration.

Cycle (~7s): snapshot → VLM describes → Haiku decides action+narration → move → speak → repeat
"""

import asyncio
import base64
import json
import logging
import time
from collections import deque

import boto3
import httpx

log = logging.getLogger("naboo.explore")

CAMERA_URL = "http://192.168.0.163/"
VLM_URL = "http://192.168.0.50:11436/vision"
HAIKU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_REGION = "eu-west-2"

SPEED_FWD, SPEED_TURN, SPEED_BACK = 30, 35, 25
SAFETY_CM = 10
LOW_BATTERY = 20
MAX_SECS = 300
TELEM_TIMEOUT = 30  # seconds without telemetry (generous for WiFi drops)
BOUNCE_WINDOW = 20
BOUNCE_LIMIT = 4
MOVE_SECS = 1.5

VLM_QUESTION = (
    "Describe what you see in one sentence. Note any obstacles, walls, "
    "furniture, people, or open space ahead. Be specific about directions."
)

HAIKU_SYSTEM = (
    "You are Naboo, a curious little robot exploring a room. "
    "Given a scene description, sensor data, and recent actions, decide the next move.\n\n"
    "Respond with EXACTLY this JSON (no markdown, no extras):\n"
    '{"action": "forward"|"turn_left"|"turn_right"|"stop", "narration": "..."}\n\n'
    "Rules:\n"
    "- narration: one short kid-friendly sentence about what you see (max 15 words)\n"
    "- If obstacle < 25cm ahead, turn. If open space, go forward.\n"
    "- Vary direction — don't always turn the same way.\n"
    "- If battery < 25%, say you're tired and action=stop."
)


def _haiku_prompt(scene: str, dist: float, bat: float, recent: list[str]) -> str:
    r = ", ".join(recent[-5:]) if recent else "none"
    return f"Scene: {scene}\nUltrasonic: {dist:.0f}cm\nBattery: {bat:.0f}%\nRecent: {r}\n\nNext move?"


class ExploreController:
    """Autonomous VLM-in-the-loop exploration."""

    def __init__(self, mqtt_client):
        self.mqtt = mqtt_client
        self._task: asyncio.Task | None = None
        self._running = False
        self._distance: float = 999.0
        self._battery: float = 100.0
        self._last_telem: float = 0.0
        self._collision: bool = False
        self._recent: list[str] = []
        self._bounces: deque[float] = deque()
        self._bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

    # --- Public API ---

    async def start(self):
        if self._running:
            log.warning("Explore already running")
            return
        self._running = True
        self._collision = False
        self._recent.clear()
        self._bounces.clear()
        self.mqtt.subscribe("mbot2/telemetry")
        self.mqtt.subscribe("mbot2/collision")
        self._task = asyncio.create_task(self._loop())
        log.info("🚀 Explore started")

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
        log.info("🛑 Explore stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # --- MQTT callbacks (called from paho thread) ---

    def on_telemetry(self, data: dict):
        self._distance = float(data.get("d", 999))
        self._battery = float(data.get("b", 100))
        self._last_telem = time.monotonic()

    def on_collision(self):
        self._collision = True
        log.warning("⚠️ Collision detected")

    # --- Core loop ---

    async def _loop(self):
        t0 = time.monotonic()
        try:
            self._speak("Time to explore! Let's see what's around.")
            await asyncio.sleep(1.5)

            while self._running:
                # Safety checks
                if time.monotonic() - t0 > MAX_SECS:
                    self._speak("I've been exploring a while. Time to rest!")
                    break
                if self._battery < LOW_BATTERY:
                    self._speak("My battery is low. Stopping to be safe.")
                    break
                if self._last_telem and (time.monotonic() - self._last_telem > TELEM_TIMEOUT):
                    self._speak("I lost my sensors. Stopping to be safe.")
                    break

                # Emergency: too close
                if self._distance < SAFETY_CM:
                    log.warning("🚨 Emergency — obstacle at %.0fcm", self._distance)
                    self._motor("stop", 0)
                    self._motor("move_backward", SPEED_BACK)
                    await asyncio.sleep(0.8)
                    self._motor("stop", 0)
                    self._record_bounce()
                    await asyncio.sleep(0.3)
                    continue

                # Collision recovery
                if self._collision:
                    self._collision = False
                    self._motor("stop", 0)
                    self._motor("move_backward", SPEED_BACK)
                    await asyncio.sleep(0.8)
                    self._motor("stop", 0)
                    self._record_bounce()
                    await asyncio.sleep(0.3)
                    continue

                # Stuck → 180° turn
                if self._is_stuck():
                    log.info("🔄 Stuck — 180° turn")
                    self._speak("I keep bumping into things. Let me turn around!")
                    self._motor("turn_left", SPEED_TURN)
                    await asyncio.sleep(2.5)
                    self._motor("stop", 0)
                    self._bounces.clear()
                    self._recent.append("turn_180")
                    await asyncio.sleep(0.5)
                    continue

                # === Decision cycle ===
                try:
                    image_b64 = await self._capture()
                    if not image_b64:
                        await asyncio.sleep(2)
                        continue

                    scene = await self._describe(image_b64) or "Camera error — can't see."
                    action, narration = await self._decide(scene)

                    log.info("🤖 %s | %s | dist=%.0fcm bat=%.0f%%",
                             action, narration, self._distance, self._battery)

                    if action == "stop":
                        self._speak(narration)
                        break
                    elif action == "forward":
                        self._motor("move_forward", SPEED_FWD)
                    elif action == "turn_left":
                        self._motor("turn_left", SPEED_TURN)
                    elif action == "turn_right":
                        self._motor("turn_right", SPEED_TURN)
                    else:
                        self._motor("move_forward", SPEED_FWD)

                    self._recent.append(action)
                    self._speak(narration)
                    await asyncio.sleep(MOVE_SECS)
                    self._motor("stop", 0)
                    await asyncio.sleep(0.3)

                except Exception as e:
                    log.error("❌ Cycle error: %s", e, exc_info=True)
                    self._motor("stop", 0)
                    await asyncio.sleep(3)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("❌ Explore crashed: %s", e, exc_info=True)
        finally:
            self._motor("stop", 0)
            self._running = False

    # --- Camera ---

    async def _capture(self) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(CAMERA_URL)
                r.raise_for_status()
                return base64.b64encode(r.content).decode()
        except Exception as e:
            log.error("📷 Camera error: %s", e)
            return None

    # --- VLM ---

    async def _describe(self, image_b64: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(VLM_URL, json={"image_b64": image_b64, "question": VLM_QUESTION})
                r.raise_for_status()
                desc = r.json().get("description", "")
                log.info("👁️ VLM: %s", desc[:120])
                return desc
        except Exception as e:
            log.error("👁️ VLM error: %s", e)
            return None

    # --- Haiku decision ---

    async def _decide(self, scene: str) -> tuple[str, str]:
        prompt = _haiku_prompt(scene, self._distance, self._battery, self._recent)
        try:
            resp = await asyncio.to_thread(
                self._bedrock.invoke_model,
                modelId=HAIKU_MODEL,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 100,
                    "system": HAIKU_SYSTEM,
                    "messages": [{"role": "user", "content": prompt}],
                }),
            )
            raw = resp["body"].read().decode()
            log.debug("Haiku raw: %s", raw[:200])
            text = json.loads(raw)["content"][0]["text"].strip()
            # Handle markdown-wrapped JSON
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(text)
            action = parsed.get("action", "forward")
            narration = parsed.get("narration", "I see something interesting!")
            if action not in ("forward", "turn_left", "turn_right", "stop"):
                action = "forward"
            return action, narration
        except Exception as e:
            log.error("🧠 Haiku error: %s", e)
            return ("turn_left", "Something's in my way!") if self._distance < 30 else ("forward", "Onward!")

    # --- Helpers ---

    def _motor(self, cmd: str, speed: int):
        self.mqtt.publish("mbot2/command", json.dumps({"command_type": cmd, "parameters": {"speed": speed}}))

    def _speak(self, text: str):
        self.mqtt.publish("mbot2/speak", json.dumps({"text": text}))

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

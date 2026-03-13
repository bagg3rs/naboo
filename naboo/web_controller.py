"""
Naboo Web Controller — video feed + manual controls + telemetry.

Run: uv run python3 -m naboo.web_controller
Open: http://192.168.0.50:8888
"""

import asyncio
import json
import logging
import os
import threading

import paho.mqtt.client as mqtt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

log = logging.getLogger("naboo.web_controller")

MQTT_BROKER = os.environ.get("MQTT_BROKER", "192.168.0.50")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
CAMERA_STREAM = os.environ.get("CAMERA_STREAM", "http://192.168.0.163:8080")
CAMERA_SNAPSHOT = os.environ.get("CAMERA_SNAPSHOT", "http://192.168.0.163/")

app = FastAPI(title="Naboo Controller")

HA_URL = "http://192.168.0.201:8123"
HA_TTS_ENTITY = "tts.home_assistant_cloud"
HA_MEDIA_PLAYER = "media_player.home_assistant_voice_093cd7_media_player"
HA_TOKEN = os.environ.get("HA_TOKEN", "")


def _ha_tts(text: str):
    """Send TTS to HA Voice speaker."""
    try:
        import urllib.request
        payload = json.dumps({
            "entity_id": HA_TTS_ENTITY,
            "media_player_entity_id": HA_MEDIA_PLAYER,
            "message": text,
        }).encode()
        req = urllib.request.Request(
            f"{HA_URL}/api/services/tts/speak",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {HA_TOKEN}"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
        log.info("TTS: %s", text[:80])
    except Exception as e:
        log.error("TTS error: %s", e)

# ── MQTT ──────────────────────────────────────────────────────────────────────

mqtt_client = mqtt.Client(client_id="naboo-web-controller", protocol=mqtt.MQTTv5)
telemetry_data = {"d": 0, "b": 0, "dm": 0, "h": 0}
ws_clients: list[WebSocket] = []


def on_connect(client, userdata, flags, reason_code, properties):
    client.subscribe("mbot2/telemetry", qos=0)
    client.subscribe("mbot2/collision", qos=0)
    log.info("MQTT connected, subscribed to telemetry")


def on_message(client, userdata, msg):
    global telemetry_data
    try:
        if msg.topic == "mbot2/telemetry":
            telemetry_data = json.loads(msg.payload.decode())
            # Broadcast to all WebSocket clients
            for ws in ws_clients[:]:
                try:
                    asyncio.run_coroutine_threadsafe(
                        ws.send_json({"type": "telemetry", **telemetry_data}),
                        loop,
                    )
                except Exception:
                    pass
        elif msg.topic == "mbot2/collision":
            for ws in ws_clients[:]:
                try:
                    asyncio.run_coroutine_threadsafe(
                        ws.send_json({"type": "collision"}),
                        loop,
                    )
                except Exception:
                    pass
    except Exception as e:
        log.error("MQTT message error: %s", e)


mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# ── Commands ──────────────────────────────────────────────────────────────────

COMMANDS = {
    "forward": {"command_type": "move_forward", "parameters": {"speed": 30}},
    "backward": {"command_type": "move_backward", "parameters": {"speed": 25}},
    "left": {"command_type": "turn_left", "parameters": {"speed": 25}},
    "right": {"command_type": "turn_right", "parameters": {"speed": 25}},
    "stop": {"command_type": "stop", "parameters": {"speed": 0}},
}


def send_command(cmd: str):
    if cmd in COMMANDS:
        mqtt_client.publish("mbot2/command", json.dumps(COMMANDS[cmd]))
        log.info("Command: %s", cmd)


# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Naboo Controller</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a2e; color: #e0e0e0; font-family: system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center; height: 100vh; padding: 10px; }
  h1 { color: #a855f7; margin: 8px 0; font-size: 1.4em; }
  .video-container { position: relative; width: 100%; max-width: 640px;
                     border: 2px solid #a855f7; border-radius: 8px; overflow: hidden; }
  .video-container img { width: 100%; display: block; }
  .controls { display: grid; grid-template-areas: ". up ." "left stop right" ". down .";
              gap: 8px; margin: 12px 0; }
  .btn { width: 70px; height: 70px; border: none; border-radius: 12px; font-size: 28px;
         cursor: pointer; background: #16213e; color: #e0e0e0; transition: all 0.1s;
         display: flex; align-items: center; justify-content: center; user-select: none;
         -webkit-user-select: none; touch-action: manipulation; }
  .btn:active, .btn.active { background: #a855f7; transform: scale(0.95); }
  .btn-up { grid-area: up; }
  .btn-down { grid-area: down; }
  .btn-left { grid-area: left; }
  .btn-right { grid-area: right; }
  .btn-stop { grid-area: stop; background: #dc2626; }
  .btn-stop:active, .btn-stop.active { background: #ef4444; }
  .telemetry { display: flex; gap: 20px; margin: 10px 0; font-size: 1.1em; }
  .telemetry span { padding: 6px 12px; background: #16213e; border-radius: 8px; }
  .status { font-size: 0.9em; color: #888; margin-top: 8px; }
  .collision { animation: flash 0.3s ease-in-out 3; }
  @keyframes flash { 50% { border-color: #dc2626; } }
  .explore-btn { margin: 8px; padding: 12px 24px; border: 2px solid #a855f7; background: transparent;
                 color: #a855f7; border-radius: 8px; font-size: 1em; cursor: pointer; }
  .explore-btn.active { background: #a855f7; color: white; }
</style>
</head>
<body>
<h1>🤖 Naboo Controller</h1>

<div class="video-container" id="videoBox">
  <img src="CAMERA_STREAM_URL" alt="Camera Feed" onerror="this.src='CAMERA_SNAPSHOT_URL'; setTimeout(()=>this.src='CAMERA_STREAM_URL',3000)">
</div>

<div class="telemetry">
  <span>📏 <span id="dist">--</span>cm</span>
  <span>🔋 <span id="batt">--</span>%</span>
  <span>🧭 <span id="heading">--</span>°</span>
</div>

<div class="controls">
  <button class="btn btn-up" data-cmd="forward">▲</button>
  <button class="btn btn-left" data-cmd="left">◄</button>
  <button class="btn btn-stop" data-cmd="stop">■</button>
  <button class="btn btn-right" data-cmd="right">►</button>
  <button class="btn btn-down" data-cmd="backward">▼</button>
</div>

<button class="explore-btn" id="exploreBtn" onclick="toggleExplore()">🔍 Explore</button>

<div style="display:flex; gap:8px; margin:10px 0; width:100%; max-width:640px;">
  <input type="text" id="ttsInput" placeholder="Type to speak..." 
         style="flex:1; padding:10px; border-radius:8px; border:1px solid #a855f7; background:#16213e; color:#e0e0e0; font-size:1em;"
         onkeydown="if(event.key==='Enter')sendTTS()">
  <button onclick="sendTTS()" style="padding:10px 16px; border-radius:8px; border:none; background:#a855f7; color:white; font-size:1em; cursor:pointer;">🔊</button>
</div>

<div class="status" id="status">Connecting...</div>

<script>
const ws = new WebSocket(`ws://${location.host}/ws`);
let exploring = false;

ws.onopen = () => { document.getElementById('status').textContent = 'Connected'; };
ws.onclose = () => { document.getElementById('status').textContent = 'Disconnected'; };
ws.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data.type === 'telemetry') {
    document.getElementById('dist').textContent = Math.round(data.d || 0);
    document.getElementById('batt').textContent = Math.round(data.b || 0);
    document.getElementById('heading').textContent = Math.round(data.h || 0);
  } else if (data.type === 'collision') {
    document.getElementById('videoBox').classList.add('collision');
    setTimeout(() => document.getElementById('videoBox').classList.remove('collision'), 1000);
  }
};

// Button controls — hold to move, release to stop
document.querySelectorAll('.btn[data-cmd]').forEach(btn => {
  const cmd = btn.dataset.cmd;

  const start = (e) => {
    e.preventDefault();
    btn.classList.add('active');
    ws.send(JSON.stringify({cmd}));
  };
  const end = (e) => {
    e.preventDefault();
    btn.classList.remove('active');
    if (cmd !== 'stop') ws.send(JSON.stringify({cmd: 'stop'}));
  };

  btn.addEventListener('mousedown', start);
  btn.addEventListener('mouseup', end);
  btn.addEventListener('mouseleave', end);
  btn.addEventListener('touchstart', start, {passive: false});
  btn.addEventListener('touchend', end, {passive: false});
});

// Keyboard controls
const keyMap = {ArrowUp: 'forward', ArrowDown: 'backward', ArrowLeft: 'left', ArrowRight: 'right', ' ': 'stop'};
const pressed = new Set();
document.addEventListener('keydown', (e) => {
  if (keyMap[e.key] && !pressed.has(e.key)) {
    pressed.add(e.key);
    ws.send(JSON.stringify({cmd: keyMap[e.key]}));
    const btn = document.querySelector(`[data-cmd="${keyMap[e.key]}"]`);
    if (btn) btn.classList.add('active');
  }
});
document.addEventListener('keyup', (e) => {
  if (keyMap[e.key]) {
    pressed.delete(e.key);
    if (keyMap[e.key] !== 'stop') ws.send(JSON.stringify({cmd: 'stop'}));
    const btn = document.querySelector(`[data-cmd="${keyMap[e.key]}"]`);
    if (btn) btn.classList.remove('active');
  }
});

function toggleExplore() {
  exploring = !exploring;
  const btn = document.getElementById('exploreBtn');
  btn.classList.toggle('active', exploring);
  btn.textContent = exploring ? '🛑 Stop Explore' : '🔍 Explore';
  ws.send(JSON.stringify({cmd: exploring ? 'explore' : 'stop_explore'}));
}

function sendTTS() {
  const input = document.getElementById('ttsInput');
  const text = input.value.trim();
  if (text) {
    ws.send(JSON.stringify({cmd: 'tts', text: text}));
    input.value = '';
  }
}
</script>
</body>
</html>""".replace("CAMERA_STREAM_URL", CAMERA_STREAM).replace("CAMERA_SNAPSHOT_URL", CAMERA_SNAPSHOT)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    log.info("WebSocket client connected (%d total)", len(ws_clients))
    try:
        # Send current telemetry immediately
        await ws.send_json({"type": "telemetry", **telemetry_data})
        while True:
            data = await ws.receive_json()
            cmd = data.get("cmd", "")
            if cmd == "explore":
                # Publish explore trigger via MQTT question
                mqtt_client.publish("naboo/questions", json.dumps({
                    "question": "go explore",
                    "user": "web-controller",
                    "conversation_id": f"web-{id(ws)}",
                }))
            elif cmd == "stop_explore":
                mqtt_client.publish("naboo/questions", json.dumps({
                    "question": "stop exploring",
                    "user": "web-controller",
                    "conversation_id": f"web-{id(ws)}",
                }))
            elif cmd == "tts":
                text = data.get("text", "")
                if text:
                    _ha_tts(text)
            else:
                send_command(cmd)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error("WebSocket error: %s", e)
    finally:
        ws_clients.remove(ws)
        log.info("WebSocket client disconnected (%d remaining)", len(ws_clients))


# ── Main ──────────────────────────────────────────────────────────────────────

loop = None


def main():
    global loop
    import uvicorn

    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()
    log.info("MQTT connected to %s:%d", MQTT_BROKER, MQTT_PORT)

    loop = asyncio.new_event_loop()

    config = uvicorn.Config(app, host="0.0.0.0", port=8888, loop=loop, log_level="info")
    server = uvicorn.Server(config)

    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

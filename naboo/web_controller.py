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
CAMERA_STREAM = os.environ.get("CAMERA_STREAM", "http://192.168.0.31:8080/stream")
CAMERA_SNAPSHOT = os.environ.get("CAMERA_SNAPSHOT", "http://192.168.0.31:8080/")
DETECT_URL = os.environ.get("DETECT_URL", "http://192.168.0.50:8081/detect")
COLLECT_URL = os.environ.get("COLLECT_URL", "http://192.168.0.50:8082")

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

from naboo.collect_drive import CollectDriver

# ── MQTT ──────────────────────────────────────────────────────────────────────

mqtt_client = mqtt.Client(client_id="naboo-web-controller", protocol=mqtt.MQTTv5)
telemetry_data = {"d": 0, "b": 0, "dm": 0, "h": 0}
ws_clients: list[WebSocket] = []
collect_driver = None  # type: CollectDriver


def on_connect(client, userdata, flags, reason_code, properties):
    client.subscribe("mbot2/telemetry", qos=0)
    client.subscribe("mbot2/collision", qos=0)
    log.info("MQTT connected, subscribed to telemetry")


def on_message(client, userdata, msg):
    global telemetry_data
    try:
        if msg.topic == "mbot2/telemetry":
            telemetry_data = json.loads(msg.payload.decode())
            # Forward to collect driver
            if collect_driver:
                collect_driver.on_telemetry(telemetry_data)
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
            if collect_driver:
                collect_driver.on_collision()
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


def send_command(cmd: str, speed: int = None):
    if cmd in COMMANDS:
        payload = dict(COMMANDS[cmd])
        if speed is not None:
            payload["parameters"] = {"speed": speed}
        mqtt_client.publish("mbot2/command", json.dumps(payload))
        log.info("Command: %s speed=%s", cmd, speed)


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
         display: flex; flex-direction: column; align-items: center; min-height: 100vh; padding: 10px; }
  h1 { color: #a855f7; margin: 8px 0; font-size: 1.4em; }
  .video-container { position: relative; width: 100%; max-width: 720px;
                     border: 2px solid #a855f7; border-radius: 8px; overflow: hidden; }
  .video-container img { width: 100%; height: auto; display: block; }
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
  .detect-btn { margin: 8px; padding: 12px 24px; border: 2px solid #22c55e; background: transparent;
                color: #22c55e; border-radius: 8px; font-size: 1em; cursor: pointer; }
  .detect-btn.active { background: #22c55e; color: white; }
  .collect-btn { margin: 8px; padding: 12px 24px; border: 2px solid #f59e0b; background: transparent;
                 color: #f59e0b; border-radius: 8px; font-size: 1em; cursor: pointer; }
  .collect-btn.active { background: #f59e0b; color: white; }
  .record-btn { margin: 8px; padding: 12px 24px; border: 2px solid #ef4444; background: transparent;
                color: #ef4444; border-radius: 8px; font-size: 1em; cursor: pointer; }
  .record-btn.active { background: #ef4444; color: white; animation: pulse 1s infinite; }
  @keyframes pulse { 50% { opacity: 0.7; } }
  .depth-container { width: 100%; max-width: 720px; display: flex; gap: 8px; margin: 8px 0; }
  .depth-container img { flex: 1; border-radius: 8px; border: 1px solid #333; }
  .detect-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; }
  .detect-label { position: absolute; background: rgba(34,197,94,0.85); color: #000; padding: 2px 6px;
                  font-size: 0.75em; font-weight: bold; border-radius: 3px; white-space: nowrap; }
  .detect-box { position: absolute; border: 2px solid #22c55e; border-radius: 2px; }
  .detect-info { position: absolute; bottom: 4px; right: 4px; background: rgba(0,0,0,0.7);
                 color: #22c55e; padding: 2px 8px; font-size: 0.75em; border-radius: 4px; }
  .speed-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; font-size: 0.95em; }
  .speed-row input[type=range] { flex: 1; accent-color: #a855f7; }
</style>
</head>
<body>
<h1>🤖 Naboo Controller</h1>

<div class="video-container" id="videoBox">
  <img id="camFeed" src="CAMERA_STREAM_URL" alt="Camera Feed" onerror="this.src='CAMERA_SNAPSHOT_URL'; setTimeout(()=>this.src='CAMERA_STREAM_URL',3000)">
  <div class="detect-overlay" id="detectOverlay"></div>
</div>

<div class="telemetry">
  <span>📏 <span id="dist">--</span>cm</span>
  <span>🔋 <span id="batt">--</span>%</span>
  <span>🧭 <span id="heading">--</span>°</span>
  <span>📐 <span id="tilt">--</span></span>
</div>

<canvas id="pathCanvas" width="300" height="300" style="border:1px solid #333; border-radius:8px; background:#111; margin:8px 0;"></canvas>

<div class="controls">
  <button class="btn btn-up" data-cmd="forward">▲</button>
  <button class="btn btn-left" data-cmd="left">◄</button>
  <button class="btn btn-stop" data-cmd="stop">■</button>
  <button class="btn btn-right" data-cmd="right">►</button>
  <button class="btn btn-down" data-cmd="backward">▼</button>
</div>

<button class="explore-btn" id="exploreBtn" onclick="toggleExplore()">🔍 Explore</button>
<button class="collect-btn" id="collectBtn" onclick="toggleCollectAndRecord()">📊 Collect + Record</button>
<button class="detect-btn" id="detectBtn" onclick="toggleDetect()">👁️ Detect</button>
<button class="record-btn" id="recordBtn" onclick="toggleRecord()">🔴 Record Only</button>
<div id="recordInfo" style="color:#ef4444; font-size:0.9em; display:none;">Recording: <span id="frameCount">0</span> frames</div>

<div class="depth-container" id="depthContainer" style="display:none;">
  <img id="depthImg" alt="Depth Map" style="max-height:200px;">
</div>

<div class="speed-row" style="width:100%; max-width:640px;">
  <span>🏎️ Drive</span>
  <input type="range" id="speedSlider" min="15" max="60" value="35" oninput="document.getElementById('speedVal').textContent=this.value">
  <span id="speedVal" style="min-width:2em; text-align:center;">35</span>
</div>
<div class="speed-row" style="width:100%; max-width:640px;">
  <span>🔄 Turn</span>
  <input type="range" id="turnSlider" min="10" max="40" value="20" oninput="document.getElementById('turnVal').textContent=this.value">
  <span id="turnVal" style="min-width:2em; text-align:center;">20</span>
</div>

<div style="display:flex; gap:8px; margin:10px 0; width:100%; max-width:640px;">
  <input type="text" id="ttsInput" placeholder="Type to speak..." 
         style="flex:1; padding:10px; border-radius:8px; border:1px solid #a855f7; background:#16213e; color:#e0e0e0; font-size:1em;"
         onkeydown="if(event.key==='Enter')sendTTS()">
  <button onclick="sendTTS()" style="padding:10px 16px; border-radius:8px; border:none; background:#a855f7; color:white; font-size:1em; cursor:pointer;">🔊</button>
</div>

<div class="status" id="status">Connecting...</div>

<script>
let ws;
let exploring = false;

function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { document.getElementById('status').textContent = 'Connected'; };
  ws.onclose = () => {
    document.getElementById('status').textContent = 'Reconnecting...';
    setTimeout(connectWS, 2000);
  };
  ws.onerror = () => { ws.close(); };
  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'telemetry') {
      document.getElementById('dist').textContent = Math.round(data.d || 0);
      document.getElementById('batt').textContent = Math.round(data.b || 0);
      document.getElementById('heading').textContent = Math.round(data.h || 0);
      const roll = Math.round(data.roll || 0);
      const pitch = Math.round(data.pitch || 0);
      const az = data.az || 0;
      document.getElementById('tilt').textContent = az > 5 ? '🙃 UPSIDE DOWN' : `R${roll}° P${pitch}°`;
    } else if (data.type === 'collision') {
      document.getElementById('videoBox').classList.add('collision');
      setTimeout(() => document.getElementById('videoBox').classList.remove('collision'), 1000);
    }
  };
}
connectWS();

// Path tracker — estimate position from motor commands
const pathCanvas = document.getElementById('pathCanvas');
const pathCtx = pathCanvas.getContext('2d');
let pathX = 150, pathY = 150, pathHeading = 0; // Start center, facing up
let pathHistory = [{x: pathX, y: pathY}];
const PIXELS_PER_MM = 0.3;  // Scale factor
const TURN_RATE = 90;  // degrees per second at speed 20

function updatePath(steering, throttle) {
  const dt = 0.25;  // ~4Hz update rate
  if (Math.abs(steering) > 0.1) {
    pathHeading += steering * TURN_RATE * dt;
  }
  if (Math.abs(throttle) > 0.05) {
    const speed = throttle * 100 * PIXELS_PER_MM;  // mm/s estimate
    pathX += Math.sin(pathHeading * Math.PI / 180) * speed * dt;
    pathY -= Math.cos(pathHeading * Math.PI / 180) * speed * dt;
  }
  pathHistory.push({x: pathX, y: pathY});
  if (pathHistory.length > 2000) pathHistory.shift();
  drawPath();
}

function drawPath() {
  pathCtx.fillStyle = '#111';
  pathCtx.fillRect(0, 0, 300, 300);
  if (pathHistory.length < 2) return;

  // Draw trail
  pathCtx.strokeStyle = '#22c55e';
  pathCtx.lineWidth = 1.5;
  pathCtx.globalAlpha = 0.6;
  pathCtx.beginPath();
  pathCtx.moveTo(pathHistory[0].x, pathHistory[0].y);
  for (let i = 1; i < pathHistory.length; i++) {
    pathCtx.lineTo(pathHistory[i].x, pathHistory[i].y);
  }
  pathCtx.stroke();
  pathCtx.globalAlpha = 1;

  // Draw current position
  const last = pathHistory[pathHistory.length - 1];
  pathCtx.fillStyle = '#ef4444';
  pathCtx.beginPath();
  pathCtx.arc(last.x, last.y, 4, 0, Math.PI * 2);
  pathCtx.fill();

  // Draw heading arrow
  const arrowLen = 12;
  const ax = last.x + Math.sin(pathHeading * Math.PI / 180) * arrowLen;
  const ay = last.y - Math.cos(pathHeading * Math.PI / 180) * arrowLen;
  pathCtx.strokeStyle = '#ef4444';
  pathCtx.lineWidth = 2;
  pathCtx.beginPath();
  pathCtx.moveTo(last.x, last.y);
  pathCtx.lineTo(ax, ay);
  pathCtx.stroke();

  // Start marker
  pathCtx.fillStyle = '#3b82f6';
  pathCtx.beginPath();
  pathCtx.arc(pathHistory[0].x, pathHistory[0].y, 3, 0, Math.PI * 2);
  pathCtx.fill();
}

// Button controls — hold to move, release to stop
document.querySelectorAll('.btn[data-cmd]').forEach(btn => {
  const cmd = btn.dataset.cmd;

  const start = (e) => {
    e.preventDefault();
    btn.classList.add('active');
    const isTurn = (cmd === 'left' || cmd === 'right');
    const speed = parseInt(document.getElementById(isTurn ? 'turnSlider' : 'speedSlider').value);
    ws.send(JSON.stringify({cmd, speed}));
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
    const cmd = keyMap[e.key];
    const isTurn = (cmd === 'left' || cmd === 'right');
    const speed = parseInt(document.getElementById(isTurn ? 'turnSlider' : 'speedSlider').value);
    ws.send(JSON.stringify({cmd, speed}));
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

let collecting = false;
function toggleCollect() {
  collecting = !collecting;
  const btn = document.getElementById('collectBtn');
  btn.classList.toggle('active', collecting);
  btn.textContent = collecting ? '🛑 Stop Collect' : '📊 Collect + Record';
  ws.send(JSON.stringify({cmd: collecting ? 'collect' : 'stop_collect'}));
}

function toggleCollectAndRecord() {
  if (!collecting) {
    // Start record, then collect via HTTP
    fetch(COLLECT_URL + '/record/start', {method: 'POST'}).then(r => r.json()).then(() => {
      isRecording = true;
      document.getElementById('recordBtn').classList.add('active');
      document.getElementById('recordBtn').textContent = '⏹ Stop Record';
      document.getElementById('recordInfo').style.display = 'block';
      document.getElementById('depthContainer').style.display = 'flex';
      recordTimer = setInterval(updateRecordStatus, 500);
      depthTimer = setInterval(updateDepth, 300);
      return fetch('/api/collect/start', {method: 'POST'});
    }).then(r => r.json()).then(() => {
      collecting = true;
      document.getElementById('collectBtn').classList.add('active');
      document.getElementById('collectBtn').textContent = '🛑 Stop';
    }).catch(e => console.error('Start error:', e));
  } else {
    // Stop collect, then record via HTTP
    fetch('/api/collect/stop', {method: 'POST'}).then(r => r.json()).then(() => {
      collecting = false;
      document.getElementById('collectBtn').classList.remove('active');
      document.getElementById('collectBtn').textContent = '📊 Collect + Record';
      return fetch(COLLECT_URL + '/record/stop', {method: 'POST'});
    }).then(r => r.json()).then(data => {
      isRecording = false;
      document.getElementById('recordBtn').classList.remove('active');
      document.getElementById('recordBtn').textContent = '🔴 Record Only';
      document.getElementById('recordInfo').style.display = 'none';
      document.getElementById('depthContainer').style.display = 'none';
      clearInterval(recordTimer);
      clearInterval(depthTimer);
      if (data.frames) alert('Saved ' + data.frames + ' frames (' + data.size_mb + 'MB)');
    }).catch(e => console.error('Stop error:', e));
  }
}

// Sync collect+record state on page load
fetch('/api/collect/status').then(r => r.json()).then(data => {
  if (data.running) {
    collecting = true;
    document.getElementById('collectBtn').classList.add('active');
    document.getElementById('collectBtn').textContent = '🛑 Stop';
  }
}).catch(() => {});

function sendTTS() {
  const input = document.getElementById('ttsInput');
  const text = input.value.trim();
  if (text) {
    ws.send(JSON.stringify({cmd: 'tts', text: text}));
    input.value = '';
  }
}

// Object detection overlay
let detecting = false;
let detectTimer = null;
const DETECT_URL = 'DETECT_SERVICE_URL';

function toggleDetect() {
  detecting = !detecting;
  const btn = document.getElementById('detectBtn');
  btn.classList.toggle('active', detecting);
  btn.textContent = detecting ? '🛑 Stop Detect' : '👁️ Detect';
  if (detecting) {
    runDetection();
  } else {
    clearTimeout(detectTimer);
    document.getElementById('detectOverlay').innerHTML = '';
  }
}

async function runDetection() {
  if (!detecting) return;
  try {
    const r = await fetch(DETECT_URL);
    const data = await r.json();
    drawDetections(data);
  } catch (e) {
    console.error('Detection error:', e);
  }
  detectTimer = setTimeout(runDetection, 500);  // 2fps detection
}

function drawDetections(data) {
  const overlay = document.getElementById('detectOverlay');
  const img = document.getElementById('camFeed');
  const w = img.clientWidth;
  const h = img.clientHeight;
  const [srcW, srcH] = data.resolution || [1280, 720];
  const scaleX = w / srcW;
  const scaleY = h / srcH;

  let html = '';
  for (const obj of (data.objects || [])) {
    const [x1, y1, x2, y2] = obj.bbox;
    const left = x1 * scaleX;
    const top = y1 * scaleY;
    const bw = (x2 - x1) * scaleX;
    const bh = (y2 - y1) * scaleY;
    html += `<div class="detect-box" style="left:${left}px;top:${top}px;width:${bw}px;height:${bh}px"></div>`;
    html += `<div class="detect-label" style="left:${left}px;top:${Math.max(0,top-20)}px">${obj.label} ${Math.round(obj.confidence*100)}%</div>`;
  }
  html += `<div class="detect-info">${data.count || 0} objects · ${data.inference_ms || '?'}ms</div>`;
  overlay.innerHTML = html;
}

// Recording for TinyNav data collection
const COLLECT_URL = 'COLLECT_SERVICE_URL';
let isRecording = false;
let recordTimer = null;
let depthTimer = null;
let currentSteering = 0;
let currentThrottle = 0;

// Sync record button with backend state on page load
fetch(COLLECT_URL + '/record/status').then(r => r.json()).then(data => {
  if (data.recording) {
    isRecording = true;
    const btn = document.getElementById('recordBtn');
    btn.classList.add('active');
    btn.textContent = '⏹ Stop';
    document.getElementById('recordInfo').style.display = 'block';
    document.getElementById('frameCount').textContent = data.frames;
    document.getElementById('depthContainer').style.display = 'flex';
    recordTimer = setInterval(updateRecordStatus, 500);
    depthTimer = setInterval(updateDepth, 300);
  }
}).catch(() => {});

function toggleRecord() {
  if (!isRecording) {
    fetch(COLLECT_URL + '/record/start', {method: 'POST'}).then(r => r.json()).then(data => {
      if (data.status === 'recording') {
        isRecording = true;
        const btn = document.getElementById('recordBtn');
        btn.classList.add('active');
        btn.textContent = '⏹ Stop';
        document.getElementById('recordInfo').style.display = 'block';
        document.getElementById('depthContainer').style.display = 'flex';
        recordTimer = setInterval(updateRecordStatus, 500);
        depthTimer = setInterval(updateDepth, 300);
      }
    });
  } else {
    fetch(COLLECT_URL + '/record/stop', {method: 'POST'}).then(r => r.json()).then(data => {
      isRecording = false;
      const btn = document.getElementById('recordBtn');
      btn.classList.remove('active');
      btn.textContent = '🔴 Record';
      document.getElementById('recordInfo').style.display = 'none';
      document.getElementById('depthContainer').style.display = 'none';
      clearInterval(recordTimer);
      clearInterval(depthTimer);
      if (data.frames) alert(`Saved ${data.frames} frames (${data.size_mb}MB)`);
    });
  }
}

function updateRecordStatus() {
  fetch(COLLECT_URL + '/record/status').then(r => r.json()).then(data => {
    document.getElementById('frameCount').textContent = data.frames;
    // Update path from motor state during collect mode
    if (data.motor) {
      updatePath(data.motor.steering || 0, data.motor.throttle || 0);
    }
  }).catch(() => {});
}

function updateDepth() {
  document.getElementById('depthImg').src = COLLECT_URL + '/depth?t=' + Date.now();
}

// Send motor state to collection service when recording
const origSendCommand = (cmd, speed) => {
  // Map commands to steering/throttle
  if (cmd === 'forward') { currentSteering = 0; currentThrottle = speed/60; }
  else if (cmd === 'backward') { currentSteering = 0; currentThrottle = -(speed/60); }
  else if (cmd === 'left') { currentSteering = -1; currentThrottle = speed/60; }
  else if (cmd === 'right') { currentSteering = 1; currentThrottle = speed/60; }
  else if (cmd === 'stop') { currentSteering = 0; currentThrottle = 0; }
  updatePath(currentSteering, currentThrottle);

  if (isRecording) {
    fetch(COLLECT_URL + '/motor', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({steering: currentSteering, throttle: currentThrottle})
    }).catch(() => {});
  }
};

// Hook into existing command sending
const _origWsSend = ws.send.bind(ws);
ws.send = function(data) {
  _origWsSend(data);
  try {
    const parsed = JSON.parse(data);
    if (parsed.cmd) origSendCommand(parsed.cmd, parsed.speed || 30);
  } catch(e) {}
};
</script>
</body>
</html>""".replace("CAMERA_STREAM_URL", CAMERA_STREAM).replace("CAMERA_SNAPSHOT_URL", CAMERA_SNAPSHOT).replace("DETECT_SERVICE_URL", DETECT_URL).replace("COLLECT_SERVICE_URL", COLLECT_URL)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.post("/api/collect/start")
async def api_collect_start():
    if collect_driver and not collect_driver.is_running:
        await collect_driver.start()
        return {"status": "started"}
    return {"status": "already_running" if collect_driver and collect_driver.is_running else "no_driver"}

@app.post("/api/collect/stop")
async def api_collect_stop():
    if collect_driver and collect_driver.is_running:
        await collect_driver.stop()
        return {"status": "stopped"}
    return {"status": "not_running"}

@app.get("/api/collect/status")
async def api_collect_status():
    return {"running": collect_driver.is_running if collect_driver else False}


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
            elif cmd == "collect":
                if collect_driver and not collect_driver.is_running:
                    asyncio.ensure_future(collect_driver.start())
            elif cmd == "stop_collect":
                if collect_driver and collect_driver.is_running:
                    asyncio.ensure_future(collect_driver.stop())
            elif cmd == "tts":
                text = data.get("text", "")
                if text:
                    mqtt_client.publish("mbot2/speak", json.dumps({"text": text}))
                    log.info("TTS (mbot2): %s", text[:80])
            else:
                send_command(cmd, data.get("speed"))
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
    global loop, collect_driver
    import uvicorn

    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()
    log.info("MQTT connected to %s:%d", MQTT_BROKER, MQTT_PORT)

    collect_driver = CollectDriver(mqtt_client)

    loop = asyncio.new_event_loop()

    config = uvicorn.Config(app, host="0.0.0.0", port=8888, loop=loop, log_level="info")
    server = uvicorn.Server(config)

    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

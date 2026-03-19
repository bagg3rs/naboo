"""
Naboo Data Collection Service — records depth frames + motor commands for TinyNav training.

Runs on Mac mini (.50). Fetches camera frames, generates MiDaS depth maps,
records paired (depth, steering, throttle) data when recording is active.

Endpoints:
  POST /record/start    → Start recording
  POST /record/stop     → Stop recording, save dataset
  GET  /record/status   → Recording status + frame count
  GET  /depth           → Current depth map as grayscale JPEG
  GET  /depth?raw=1     → Current 24x24 depth as JSON array

Port: 8082
"""

import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from threading import Lock, Thread

import cv2
import httpx
import numpy as np
import paho.mqtt.client as mqtt
import torch
from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("naboo-collect")

app = Flask(__name__)
CORS(app)

# --- Prometheus metrics ---
DEPTH_INFERENCE_MS = Histogram("naboo_depth_inference_ms", "MiDaS depth inference latency", buckets=[50, 100, 150, 200, 300, 500, 1000])
DEPTH_FETCH_MS = Histogram("naboo_depth_fetch_ms", "Camera frame fetch latency", buckets=[50, 100, 200, 500, 1000, 2000])
FRAMES_RECORDED = Counter("naboo_frames_recorded_total", "Total depth frames recorded")
FRAMES_FROZEN = Counter("naboo_frames_frozen_total", "Duplicate/frozen frames skipped")
RECORDINGS_SAVED = Counter("naboo_recordings_saved_total", "Recording sessions saved")
RECORDING_ACTIVE = Gauge("naboo_recording_active", "Whether recording is active (1/0)")
RECORDING_FRAMES = Gauge("naboo_recording_frames", "Frames in current recording session")
MOTOR_STEERING = Gauge("naboo_motor_steering", "Current motor steering value")
MOTOR_THROTTLE = Gauge("naboo_motor_throttle", "Current motor throttle value")
MOTOR_COMMANDS = Counter("naboo_motor_commands_total", "Motor commands received", ["command_type"])
DEPTH_RANGE_MIN = Gauge("naboo_depth_range_min", "Min depth value in latest frame")
DEPTH_RANGE_MAX = Gauge("naboo_depth_range_max", "Max depth value in latest frame")
DEPTH_STD = Gauge("naboo_depth_std", "Depth standard deviation (frame quality)")
CAMERA_ERRORS = Counter("naboo_camera_errors_total", "Camera fetch/depth computation errors")
TURN_FRAMES = Counter("naboo_turn_frames_total", "Frames with non-zero steering")

# Config
CAMERA_URL = os.environ.get("NABOO_CAMERA_URL", "http://192.168.0.31:8080/")
MQTT_BROKER = os.environ.get("MQTT_BROKER", "192.168.0.50")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "recordings"))
DEPTH_SIZE = 24  # TinyNav input resolution
WINDOW_SIZE = 10  # Number of frames to stack

# --- MQTT motor tracking ---
# Maps mbot2/command messages to steering/throttle values
COMMAND_MAP = {
    "move_forward":  {"steering": 0.0, "throttle": 1.0},
    "force_forward": {"steering": 0.0, "throttle": 1.0},
    "move_backward": {"steering": 0.0, "throttle": -1.0},
    "turn_left":     {"steering": -1.0, "throttle": 0.5},
    "turn_right":    {"steering": 1.0, "throttle": 0.5},
    "stop":          {"steering": 0.0, "throttle": 0.0},
}

current_motor = {"steering": 0.0, "throttle": 0.0}

def on_mqtt_connect(client, userdata, flags, reason_code, properties):
    client.subscribe("mbot2/command", qos=0)
    log.info("MQTT connected, subscribed to mbot2/command")

def on_mqtt_message(client, userdata, msg):
    """Track actual motor commands from ANY source (web controller, explore, etc).
    Also record a frame immediately on command change to capture brief turns."""
    try:
        data = json.loads(msg.payload.decode())
        cmd_type = data.get("command_type", "")
        if cmd_type in COMMAND_MAP:
            speed = data.get("parameters", {}).get("speed", 30) / 60.0  # Normalize to 0-1
            mapped = COMMAND_MAP[cmd_type]
            new_steering = mapped["steering"]
            new_throttle = mapped["throttle"] * speed

            # Record immediately on motor change (captures brief turns)
            old_s, old_t = current_motor["steering"], current_motor["throttle"]
            current_motor["steering"] = new_steering
            current_motor["throttle"] = new_throttle
            MOTOR_STEERING.set(new_steering)
            MOTOR_THROTTLE.set(new_throttle)
            MOTOR_COMMANDS.labels(command_type=cmd_type).inc()

            if (new_steering != old_s or new_throttle != old_t):
                with record_lock:
                    if recording:
                        with depth_lock:
                            if current_depth_24 is not None:
                                recorded_frames.append({
                                    "depth_24": current_depth_24.copy(),
                                    "steering": new_steering,
                                    "throttle": new_throttle,
                                    "timestamp": time.time(),
                                })
                                FRAMES_RECORDED.inc()
                                RECORDING_FRAMES.set(len(recorded_frames))
                                if abs(new_steering) > 0:
                                    TURN_FRAMES.inc()
    except Exception as e:
        log.error(f"MQTT motor parse error: {e}")

mqtt_client = mqtt.Client(client_id="naboo-collector", protocol=mqtt.MQTTv5)
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message

# --- MiDaS depth model ---
log.info("Loading MiDaS small...")
midas = torch.hub.load("intel-isl/MiDaS", "MiDaS_small", trust_repo=True)
midas.eval()
midas_transforms = torch.hub.load("intel-isl/MiDaS", "transforms", trust_repo=True)
midas_transform = midas_transforms.small_transform
log.info("MiDaS loaded OK")

# --- State ---
recording = False
record_lock = Lock()
recorded_frames = []  # List of {"depth_24": np.array, "steering": float, "throttle": float, "timestamp": float}
current_depth = None  # Latest depth map (full res for visualisation)
current_depth_24 = None  # Latest 24x24 depth (for recording/display)
depth_lock = Lock()


def fetch_and_compute_depth():
    """Fetch camera frame, compute MiDaS depth, return (full_depth, depth_24x24)."""
    try:
        t_fetch = time.time()
        r = httpx.get(CAMERA_URL, timeout=5.0)
        DEPTH_FETCH_MS.observe((time.time() - t_fetch) * 1000)
        if r.status_code != 200:
            CAMERA_ERRORS.inc()
            return None, None
        arr = np.frombuffer(r.content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        t_infer = time.time()
        input_batch = midas_transform(img_rgb)
        with torch.no_grad():
            prediction = midas(input_batch)

            # Full res for visualisation
            full_depth = torch.nn.functional.interpolate(
                prediction.unsqueeze(1), size=img.shape[:2],
                mode="bicubic", align_corners=False
            ).squeeze().cpu().numpy()

            # 24x24 for TinyNav
            depth_24 = torch.nn.functional.interpolate(
                prediction.unsqueeze(1), size=(DEPTH_SIZE, DEPTH_SIZE),
                mode="bicubic", align_corners=False
            ).squeeze().cpu().numpy()

        DEPTH_INFERENCE_MS.observe((time.time() - t_infer) * 1000)
        DEPTH_RANGE_MIN.set(float(depth_24.min()))
        DEPTH_RANGE_MAX.set(float(depth_24.max()))
        DEPTH_STD.set(float(depth_24.std()))
        return full_depth, depth_24
    except Exception as e:
        CAMERA_ERRORS.inc()
        log.error(f"Depth computation failed: {e}")
        return None, None


def depth_loop():
    """Continuously compute depth maps."""
    global current_depth, current_depth_24
    _prev_depth = None
    while True:
        t0 = time.time()
        full_depth, depth_24 = fetch_and_compute_depth()
        if full_depth is not None:
            # Check for frozen frame
            is_frozen = _prev_depth is not None and np.array_equal(depth_24, _prev_depth)
            if is_frozen:
                FRAMES_FROZEN.inc()
            _prev_depth = depth_24.copy()

            with depth_lock:
                current_depth = full_depth
                current_depth_24 = depth_24

            # Record if active (skip frozen frames)
            with record_lock:
                if recording and depth_24 is not None and not is_frozen:
                    recorded_frames.append({
                        "depth_24": depth_24.copy(),
                        "steering": current_motor["steering"],
                        "throttle": current_motor["throttle"],
                        "timestamp": time.time(),
                    })
                    FRAMES_RECORDED.inc()
                    RECORDING_FRAMES.set(len(recorded_frames))
                    if abs(current_motor["steering"]) > 0:
                        TURN_FRAMES.inc()
                    if len(recorded_frames) % 50 == 0:
                        log.info(f"Recorded {len(recorded_frames)} frames")

        elapsed = time.time() - t0
        sleep_time = max(0.05, 0.25 - elapsed)  # ~4Hz target
        time.sleep(sleep_time)


def depth_to_image(depth_map):
    """Convert depth map to grayscale JPEG bytes."""
    # Normalize to 0-255
    d_min, d_max = depth_map.min(), depth_map.max()
    if d_max - d_min > 0:
        normalized = ((depth_map - d_min) / (d_max - d_min) * 255).astype(np.uint8)
    else:
        normalized = np.zeros_like(depth_map, dtype=np.uint8)
    # Apply colormap for better visualisation
    colored = cv2.applyColorMap(normalized, cv2.COLORMAP_INFERNO)
    _, buf = cv2.imencode(".jpg", colored, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes()


# --- Routes ---

@app.route("/depth")
def depth():
    """Return current depth map."""
    raw = request.args.get("raw") is not None
    small = request.args.get("small") is not None

    with depth_lock:
        if small and current_depth_24 is not None:
            if raw:
                return jsonify({"depth": current_depth_24.tolist(), "size": DEPTH_SIZE})
            return Response(depth_to_image(current_depth_24), mimetype="image/jpeg")
        elif current_depth is not None:
            if raw:
                return jsonify({"depth": current_depth_24.tolist() if current_depth_24 is not None else [], "size": DEPTH_SIZE})
            return Response(depth_to_image(current_depth), mimetype="image/jpeg")

    return jsonify({"error": "No depth data yet"}), 503


@app.route("/motor", methods=["POST"])
def update_motor():
    """Update current motor state (called by web controller during recording)."""
    data = request.get_json(silent=True) or {}
    current_motor["steering"] = float(data.get("steering", 0))
    current_motor["throttle"] = float(data.get("throttle", 0))
    return jsonify({"ok": True})


@app.route("/record/start", methods=["POST"])
def record_start():
    global recording, recorded_frames
    with record_lock:
        if recording:
            return jsonify({"error": "Already recording"}), 400
        recorded_frames = []
        recording = True
    RECORDING_ACTIVE.set(1)
    RECORDING_FRAMES.set(0)
    log.info("Recording started")
    return jsonify({"status": "recording"})


@app.route("/record/stop", methods=["POST"])
def record_stop():
    global recording
    with record_lock:
        if not recording:
            return jsonify({"error": "Not recording"}), 400
        recording = False
        frames = list(recorded_frames)

    if not frames:
        return jsonify({"error": "No frames recorded"}), 400

    # Save dataset
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(DATA_DIR, f"recording_{timestamp}.npz")

    depths = np.array([f["depth_24"] for f in frames])
    steerings = np.array([f["steering"] for f in frames])
    throttles = np.array([f["throttle"] for f in frames])
    timestamps = np.array([f["timestamp"] for f in frames])

    np.savez_compressed(filepath,
        depths=depths,
        steerings=steerings,
        throttles=throttles,
        timestamps=timestamps,
    )

    size_mb = os.path.getsize(filepath) / 1024 / 1024
    log.info(f"Saved {len(frames)} frames to {filepath} ({size_mb:.1f}MB)")
    RECORDING_ACTIVE.set(0)
    RECORDING_FRAMES.set(0)
    RECORDINGS_SAVED.inc()

    return jsonify({
        "status": "saved",
        "frames": len(frames),
        "file": filepath,
        "size_mb": round(size_mb, 1),
    })


@app.route("/record/status")
def record_status():
    with record_lock:
        return jsonify({
            "recording": recording,
            "frames": len(recorded_frames),
            "motor": current_motor,
        })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "model": "midas_small",
        "has_depth": current_depth is not None,
        "recording": recording,
        "frames": len(recorded_frames),
    })


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    # Connect MQTT to track motor commands from any source
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.loop_start()
    log.info(f"MQTT connected to {MQTT_BROKER}:{MQTT_PORT}")

    t = Thread(target=depth_loop, daemon=True)
    t.start()
    log.info(f"Starting Naboo data collection service on :8082 (camera: {CAMERA_URL})")
    app.run(host="0.0.0.0", port=8082, threaded=True)

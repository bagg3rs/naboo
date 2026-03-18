"""
Naboo Object Detection Service — runs on Mac mini (.50).

Grabs snapshot from Pi Zero camera server, runs MobileNet SSD inference locally.
Much faster than running on Pi Zero (~10-50ms vs 70s).

Endpoints:
  GET /detect          → JSON detections from latest Pi camera snapshot
  GET /detect?annotate → JPEG with bounding boxes drawn
  GET /health          → Health check

Port: 8081 (avoids conflict with Ollama 11434, MLX 11435, MQTT 1883)
"""

import io
import logging
import os
import time

import cv2
import httpx
import numpy as np
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("naboo-detect")

app = Flask(__name__)
CORS(app)  # Allow cross-origin requests from web controller

# Config
CAMERA_URL = os.environ.get("NABOO_CAMERA_URL", "http://192.168.0.31:8080/")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
CONFIDENCE_THRESHOLD = 0.4

# COCO class labels for MobileNet SSD
CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike",
    "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor"
]

COLORS = {
    "person": (0, 255, 0),
    "chair": (255, 165, 0),
    "sofa": (0, 165, 255),
    "cat": (255, 0, 255),
    "dog": (255, 0, 255),
    "bird": (0, 255, 255),
    "tvmonitor": (255, 255, 0),
}

# Load model at startup (M4 handles this in <1s)
prototxt = os.path.join(MODEL_DIR, "deploy.prototxt")
weights = os.path.join(MODEL_DIR, "mobilenet_ssd.caffemodel")
net = cv2.dnn.readNetFromCaffe(prototxt, weights)
log.info("MobileNet SSD loaded OK")


def fetch_frame():
    """Grab a snapshot from the Pi Zero camera server."""
    try:
        r = httpx.get(CAMERA_URL, timeout=5.0)
        if r.status_code != 200:
            return None
        arr = np.frombuffer(r.content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        log.error(f"Camera fetch failed: {e}")
        return None


def run_detection(img, threshold=None):
    """Run MobileNet SSD on a BGR image. Returns list of detections."""
    if threshold is None:
        threshold = CONFIDENCE_THRESHOLD

    h, w = img.shape[:2]
    blob = cv2.dnn.blobFromImage(img, 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    detections_raw = net.forward()

    results = []
    for i in range(detections_raw.shape[2]):
        confidence = float(detections_raw[0, 0, i, 2])
        if confidence < threshold:
            continue
        class_id = int(detections_raw[0, 0, i, 1])
        label = CLASSES[class_id] if class_id < len(CLASSES) else f"class_{class_id}"
        if label == "background":
            continue
        box = detections_raw[0, 0, i, 3:7] * np.array([w, h, w, h])
        x1, y1, x2, y2 = box.astype(int).tolist()
        results.append({
            "label": label,
            "confidence": round(confidence, 3),
            "bbox": [x1, y1, x2, y2],
        })

    return results


def annotate_image(img, detections):
    """Draw bounding boxes and labels on image."""
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        label = det["label"]
        conf = det["confidence"]
        color = COLORS.get(label, (0, 255, 0))

        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        text = f"{label} {conf:.0%}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw, y1), color, -1)
        cv2.putText(img, text, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    return img


@app.route("/detect")
def detect():
    """Grab frame from Pi camera, run detection, return JSON or annotated JPEG."""
    threshold = request.args.get("threshold", CONFIDENCE_THRESHOLD, type=float)
    annotate = request.args.get("annotate") is not None

    t0 = time.time()
    img = fetch_frame()
    fetch_ms = round((time.time() - t0) * 1000)

    if img is None:
        return jsonify({"error": "Could not fetch camera frame"}), 503

    t1 = time.time()
    detections = run_detection(img, threshold=threshold)
    inference_ms = round((time.time() - t1) * 1000)

    if annotate:
        annotated = annotate_image(img.copy(), detections)
        _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return Response(buf.tobytes(), mimetype="image/jpeg",
                        headers={"X-Inference-Ms": str(inference_ms),
                                 "X-Objects": str(len(detections))})

    return jsonify({
        "objects": detections,
        "count": len(detections),
        "fetch_ms": fetch_ms,
        "inference_ms": inference_ms,
        "total_ms": fetch_ms + inference_ms,
        "resolution": [img.shape[1], img.shape[0]],
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "model": "mobilenet_ssd_v1", "camera": CAMERA_URL})


if __name__ == "__main__":
    log.info(f"Starting Naboo detection service on :8081 (camera: {CAMERA_URL})")
    app.run(host="0.0.0.0", port=8081, threaded=True)

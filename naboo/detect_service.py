"""
Naboo Object Detection Service — YOLOv8n on Mac mini (.50).

Grabs snapshot from Pi Zero camera server, runs YOLOv8 nano inference locally.
~10-30ms inference on M4 with much tighter bounding boxes than MobileNet SSD.

Endpoints:
  GET /detect          → JSON detections from latest Pi camera snapshot
  GET /detect?annotate → JPEG with bounding boxes drawn
  GET /health          → Health check

Port: 8081
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
CORS(app)

# Config
CAMERA_URL = os.environ.get("NABOO_CAMERA_URL", "http://192.168.0.31:8080/")
CONFIDENCE_THRESHOLD = 0.35

# Load YOLOv8n — auto-downloads on first run (~6MB)
from ultralytics import YOLO
model = YOLO("yolov8n.pt")
log.info("YOLOv8n loaded OK")

# COCO class names come from the model
CLASS_NAMES = model.names  # dict {0: 'person', 1: 'bicycle', ...}

COLORS = {
    "person": (0, 255, 0),
    "chair": (255, 165, 0),
    "sofa": (0, 165, 255),
    "couch": (0, 165, 255),
    "cat": (255, 0, 255),
    "dog": (255, 0, 255),
    "bird": (0, 255, 255),
    "tv": (255, 255, 0),
    "laptop": (255, 255, 0),
    "cell phone": (200, 200, 0),
    "bottle": (0, 200, 200),
    "cup": (0, 200, 200),
}


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
    """Run YOLOv8n on a BGR image. Returns list of detections."""
    if threshold is None:
        threshold = CONFIDENCE_THRESHOLD

    results = model(img, conf=threshold, verbose=False)[0]

    detections = []
    for box in results.boxes:
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int).tolist()
        confidence = float(box.conf[0])
        class_id = int(box.cls[0])
        label = CLASS_NAMES.get(class_id, f"class_{class_id}")

        detections.append({
            "label": label,
            "confidence": round(confidence, 3),
            "bbox": [x1, y1, x2, y2],
        })

    return detections


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
    return jsonify({"status": "ok", "model": "yolov8n", "camera": CAMERA_URL})


if __name__ == "__main__":
    log.info(f"Starting Naboo detection service on :8081 (camera: {CAMERA_URL})")
    app.run(host="0.0.0.0", port=8081, threaded=True)

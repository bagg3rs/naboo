"""
Naboo Pi Camera Server — lightweight HTTP snapshot + MJPEG stream + object detection.

Endpoints:
  GET /         → JPEG snapshot (compatible with XIAO interface)
  GET /stream   → MJPEG live stream (for web controller)
  GET /detect   → JSON object detection (MobileNet SSD via OpenCV DNN)
  GET /health   → Health check

Designed for Pi Zero W + Camera Module 3 NoIR Wide (IMX708).
Runs as a systemd service on port 8080.
"""

import io
import json
import logging
import os
import time
from threading import Condition, Lock, Thread

import numpy as np
from flask import Flask, Response, jsonify, request
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("naboo-cam")

app = Flask(__name__)

# --- Model config ---
MODEL_DIR = os.environ.get("MODEL_DIR", "/opt/naboo-cam/models")
CONFIDENCE_THRESHOLD = 0.4

# COCO class labels for MobileNet SSD
CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike",
    "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor"
]

# --- Frame buffer ---
class FrameBuffer:
    """Thread-safe latest frame holder with notification."""
    def __init__(self):
        self.frame = None          # JPEG bytes
        self.frame_array = None    # numpy RGB array (for detection)
        self.condition = Condition()

    def update(self, jpeg_bytes, rgb_array):
        with self.condition:
            self.frame = jpeg_bytes
            self.frame_array = rgb_array
            self.condition.notify_all()

    def wait_for_frame(self, timeout=5.0):
        with self.condition:
            self.condition.wait(timeout=timeout)
            return self.frame

buffer = FrameBuffer()

# --- Object detector ---
class Detector:
    """MobileNet SSD object detection via OpenCV DNN."""
    def __init__(self):
        self.net = None
        self._lock = Lock()
        self._load_attempted = False

    def load(self):
        """Lazy-load the model."""
        if self._load_attempted:
            return self.net is not None
        self._load_attempted = True
        try:
            import cv2
            prototxt = os.path.join(MODEL_DIR, "deploy.prototxt")
            weights = os.path.join(MODEL_DIR, "mobilenet_ssd.caffemodel")
            if not os.path.exists(prototxt) or not os.path.exists(weights):
                log.warning(f"Model files not found in {MODEL_DIR}")
                return False
            self.net = cv2.dnn.readNetFromCaffe(prototxt, weights)
            log.info("MobileNet SSD loaded OK")
            return True
        except Exception as e:
            log.error(f"Failed to load model: {e}")
            return False

    def detect(self, rgb_array, threshold=None):
        """Run detection on an RGB numpy array. Returns list of detections."""
        if threshold is None:
            threshold = CONFIDENCE_THRESHOLD
        if self.net is None:
            if not self.load():
                return None

        import cv2
        with self._lock:
            h, w = rgb_array.shape[:2]
            # MobileNet SSD expects 300x300 BGR
            blob = cv2.dnn.blobFromImage(
                cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR),
                0.007843, (300, 300), 127.5
            )
            self.net.setInput(blob)
            detections_raw = self.net.forward()

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

detector = Detector()


# --- Camera capture ---
def capture_loop():
    """Continuous capture — always has a fresh frame for snapshots + detection."""
    from picamera2 import Picamera2

    cam = Picamera2()

    config = cam.create_still_configuration(
        main={"size": (1280, 720), "format": "RGB888"},
        buffer_count=2,
    )
    cam.configure(config)

    # Camera Module 3 NoIR Wide — autofocus + HDR
    cam.set_controls({
        "AfMode": 2,       # Continuous autofocus
        "AeEnable": True,  # Auto exposure
        "AwbEnable": True,  # Auto white balance
    })

    cam.start()
    log.info("Camera started (1280x720, continuous AF)")
    time.sleep(2)  # Let camera settle

    while True:
        try:
            frame = cam.capture_array()

            # Encode to JPEG
            img = Image.fromarray(frame)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            buf.seek(0)

            buffer.update(buf.getvalue(), frame)

            time.sleep(0.1)  # ~10fps
        except Exception as e:
            log.error(f"Capture error: {e}")
            time.sleep(1)


# --- Routes ---

@app.route("/")
def snapshot():
    """Return latest frame as JPEG."""
    frame = buffer.frame
    if frame is None:
        return "Camera not ready", 503
    return Response(frame, mimetype="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store"})


@app.route("/stream")
def stream():
    """MJPEG stream for web controller / browser viewing."""
    def generate():
        while True:
            frame = buffer.wait_for_frame(timeout=5.0)
            if frame is None:
                continue
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/detect")
def detect():
    """Run object detection on latest frame. Returns JSON.

    Query params:
        threshold: float (0-1, default 0.4)
    """
    rgb = buffer.frame_array
    if rgb is None:
        return jsonify({"error": "Camera not ready"}), 503

    threshold = request.args.get("threshold", CONFIDENCE_THRESHOLD, type=float)
    t0 = time.time()
    results = detector.detect(rgb, threshold=threshold)
    elapsed_ms = round((time.time() - t0) * 1000)

    if results is None:
        return jsonify({"error": "Model not loaded — check /opt/naboo-cam/models/"}), 500

    return jsonify({
        "objects": results,
        "count": len(results),
        "inference_ms": elapsed_ms,
        "resolution": [1280, 720],
    })


@app.route("/health")
def health():
    """Health check."""
    status = {
        "status": "ok" if buffer.frame is not None else "no_frames",
        "resolution": "1280x720",
        "camera": "imx708_wide_noir",
        "detector": "loaded" if detector.net is not None else "not_loaded",
    }
    code = 200 if buffer.frame is not None else 503
    return jsonify(status), code


if __name__ == "__main__":
    t = Thread(target=capture_loop, daemon=True)
    t.start()

    # Lazy-load detector on first /detect call (saves RAM if unused)
    log.info("Starting Naboo camera server on :8080")
    app.run(host="0.0.0.0", port=8080, threaded=True)

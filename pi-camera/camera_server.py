"""
Naboo Pi Camera Server — hardware-accelerated MJPEG stream + snapshot + detection.

Uses picamera2's built-in MJPEG encoder (GPU hardware) instead of software PIL encoding.
Much smoother video on Pi Zero W.

Endpoints:
  GET /         → JPEG snapshot
  GET /stream   → MJPEG live stream (hardware encoded)
  GET /detect   → JSON object detection (MobileNet SSD via OpenCV DNN)
  GET /health   → Health check

Designed for Pi Zero W + Camera Module 3 NoIR Wide (IMX708).
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

CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike",
    "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor"
]

# --- Frame buffers (separate for stream vs snapshot) ---
class FrameBuffer:
    """Thread-safe latest frame holder."""
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def update(self, jpeg_bytes):
        with self.condition:
            self.frame = jpeg_bytes
            self.condition.notify_all()

    def wait_for_frame(self, timeout=5.0):
        with self.condition:
            self.condition.wait(timeout=timeout)
            return self.frame

stream_buffer = FrameBuffer()   # 640x480 hardware MJPEG (smooth stream)
snapshot_buffer = FrameBuffer() # 1280x720 PIL (high-res snapshots + detection)
detection_array = None          # numpy RGB for detection endpoint

# --- Detector ---
class Detector:
    def __init__(self):
        self.net = None
        self._lock = Lock()
        self._load_attempted = False

    def load(self):
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
        if threshold is None:
            threshold = CONFIDENCE_THRESHOLD
        if self.net is None:
            if not self.load():
                return None
        import cv2
        with self._lock:
            h, w = rgb_array.shape[:2]
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
                "label": label, "confidence": round(confidence, 3),
                "bbox": [x1, y1, x2, y2],
            })
        return results

detector = Detector()


# --- Camera capture (hardware MJPEG) ---
def capture_loop():
    """Use picamera2 hardware MJPEG encoder for smooth streaming."""
    from picamera2 import Picamera2
    from picamera2.encoders import MJPEGEncoder
    from picamera2.outputs import FileOutput
    from libcamera import Transform

    cam = Picamera2()

    # Video config for streaming (lower res = smoother on Pi Zero)
    video_config = cam.create_video_configuration(
        main={"size": (1280, 720), "format": "RGB888"},
        lores={"size": (640, 480), "format": "YUV420"},
        encode="lores",
        transform=Transform(hflip=True, vflip=True),
        buffer_count=4,
    )
    cam.configure(video_config)

    cam.set_controls({
        "AfMode": 2,
        "AeEnable": True,
        "AwbEnable": True,        # Auto white balance — NoIR tint is unavoidable without IR filter
        "FrameRate": 20.0,
    })

    # Custom output that feeds our stream buffer
    class BufferOutput(io.BufferedIOBase):
        def __init__(self):
            self._buf = io.BytesIO()

        def writable(self):
            return True

        def write(self, data):
            if data[:2] == b'\xff\xd8':  # JPEG SOI marker — new frame
                if self._buf.tell() > 0:
                    self._buf.seek(0)
                    frame_data = self._buf.read()
                    stream_buffer.update(frame_data)
                self._buf = io.BytesIO()
            self._buf.write(data)
            return len(data)

    output = BufferOutput()
    encoder = MJPEGEncoder(bitrate=5000000)  # 5Mbps — good quality, smooth

    cam.start()
    log.info("Camera started (1280x720 main + 640x480 encode, hardware MJPEG, 20fps target)")
    time.sleep(1)

    cam.start_encoder(encoder, FileOutput(output))

    # Capture full-res frames for snapshot/detection (separate from stream)
    global detection_array
    while True:
        try:
            frame = cam.capture_array("main")
            detection_array = frame
            # Encode high-res snapshot
            img = Image.fromarray(frame)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            buf.seek(0)
            snapshot_buffer.update(buf.getvalue())
            time.sleep(0.3)  # Full-res every 300ms — keeps detection in sync with stream
        except Exception as e:
            log.error(f"Capture error: {e}")
            time.sleep(1)


# --- Routes ---

@app.route("/")
def snapshot():
    # Prefer high-res snapshot, fallback to stream frame
    frame = snapshot_buffer.frame or stream_buffer.frame
    if frame is None:
        return "Camera not ready", 503
    return Response(frame, mimetype="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store"})


@app.route("/stream")
def stream():
    def generate():
        while True:
            frame = stream_buffer.wait_for_frame(timeout=5.0)
            if frame is None:
                continue
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/detect")
def detect():
    rgb = detection_array
    if rgb is None:
        return jsonify({"error": "Camera not ready"}), 503
    threshold = request.args.get("threshold", CONFIDENCE_THRESHOLD, type=float)
    t0 = time.time()
    results = detector.detect(rgb, threshold=threshold)
    elapsed_ms = round((time.time() - t0) * 1000)
    if results is None:
        return jsonify({"error": "Model not loaded"}), 500
    return jsonify({
        "objects": results, "count": len(results),
        "inference_ms": elapsed_ms, "resolution": [1280, 720],
    })


@app.route("/health")
def health():
    status = {
        "status": "ok" if stream_buffer.frame is not None else "no_frames",
        "resolution": "1280x720",
        "camera": "imx708_wide_noir",
        "encoder": "hardware_mjpeg",
        "detector": "loaded" if detector.net is not None else "not_loaded",
    }
    return jsonify(status), 200 if stream_buffer.frame is not None else 503


if __name__ == "__main__":
    t = Thread(target=capture_loop, daemon=True)
    t.start()
    log.info("Starting Naboo camera server on :8080")
    app.run(host="0.0.0.0", port=8080, threaded=True)

"""
Naboo Pi Camera Server — lightweight HTTP snapshot + MJPEG stream.

Endpoints:
  GET /         → JPEG snapshot (compatible with XIAO interface)
  GET /stream   → MJPEG live stream (for web controller)
  GET /health   → Health check

Designed for Pi Zero W + Camera Module 3 NoIR Wide (IMX708).
Runs as a systemd service on port 8080.
"""

import io
import logging
import time
from threading import Condition, Thread

from flask import Flask, Response, send_file
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("naboo-cam")

app = Flask(__name__)

# Global frame buffer for streaming
class FrameBuffer:
    """Thread-safe latest frame holder with notification."""
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def update(self, frame_bytes):
        with self.condition:
            self.frame = frame_bytes
            self.condition.notify_all()

    def wait_for_frame(self, timeout=5.0):
        with self.condition:
            self.condition.wait(timeout=timeout)
            return self.frame

buffer = FrameBuffer()

# Camera capture thread — keeps grabbing frames
def capture_loop():
    """Continuous capture at ~10fps for streaming, always has a fresh frame for snapshots."""
    from picamera2 import Picamera2

    cam = Picamera2()

    # Configure for decent quality but not too heavy for Pi Zero
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

    # Let the camera settle (exposure, focus)
    time.sleep(2)

    while True:
        try:
            frame = cam.capture_array()

            # Encode to JPEG
            img = Image.fromarray(frame)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            buf.seek(0)

            buffer.update(buf.getvalue())

            # ~10fps on Pi Zero (100ms between frames)
            time.sleep(0.1)
        except Exception as e:
            log.error(f"Capture error: {e}")
            time.sleep(1)


@app.route("/")
def snapshot():
    """Return latest frame as JPEG — same interface as XIAO ESP32S3."""
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


@app.route("/health")
def health():
    """Health check — returns 200 if camera is capturing."""
    if buffer.frame is not None:
        return {"status": "ok", "resolution": "1280x720", "camera": "imx708_wide"}
    return {"status": "no_frames"}, 503


if __name__ == "__main__":
    # Start capture thread
    t = Thread(target=capture_loop, daemon=True)
    t.start()

    log.info("Starting Naboo camera server on :8080")
    app.run(host="0.0.0.0", port=8080, threaded=True)

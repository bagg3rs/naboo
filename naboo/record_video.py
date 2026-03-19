"""
Record Naboo footage — camera + depth heatmap side-by-side video.

Captures MJPEG stream from Pi camera + depth heatmap from collect service,
composites them side-by-side, and saves as MP4.

Usage: python3 -m naboo.record_video [--duration 60] [--output data/videos/]
"""

import argparse
import os
import subprocess
import sys
import time
import threading

import cv2
import httpx
import numpy as np

CAMERA_STREAM = os.environ.get("CAMERA_URL", "http://192.168.0.31:8080") + "/stream"
DEPTH_URL = os.environ.get("COLLECT_URL", "http://localhost:8082") + "/depth"
CNN_URL = os.environ.get("CNN_URL", "http://localhost:8888") + "/api/cnn/status"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "videos")
FPS = 4


def get_depth_frame():
    """Get depth heatmap from collect service."""
    try:
        r = httpx.get(DEPTH_URL, timeout=3)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            arr = np.frombuffer(r.content, np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        pass
    return None


def get_cnn_status():
    """Get CNN inference status for overlay."""
    try:
        r = httpx.get(CNN_URL, timeout=1)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def record(duration, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    outpath = os.path.join(output_dir, f"naboo_{timestamp}.mp4")

    # Open MJPEG stream
    cap = cv2.VideoCapture(CAMERA_STREAM)
    if not cap.isOpened():
        print(f"ERROR: Cannot open camera stream {CAMERA_STREAM}")
        sys.exit(1)

    # Get camera frame size
    ret, frame = cap.read()
    if not ret:
        print("ERROR: Cannot read camera frame")
        sys.exit(1)

    cam_h, cam_w = frame.shape[:2]
    # Depth will be resized to match camera height
    depth_w = cam_h  # Square depth map
    total_w = cam_w + depth_w + 2  # 2px divider

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(outpath, fourcc, FPS, (total_w, cam_h))

    print(f"📹 Recording to {outpath}")
    print(f"   Camera: {cam_w}x{cam_h}, Composite: {total_w}x{cam_h}")
    print(f"   Duration: {duration}s, FPS: {FPS}")
    print(f"   Press Ctrl+C to stop early")

    frames = 0
    t_start = time.time()

    try:
        while time.time() - t_start < duration:
            t0 = time.time()

            # Camera frame
            ret, cam_frame = cap.read()
            if not ret:
                continue

            # Depth frame
            depth_frame = get_depth_frame()
            if depth_frame is not None:
                depth_resized = cv2.resize(depth_frame, (depth_w, cam_h))
            else:
                depth_resized = np.zeros((cam_h, depth_w, 3), dtype=np.uint8)

            # CNN status overlay on depth
            cnn = get_cnn_status()
            if cnn and cnn.get("running"):
                s = cnn.get("stats", {})
                text = f"S:{s.get('last_steer', 0):.2f} T:{s.get('last_throttle', 0):.2f}"
                cv2.putText(depth_resized, "CNN", (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (139, 92, 246), 2)
                cv2.putText(depth_resized, text, (10, 55),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                ms_text = f"{s.get('avg_ms', 0):.1f}ms"
                cv2.putText(depth_resized, ms_text, (10, 75),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

            # Compose side-by-side
            divider = np.full((cam_h, 2, 3), 60, dtype=np.uint8)
            composite = np.hstack([cam_frame, divider, depth_resized])

            # Timestamp
            elapsed = time.time() - t_start
            ts_text = f"{elapsed:.1f}s  f:{frames}"
            cv2.putText(composite, ts_text, (10, cam_h - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

            writer.write(composite)
            frames += 1

            # Maintain FPS
            dt = time.time() - t0
            sleep = max(0, (1.0 / FPS) - dt)
            time.sleep(sleep)

            if frames % (FPS * 10) == 0:
                print(f"   {frames} frames ({elapsed:.0f}s)")

    except KeyboardInterrupt:
        print("\n   Stopped early")

    cap.release()
    writer.release()

    size_mb = os.path.getsize(outpath) / 1024 / 1024
    print(f"\n✅ Saved: {outpath}")
    print(f"   {frames} frames, {size_mb:.1f}MB")
    return outpath


def main():
    parser = argparse.ArgumentParser(description="Record Naboo footage")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--output", default=OUTPUT_DIR, help="Output directory")
    args = parser.parse_args()
    record(args.duration, args.output)


if __name__ == "__main__":
    main()

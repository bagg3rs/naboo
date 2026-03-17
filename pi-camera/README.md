# Pi Zero W — Naboo Camera Server

Lightweight HTTP camera server for Raspberry Pi Camera Module 3 NoIR Wide on Pi Zero W.

Replaces the XIAO ESP32S3 camera — same HTTP snapshot interface, much better image quality.

## Hardware
- Raspberry Pi Zero W (or Zero 2 W)
- Raspberry Pi Camera Module 3 NoIR Wide (SC1226, Sony IMX708, 120° FoV)

## Setup

### 1. Flash Pi OS Lite
Use Raspberry Pi Imager:
- **OS:** Raspberry Pi OS Lite (32-bit, Bookworm)
- **Hostname:** `naboo-cam`
- **WiFi:** Configure your network
- **SSH:** Enable with password or key
- **Username:** `pi` (or your preference)

### 2. First Boot
```bash
ssh pi@naboo-cam.local

# Update
sudo apt update && sudo apt upgrade -y

# picamera2 is pre-installed on Pi OS Lite (Bookworm) — just need Flask
sudo apt install -y python3-flask

# Check camera is detected
libcamera-hello --list-cameras
```

You should see:
```
0 : imx708_wide [4608x2592 10-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx708@1a)
```

### 3. Install Camera Server
```bash
sudo mkdir -p /opt/naboo-cam
sudo cp camera_server.py /opt/naboo-cam/
sudo cp naboo-cam.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now naboo-cam
```

### 4. Test
```bash
# Snapshot (JPEG)
curl -o test.jpg http://naboo-cam.local:8080/

# MJPEG stream (open in browser)
open http://naboo-cam.local:8080/stream

# Health check
curl http://naboo-cam.local:8080/health
```

### 5. Set Static IP
Assign `.163` via router DHCP reservation (same as old XIAO) — zero code changes needed.
Or set a new IP and update `NABOO_CAMERA_URL` env var + hardcoded URLs in `explore.py`.

## Endpoints
| Path | Port | Description |
|------|------|-------------|
| `/` | 8080 | JPEG snapshot (same as XIAO interface) |
| `/stream` | 8080 | MJPEG live stream |
| `/health` | 8080 | Health check |

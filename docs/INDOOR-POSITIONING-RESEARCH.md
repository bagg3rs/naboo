# Indoor Positioning & Mapping Research for Naboo

## What We Have Now
- **Ultrasonic:** 1x forward-facing, distance to obstacle ahead
- **Odometry:** Wheel encoders → distance (mm) + heading (°) — drifts over time
- **IMU:** 6-axis (accel + gyro) → roll, pitch, yaw — good for orientation, bad for position
- **VLM:** Camera snapshots → scene descriptions (semantic labels)

## Methods Comparison

### 1. Dead Reckoning (Odometry + IMU)
- **What:** Track position from wheel encoders + heading from gyro
- **Accuracy:** Drifts ~5-10% per meter, unbounded over time
- **Cost:** Free (already have hardware)
- **Good for:** Short-term local navigation, occupancy grid within a room
- **Bad for:** Absolute position, multi-room, returning to start
- **Sources:** Standard robotics; our firmware already sends `dm`/`h` telemetry

### 2. BLE RSSI Beacons (Room-Level)
- **What:** Fixed ESP32 boards in each room scan for BLE beacon on robot
- **Accuracy:** Room-level reliable, ~2-3m with calibration
- **Cost:** £3-5 per ESP32 board × 3-4 rooms = £12-20
- **Good for:** "Which room am I in?", zone-based navigation
- **Bad for:** Fine positioning within a room
- **Setup:** ESPHome `ble_rssi` sensor → HA → MQTT to Naboo agent
- **Sources:**
  - ESPHome BLE RSSI docs: https://esphome.io/components/sensor/ble_rssi/
  - ESPHome BLE Presence: https://esphome.io/components/binary_sensor/ble_presence/
  - Room presence guide: https://esp32.co.uk/room-level-presence-with-ble-beacons-in-home-assistant-esp32-bluetooth-proxy-guide/
  - HA community: https://community.home-assistant.io/t/use-esp32-for-bluetooth-room-presence-detection/478022

### 3. UWB (Ultra-Wideband) — Most Promising
- **What:** ESP32 + DW1000/DWM3000 modules, time-of-flight ranging
- **Accuracy:** ±10cm (±5cm with calibration)
- **Cost:** ~$20-30 per anchor × 3-4 = $60-120
- **Good for:** Precise position tracking, mapping, return-to-base
- **Bad for:** Needs line-of-sight between anchors and tag
- **Setup:** 3-4 anchors (walls/ceiling), 1 tag on robot, trilateration
- **Key modules:**
  - Makerfabs ESP32 UWB (DW1000) ~$20: https://www.makerfabs.com/
  - Qorvo DWM3000 (newer, better multipath) ~$30
- **Sources:**
  - How2Electronics tutorial: https://how2electronics.com/esp32-dw1000-uwb-indoor-location-positioning-system/
  - CircuitDigest DWM3000 guide: https://circuitdigest.com/microcontrollers-projects/diy-indoor-uwb-positioning-system-using-esp32-and-qorvo-dwm3000
  - Arduino UWB localization code: https://github.com/jremington/UWB-Indoor-Localization_Arduino
  - Makerfabs product guide: https://www.makerfabs.com/blog/post/makerfabs-uwb-product-series-and-how-to-choose-the-right-one-guide
  - Accuracy study (MAE 5cm): https://journal.umy.ac.id/index.php/jrc/article/view/20825

### 4. WiFi FTM/RTT (Fine Time Measurement)
- **What:** ESP32-C3/S2 measures round-trip time to WiFi APs
- **Accuracy:** ~10cm with ML calibration, ~0.5-5m raw
- **Cost:** Free if using existing APs (needs FTM support), or ~£3/ESP32 as custom APs
- **Good for:** Using existing infrastructure, no extra hardware on robot
- **Bad for:** Needs FTM-capable APs, 2.4GHz only on ESP32, multipath issues
- **Setup:** 3-8 ESP32-C3 as FTM responders, robot has ESP32 client
- **Sources:**
  - ESP32-C3 FTM dataset/code: https://github.com/WiFiLocalization/ESP32C3_WiFi_FTM_RSSI_Indoor_Localization
  - Research paper: https://arxiv.org/html/2401.16517v1
  - UCL decimeter-level study: https://discovery.ucl.ac.uk/id/eprint/10175123/

### 5. Visual SLAM (Camera-Based)
- **What:** Camera images → feature matching → 3D map + position
- **Accuracy:** cm-level with good camera and compute
- **Cost:** Free (have camera), but needs significant compute
- **Good for:** Rich maps with object recognition, works in any environment
- **Bad for:** Needs good lighting + forward-facing camera, heavy compute
- **Our constraint:** OV5640 under chassis = poor images; would need camera remount
- **Sources:**
  - Narwal mapping comparison: https://ca.narwal.com/blogs/product/robot-vacuum-mapping-test
  - Smart Home Hookup 2025 review: http://www.thesmarthomehookup.com/ultimate-robot-vacuum-and-mop-comparison-2025/

### 6. LIDAR (Laser Scanning)
- **What:** Rotating laser measures 360° distances
- **Accuracy:** mm-level, industry standard for robot mapping
- **Cost:** £20-50 for RPLIDAR A1 (cheapest decent), £100+ for A2/A3
- **Good for:** Fast, accurate 2D maps; works in dark; industry standard
- **Bad for:** Cost, size (might not fit on mBot2), power consumption
- **Sources:**
  - RPLIDAR A1 (~$20): https://www.slamtec.com/en/Lidar/A1
  - Robot vacuum comparison: https://ca.narwal.com/blogs/product/robot-vacuum-mapping-test

## Recommended Approach for Naboo

### Phase 1: Now (Free)
- **Dead reckoning** with IMU + odometry → basic occupancy grid
- **VLM room labels** → semantic room identification
- Already have all hardware

### Phase 2: Soon (~£15)
- **BLE beacons** in 3-4 rooms → reliable "which room" detection
- Use existing ESP32 boards or buy cheap ones
- Correct odometry drift when entering known rooms

### Phase 3: Later (~£80-120)
- **UWB anchors** → precise ±10cm positioning
- 4x Makerfabs ESP32 UWB boards + 1 tag on robot
- Real mapping, return-to-base, autonomous room-to-room navigation

### Wild Card
- **RPLIDAR A1** (~£20 on AliExpress) → instant accurate mapping
- Would need a mount on top of mBot2
- Most bang for buck if it physically fits

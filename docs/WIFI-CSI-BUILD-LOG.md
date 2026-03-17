# WiFi CSI Presence Detection — Build Log

> Building whole-house presence detection using ESP32-C3 boards and WiFi Channel State Information (CSI). No cameras, no wearables — just WiFi signal distortion from human bodies.

## The Idea

Traditional PIR sensors only detect **movement**. Someone sitting still on the sofa? Invisible. WiFi CSI detects **presence** — even a person lying in bed breathing distorts WiFi signals enough to register.

We're using [ESPectre](https://github.com/francescopace/espectre), an ESPHome external component that turns ESP32 boards into CSI presence sensors with:
- Binary motion detection (yes/no)
- Movement score (0-100)
- Adjustable sensitivity threshold
- Auto-calibration on boot (10s still room)

## Hardware

- **5x Seeed XIAO ESP32-C3** (~£4.80 each from The Pi Hut)
- Total cost: ~£24 for whole-house coverage

### Placement Plan
| Location | IP | Hostname |
|---|---|---|
| Master Bedroom (1F) | .174 | `esp-csi-bedroom` |
| Hall (1F) | .175 | `esp-csi-hall` |
| Living Room (1F) | .176 | `esp-csi-living` |
| Kitchen (GF) | .177 | `esp-csi-kitchen` |
| Bed 3 (GF) | .178 | `esp-csi-bed3` |

## ESPHome Configuration

Key requirements discovered during setup:

### 1. ESP-IDF Framework Required
CSI needs the ESP-IDF framework, not Arduino:
```yaml
esp32:
  board: seeed_xiao_esp32c3
  framework:
    type: esp-idf
    sdkconfig_options:
      CONFIG_PM_ENABLE: "n"
      CONFIG_ESP_WIFI_STA_DISCONNECTED_PM_ENABLE: "n"
      CONFIG_ESP_WIFI_CSI_ENABLED: "y"
```

### 2. ESPectre Component
Originally at `jagenjo/espectre`, now moved to:
```yaml
external_components:
  - source: github://francescopace/espectre
```

### 3. OTA Platform (ESPHome 2025.12+)
Newer ESPHome requires explicit platform:
```yaml
ota:
  - platform: esphome
    password: !secret ota_password
```

### 4. Static IPs
Can't access MikroTik router for DHCP reservations, so static IPs set in ESPHome config directly:
```yaml
wifi:
  manual_ip:
    static_ip: 192.168.0.174
    gateway: 192.168.0.1
    subnet: 255.255.255.0
    dns1: 192.168.0.30  # AdGuard
```

### 5. Dual Output — MQTT + HA API
Each sensor publishes to both:
- **MQTT** (`esp-csi/<room>/#`) — for Naboo robot agent to consume
- **HA Native API** — for Home Assistant automations (lights, heating, etc.)

## Full Config (Bedroom — First Sensor)

```yaml
esphome:
  name: esp-csi-bedroom
  friendly_name: "CSI Presence - Bedroom"

esp32:
  board: seeed_xiao_esp32c3
  framework:
    type: esp-idf
    sdkconfig_options:
      CONFIG_PM_ENABLE: "n"
      CONFIG_ESP_WIFI_STA_DISCONNECTED_PM_ENABLE: "n"
      CONFIG_ESP_WIFI_CSI_ENABLED: "y"

external_components:
  - source: github://francescopace/espectre

wifi:
  ssid: "internetoftings"
  password: !secret wifi_password
  manual_ip:
    static_ip: 192.168.0.174
    gateway: 192.168.0.1
    subnet: 255.255.255.0
    dns1: 192.168.0.30

mqtt:
  broker: 192.168.0.50
  port: 1883
  topic_prefix: esp-csi/bedroom

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome
    password: !secret ota_password

logger:
  level: INFO

light:
  - platform: status_led
    name: "Status LED"
    pin: GPIO10
```

## Build Progress

- [x] Research WiFi CSI options (Mar 13)
- [x] Order 5x XIAO ESP32-C3 from Pi Hut
- [x] Create ESPHome config for bedroom sensor
- [x] Fix ESPectre repo URL (moved to francescopace)
- [x] Fix OTA platform syntax for ESPHome 2025.12+
- [ ] First successful compile + flash
- [ ] Calibrate bedroom sensor
- [ ] Deploy remaining 4 sensors
- [ ] Naboo agent MQTT subscriber
- [ ] HA automations (lights, heating)

## Integration Points

### Naboo (Robot Agent)
Subscribes to `esp-csi/+/binary_sensor/motion_detected/state` and `esp-csi/+/sensor/movement_score/state` to know which rooms have people — used for:
- Navigation decisions (go to where people are)
- "Find Ziggy" queries
- Avoiding empty rooms during explore mode

### Home Assistant
Native API auto-discovery gives HA:
- `binary_sensor.esp_csi_bedroom_motion_detected`
- `sensor.esp_csi_bedroom_movement_score`
- `number.esp_csi_bedroom_threshold`

Use for: adaptive lighting, heating schedules, security (unexpected presence), sleep detection.

---

*Started: 2026-03-17*

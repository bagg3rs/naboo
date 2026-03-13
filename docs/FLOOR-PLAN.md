# Naboo Floor Plan & Navigation Map

## House Layout (2-storey)

### Ground Floor (Lower)
```
    FRONT OF HOUSE
┌──────────┬───────────────┐
│  Bed 3   │               │
│  (front) │   En-suite    │
│          │               │
├──────────┼───────────────┤
│          │               │
│ Kitchen  │    Dining     │
│          │               │
│          │               │
├──────────┴───────────────┤
│       Terrace            │
├──────────────────────────┤
│       Garden             │
└──────────────────────────┘
    REAR OF HOUSE
```

### First Floor (Upper)
```
    FRONT OF HOUSE
┌──────────┬───────────────┐
│ Master   │               │
│ Bed 1    │   Bathroom    │
│          │               │
├──────────┼───────────────┤
│          │               │
│   Hall   │   En-suite    │
│ (stairs) │               │
├──────────┼───────────────┤
│  Bed 2   │               │
│          │               │
├──────────┤  Living Room  │
│  Bed 3   │  (long)       │
│ (small)  │               │
│          │               │
└──────────┴───────────────┘
    REAR OF HOUSE
```

## Navigation Zones

| Zone ID | Name | Floor | Naboo-accessible | Notes |
|---------|------|-------|------------------|-------|
| GF_BED3 | Bedroom 3 | Ground | ✅ | Front of house |
| GF_ENSUITE | En-suite | Ground | ⚠️ | Probably too small |
| GF_KITCHEN | Kitchen | Ground | ✅ | Main activity area |
| GF_DINING | Dining | Ground | ✅ | Open to kitchen? |
| GF_TERRACE | Terrace | Ground | ❌ | Step/door barrier |
| 1F_MASTER | Master Bedroom 1 | First | ✅ | Front of house |
| 1F_BATH | Bathroom | First | ⚠️ | May be too small |
| 1F_HALL | Hall / Stairs | First | ⚠️ | Stairs = danger |
| 1F_ENSUITE | En-suite | First | ❌ | Too small |
| 1F_BED2 | Bedroom 2 | First | ✅ | |
| 1F_BED3 | Bedroom 3 (small) | First | ⚠️ | Small room |
| 1F_LIVING | Living Room | First | ✅ | Main play area, long room |

## No-Go Zones
- **Stairs** — mBot2 cannot do stairs, will fall
- **Terrace/Garden** — step down, outdoor
- **En-suites** — too narrow for safe navigation
- **Near stairs on hall** — buffer zone needed

## ESP32 CSI Sensor Placement (Recommended)
1. **📡 #1 — Hall (1F)** — central hub, covers master bed + stairway + access to bed 2
2. **📡 #2 — Living Room midpoint (1F)** — covers long room + bed 2/bed 3 end
3. **📡 #3 — Kitchen (GF)** — covers kitchen + dining area
4. **📡 #4 — Bed 3 (GF)** — front of ground floor, ensures full GF coverage

## Hardware
- **Camera (transmitter):** XIAO ESP32S3 Sense on Naboo (192.168.0.163) — WiFi CSI source
- **CyberPi:** 192.168.0.168 — additional WiFi signal source
- **Fixed receivers:** 4x Seeed XIAO ESP32-C3 @ £4.80 each from The Pi Hut

## Naboo's Operating Area
Primary: First floor (Living Room ↔ Hall ↔ Bedrooms)
Secondary: Ground floor (Kitchen ↔ Dining ↔ Bed 3)
Cannot transit between floors independently.

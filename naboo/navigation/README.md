# Vision-Based Navigation — TinyNav Adaptation

Based on [TinyNav](https://arxiv.org/abs/2603.11071) (March 2026).

## Architecture

### Pipeline (runs on Mac mini .50)
```
Pi Camera (.31:8080) → fetch snapshot (150ms)
  → MiDaS small depth estimation (100ms) → 24×24 depth frame
  → Stack last 10 frames as channels → 24×24×10 tensor
  → TinyNav CNN (< 5ms) → steering [-1, 1] + throttle [0, 1]
  → MQTT → mBot2/command
```

**Target: ~4Hz reactive navigation (250ms per cycle)**

### CNN Model (from TinyNav, ~23k params)
```
Input: 24×24×10 (10 depth frames, channel-stacked)

Conv2D(16, 3×3, stride=2, relu) → 12×12
SeparableConv2D(32, 3×3, stride=2, relu) → 6×6
SeparableConv2D(32, 3×3, relu)
SeparableConv2D(48, 3×3, relu)
Conv2D(1, 1×1, sigmoid) → spatial attention
Multiply (attention × features)
GlobalAveragePooling2D
Dense(64, relu) + Dropout(0.4)
├── Dense(1, tanh) → steering
└── Dense(1, sigmoid) → throttle
```

### Two-brain architecture
- **Fast brain (CNN):** 4Hz reactive navigation — handles obstacle avoidance, steering, speed
- **Slow brain (VLM + Haiku):** Every ~5s — describes scene, generates narration, personality

CNN replaces Haiku for navigation decisions. VLM still provides understanding and voice.

## Data Collection Plan
1. Drive Naboo manually via web controller
2. Record per frame: depth map (24×24) + motor commands (steering, throttle)
3. Save as sliding windows of 10 frames
4. Train/test split 60/40
5. Augment: horizontal flip, rotation

## Implementation Steps
1. ✅ MiDaS depth estimation on .50 (167ms, tested)
2. [ ] Add `/depth` endpoint to detect_service (returns depth map or heatmap)
3. [ ] Add depth visualisation to web controller
4. [ ] Build data collection mode in web controller (record depth + commands)
5. [ ] Train TinyNav CNN on collected data
6. [ ] Wire CNN into explore module as fast navigation controller
7. [ ] VLM continues providing narration alongside CNN navigation

## References
- Paper: https://arxiv.org/abs/2603.11071
- CNN code: https://github.com/regularpooria/tinynav_cnn
- Firmware: https://github.com/regularpooria/TinyNav
- Dataset: https://huggingface.co/datasets/regularpooria/tinynav_depth_camera_circuits

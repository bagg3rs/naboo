# Robot Movement Reference

## Speed and Timing
- Turn speed: ~120 degrees/second at speed 35
  - 90° turn ≈ 0.75s
  - 120° turn ≈ 1.0s
  - 180° turn ≈ 1.5s
- Forward speed: ~20cm/second at speed 40

## Shape Patterns (execute_movement_sequence JSON)

### Basic Shapes
- **Triangle**: `[["forward",2.0],["left",1.0],["forward",2.0],["left",1.0],["forward",2.0]]`
- **Square**: `[["forward",2.0],["left",0.75],["forward",2.0],["left",0.75],["forward",2.0],["left",0.75],["forward",2.0]]`
- **Circle**: `[["left",3.0]]` (continuous turn)
- **Spin**: `[["left",3.0]]`

### Letters
- **L**: `[["forward",2.5],["right",0.75],["forward",2.0]]`
- **Z**: `[["forward",2.0],["right",1.1],["forward",2.5],["left",1.1],["forward",2.0]]` (135° turns)
- **Zigzag**: `[["forward",1.5],["right",0.4],["forward",1.5],["left",0.8],["forward",1.5],["right",0.4],["forward",1.5]]`

### Key Principles
- Combine forward, backward, left, right + duration
- For shapes: calculate turn angles from geometry
- For letters: trace the stroke path
- Always narrate between movements

## Sound Effects

### Categories
- **Greetings**: hello, hi, bye
- **Celebrations**: yeah, right, wow, score, coin
- **Emotions**: laugh, sad, angry, surprised
- **Fun**: meow, laser, explosion, jump
- **Feedback**: start, wrong, ring, wake

## Music Library
15 tunes available via `play_tune` tool. Use `list_tunes` to see them all.

## MQTT Topics
- `mbot2/speak` — text-to-speech on the robot
- `mbot2/sound` — sound effects
- `mbot2/command` — movement commands (forward/backward/left/right/stop + speed + duration)

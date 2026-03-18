"""
CSI Presence Tracker — Infers room occupancy from ESP-CSI motion events.

ML mode gives clean motion detection (ON/OFF) but doesn't detect static presence.
This module tracks motion history to infer occupancy:
  - Motion spike → room occupied
  - No motion for X minutes AND no exit detected → still occupied (decay timer)
  - No motion for Y minutes → room empty

MQTT Topics consumed:
  esp-csi/<room>/binary_sensor/motion_detected/state  → ON/OFF
  esp-csi/<room>/sensor/movement_score/state           → float

MQTT Topics published:
  naboo/presence/<room>/state     → occupied/empty
  naboo/presence/<room>/since     → ISO timestamp of last state change
  naboo/presence/<room>/score     → current movement score
"""

import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field


@dataclass
class RoomState:
    name: str
    occupied: bool = False
    last_motion_time: float = 0.0      # epoch when last motion detected
    last_score: float = 0.0
    state_changed_at: float = 0.0
    motion_count_5min: int = 0          # motion events in last 5 minutes
    motion_history: list = field(default_factory=list)  # (timestamp, score) tuples

    # Configurable timers (seconds)
    OCCUPIED_TIMEOUT: float = 600       # 10 min no motion → empty
    MOTION_HISTORY_WINDOW: float = 300  # 5 min window for motion counting


class CSIPresenceTracker:
    """Tracks room occupancy across multiple CSI sensors."""

    def __init__(self, mqtt_client, rooms=None):
        self.mqtt = mqtt_client
        self.rooms: dict[str, RoomState] = {}

        if rooms is None:
            rooms = ["bedroom", "hall", "living", "kitchen", "bed3"]
        for room in rooms:
            self.rooms[room] = RoomState(name=room)

    def subscribe(self):
        """Subscribe to all CSI sensor topics."""
        self.mqtt.subscribe("esp-csi/+/binary_sensor/motion_detected/state")
        self.mqtt.subscribe("esp-csi/+/sensor/movement_score/state")
        self.mqtt.message_callback_add(
            "esp-csi/+/binary_sensor/motion_detected/state",
            self._on_motion
        )
        self.mqtt.message_callback_add(
            "esp-csi/+/sensor/movement_score/state",
            self._on_score
        )

    def _extract_room(self, topic: str) -> str:
        """Extract room name from topic like esp-csi/bedroom/..."""
        parts = topic.split("/")
        return parts[1] if len(parts) > 1 else "unknown"

    def _on_motion(self, client, userdata, msg):
        """Handle motion detected ON/OFF."""
        room_name = self._extract_room(msg.topic)
        if room_name not in self.rooms:
            self.rooms[room_name] = RoomState(name=room_name)

        room = self.rooms[room_name]
        state = msg.payload.decode().strip()
        now = time.time()

        if state == "ON":
            room.last_motion_time = now
            room.motion_history.append((now, room.last_score))
            self._prune_history(room, now)

            if not room.occupied:
                room.occupied = True
                room.state_changed_at = now
                self._publish_state(room)

    def _on_score(self, client, userdata, msg):
        """Handle movement score updates."""
        room_name = self._extract_room(msg.topic)
        if room_name not in self.rooms:
            self.rooms[room_name] = RoomState(name=room_name)

        try:
            score = float(msg.payload.decode().strip())
            self.rooms[room_name].last_score = score
        except ValueError:
            pass

    def check_timeouts(self):
        """Call periodically (e.g. every 30s) to decay occupied → empty."""
        now = time.time()
        for room in self.rooms.values():
            if not room.occupied:
                continue

            self._prune_history(room, now)
            elapsed = now - room.last_motion_time

            if elapsed > room.OCCUPIED_TIMEOUT:
                room.occupied = False
                room.state_changed_at = now
                self._publish_state(room)

    def _prune_history(self, room: RoomState, now: float):
        """Remove motion events older than the history window."""
        cutoff = now - room.MOTION_HISTORY_WINDOW
        room.motion_history = [
            (t, s) for t, s in room.motion_history if t > cutoff
        ]
        room.motion_count_5min = len(room.motion_history)

    def _publish_state(self, room: RoomState):
        """Publish inferred presence state to MQTT."""
        state = "occupied" if room.occupied else "empty"
        since = datetime.fromtimestamp(
            room.state_changed_at, tz=timezone.utc
        ).isoformat()

        self.mqtt.publish(
            f"naboo/presence/{room.name}/state", state, retain=True
        )
        self.mqtt.publish(
            f"naboo/presence/{room.name}/since", since, retain=True
        )

    def get_occupied_rooms(self) -> list[str]:
        """Return list of currently occupied room names."""
        return [r.name for r in self.rooms.values() if r.occupied]

    def get_room_status(self, room_name: str) -> dict:
        """Get detailed status for a room."""
        room = self.rooms.get(room_name)
        if not room:
            return {"error": f"Unknown room: {room_name}"}

        return {
            "room": room.name,
            "occupied": room.occupied,
            "last_motion": room.last_motion_time,
            "last_score": room.last_score,
            "motion_count_5min": room.motion_count_5min,
            "state_changed_at": room.state_changed_at,
        }

    def get_all_status(self) -> dict:
        """Get status for all rooms."""
        return {
            name: self.get_room_status(name)
            for name in self.rooms
        }

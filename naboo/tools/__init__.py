"""Naboo tools — robot, voice, vision, search, bird feeder."""

from .strands_tools import (
    robot_speak, robot_sound,
    robot_control, execute_movement_sequence,
    get_weather, web_search,
    query_vision,
    auto_mode,
    get_bird_stats, get_bird_patterns, get_busiest_bird_days, get_hourly_bird_activity,
    play_tune, list_tunes,
    set_mqtt_client, set_response_handler, set_vision_cache, set_auto_mode_controller,
)

__all__ = [
    "robot_speak", "robot_sound",
    "robot_control", "execute_movement_sequence",
    "get_weather", "web_search",
    "query_vision",
    "auto_mode",
    "get_bird_stats", "get_bird_patterns", "get_busiest_bird_days", "get_hourly_bird_activity",
    "play_tune", "list_tunes",
    "set_mqtt_client", "set_response_handler", "set_vision_cache", "set_auto_mode_controller",
]

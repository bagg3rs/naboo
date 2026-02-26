"""Strands SDK tool definitions for Naboo agent.

These tools use the @tool decorator from strands-agents for cleaner
definitions and automatic integration with the Strands agent loop.
"""

import json
import logging
import time
import hashlib
import uuid
import threading
from datetime import datetime, timedelta
from typing import Optional, Any

import paho.mqtt.client as mqtt
from strands import tool

logger = logging.getLogger(__name__)

# Global MQTT client (injected at startup)
_mqtt_client: Optional[mqtt.Client] = None

# Rate limiting for robot commands
_last_command_time: float = 0
_command_lock: bool = False
MIN_COMMAND_INTERVAL = 0.5  # 500ms minimum between commands (2Hz max)

# Vision cache manager (injected at startup)
_vision_cache: Optional[Any] = None

# Response handler for deduplication (injected at startup)
_response_handler: Optional[Any] = None

# Last telemetry data for scene similarity
_last_telemetry: dict = {"distance": None, "timestamp": None}

# Last vision query time for cache invalidation
_last_vision_query_time: Optional[float] = None


def set_mqtt_client(client: mqtt.Client) -> None:
    """Inject MQTT client for tools to use."""
    global _mqtt_client
    _mqtt_client = client
    logger.info("MQTT client injected into Strands tools")


def get_mqtt_client() -> Optional[mqtt.Client]:
    """Get the current MQTT client."""
    return _mqtt_client


def set_vision_cache(cache) -> None:
    """Inject vision cache manager for tools to use."""
    global _vision_cache
    _vision_cache = cache
    logger.info("Vision cache manager injected into Strands tools")


def set_response_handler(handler) -> None:
    """Inject response handler for deduplication.
    
    The response handler tracks when robot_speak is called so we can
    suppress duplicate responses to Home Assistant.
    
    Args:
        handler: ResponseHandler instance
    """
    global _response_handler
    _response_handler = handler
    logger.info("Response handler injected into Strands tools")


def update_telemetry(distance: float) -> None:
    """Update last telemetry data for scene similarity heuristics."""
    global _last_telemetry
    _last_telemetry = {
        "distance": distance,
        "timestamp": time.time()
    }


def _compute_scene_key(camera: str, query: str, distance: Optional[float]) -> str:
    """
    Compute cache key for vision query based on scene similarity.
    
    Scene similarity heuristics:
    - Same camera entity
    - Similar query (exact match for now)
    - Similar distance reading (within 20cm buckets)
    
    Args:
        camera: Camera entity ID
        query: Vision query text
        distance: Current distance reading in cm (None if unavailable)
        
    Returns:
        Cache key string
    """
    # Bucket distance into 20cm ranges for similarity
    # e.g., 0-20cm, 20-40cm, 40-60cm, etc.
    distance_bucket = "unknown"
    if distance is not None:
        distance_bucket = str(int(distance // 20) * 20)
    
    # Create key from camera, query, and distance bucket
    key_parts = [camera, query.lower().strip(), distance_bucket]
    key_string = "|".join(key_parts)
    
    # Hash for consistent key length
    return hashlib.md5(key_string.encode()).hexdigest()


def _is_scene_similar(distance: Optional[float], time_since_last: float) -> bool:
    """
    Check if current scene is similar to last cached scene.
    
    Similarity criteria:
    - Time since last query < 30 seconds (cache TTL)
    - Distance change < 20cm (scene hasn't changed much)
    
    Args:
        distance: Current distance reading in cm
        time_since_last: Time since last query in seconds
        
    Returns:
        True if scene is similar enough to use cache
    """
    # If too much time has passed, scene likely changed
    if time_since_last > 30.0:
        return False
    
    # If we don't have distance data, be conservative
    if distance is None or _last_telemetry["distance"] is None:
        return time_since_last < 5.0  # Only cache for 5s without distance
    
    # Check if distance changed significantly
    distance_change = abs(distance - _last_telemetry["distance"])
    return distance_change < 20.0  # Within 20cm = similar scene


def _resolve_bird_stats_date(date: str) -> str:
    """Resolve date string to YYYY-MM-DD format.
    
    Handles special keywords "today" and "yesterday" as well as
    explicit YYYY-MM-DD format dates.
    
    Args:
        date: "today", "yesterday", or YYYY-MM-DD format string
        
    Returns:
        Date in YYYY-MM-DD format
        
    Raises:
        ValueError: If date format is invalid
    """
    date_lower = date.lower().strip()
    
    if date_lower == "today":
        return datetime.now().strftime("%Y-%m-%d")
    
    if date_lower == "yesterday":
        yesterday = datetime.now() - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d")
    
    # Validate YYYY-MM-DD format
    try:
        parsed = datetime.strptime(date, "%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"Invalid date format: '{date}'. "
            "Use 'today', 'yesterday', or YYYY-MM-DD format (e.g., '2026-01-20')."
        )


def _format_bird_stats_response(data: dict) -> str:
    """Format bird stats response for conversational output.
    
    Formats bird feeder statistics into a kid-friendly conversational string.
    Handles zero visits, sorts species by count, limits to top 5, formats
    species names (replacing underscores with spaces), and pluralizes correctly.
    
    Args:
        data: Response dict with total_visits, species_counts, etc.
              Expected structure:
              {
                  "total_visits": 47,
                  "unique_species": 8,
                  "species_counts": {"robin": 12, "blue_tit": 9, ...}
              }
        
    Returns:
        Kid-friendly conversational string describing bird visits
    """
    total_visits = data.get("total_visits", 0)
    
    # Requirement 4.1: Handle zero visits case
    if total_visits == 0:
        return "No birds have visited the feeder today yet."
    
    # Requirement 4.2: Format conversational response with total count
    # Use singular/plural for "bird"
    bird_word = "bird" if total_visits == 1 else "birds"
    response_parts = [f"I've seen {total_visits} {bird_word} today!"]
    
    # Get species counts
    species_counts = data.get("species_counts", {})
    
    if species_counts:
        # Requirement 4.3: Sort by count descending, limit to top 5
        sorted_species = sorted(
            species_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        # Format each species
        species_parts = []
        for species_name, count in sorted_species:
            # Requirement 4.4: Replace underscores with spaces
            formatted_name = species_name.replace("_", " ")
            
            # Requirement 4.5: Pluralize based on count
            if count == 1:
                species_parts.append(f"{count} {formatted_name}")
            else:
                # Smart pluralization for common bird name endings
                if formatted_name.endswith(('ch', 'sh', 's', 'x', 'z')):
                    plural_name = formatted_name + "es"
                elif formatted_name.endswith('y') and len(formatted_name) > 1 and formatted_name[-2] not in 'aeiou':
                    plural_name = formatted_name[:-1] + "ies"
                else:
                    plural_name = formatted_name + "s"
                species_parts.append(f"{count} {plural_name}")
        
        # Join species with commas and "and" for the last one
        if len(species_parts) == 1:
            species_text = species_parts[0]
        elif len(species_parts) == 2:
            species_text = f"{species_parts[0]} and {species_parts[1]}"
        else:
            species_text = ", ".join(species_parts[:-1]) + f", and {species_parts[-1]}"
        
        response_parts.append(f"The most common visitors were {species_text}.")
    
    return " ".join(response_parts)


@tool
def get_bird_stats(date: str = "today") -> str:
    """Get bird feeder statistics for a given date.
    
    Use this tool when asked about:
    - Birds at the feeder
    - How many birds visited
    - What species have been seen
    - Bird activity for today/yesterday/specific date
    
    Args:
        date: Date to query - "today", "yesterday", or YYYY-MM-DD format
    
    Returns:
        Human-readable summary of bird visits
    """
    import os
    import httpx
    
    # DIAGNOSTIC LOGGING: Log tool call with input summary
    logger.info(f"TOOL: get_bird_stats called with date='{date}'")
    
    # Check stats URL is configured
    stats_url = os.getenv("BIRDFEEDER_STATS_URL")
    if not stats_url:
        result = "I can't check the bird feeder right now - it's not set up."
        logger.info(f"TOOL: get_bird_stats result='{result}' (no BIRDFEEDER_STATS_URL)")
        return result
    
    # Validate and resolve date
    try:
        resolved_date = _resolve_bird_stats_date(date)
    except ValueError as e:
        result = "I didn't understand that date. Try 'today', 'yesterday', or a date like '2026-01-20'."
        logger.info(f"TOOL: get_bird_stats result='{result}' (date validation error: {e})")
        return result
    
    # Build request URL
    request_url = f"{stats_url.rstrip('/')}/stats"
    logger.info(f"TOOL: get_bird_stats requesting {request_url}?date={resolved_date}")
    
    try:
        # Make HTTP GET request with 5s timeout
        response = httpx.get(
            request_url,
            params={"date": resolved_date},
            timeout=5.0
        )
        response.raise_for_status()
        
        # Parse JSON response
        payload = response.json()
        logger.info(f"TOOL: get_bird_stats received response: {payload.get('total_visits', 0)} visits")
        
        # Format and return result
        result = _format_bird_stats_response(payload)
        logger.info(f"TOOL: get_bird_stats result='{result[:50]}{'...' if len(result) > 50 else ''}'")
        return result
        
    except httpx.TimeoutException:
        result = "Sorry, I couldn't get the bird stats right now. The bird feeder might be busy."
        logger.info(f"TOOL: get_bird_stats result='{result}' (timeout)")
        return result
        
    except httpx.ConnectError as e:
        logger.error(f"TOOL: get_bird_stats connection error: {e}")
        result = "I can't reach the bird feeder right now. It might be offline."
        logger.info(f"TOOL: get_bird_stats result='{result}'")
        return result
        
    except httpx.HTTPStatusError as e:
        logger.error(f"TOOL: get_bird_stats HTTP error: {e.response.status_code}")
        result = "I had trouble checking the bird feeder. Let me try again later."
        logger.info(f"TOOL: get_bird_stats result='{result}'")
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"TOOL: get_bird_stats JSON parse error: {e}")
        result = "I got a response but couldn't understand it. Let me try again later."
        logger.info(f"TOOL: get_bird_stats result='{result}'")
        return result
        
    except Exception as e:
        logger.error(f"TOOL: get_bird_stats error: {e}", exc_info=True)
        result = "I had trouble checking the bird feeder. Let me try again later."
        logger.info(f"TOOL: get_bird_stats result='{result}'")
        return result


@tool
def get_bird_patterns(days: int = 7) -> str:
    """Get bird feeder activity patterns over recent days.
    
    Use this tool when asked about:
    - When is the feeder busiest
    - What time do birds usually visit
    - Peak activity hours
    - Typical patterns
    
    Args:
        days: Number of days to analyze (default 7)
    
    Returns:
        Human-readable summary of activity patterns
    """
    import os
    import httpx
    
    logger.info(f"TOOL: get_bird_patterns called with days={days}")
    
    stats_url = os.getenv("BIRDFEEDER_STATS_URL")
    if not stats_url:
        return "I can't check the bird feeder right now - it's not set up."
    
    request_url = f"{stats_url.rstrip('/')}/stats/patterns"
    
    try:
        response = httpx.get(request_url, params={"days": days}, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        
        # Format response
        parts = []
        
        if "hourly_averages" in data:
            # Find peak hours (top 3 by average visits)
            hourly = data["hourly_averages"]
            sorted_hours = sorted(hourly.items(), key=lambda x: x[1], reverse=True)
            peak_hours = [h for h, _ in sorted_hours[:3] if sorted_hours[0][1] > 0]
            
            if peak_hours:
                hour_strs = [f"{int(h)}:00" for h in peak_hours]
                parts.append(f"Over the last {days} days, the busiest times are typically around {', '.join(hour_strs)}.")
        
        if "total_visits" in data:
            avg_daily = data["total_visits"] / days if days > 0 else 0
            parts.append(f"On average, about {avg_daily:.0f} birds visit per day.")
        
        if not parts:
            return f"I looked at the last {days} days but didn't find clear patterns yet."
        
        return " ".join(parts)
        
    except Exception as e:
        logger.error(f"TOOL: get_bird_patterns error: {e}")
        return "I had trouble checking the bird feeder patterns."


@tool
def get_busiest_bird_days(count: int = 5) -> str:
    """Get the busiest days at the bird feeder.
    
    Use this tool when asked about:
    - Busiest day at the feeder
    - Record number of visits
    - Best days for bird watching
    
    Args:
        count: Number of top days to return (default 5)
    
    Returns:
        Human-readable summary of busiest days
    """
    import os
    import httpx
    
    logger.info(f"TOOL: get_busiest_bird_days called with count={count}")
    
    stats_url = os.getenv("BIRDFEEDER_STATS_URL")
    if not stats_url:
        return "I can't check the bird feeder right now - it's not set up."
    
    request_url = f"{stats_url.rstrip('/')}/stats/busiest"
    
    try:
        response = httpx.get(request_url, params={"count": count}, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("busiest_days"):
            return "I don't have enough data yet to tell you the busiest days."
        
        days = data["busiest_days"]
        parts = []
        
        # Format top day specially
        if days:
            top = days[0]
            from datetime import datetime
            try:
                date_obj = datetime.strptime(top["date"], "%Y-%m-%d")
                date_str = date_obj.strftime("%B %d")
            except:
                date_str = top["date"]
            parts.append(f"The busiest day was {date_str} with {top['total_visits']} visits!")
        
        # Mention runner-ups if available
        if len(days) > 1:
            runner_ups = [f"{d['date']}: {d['total_visits']}" for d in days[1:3]]
            if runner_ups:
                parts.append(f"Other busy days: {', '.join(runner_ups)}.")
        
        return " ".join(parts)
        
    except Exception as e:
        logger.error(f"TOOL: get_busiest_bird_days error: {e}")
        return "I had trouble checking the busiest days."


@tool
def get_hourly_bird_activity(date: str = "today") -> str:
    """Get hourly breakdown of bird activity for a specific day.
    
    Use this tool when asked about:
    - How many birds at a specific hour
    - Morning vs afternoon activity
    - Hourly breakdown for today
    
    Args:
        date: Date to query - "today", "yesterday", or YYYY-MM-DD format
    
    Returns:
        Human-readable hourly breakdown
    """
    import os
    import httpx
    
    logger.info(f"TOOL: get_hourly_bird_activity called with date={date}")
    
    stats_url = os.getenv("BIRDFEEDER_STATS_URL")
    if not stats_url:
        return "I can't check the bird feeder right now - it's not set up."
    
    try:
        resolved_date = _resolve_bird_stats_date(date)
    except ValueError:
        return "I didn't understand that date. Try 'today', 'yesterday', or a date like '2026-01-20'."
    
    request_url = f"{stats_url.rstrip('/')}/stats/hourly"
    
    try:
        response = httpx.get(request_url, params={"date": resolved_date}, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("hourly_counts"):
            return f"No bird activity recorded for {resolved_date}."
        
        hourly = data["hourly_counts"]
        total = sum(hourly.values())
        
        # Find active hours
        active_hours = {h: c for h, c in hourly.items() if c > 0}
        if not active_hours:
            return f"No birds visited on {resolved_date}."
        
        # Find peak hour
        peak_hour = max(active_hours, key=active_hours.get)
        peak_count = active_hours[peak_hour]
        
        # Morning vs afternoon
        morning = sum(c for h, c in hourly.items() if int(h) < 12)
        afternoon = sum(c for h, c in hourly.items() if int(h) >= 12)
        
        parts = [f"On {resolved_date}, there were {total} visits total."]
        parts.append(f"The busiest hour was {peak_hour}:00 with {peak_count} visits.")
        
        if morning > 0 and afternoon > 0:
            if morning > afternoon:
                parts.append(f"Morning was busier ({morning} visits) than afternoon ({afternoon}).")
            else:
                parts.append(f"Afternoon was busier ({afternoon} visits) than morning ({morning}).")
        
        return " ".join(parts)
        
    except Exception as e:
        logger.error(f"TOOL: get_hourly_bird_activity error: {e}")
        return "I had trouble checking the hourly activity."


@tool
def robot_speak(text: str) -> str:
    """
    Say text out loud through the robot's speaker using text-to-speech.
    Use this for every response so the child can hear you.

    Args:
        text: The text to speak out loud

    Returns:
        Confirmation of what was spoken
    """
    import re
    import os
    
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: robot_speak called with text='{text[:50]}{'...' if len(text) > 50 else ''}'")
    
    # Check if robot TTS is enabled (default: true for backward compatibility)
    robot_tts_enabled = os.getenv("ROBOT_TTS_ENABLED", "true").lower() == "true"
    if not robot_tts_enabled:
        logger.info("TOOL: robot_speak - Robot TTS disabled (ROBOT_TTS_ENABLED=false), skipping")
        return f"(Robot TTS disabled) Would have said: {text[:50]}..."
    
    if _mqtt_client is None:
        result = "Error: MQTT client not configured"
        logger.info(f"TOOL: robot_speak result='{result}'")
        return result

    # Strip emojis and other unicode symbols that TTS would speak literally
    # This regex removes most emoji and symbol characters
    clean_text = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF]', '', text)
    # Also remove :emoji_name: style text
    clean_text = re.sub(r':[a-zA-Z_]+:', '', clean_text)
    # Clean up any double spaces left behind
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    _mqtt_client.publish("mbot2/speak", clean_text)
    
    # Mark that robot spoke for response deduplication (Requirements 1.2, 2.1)
    if _response_handler is not None:
        _response_handler.mark_robot_spoke(clean_text)
        # DIAGNOSTIC LOGGING: Log that HA response will be suppressed (Requirements 8.3)
        logger.info(f"TOOL: robot_speak - HA response will be suppressed (robot_spoke=True)")
    
    result = f"Spoke: {clean_text}"
    logger.info(f"TOOL: robot_speak result='{result[:50]}{'...' if len(result) > 50 else ''}'")
    return result


@tool
def robot_sound(sound: str) -> str:
    """
    Play a preset sound effect on the robot.

    Args:
        sound: Sound name - one of: hello, hi, bye, yeah, wow, laugh,
               sad, angry, surprised, meow, start, laser, explosion,
               right, wrong, ring, jump, score, coin, wake

    Returns:
        Confirmation of sound played
    """
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: robot_sound called with sound='{sound}'")
    
    valid_sounds = [
        "hello", "hi", "bye", "yeah", "wow", "laugh", "sad",
        "angry", "surprised", "meow", "start", "laser",
        "explosion", "right", "wrong", "ring", "jump",
        "score", "coin", "wake"
    ]

    sound = sound.lower().strip()
    if sound not in valid_sounds:
        result = f"Unknown sound: {sound}. Available: {', '.join(valid_sounds)}"
        logger.info(f"TOOL: robot_sound result='{result[:50]}...'")
        return result

    if _mqtt_client is None:
        result = "Error: MQTT client not configured"
        logger.info(f"TOOL: robot_sound result='{result}'")
        return result

    _mqtt_client.publish("mbot2/sound", sound)
    result = f"Played sound: {sound}"
    logger.info(f"TOOL: robot_sound result='{result}'")
    return result


@tool
def robot_control(command: str, duration_seconds: float = 2.0) -> str:
    """
    Control the robot to move, navigate, or observe things.
    Each movement command runs for the specified duration then stops.
    
    IMPORTANT: This tool has rate limiting - wait at least 500ms between calls.

    Args:
        command: Natural language command like "move forward",
                 "turn left", "stop"
        duration_seconds: How long to run the movement (default 2 seconds)

    Returns:
        Result of the command execution
    """
    global _last_command_time, _command_lock
    
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: robot_control called with command='{command}' duration={duration_seconds}s")
    
    if _mqtt_client is None:
        result = "Error: MQTT client not configured"
        logger.info(f"TOOL: robot_control result='{result}'")
        return result

    # Rate limiting check
    now = time.time()
    time_since_last = now - _last_command_time
    if time_since_last < MIN_COMMAND_INTERVAL:
        wait_time = MIN_COMMAND_INTERVAL - time_since_last
        logger.warning(f"TOOL: robot_control rate limited - waiting {wait_time:.2f}s")
        time.sleep(wait_time)
    
    # Command lock check
    if _command_lock:
        result = "Command in progress - please wait"
        logger.warning(f"TOOL: robot_control result='{result}' (command_lock=True)")
        return result
    
    _command_lock = True
    _last_command_time = time.time()
    
    try:
        # Parse natural language command into firmware format
        command_lower = command.lower().strip()
        
        # Map natural language to command_type
        command_type = None
        parameters = {"speed": 40}  # Default speed
        
        if "forward" in command_lower or "ahead" in command_lower:
            command_type = "move_forward"
        elif "backward" in command_lower or "back" in command_lower or "reverse" in command_lower:
            command_type = "move_backward"
        elif "left" in command_lower:
            command_type = "turn_left"
            parameters["degrees"] = 90  # Default 90 degree turn
        elif "right" in command_lower:
            command_type = "turn_right"
            parameters["degrees"] = 90
        elif "stop" in command_lower or "halt" in command_lower:
            command_type = "stop"
            parameters = {}
        
        if command_type is None:
            result = f"Unknown command: {command}. Try: move forward, move backward, turn left, turn right, stop"
            logger.warning(f"TOOL: robot_control result='{result[:50]}...'")
            return result
        
        payload = json.dumps({"command_type": command_type, "parameters": parameters})
        _mqtt_client.publish("mbot2/command", payload)
        logger.info(f"TOOL: robot_control sent command_type={command_type} parameters={parameters}")
        
        # Wait for movement to complete (except for stop)
        if command_type != "stop":
            time.sleep(duration_seconds)
            # Send stop command after duration
            stop_payload = json.dumps({"command_type": "stop", "parameters": {}})
            _mqtt_client.publish("mbot2/command", stop_payload)
            logger.info(f"TOOL: robot_control auto-stopped after {duration_seconds}s")
        
        result = f"Executed: {command_type} for {duration_seconds}s"
        logger.info(f"TOOL: robot_control result='{result}'")
        return result
    
    finally:
        _command_lock = False


@tool
def execute_movement_sequence(moves: str, description: str = "custom pattern") -> str:
    """
    Execute a sequence of robot movements to draw shapes, letters, or patterns.
    Use this to make the robot draw ANY shape by generating the right moves.
    
    The robot turns at ~150 degrees/second at speed 35, and moves ~25cm/second at speed 40.
    
    Args:
        moves: JSON array of moves. Each move is [action, duration_seconds].
               Actions: "forward", "backward", "left", "right"
               Example: '[["forward", 1.5], ["left", 0.6], ["forward", 1.5]]'
               
               Timing guide:
               - 90° turn ≈ 0.6s, 180° turn ≈ 1.2s, 360° turn ≈ 2.4s
               - 1 second forward ≈ 25cm
               
        description: What shape/pattern this creates (for the response)

    Returns:
        Confirmation of pattern executed
    """
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: execute_movement_sequence called with description='{description}' moves_length={len(moves)}")
    
    if _mqtt_client is None:
        result = "Error: MQTT client not configured"
        logger.info(f"TOOL: execute_movement_sequence result='{result}'")
        return result
    
    try:
        pattern = json.loads(moves)
    except json.JSONDecodeError as e:
        result = f"Invalid moves JSON: {e}"
        logger.info(f"TOOL: execute_movement_sequence result='{result}'")
        return result
    
    if not isinstance(pattern, list):
        result = "Moves must be a JSON array of [action, duration] pairs"
        logger.info(f"TOOL: execute_movement_sequence result='{result}'")
        return result
    
    logger.info(f"TOOL: execute_movement_sequence executing {len(pattern)} moves for '{description}'")
    
    # Execute each move in the pattern
    for i, move in enumerate(pattern):
        if not isinstance(move, list) or len(move) != 2:
            logger.warning(f"TOOL: execute_movement_sequence skipping invalid move at index {i}: {move}")
            continue
            
        action, duration = move
        action = str(action).lower()
        
        try:
            duration = float(duration)
        except (ValueError, TypeError):
            duration = 1.0
        
        # Cap duration for safety
        duration = min(duration, 10.0)
        
        if action == "forward":
            cmd = {"command_type": "move_forward", "parameters": {"speed": 40}}
        elif action == "backward":
            cmd = {"command_type": "move_backward", "parameters": {"speed": 40}}
        elif action == "left":
            cmd = {"command_type": "turn_left", "parameters": {"speed": 35}}
        elif action == "right":
            cmd = {"command_type": "turn_right", "parameters": {"speed": 35}}
        elif action == "pause" or action == "wait":
            time.sleep(duration)
            continue
        else:
            logger.warning(f"TOOL: execute_movement_sequence unknown action: {action}")
            continue
        
        _mqtt_client.publish("mbot2/command", json.dumps(cmd))
        time.sleep(duration)
    
    # Stop at the end
    _mqtt_client.publish("mbot2/command", json.dumps({"command_type": "stop", "parameters": {}}))
    
    result = f"Completed: {description}!"
    logger.info(f"TOOL: execute_movement_sequence result='{result}'")
    return result


@tool
def get_weather(location: str = "London") -> str:
    """
    Get current weather for a location.

    Args:
        location: City name (default: London)

    Returns:
        Weather description with temperature and conditions
    """
    import os
    import json
    
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: get_weather called with location='{location}'")
    
    # Common city coordinates (for quick lookup)
    CITY_COORDS = {
        "london": (51.5074, -0.1278),
        "new york": (40.7128, -74.0060),
        "paris": (48.8566, 2.3522),
        "tokyo": (35.6762, 139.6503),
        "sydney": (-33.8688, 151.2093),
        "auckland": (-36.8485, 174.7633),
        "queenstown": (-45.0312, 168.6626),
        "los angeles": (34.0522, -118.2437),
        "berlin": (52.5200, 13.4050),
        "rome": (41.9028, 12.4964),
        "madrid": (40.4168, -3.7038),
        "amsterdam": (52.3676, 4.9041),
        "dublin": (53.3498, -6.2603),
        "edinburgh": (55.9533, -3.1883),
        "manchester": (53.4808, -2.2426),
        "birmingham": (52.4862, -1.8904),
        "glasgow": (55.8642, -4.2518),
        "liverpool": (53.4084, -2.9916),
        "bristol": (51.4545, -2.5879),
        "leeds": (53.8008, -1.5491),
    }
    
    # Try PirateWeather API first
    api_key = os.getenv("PIRATEWEATHER_API_KEY")
    if api_key:
        try:
            import httpx
            
            # Get coordinates for the location
            location_lower = location.lower().strip()
            coords = CITY_COORDS.get(location_lower)
            
            if not coords:
                # Default to London if unknown city
                logger.warning(f"Unknown city '{location}', defaulting to London coordinates")
                coords = CITY_COORDS["london"]
            
            lat, lon = coords
            url = f"https://api.pirateweather.net/forecast/{api_key}/{lat},{lon}"
            params = {"units": "uk2", "exclude": "minutely,hourly,alerts"}  # uk2 = Celsius, mph
            
            logger.info(f"Fetching weather from PirateWeather for {location} ({lat}, {lon})")
            response = httpx.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            current = data.get("currently", {})
            
            temp = current.get("temperature", "?")
            summary = current.get("summary", "Unknown conditions")
            humidity = current.get("humidity", 0) * 100
            wind_speed = current.get("windSpeed", 0)
            
            # Round temperature
            if isinstance(temp, (int, float)):
                temp = round(temp)
            
            result = f"Weather in {location}: {summary}, {temp}°C, humidity {humidity:.0f}%, wind {wind_speed:.0f} mph"
            logger.info(f"PirateWeather result: {result}")
            return result
            
        except Exception as e:
            logger.warning(f"PirateWeather API failed: {e}, falling back to search")

    # Fallback: Use DuckDuckGo search for weather (via ddgs package)
    try:
        from ddgs import DDGS
        import time

        logger.info(f"Searching DuckDuckGo for weather in {location}")
        
        # Try multiple search strategies
        search_queries = [
            f"weather {location} today temperature",
            f"{location} weather forecast",
        ]
        
        for query in search_queries:
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=5))
                    logger.info(f"DuckDuckGo query '{query}' returned {len(results)} results")
                    
                    if results:
                        for r in results:
                            body = r.get("body", "")
                            if body and any(word in body.lower() for word in ["°", "temperature", "rain", "sunny", "cloudy", "weather", "forecast", "cold", "warm", "hot"]):
                                result = f"Weather in {location}: {body[:250]}"
                                logger.info(f"Returning weather result: {result[:100]}...")
                                return result
                        
                        first = results[0].get("body", "")
                        if first:
                            result = f"Weather info for {location}: {first[:250]}"
                            logger.info(f"TOOL: get_weather result='{result[:50]}...'")
                            return result
                
                time.sleep(0.5)
                
            except Exception as e:
                logger.warning(f"Search query '{query}' failed: {e}")
                continue

        result = f"I couldn't check the weather for {location} right now."
        logger.info(f"TOOL: get_weather result='{result}'")
        return result

    except ImportError:
        logger.error("ddgs package not installed - run: uv add ddgs")
        result = "Weather service unavailable."
        logger.info(f"TOOL: get_weather result='{result}'")
        return result
    except Exception as e:
        logger.error(f"Weather search error: {e}")
        result = f"Couldn't get weather for {location} right now."
        logger.info(f"TOOL: get_weather result='{result}'")
        return result


# Music library - tunes stored as lists of (note, beat) tuples
# Notes are MIDI numbers: 60=C4, 62=D4, 64=E4, 65=F4, 67=G4, 69=A4, 71=B4, 72=C5
# Beat is duration in seconds
MUSIC_LIBRARY = {
    "happy_birthday": [
        (60, 0.3), (60, 0.3), (62, 0.6), (60, 0.6), (65, 0.6), (64, 1.2),
        (60, 0.3), (60, 0.3), (62, 0.6), (60, 0.6), (67, 0.6), (65, 1.2),
        (60, 0.3), (60, 0.3), (72, 0.6), (69, 0.6), (65, 0.6), (64, 0.6), (62, 1.2),
        (70, 0.3), (70, 0.3), (69, 0.6), (65, 0.6), (67, 0.6), (65, 1.2),
    ],
    "twinkle": [
        (60, 0.4), (60, 0.4), (67, 0.4), (67, 0.4), (69, 0.4), (69, 0.4), (67, 0.8),
        (65, 0.4), (65, 0.4), (64, 0.4), (64, 0.4), (62, 0.4), (62, 0.4), (60, 0.8),
        (67, 0.4), (67, 0.4), (65, 0.4), (65, 0.4), (64, 0.4), (64, 0.4), (62, 0.8),
        (67, 0.4), (67, 0.4), (65, 0.4), (65, 0.4), (64, 0.4), (64, 0.4), (62, 0.8),
        (60, 0.4), (60, 0.4), (67, 0.4), (67, 0.4), (69, 0.4), (69, 0.4), (67, 0.8),
        (65, 0.4), (65, 0.4), (64, 0.4), (64, 0.4), (62, 0.4), (62, 0.4), (60, 0.8),
    ],
    "mary_had_a_lamb": [
        (64, 0.4), (62, 0.4), (60, 0.4), (62, 0.4), (64, 0.4), (64, 0.4), (64, 0.8),
        (62, 0.4), (62, 0.4), (62, 0.8), (64, 0.4), (67, 0.4), (67, 0.8),
        (64, 0.4), (62, 0.4), (60, 0.4), (62, 0.4), (64, 0.4), (64, 0.4), (64, 0.4), (64, 0.4),
        (62, 0.4), (62, 0.4), (64, 0.4), (62, 0.4), (60, 0.8),
    ],
    "jingle_bells": [
        (64, 0.3), (64, 0.3), (64, 0.6),
        (64, 0.3), (64, 0.3), (64, 0.6),
        (64, 0.3), (67, 0.3), (60, 0.4), (62, 0.4), (64, 0.8),
        (65, 0.3), (65, 0.3), (65, 0.4), (65, 0.3),
        (65, 0.3), (64, 0.3), (64, 0.3), (64, 0.2), (64, 0.2),
        (64, 0.3), (62, 0.3), (62, 0.3), (64, 0.3), (62, 0.6), (67, 0.6),
    ],
    "star_wars": [
        # Star Wars main theme (correct notes)
        # D D D G D' C B A G' D'
        (50, 0.15), (50, 0.15), (50, 0.15), (55, 0.5), (62, 0.5),
        (60, 0.15), (59, 0.15), (57, 0.15), (67, 0.5), (62, 0.25),
        (60, 0.15), (59, 0.15), (57, 0.15), (67, 0.5), (62, 0.25),
        (60, 0.15), (59, 0.15), (60, 0.15), (57, 0.5),
    ],
    "imperial_march": [
        # Imperial March (Darth Vader's theme) - key of G minor
        # G G G Eb Bb G Eb Bb G
        (55, 0.5), (55, 0.5), (55, 0.5), (51, 0.35), (58, 0.15),
        (55, 0.5), (51, 0.35), (58, 0.15), (55, 1.0),
        # D D D Eb Bb G Eb Bb G
        (62, 0.5), (62, 0.5), (62, 0.5), (63, 0.35), (58, 0.15),
        (55, 0.5), (51, 0.35), (58, 0.15), (55, 1.0),
    ],
    "super_mario": [
        (64, 0.15), (64, 0.15), (0, 0.15), (64, 0.15), (0, 0.15), (60, 0.15), (64, 0.3),
        (67, 0.3), (0, 0.3), (55, 0.3),
    ],
    "zelda": [
        (62, 0.6), (55, 0.2), (55, 0.2), (55, 0.2), (55, 0.2), (55, 0.2), (55, 0.2),
        (55, 0.15), (57, 0.15), (59, 0.15), (60, 0.6),
        (60, 0.15), (60, 0.15), (60, 0.15), (60, 0.15), (60, 0.15), (60, 0.15),
        (60, 0.15), (62, 0.15), (64, 0.15), (65, 0.6),
    ],
    "victory": [
        (60, 0.15), (60, 0.15), (60, 0.15), (60, 0.4),
        (57, 0.4), (59, 0.4), (60, 0.3), (59, 0.15), (60, 0.6),
    ],
    "charge": [
        (60, 0.15), (64, 0.15), (67, 0.15), (72, 0.4), (67, 0.15), (72, 0.6),
    ],
    "doorbell": [
        (67, 0.4), (60, 0.6),
    ],
    "alert": [
        (72, 0.2), (0, 0.1), (72, 0.2), (0, 0.1), (72, 0.2),
    ],
    "success": [
        (60, 0.15), (64, 0.15), (67, 0.15), (72, 0.4),
    ],
    "error": [
        (55, 0.3), (0, 0.1), (52, 0.5),
    ],
    "wake_up": [
        (60, 0.2), (62, 0.2), (64, 0.2), (65, 0.2), (67, 0.2), (69, 0.2), (71, 0.2), (72, 0.4),
    ],
}


@tool
def play_tune(tune_name: str) -> str:
    """
    Play a tune from the music library on the robot.
    
    Args:
        tune_name: Name of the tune to play. Available tunes:
            - happy_birthday: Happy Birthday song
            - twinkle: Twinkle Twinkle Little Star
            - mary_had_a_lamb: Mary Had a Little Lamb
            - jingle_bells: Jingle Bells chorus
            - star_wars: Star Wars main theme
            - imperial_march: Darth Vader's theme
            - super_mario: Super Mario Bros theme
            - zelda: Legend of Zelda theme
            - victory: Victory fanfare
            - charge: Charge! bugle call
            - doorbell: Simple doorbell
            - alert: Alert/notification sound
            - success: Success chime
            - error: Error sound
            - wake_up: Ascending scale wake up

    Returns:
        Confirmation of tune played or error message
    """
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: play_tune called with tune_name='{tune_name}'")
    
    if _mqtt_client is None:
        result = "Error: MQTT client not configured"
        logger.info(f"TOOL: play_tune result='{result}'")
        return result
    
    tune_name = tune_name.lower().strip().replace(" ", "_").replace("-", "_")
    
    # Handle common aliases
    aliases = {
        "birthday": "happy_birthday",
        "twinkle_twinkle": "twinkle",
        "little_star": "twinkle",
        "mary": "mary_had_a_lamb",
        "lamb": "mary_had_a_lamb",
        "jingle": "jingle_bells",
        "bells": "jingle_bells",
        "starwars": "star_wars",
        "darth_vader": "imperial_march",
        "vader": "imperial_march",
        "mario": "super_mario",
        "link": "zelda",
        "win": "victory",
        "fanfare": "victory",
        "ding_dong": "doorbell",
        "bell": "doorbell",
        "alarm": "alert",
        "notification": "alert",
        "ok": "success",
        "done": "success",
        "fail": "error",
        "wrong": "error",
        "morning": "wake_up",
        "scale": "wake_up",
    }
    
    tune_name = aliases.get(tune_name, tune_name)
    
    if tune_name not in MUSIC_LIBRARY:
        available = ", ".join(sorted(MUSIC_LIBRARY.keys()))
        result = f"Unknown tune: {tune_name}. Available tunes: {available}"
        logger.info(f"TOOL: play_tune result='{result[:50]}...'")
        return result
    
    tune = MUSIC_LIBRARY[tune_name]
    logger.info(f"TOOL: play_tune executing tune='{tune_name}' notes={len(tune)}")
    
    # Send entire tune to robot in one message for faster playback
    payload = json.dumps({
        "command_type": "play_tune",
        "parameters": {"notes": tune}
    })
    _mqtt_client.publish("mbot2/command", payload)
    
    # Calculate total duration for response
    total_duration = sum(beat for _, beat in tune)
    
    result = f"Playing tune: {tune_name} ({len(tune)} notes, ~{total_duration:.1f}s)"
    logger.info(f"TOOL: play_tune result='{result}'")
    return result


@tool
def list_tunes() -> str:
    """
    List all available tunes in the music library.
    
    Returns:
        List of available tune names with descriptions
    """
    # DIAGNOSTIC LOGGING: Log tool call (Requirements 8.2)
    logger.info(f"TOOL: list_tunes called")
    
    descriptions = {
        "happy_birthday": "Happy Birthday song",
        "twinkle": "Twinkle Twinkle Little Star",
        "mary_had_a_lamb": "Mary Had a Little Lamb",
        "jingle_bells": "Jingle Bells chorus",
        "star_wars": "Star Wars main theme",
        "imperial_march": "Darth Vader's theme",
        "super_mario": "Super Mario Bros theme",
        "zelda": "Legend of Zelda theme",
        "victory": "Victory fanfare",
        "charge": "Charge! bugle call",
        "doorbell": "Simple doorbell",
        "alert": "Alert/notification sound",
        "success": "Success chime",
        "error": "Error sound",
        "wake_up": "Ascending scale wake up",
    }
    
    lines = ["Available tunes:"]
    for name in sorted(MUSIC_LIBRARY.keys()):
        desc = descriptions.get(name, "")
        lines.append(f"  - {name}: {desc}")
    
    result = "\n".join(lines)
    logger.info(f"TOOL: list_tunes result='{len(MUSIC_LIBRARY)} tunes listed'")
    return result


@tool
def web_search(query: str) -> str:
    """
    Search the web for current information about a topic.
    Use this when asked about current events, sports scores,
    recent news, or facts you're not sure about.

    Args:
        query: What to search for

    Returns:
        Summary of search results
    """
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: web_search called with query='{query[:50]}{'...' if len(query) > 50 else ''}'")
    
    try:
        # Use the new ddgs package (duckduckgo_search was renamed)
        from ddgs import DDGS

        results = []
        
        with DDGS() as ddgs:
            # Try news search first for current events/sports
            try:
                news_results = list(ddgs.news(query, max_results=3))
                results.extend(news_results)
                logger.debug(f"News search returned {len(news_results)} results")
            except Exception as e:
                logger.debug(f"News search failed: {e}")
            
            # Also try text search
            if len(results) < 3:
                try:
                    text_results = list(ddgs.text(query, max_results=3))
                    results.extend(text_results)
                    logger.debug(f"Text search returned {len(text_results)} results")
                except Exception as e:
                    logger.debug(f"Text search failed: {e}")

        if not results:
            result = f"No results found for '{query}'. I'll answer from what I know, but it might not be current."
            logger.info(f"TOOL: web_search result='{result[:50]}...'")
            return result

        # Format results in a kid-friendly way
        summaries = []
        seen = set()
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            # Skip duplicates and non-English results
            if title in seen or not body:
                continue
            seen.add(title)
            
            if title and body:
                summaries.append(f"{title}: {body}")
            elif body:
                summaries.append(body)
            
            if len(summaries) >= 3:
                break

        if not summaries:
            result = f"No useful results found for '{query}'. I'll answer from what I know."
            logger.info(f"TOOL: web_search result='{result[:50]}...'")
            return result

        result = "\n\n".join(summaries)
        logger.info(f"TOOL: web_search result='{len(summaries)} summaries returned'")
        return result

    except ImportError:
        logger.error("ddgs package not installed - run: uv add ddgs")
        result = "Search unavailable. I'll answer from what I know, but it might not be current."
        logger.info(f"TOOL: web_search result='{result[:50]}...'")
        return result
    except Exception as e:
        logger.error(f"Web search error: {e}")
        result = "Search unavailable right now. I'll answer from what I know, but it might not be current."
        logger.info(f"TOOL: web_search result='{result[:50]}...'")
        return result


@tool
def query_vision(query: str, camera: str = "robot") -> str:
    """
    IMPORTANT: Use this tool whenever asked about vision, seeing, camera, or what's visible.
    
    This tool accesses cameras to see what's happening RIGHT NOW.
    The cameras can detect people, objects, and activities in real-time.
    
    Use this when the user asks:
    - "What can you see?" or "What do you see?"
    - "Can you see me?" or "Do you see anyone?"
    - "What's in the room?" or "Look around"
    - "Is anyone there?" or "Who's there?"
    - "What's in the living room?" or "Show me the garden"
    - Any question about the current visual scene
    
    Args:
        query: What you want to know about the scene, e.g. "What do you see?", "Is anyone there?", "What's happening?"
        camera: Which camera to use - "robot" (default, XIAO on robot), "living_room" (Ring camera), "garden" (Ring camera)
    
    Returns:
        Real-time description of what the camera sees
    """
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: query_vision called with query='{query[:50]}{'...' if len(query) > 50 else ''}' camera='{camera}'")
    
    if not _mqtt_client:
        result = "I can't access the camera right now - my connection isn't working."
        logger.info(f"TOOL: query_vision result='{result}'")
        return result
    
    try:
        # Map camera names to entity IDs
        camera_map = {
            "robot": "camera.xiao_camera",  # XIAO ESP32S3 on robot
            "living_room": "camera.living_room_live_view",  # Ring living room
            "garden": "camera.garden_live_view",  # Ring garden
        }
        
        camera_entity = camera_map.get(camera, "camera.xiao_camera")
        
        # Get current distance from last telemetry for scene similarity
        current_distance = _last_telemetry.get("distance")
        
        # Calculate time since last vision query (not telemetry!)
        global _last_vision_query_time
        time_since_last = float('inf')  # Default to infinity if no previous query
        if _last_vision_query_time is not None:
            time_since_last = time.time() - _last_vision_query_time
        
        # Check cache if available
        cache_key = None
        if _vision_cache is not None:
            cache_key = _compute_scene_key(camera_entity, query, current_distance)
            
            # Try to get from cache
            cached_result = _vision_cache.get(cache_key)
            if cached_result is not None:
                logger.info(f"TOOL: query_vision cache HIT for {camera} (key={cache_key[:8]}...)")
                logger.info(f"TOOL: query_vision scene similarity: distance={current_distance}cm, time_since_last={time_since_last:.1f}s")
                logger.info(f"TOOL: query_vision result='{cached_result[:50]}...' (cached)")
                return cached_result
            
            logger.info(f"TOOL: query_vision cache MISS for {camera} (key={cache_key[:8]}...)")
        
        # Create query message
        timestamp = time.time()
        query_msg = {
            "timestamp": timestamp,
            "query": query,
            "user": "naboo",
            "camera": camera_entity  # Specify which camera to use
        }
        
        # Track response
        response_received = {"data": None, "done": False}
        
        def handle_response(client, userdata, msg):
            """Handle vision response."""
            try:
                payload = json.loads(msg.payload.decode('utf-8'))
                if payload.get("timestamp") == timestamp:
                    response_received["data"] = payload
                    response_received["done"] = True
            except Exception as e:
                logger.error(f"Error handling vision response: {e}")
        
        # Subscribe to responses
        _mqtt_client.subscribe("mbot2/vision/description", qos=1)
        _mqtt_client.message_callback_add("mbot2/vision/description", handle_response)
        
        # Publish query
        _mqtt_client.publish(
            "mbot2/vision/query",
            json.dumps(query_msg),
            qos=1
        )
        logger.info(f"Published vision query: {query}")
        
        # Wait for response (max 10 seconds)
        start_time = time.time()
        while time.time() - start_time < 10.0:
            if response_received["done"]:
                break
            time.sleep(0.1)
        
        # Clean up callback
        _mqtt_client.message_callback_remove("mbot2/vision/description")
        
        if not response_received["done"]:
            result = "The camera didn't respond in time. It might be busy or not working right now."
            logger.info(f"TOOL: query_vision result='{result}'")
            return result
        
        # Format response
        data = response_received["data"]
        description = data.get("description", "I couldn't see anything.")
        objects = data.get("objects", [])
        activities = data.get("activities", [])
        
        # Build kid-friendly response
        parts = [description]
        
        if objects:
            parts.append(f"I can see: {', '.join(objects[:5])}")
        
        if activities:
            parts.append(f"What's happening: {', '.join(activities[:3])}")
        
        result = " ".join(parts)
        
        # Update last vision query time
        _last_vision_query_time = time.time()
        
        # Cache the result if cache is available
        if _vision_cache is not None and cache_key is not None:
            # Estimate cost saved (typical vision query ~$0.001)
            cost_saved = 0.001
            _vision_cache.set(cache_key, result, cost_saved=cost_saved)
            logger.info(f"TOOL: query_vision cached result (key={cache_key[:8]}..., cost_saved=${cost_saved:.4f})")
        
        logger.info(f"TOOL: query_vision result='{result[:50]}{'...' if len(result) > 50 else ''}'")
        return result
        
    except Exception as e:
        logger.error(f"Error querying vision: {e}", exc_info=True)
        result = f"I had trouble looking at the camera: {str(e)}"
        logger.info(f"TOOL: query_vision result='{result[:50]}...'")
        return result


# Auto mode controller (injected at startup)
_auto_mode_controller: Optional[Any] = None


def set_auto_mode_controller(controller) -> None:
    """Inject auto mode controller for tools to use."""
    global _auto_mode_controller
    _auto_mode_controller = controller
    logger.info("Auto mode controller injected into Strands tools")


@tool
def auto_mode(action: str) -> str:
    """
    Control autonomous exploration mode (Auto Mode).
    
    Auto mode makes the robot explore autonomously using:
    - Ultrasonic sensors to detect obstacles
    - Camera vision to analyze what's blocking the path
    - Smart decision-making to navigate around obstacles
    
    The robot will move around, avoid obstacles, and explore the environment
    until stopped or it runs out of battery/time.
    
    Args:
        action: Either "start" to begin auto mode, or "stop" to end it
    
    Returns:
        Status message about auto mode
    """
    # DIAGNOSTIC LOGGING: Log tool call with input summary (Requirements 8.2)
    logger.info(f"TOOL: auto_mode called with action='{action}'")
    
    if not _auto_mode_controller:
        result = "Auto mode is not available right now."
        logger.info(f"TOOL: auto_mode result='{result}'")
        return result
    
    action = action.lower().strip()
    
    if action == "start":
        # Start auto mode
        try:
            if _auto_mode_controller.is_active:
                result = "Auto mode is already running!"
                logger.info(f"TOOL: auto_mode result='{result}'")
                return result
            
            # Always run in a new thread with its own event loop
            # This is necessary because Strands tools run in thread pools without event loops
            import asyncio
            import threading
            
            result_container = {"result": None, "error": None, "done": False}
            
            def run_auto_mode():
                """Run auto mode in a dedicated thread with its own event loop."""
                logger.info("TOOL: auto_mode thread started")
                try:
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    logger.info(f"TOOL: auto_mode created new event loop in thread: {loop}")
                    
                    # Store loop reference in controller for future use
                    _auto_mode_controller._loop = loop
                    logger.info("TOOL: auto_mode stored loop reference in controller")
                    
                    # Start auto mode (creates the task)
                    logger.info("TOOL: auto_mode calling start_auto_mode()")
                    result = loop.run_until_complete(_auto_mode_controller.start_auto_mode())
                    logger.info(f"TOOL: auto_mode start_auto_mode() returned: {result}")
                    result_container["result"] = result
                    result_container["done"] = True
                    
                    # CRITICAL: Keep the loop running while auto mode is active
                    # The loop must stay alive or the auto mode task will be cancelled!
                    logger.info("TOOL: auto_mode keeping event loop alive while active...")
                    while _auto_mode_controller.state.is_active:
                        # Run the loop for a short time to process tasks
                        loop.run_until_complete(asyncio.sleep(0.1))
                    
                    logger.info("TOOL: auto_mode stopped, loop can exit now")
                    
                except Exception as e:
                    logger.error(f"TOOL: auto_mode error in thread: {e}", exc_info=True)
                    result_container["error"] = str(e)
                    result_container["done"] = True
                finally:
                    logger.info("TOOL: auto_mode thread finishing")
                    # Close the loop now that auto mode is done
                    loop.close()
            
            # Start auto mode in background thread
            thread = threading.Thread(target=run_auto_mode, daemon=True, name="auto_mode")
            thread.start()
            
            # Wait briefly to see if it starts successfully
            thread.join(timeout=1.0)
            
            if result_container["error"]:
                result = f"Failed to start auto mode: {result_container['error']}"
                logger.info(f"TOOL: auto_mode result='{result}'")
                return result
            elif result_container["done"]:
                logger.info(f"TOOL: auto_mode result='{result_container['result']}'")
                return result_container["result"]
            else:
                # Still starting - return brief success message (TTS blocks movement!)
                result = "Auto mode on!"
                logger.info(f"TOOL: auto_mode result='{result}'")
                return result
            
        except Exception as e:
            logger.error(f"TOOL: auto_mode error starting: {e}", exc_info=True)
            result = f"Failed to start auto mode: {str(e)}"
            logger.info(f"TOOL: auto_mode result='{result}'")
            return result
    
    elif action == "stop":
        # Stop auto mode
        try:
            if not _auto_mode_controller.is_active:
                result = "Auto mode is not running."
                logger.info(f"TOOL: auto_mode result='{result}'")
                return result
            
            import asyncio
            
            # Get the loop from the controller
            loop = getattr(_auto_mode_controller, '_loop', None)
            if loop is None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    # Just set the flag directly
                    _auto_mode_controller.state.is_active = False
                    result = "Auto mode stopped."
                    logger.info(f"TOOL: auto_mode result='{result}'")
                    return result
            
            future = asyncio.run_coroutine_threadsafe(
                _auto_mode_controller.stop_auto_mode(),
                loop
            )
            result = future.result(timeout=2.0)
            logger.info(f"TOOL: auto_mode result='{result}'")
            return result
            
        except Exception as e:
            logger.error(f"TOOL: auto_mode error stopping: {e}", exc_info=True)
            result = f"Failed to stop auto mode: {str(e)}"
            logger.info(f"TOOL: auto_mode result='{result}'")
            return result
    
    else:
        result = f"Unknown action: {action}. Use 'start' or 'stop'."
        logger.info(f"TOOL: auto_mode result='{result}'")
        return result

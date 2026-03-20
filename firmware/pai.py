"""mBot2 firmware - CyberPi with MQTT control, TTS, sound effects, and encoder odometry"""
import time
import json
import cyberpi
import mbot2
import mbuild
from mqtt import MQTTClient

# Try to import encoder motor API
try:
    from mbot2.motor import encoder_motor
    HAS_ENCODER = True
except ImportError:
    HAS_ENCODER = False

# Config
WIFI_SSID = "internetoftings"
WIFI_PASS = "CHANGEME"  # Set your WiFi password here
MQTT_HOST = "192.168.0.50"
MQTT_PORT = 1883

# Safety thresholds
COLLISION_DISTANCE = 20  # cm - emergency stop if closer than this
SLOW_DISTANCE = 35       # cm - path considered clear when farther than this

# Orientation state
_is_upside_down = False
_last_orientation_check = 0
_upside_down_announced = False

# Odometry constants (mBot2 specs)
WHEEL_DIAMETER_MM = 65    # mBot2 wheel diameter
WHEEL_BASE_MM = 130       # Distance between wheels
ENCODER_PPR = 800         # Pulses per rotation (from docs)
MM_PER_DEGREE = (3.14159 * WHEEL_DIAMETER_MM) / 360  # ~0.567mm per degree

mqtt_client = None
is_moving = False
move_color = (0, 255, 0)  # Default green for forward
move_direction = "stopped"  # Track current direction
collision_blocked = False  # True when too close to move forward

# Odometry state
_last_left_pos = 0
_last_right_pos = 0
_total_distance_mm = 0    # Total distance traveled
_heading_degrees = 0      # Current heading (0 = starting direction)

# Command queue - callbacks just append, main loop processes
pending_topic = None
pending_payload = None
command_lock = False

# Eye expression state
_eye_state = "idle"
_eye_brightness = 100
_eye_blink_time = 0
_breath_phase = 0

# Dance mode state
_is_dancing = False
_dance_step = 0
_last_dance_step = 0
_dance_cooldown_until = 0  # Ignore loudness until this time (prevents button sounds triggering dance)
DANCE_LOUDNESS_THRESHOLD = 50  # Trigger dance when loudness > this (raised to avoid false triggers)
DANCE_STEP_DURATION = 400  # ms per dance move
DANCE_COOLDOWN_MS = 2000  # Ignore loudness for 2s after button press

# Power saving / sleep mode state - DISABLED FOR NOW (causing MQTT disconnection issues)
_is_sleeping = False
_last_activity_time = 0
_sleep_start_time = 0
_last_distance_reading = 300  # Last ultrasonic reading
_wake_motion_threshold = 30   # cm - wake up if something closer than this (reduced from 50)
_pickup_threshold = 20        # m/s2 - wake up if accelerometer > this (increased from 15 to reduce false wakes)
SLEEP_TIMEOUT_MS = 999999999  # DISABLED - was 30000 (30 seconds)
SLEEP_CHECK_INTERVAL = 2000   # Check for wake conditions every 2 seconds when sleeping
ACTIVITY_DISTANCE_CHANGE = 30 # cm - consider it activity if distance changes by this much (increased from 20)

# Explore state
_is_exploring = False
_explore_last_turn = 0
_explore_backup_until = 0

# LED API detection - try different methods
def led_on(r, g, b, led_id="all"):
    """Set LED color - correct API: cyberpi.led.on(r, g, b, led_id)"""
    try:
        if led_id == "all":
            cyberpi.led.on(r, g, b)  # All LEDs
        else:
            # Single LED - must be positional, not keyword
            cyberpi.led.on(int(r), int(g), int(b), int(led_id))
    except Exception as e:
        cyberpi.console.println("LED err:" + str(e)[:10])

def led_off(led_id="all"):
    """Turn off LED"""
    try:
        cyberpi.led.off(led_id)
    except Exception as e:
        cyberpi.console.println("LED off err")

def led_show(colors):
    """Show colors on all 5 LEDs - colors is space-separated string like 'red green blue yellow cyan'"""
    try:
        cyberpi.led.show(colors)
    except Exception as e:
        cyberpi.console.println("LED show err")

def eyes_set(state):
    """Set eye expression state"""
    global _eye_state
    _eye_state = state

def reset_odometry():
    """Reset odometry to zero"""
    global _last_left_pos, _last_right_pos, _total_distance_mm, _heading_degrees
    if HAS_ENCODER:
        try:
            encoder_motor.reset_position("S1")
            encoder_motor.reset_position("S2")
        except:
            pass
    _last_left_pos = 0
    _last_right_pos = 0
    _total_distance_mm = 0
    _heading_degrees = 0

def update_odometry():
    """Update odometry from encoder readings"""
    global _last_left_pos, _last_right_pos, _total_distance_mm, _heading_degrees
    
    if not HAS_ENCODER:
        return
    
    try:
        # Get current encoder positions (in degrees)
        left_pos = encoder_motor.get_position("S1")
        right_pos = encoder_motor.get_position("S2")
        
        # Calculate deltas since last update
        delta_left = left_pos - _last_left_pos
        delta_right = right_pos - _last_right_pos
        
        # Update last positions
        _last_left_pos = left_pos
        _last_right_pos = right_pos
        
        # Convert to mm traveled by each wheel
        left_mm = delta_left * MM_PER_DEGREE
        right_mm = delta_right * MM_PER_DEGREE
        
        # Calculate distance traveled (average of both wheels)
        distance_mm = (left_mm + right_mm) / 2
        _total_distance_mm = _total_distance_mm + abs(distance_mm)
        
        # Calculate heading change (differential drive kinematics)
        # Positive = turning left (counterclockwise)
        delta_heading = (right_mm - left_mm) / WHEEL_BASE_MM * (180 / 3.14159)
        _heading_degrees = (_heading_degrees + delta_heading) % 360
        
    except Exception as e:
        pass  # Silently fail if encoder read fails

def get_odometry():
    """Get current odometry data"""
    return {
        "dist_mm": int(_total_distance_mm),
        "heading": int(_heading_degrees),
    }

def eyes_happy():
    """Happy exploring eyes - bright green"""
    global _eye_blink_time
    now = time.ticks_ms()
    
    # Random blink every 4 seconds
    if _eye_blink_time == 0 or time.ticks_diff(now, _eye_blink_time) > 4000:
        led_off("all")
        time.sleep_ms(80)
        _eye_blink_time = now
    
    # Green eyes on positions 2 and 4
    led_on(0, 200, 50, 2)
    led_on(0, 200, 50, 4)

def eyes_looking(direction):
    """Looking left or right while turning"""
    led_off("all")
    if direction == "left":
        led_on(0, 255, 255, 1)
        led_on(0, 100, 100, 2)
    else:
        led_on(0, 255, 255, 5)
        led_on(0, 100, 100, 4)

def eyes_surprised():
    """Surprised/alert - orange/yellow"""
    led_on(255, 100, 0, 1)
    led_on(255, 200, 0, 2)
    led_on(255, 255, 100, 3)
    led_on(255, 200, 0, 4)
    led_on(255, 100, 0, 5)

def eyes_backing_up():
    """Backing up - yellow eyes"""
    led_off("all")
    led_on(200, 200, 0, 2)
    led_on(200, 200, 0, 4)

def eyes_idle():
    """Idle/waiting - blue eyes"""
    led_off("all")
    led_on(0, 0, 150, 2)
    led_on(0, 0, 150, 4)

def eyes_sleepy():
    """Sleepy - dim center"""
    led_off("all")
    led_on(50, 50, 30, 3)

def eyes_dizzy():
    """Dizzy/upside down - spinning red"""
    global _breath_phase
    _breath_phase = (_breath_phase + 30) % 500
    pos = (_breath_phase // 100) % 5 + 1
    led_off("all")
    led_on(255, 0, 0, pos)

def eyes_sleeping():
    """Sleeping mode - all LEDs off to save power"""
    led_off("all")

def eyes_waking_up():
    """Waking up animation - gentle blue fade in"""
    led_off("all")
    time.sleep_ms(100)
    led_on(0, 0, 50, "all")  # Dim blue
    time.sleep_ms(200)
    led_on(0, 0, 100, "all")  # Brighter blue
    time.sleep_ms(200)
    led_on(0, 0, 150, "all")  # Full blue (idle state)

def eyes_exploring():
    """Exploring eyes - alternating green/cyan"""
    global _breath_phase
    _breath_phase = (_breath_phase + 20) % 500
    if _breath_phase < 250:
        led_on(0, 200, 50, 2)
        led_on(0, 200, 50, 4)
        led_on(0, 100, 25, 3)
    else:
        led_on(0, 150, 200, 2)
        led_on(0, 150, 200, 4)
        led_on(0, 75, 100, 3)

def mark_activity():
    """Mark that activity occurred - resets sleep timer"""
    global _last_activity_time, _is_sleeping
    _last_activity_time = time.ticks_ms()
    
    # If we were sleeping, wake up with animation (silently)
    if _is_sleeping:
        _is_sleeping = False
        cyberpi.console.println("Waking up!")
        eyes_waking_up()

def check_for_sleep():
    """Check if we should go to sleep due to inactivity"""
    global _is_sleeping, _sleep_start_time, _last_activity_time
    
    if _is_sleeping:
        return  # Already sleeping
    
    # Don't sleep if moving or exploring
    if is_moving or _is_exploring:
        mark_activity()
        return
    
    # Check if enough time has passed since last activity
    time_since_activity = time.ticks_diff(time.ticks_ms(), _last_activity_time)
    if time_since_activity > SLEEP_TIMEOUT_MS:
        _is_sleeping = True
        _sleep_start_time = time.ticks_ms()
        cyberpi.console.println("Going to sleep...")
        eyes_sleeping()

def check_wake_conditions():
    """Check if we should wake up from sleep"""
    global _is_sleeping, _last_distance_reading
    
    if not _is_sleeping:
        return False
    
    try:
        # Check for motion detection (something moving in front)
        current_distance = mbuild.ultrasonic2.get(index=1)
        distance_change = abs(current_distance - _last_distance_reading)
        
        # Wake up if something comes close or if there's significant movement
        if current_distance < _wake_motion_threshold or distance_change > ACTIVITY_DISTANCE_CHANGE:
            mark_activity()
            cyberpi.console.println("Motion wake: d=" + str(current_distance))
            return True
        
        _last_distance_reading = current_distance
        
        # Check for pickup/shake detection
        total_accel = 0
        for axis in ['x', 'y', 'z']:
            accel = cyberpi.get_acc(axis)
            total_accel += abs(accel)
        
        if total_accel > _pickup_threshold:
            mark_activity()
            cyberpi.console.println("Pickup wake: accel=" + str(total_accel))
            return True
        
        # Check for button presses
        if cyberpi.controller.is_press("middle") or cyberpi.controller.is_press("a") or cyberpi.controller.is_press("b"):
            mark_activity()
            cyberpi.console.println("Button wake")
            return True
        
    except Exception as e:
        # If sensor reading fails, stay awake to be safe
        mark_activity()
    
    return False

def eyes_dancing():
    """Dancing eyes - rainbow colors cycling"""
    global _breath_phase
    _breath_phase = (_breath_phase + 50) % 500
    phase = _breath_phase // 100
    colors = [
        (255, 0, 0),    # Red
        (255, 165, 0),  # Orange
        (255, 255, 0),  # Yellow
        (0, 255, 0),    # Green
        (0, 0, 255),    # Blue
    ]
    for i in range(5):
        c = colors[(i + phase) % 5]
        led_on(c[0], c[1], c[2], i + 1)

def do_dance_move():
    """Execute one dance move based on current step"""
    global _dance_step, is_moving, move_direction
    
    # Dance moves: spin left, spin right, wiggle, forward-back
    move = _dance_step % 8
    
    if move == 0:
        mbot2.turn_left(speed=50)
        move_direction = "left"
    elif move == 1:
        mbot2.turn_right(speed=50)
        move_direction = "right"
    elif move == 2:
        mbot2.turn_right(speed=50)
        move_direction = "right"
    elif move == 3:
        mbot2.turn_left(speed=50)
        move_direction = "left"
    elif move == 4:
        mbot2.forward(speed=30)
        move_direction = "forward"
    elif move == 5:
        mbot2.backward(speed=30)
        move_direction = "backward"
    elif move == 6:
        mbot2.backward(speed=30)
        move_direction = "backward"
    elif move == 7:
        mbot2.forward(speed=30)
        move_direction = "forward"
    
    is_moving = True
    _dance_step = (_dance_step + 1) % 8

def check_music_and_dance():
    """Check loudness and trigger dance mode if music detected - DISABLED"""
    # Dancing disabled - too sensitive and disruptive
    return

# ── Local explore mode (works offline, no MQTT needed) ─────────────────────
def explore_start():
    """Start local exploration — ultrasonic obstacle avoidance loop"""
    global _is_exploring, is_moving, move_direction
    _is_exploring = True
    is_moving = True
    move_direction = "forward"
    cyberpi.audio.play("start")
    cyberpi.console.println("Explore ON")
    mbot2.forward(speed=40)

def explore_stop():
    """Stop local exploration"""
    global _is_exploring, is_moving, move_direction
    _is_exploring = False
    mbot2.EM_stop(port="all")
    is_moving = False
    move_direction = "stopped"
    cyberpi.audio.play("bye")
    cyberpi.console.println("Explore OFF")
    led_on(0, 0, 255, "all")

def explore_tick():
    """Run one tick of local exploration — call from main loop"""
    global _is_exploring, is_moving, move_direction, _explore_last_turn, _explore_backup_until
    
    if not _is_exploring:
        return
    
    now = time.ticks_ms()
    
    # If we're in a backup manoeuvre, wait for it to finish
    if _explore_backup_until > 0:
        if time.ticks_diff(now, _explore_backup_until) < 0:
            return  # Still backing up
        _explore_backup_until = 0
    
    try:
        dist = mbuild.ultrasonic2.get(index=1)
        
        if dist < COLLISION_DISTANCE:
            # Too close — back up, then turn
            mbot2.backward(speed=30)
            move_direction = "backward"
            _explore_backup_until = time.ticks_add(now, 600)  # Back up for 600ms
            time.sleep_ms(600)
            
            # Turn away — alternate left/right
            _explore_last_turn = 1 - _explore_last_turn
            if _explore_last_turn:
                mbot2.turn_left(speed=35)
                move_direction = "left"
            else:
                mbot2.turn_right(speed=35)
                move_direction = "right"
            time.sleep_ms(700)  # ~90 degree turn
            
            # Resume forward
            mbot2.forward(speed=40)
            move_direction = "forward"
            
        elif dist < SLOW_DISTANCE:
            # Getting close — slow down
            mbot2.forward(speed=25)
            move_direction = "forward"
        else:
            # Clear path — normal speed
            mbot2.forward(speed=40)
            move_direction = "forward"
            
    except Exception as e:
        cyberpi.console.println("Exp err:" + str(e)[:10])

# ── Button handling ────────────────────────────────────────────────────────
def check_buttons():
    """Check for button presses
    
    Triangle (A) = explore (toggle, works offline)
    Square (B)   = emergency stop everything
    Joystick mid = "what can you see?" (needs WiFi/MQTT)
    """
    global is_moving, move_direction, _is_exploring, mqtt_client
    
    try:
        # Triangle button (B on CyberPi) = toggle explore
        if cyberpi.controller.is_press("b"):
            mark_activity()
            if _is_exploring:
                explore_stop()
            else:
                explore_start()
                # Also tell the agent if MQTT is connected
                try:
                    mqtt_client.publish("naboo/questions", json.dumps({
                        "question": "I'm starting to explore! Narrate my adventure.",
                        "user": "button",
                        "conversation_id": "button-" + str(time.ticks_ms())
                    }))
                except:
                    pass  # Offline explore still works
            time.sleep_ms(500)  # Debounce
        
        # Square button (A on CyberPi) = emergency stop
        if cyberpi.controller.is_press("a"):
            mark_activity()
            if _is_exploring:
                explore_stop()
            mbot2.EM_stop(port="all")
            is_moving = False
            move_direction = "stopped"
            led_on(255, 0, 0, "all")
            cyberpi.audio.play("wrong")
            cyberpi.console.println("STOP!")
            time.sleep_ms(500)
            led_on(0, 0, 255, "all")
        
        # Joystick middle press = "what can you see?"
        if cyberpi.controller.is_press("middle"):
            mark_activity()
            # Visual feedback — cyan "thinking" eyes
            led_on(0, 200, 255, "all")
            cyberpi.audio.play("ring")
            cyberpi.console.println("Looking...")
            try:
                mqtt_client.publish("naboo/questions", json.dumps({
                    "question": "what can you see right now?",
                    "user": "button",
                    "conversation_id": "button-" + str(time.ticks_ms())
                }))
            except:
                # No MQTT — play error sound
                cyberpi.audio.play("wrong")
                cyberpi.console.println("No WiFi!")
                led_on(255, 0, 0, "all")
                time.sleep_ms(500)
            led_on(0, 0, 255, "all")
            time.sleep_ms(500)  # Debounce
    except:
        pass

def check_orientation():
    """Check if robot is upside down using accelerometer"""
    global _is_upside_down, _upside_down_announced, is_moving
    
    try:
        # Get Z-axis acceleration (gravity)
        # Normal: z ~ -9.8 (facing up), Upside down: z ~ +9.8
        z_accel = cyberpi.get_acc('z')
        
        # If Z > 5, we're upside down (gravity pointing wrong way)
        was_upside_down = _is_upside_down
        _is_upside_down = z_accel > 5
        
        if _is_upside_down:
            # Stop motors immediately!
            if is_moving:
                mbot2.EM_stop(port="all")
                is_moving = False
            
            # Stop exploring if flipped
            if _is_exploring:
                explore_stop()
            
            # Announce once when flipped
            if not _upside_down_announced:
                cyberpi.audio.play("surprised")
                cyberpi.cloud.tts("en", "Whoa! Help! I'm upside down!")
                _upside_down_announced = True
        else:
            # Reset announcement flag when right-side up
            if was_upside_down and not _is_upside_down:
                cyberpi.audio.play("yeah")
                _upside_down_announced = False
        
    except Exception as e:
        pass  # Ignore accelerometer errors

def check_shake():
    """Check if robot is being shaken"""
    try:
        if cyberpi.is_shaken():
            cyberpi.audio.play("laugh")
            return True
    except:
        pass
    return False

def update_eyes():
    """Update eye animation based on current state"""
    global _eye_state, move_direction, is_moving, collision_blocked, _is_upside_down, _is_dancing, _is_sleeping, _is_exploring
    
    try:
        # Don't update eyes if sleeping (they should stay off)
        if _is_sleeping:
            eyes_sleeping()
        elif _is_upside_down:
            eyes_dizzy()
        elif _is_dancing:
            eyes_dancing()
        elif _is_exploring:
            eyes_exploring()
        elif collision_blocked:
            eyes_surprised()
        elif not is_moving:
            eyes_idle()
        elif move_direction == "forward":
            eyes_happy()
        elif move_direction == "backward":
            eyes_backing_up()
        elif move_direction == "left":
            eyes_looking("left")
        elif move_direction == "right":
            eyes_looking("right")
        else:
            eyes_idle()
    except Exception as e:
        cyberpi.console.println("Eye err:" + str(e)[:10])

def on_message(topic, msg):
    """Handle MQTT message - just queue it, don't process here"""
    global pending_topic, pending_payload
    try:
        topic_str = topic.decode() if isinstance(topic, bytes) else str(topic)
        payload = msg.decode() if isinstance(msg, bytes) else str(msg)
        # Queue the command for main loop to process
        pending_topic = topic_str
        pending_payload = payload
    except Exception as e:
        cyberpi.console.println(str(e)[:20])

def process_command(topic_str, payload):
    """Process a command - called from main loop, not callback"""
    global is_moving, move_color, move_direction, command_lock, _is_exploring
    
    if command_lock:
        return  # Skip if already processing
    
    # Any command received counts as activity
    mark_activity()
    
    command_lock = True
    try:
        cyberpi.console.println(topic_str[-15:])  # Show last part of topic
        
        # Handle speak topic (TTS)
        if topic_str == "mbot2/speak":
            # Stop exploring and motors before speaking
            if _is_exploring:
                explore_stop()
            mbot2.EM_stop(port="all")
            is_moving = False
            led_on(255, 165, 0, "all")
            cyberpi.cloud.tts("en", payload)
            led_on(0, 0, 255, "all")
            return
        
        # Handle sound effects topic
        if topic_str == "mbot2/sound":
            mbot2.EM_stop(port="all")  # Stop motors before playing sound
            is_moving = False
            led_on(255, 0, 255, "all")
            cyberpi.audio.play(payload)
            led_on(0, 0, 255, "all")
            return
        
        # Handle command topic
        cmd = json.loads(payload)
        ct = cmd.get("command_type", "")
        sp = cmd.get("parameters", {}).get("speed", 50)
        if sp is None:
            sp = 50
        sp = int(sp)
        
        if ct == "move_forward":
            # Block forward movement if too close to obstacle
            if collision_blocked:
                cyberpi.console.println("FWD BLOCKED")
                return  # Ignore forward command
            mbot2.forward(speed=sp)
            is_moving = True
            move_direction = "forward"
            move_color = (0, 255, 0)  # Green
        elif ct == "force_forward":
            # Override collision block — use with caution
            mbot2.forward(speed=sp)
            is_moving = True
            move_direction = "forward"
            move_color = (255, 165, 0)  # Orange = forced
        elif ct == "move_backward":
            # Backward always allowed - helps get unstuck
            mbot2.backward(speed=sp)
            is_moving = True
            move_direction = "backward"
            move_color = (255, 255, 0)  # Yellow
        elif ct == "turn_left":
            # Turning always allowed
            mbot2.turn_left(speed=sp)
            is_moving = True
            move_direction = "left"
            move_color = (0, 255, 255)  # Cyan
        elif ct == "turn_right":
            # Turning always allowed
            mbot2.turn_right(speed=sp)
            is_moving = True
            move_direction = "right"
            move_color = (255, 0, 255)  # Magenta
        elif ct == "stop":
            if _is_exploring:
                explore_stop()
            mbot2.EM_stop(port="all")
            is_moving = False
            move_direction = "stopped"
            led_on(0, 0, 255, "all")  # Solid blue when stopped
        elif ct == "play_note":
            note = cmd.get("parameters", {}).get("note", 60)
            beat = cmd.get("parameters", {}).get("beat", 0.5)
            freq = int(440 * (2 ** ((note - 69) / 12)))
            led_on(255, 128, 0, "all")
            cyberpi.audio.play_tone(freq, float(beat))
            led_on(0, 0, 255, "all")
        elif ct == "play_tune":
            notes = cmd.get("parameters", {}).get("notes", [])
            for n, b in notes:
                if n == 0:
                    led_off("all")
                    time.sleep_ms(int(b * 1000))
                else:
                    norm = max(0, min(1, (n - 48) / 36))
                    if norm < 0.33:
                        r, g, b_col = 255, int(norm * 3 * 165), 0
                    elif norm < 0.66:
                        mid = (norm - 0.33) * 3
                        r, g, b_col = int(255 * (1 - mid)), 255, int(255 * mid)
                    else:
                        high = (norm - 0.66) * 3
                        r, g, b_col = int(255 * high), int(255 * (1 - high)), 255
                    
                    led_pos = ((n - 48) % 5) + 1
                    led_off("all")
                    led_on(r, g, b_col, led_pos)
                    if led_pos > 1:
                        led_on(r//3, g//3, b_col//3, led_pos - 1)
                    if led_pos < 5:
                        led_on(r//3, g//3, b_col//3, led_pos + 1)
                    
                    freq = int(440 * (2 ** ((n - 69) / 12)))
                    cyberpi.audio.play_tone(freq, float(b))
                    time.sleep_ms(int(b * 1000) + 20)
            
            led_on(0, 0, 255, "all")
        elif ct == "reset_odometry":
            reset_odometry()
            cyberpi.console.println("Odom reset")
        
        cyberpi.console.println(ct[:15])
    except Exception as e:
        cyberpi.console.println(str(e)[:20])
    finally:
        command_lock = False

def connect_wifi():
    """Connect WiFi"""
    if cyberpi.wifi.is_connect():
        return True
    led_on(255, 0, 0, "all")
    cyberpi.console.println("WiFi...")
    cyberpi.wifi.connect(WIFI_SSID, WIFI_PASS)
    t = time.ticks_ms()
    while not cyberpi.wifi.is_connect():
        if time.ticks_diff(time.ticks_ms(), t) > 15000:
            return False
        time.sleep_ms(100)
    led_on(0, 255, 0, "all")
    cyberpi.console.println("WiFi OK")
    return True

def send_telemetry():
    """Send sensor data including odometry and IMU"""
    global mqtt_client
    try:
        # Update odometry from encoders
        update_odometry()
        
        dist = mbuild.ultrasonic2.get(index=1)
        batt = cyberpi.get_battery()
        odom = get_odometry()
        
        data = json.dumps({
            "d": dist,
            "b": batt,
            "dm": odom["dist_mm"],
            "h": odom["heading"],
            "roll": cyberpi.get_roll(),
            "pitch": cyberpi.get_pitch(),
            "yaw": cyberpi.get_yaw(),
            "ax": cyberpi.get_acc("x"),
            "ay": cyberpi.get_acc("y"),
            "az": cyberpi.get_acc("z"),
        })
        mqtt_client.publish("mbot2/telemetry", data)
    except:
        pass

def check_collision():
    """Check for obstacles and stop if too close - returns distance"""
    global is_moving, move_direction, collision_blocked, mqtt_client
    try:
        dist = mbuild.ultrasonic2.get(index=1)
        
        # Update collision blocked state based on current reading
        if dist < COLLISION_DISTANCE:
            collision_blocked = True
            
            # Emergency stop if moving forward (but not if exploring — explore_tick handles its own)
            if is_moving and move_direction == "forward" and not _is_exploring:
                mbot2.EM_stop(port="all")
                is_moving = False
                move_direction = "stopped"
                led_on(255, 0, 0, "all")
                cyberpi.audio.play("wrong")
                mark_activity()
                try:
                    mqtt_client.publish("mbot2/collision", json.dumps({"distance": dist}))
                except:
                    pass
        elif dist > SLOW_DISTANCE:
            if collision_blocked:
                collision_blocked = False
                try:
                    mqtt_client.publish("mbot2/clear", json.dumps({"distance": dist}))
                except:
                    pass
        
        return dist
    except Exception as e:
        cyberpi.console.println(str(e)[:15])
        return 300  # Default to "clear" if sensor fails

def main():
    """Main"""
    global mqtt_client, pending_topic, pending_payload, _last_activity_time
    
    cyberpi.console.println("FW v8 Starting...")
    
    # Initialize activity timer
    _last_activity_time = time.ticks_ms()
    
    # Startup eye animation - confirms eyes are working
    cyberpi.console.println("Testing LEDs...")
    for i in range(5):
        led_on(0, 255, 0, i + 1)
        time.sleep_ms(100)
    time.sleep_ms(200)
    led_off("all")
    
    # Show encoder status
    if HAS_ENCODER:
        cyberpi.console.println("Encoders: OK")
        reset_odometry()
    else:
        cyberpi.console.println("Encoders: N/A")
    
    wifi_ok = connect_wifi()
    mqtt_ok = False
    
    if wifi_ok:
        cyberpi.console.println("MQTT...")
        try:
            mqtt_client = MQTTClient("mbot2", MQTT_HOST, port=MQTT_PORT, keepalive=60)
            mqtt_client.set_callback(on_message)
            mqtt_client.connect()
            mqtt_client.subscribe("mbot2/command")
            mqtt_client.subscribe("mbot2/speak")
            mqtt_client.subscribe("mbot2/sound")
            mqtt_ok = True
            cyberpi.console.println("MQTT OK!")
            led_on(0, 0, 255, "all")
        except Exception as e:
            cyberpi.console.println("MQTT fail:" + str(e)[:10])
    
    if not wifi_ok or not mqtt_ok:
        # Offline mode — buttons still work for local explore
        cyberpi.console.println("OFFLINE MODE")
        cyberpi.console.println("Play=explore Stop=stop")
        led_on(255, 165, 0, "all")  # Orange = offline
        time.sleep_ms(1000)
    
    cyberpi.console.println("Ready!")
    time.sleep_ms(500)
    
    last_tel = time.ticks_ms()
    last_led = time.ticks_ms()
    last_collision_check = time.ticks_ms()
    last_orientation_check = time.ticks_ms()
    last_sleep_check = time.ticks_ms()
    last_explore_tick = time.ticks_ms()
    
    while True:
        try:
            current_time = time.ticks_ms()
            
            # If sleeping, check wake conditions less frequently and skip most processing
            if _is_sleeping:
                if time.ticks_diff(current_time, last_sleep_check) > SLEEP_CHECK_INTERVAL:
                    check_wake_conditions()
                    last_sleep_check = current_time
                
                # Still check for MQTT messages even when sleeping
                if mqtt_ok:
                    mqtt_client.check_msg()
                
                # Process any pending command (this will wake us up)
                if pending_topic is not None:
                    topic_to_process = pending_topic
                    payload_to_process = pending_payload
                    pending_topic = None
                    pending_payload = None
                    process_command(topic_to_process, payload_to_process)
                
                # Check buttons even when sleeping (to wake with explore)
                check_buttons()
                
                time.sleep_ms(500)
                continue
            
            # Normal operation when awake
            # Check for MQTT messages (non-blocking, just queues them)
            if mqtt_ok:
                mqtt_client.check_msg()
            
            # Process any pending command
            if pending_topic is not None:
                topic_to_process = pending_topic
                payload_to_process = pending_payload
                pending_topic = None
                pending_payload = None
                process_command(topic_to_process, payload_to_process)
            
            # EXPLORE TICK - run every 100ms when exploring
            if _is_exploring and time.ticks_diff(current_time, last_explore_tick) > 100:
                explore_tick()
                last_explore_tick = current_time
            
            # COLLISION CHECK - run very frequently (every 30ms) for safety
            if time.ticks_diff(current_time, last_collision_check) > 30:
                check_collision()
                last_collision_check = current_time
            
            # ORIENTATION CHECK - check every 200ms for upside down and shake
            if time.ticks_diff(current_time, last_orientation_check) > 200:
                check_orientation()
                if check_shake():
                    mark_activity()
                check_buttons()
                last_orientation_check = current_time
            
            # Eye animations (update every 50ms for smooth effects)
            if time.ticks_diff(current_time, last_led) > 50:
                update_eyes()
                last_led = current_time
            
            # Telemetry (every 1 second) — only if MQTT connected
            if mqtt_ok and time.ticks_diff(current_time, last_tel) > 1000:
                send_telemetry()
                last_tel = current_time
            
            # Sleep check (every 5 seconds when awake)
            if time.ticks_diff(current_time, last_sleep_check) > 5000:
                check_for_sleep()
                last_sleep_check = current_time
            
            time.sleep_ms(20)  # Faster loop for better collision response
        except Exception as e:
            cyberpi.console.println(str(e)[:20])
            mbot2.EM_stop(port="all")
            break

main()

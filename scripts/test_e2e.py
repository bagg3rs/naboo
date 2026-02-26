"""Quick end-to-end test — send a question, wait for answer."""

import paho.mqtt.client as mqtt
import time
import json
import sys

BROKER = "192.168.0.170"
Q_TOPIC = "naboo/questions"
A_TOPIC = "naboo/answers"

answers = []
connected = False

def on_connect(c, u, f, reason, props):
    global connected
    connected = True
    c.subscribe(A_TOPIC)
    print(f"Connected to broker. Listening on {A_TOPIC}...")

def on_message(c, u, msg):
    payload = json.loads(msg.payload)
    answers.append(payload)
    print(f"\n✓ ANSWER: {payload.get('text', msg.payload.decode())[:300]}\n")

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.on_connect = on_connect
c.on_message = on_message
c.connect(BROKER, 1883)
c.loop_start()

for _ in range(30):
    if connected:
        break
    time.sleep(0.1)

if not connected:
    print("ERROR: Could not connect to broker")
    sys.exit(1)

question = sys.argv[1] if len(sys.argv) > 1 else "hello Naboo, what is Arsenal?"
print(f"Sending: {question!r}")
c.publish(Q_TOPIC, json.dumps({"text": question, "user": "test"}))

print("Waiting up to 20s for answer...")
for _ in range(20):
    if answers:
        break
    time.sleep(1)

c.loop_stop()
c.disconnect()

if answers:
    print(f"\nTest PASSED — got {len(answers)} answer(s)")
else:
    print("\nTest FAILED — no answer received")
    sys.exit(1)

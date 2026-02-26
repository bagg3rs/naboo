"""
Naboo agent — main entry point.

Connects to MQTT, listens for questions, routes through the model
router (S1 → S2 → Bedrock), and responds via voice/MQTT.

Run with: uv run python -m naboo
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
from strands import Agent

from naboo.config import (
    build_model_router,
    build_query_classifier,
    IOT_ENDPOINT, IOT_THING_NAME, IOT_CERT_PATH, IOT_KEY_PATH, IOT_CA_PATH,
    HA_URL, HA_TOKEN,
)
from naboo.router.query_classifier import QueryComplexity
from naboo.tools.strands_tools import (
    set_mqtt_client,
    set_response_handler,
    robot_speak, robot_sound, robot_control,
    execute_movement_sequence,
    get_weather, web_search,
    query_vision,
    auto_mode,
    get_bird_stats, get_bird_patterns,
    play_tune, list_tunes,
)
from naboo.memory.memory_loader import load_memory_context, append_session_summary

logger = logging.getLogger(__name__)

# MQTT topics
QUESTION_TOPIC       = os.getenv("NABOO_QUESTION_TOPIC", "naboo/questions")
ANSWER_TOPIC         = os.getenv("NABOO_ANSWER_TOPIC", "naboo/answers")
ROBOT_COMMAND_TOPIC  = os.getenv("ROBOT_COMMAND_TOPIC", "mbot2/commands")
ROBOT_RESPONSE_TOPIC = os.getenv("ROBOT_RESPONSE_TOPIC", "mbot2/responses")
MQTT_HOST            = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT            = int(os.getenv("MQTT_PORT", "1883"))

# All tools available to the agent
ALL_TOOLS = [
    robot_speak, robot_sound,
    robot_control, execute_movement_sequence,
    get_weather, web_search,
    query_vision,
    auto_mode,
    get_bird_stats, get_bird_patterns,
    play_tune, list_tunes,
]


def _load_system_prompt() -> str:
    """Load base system prompt + inject memory context."""
    prompt_path = Path(__file__).parent / "prompts" / "system_prompt.txt"
    base_prompt = prompt_path.read_text() if prompt_path.exists() else ""

    memory_context = load_memory_context(days_back=7)
    if memory_context:
        return base_prompt + "\n\n---\n\n" + memory_context
    return base_prompt


def _clean_response(text: str) -> str:
    """
    Strip Strands tool narration from agent responses and extract clean text.

    Strands may produce:
      - [Use robot_speak "hello"] — bracket annotation format
      - robot_speak {"text": "hello"} — inline tool call format
      - A plain conversational response (ideal)
    """
    import re
    import json as _json

    # If the whole response looks like a tool call with JSON arg, extract the text
    # e.g. robot_speak {"text": "2 plus 2 is 4."}
    tool_json_match = re.match(r'^\w+\s+(\{.+\})\s*$', text.strip(), re.DOTALL)
    if tool_json_match:
        try:
            data = _json.loads(tool_json_match.group(1))
            extracted = data.get("text") or data.get("message") or data.get("response")
            if extracted:
                return extracted.strip()
        except Exception:
            pass

    # Remove tool call annotations: [Use tool_name "..."] or [Use tool_name ...]
    text = re.sub(r'\[Use \w+[^\]]*\]', '', text)
    # Remove inline tool calls: tool_name {"text": "..."}  (at start of line)
    text = re.sub(r'^\w+\s+\{[^}]+\}\s*', '', text, flags=re.MULTILINE)
    # Remove leading/trailing quotes that sometimes wrap the response
    text = text.strip().strip('"')
    # Collapse multiple newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class NabooAgent:
    """
    Naboo Q&A agent.

    Lifecycle:
    1. start() — connect to MQTT, build Strands agent
    2. MQTT message arrives on QUESTION_TOPIC
    3. Classify complexity → select model → run Strands agent
    4. Publish answer to ANSWER_TOPIC
    5. stop() — disconnect, write session summary
    """

    def __init__(self):
        self.router = build_model_router()
        self.classifier = build_query_classifier()
        self.system_prompt = _load_system_prompt()

        self._mqtt: Optional[mqtt.Client] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._question_queue: asyncio.Queue = asyncio.Queue()
        self._session_messages: list = []

    # ── MQTT ─────────────────────────────────────────────────────────────────

    def _connect_mqtt(self) -> mqtt.Client:
        """Connect to MQTT broker (local or AWS IoT Core)."""
        client = mqtt.Client(client_id=f"naboo-agent-{IOT_THING_NAME}")

        if IOT_ENDPOINT and IOT_CERT_PATH and Path(IOT_CERT_PATH).exists():
            # AWS IoT Core — TLS with device certificate
            logger.info(f"Connecting to AWS IoT Core: {IOT_ENDPOINT}")
            client.tls_set(
                ca_certs=IOT_CA_PATH,
                certfile=IOT_CERT_PATH,
                keyfile=IOT_KEY_PATH,
            )
            client.connect(IOT_ENDPOINT, 8883, 60)
        else:
            # Local MQTT broker (dev / home network)
            logger.info(f"Connecting to local MQTT: {MQTT_HOST}:{MQTT_PORT}")
            client.connect(MQTT_HOST, MQTT_PORT, 60)

        client.on_message = self._on_message
        client.subscribe(QUESTION_TOPIC)
        client.loop_start()
        logger.info(f"Subscribed to {QUESTION_TOPIC}")
        return client

    def _on_message(self, client, userdata, msg):
        """MQTT message callback — queue question for processing."""
        try:
            payload = json.loads(msg.payload.decode())
            question = payload.get("text") or payload.get("question") or str(payload)
            user = payload.get("user", "unknown")
            # Pass conversation_id through so the HA component can match responses
            conversation_id = payload.get("conversation_id")
            # Use stored loop reference — paho runs callbacks in its own thread
            if self._loop:
                self._loop.call_soon_threadsafe(
                    self._question_queue.put_nowait,
                    {"question": question, "user": user, "conversation_id": conversation_id}
                )
        except Exception as e:
            logger.error(f"Failed to parse MQTT message: {e}")

    # ── Agent ─────────────────────────────────────────────────────────────────

    def _build_strands_agent(self, question: str) -> Agent:
        """Build Strands agent with the right model for this question."""
        complexity = self.classifier.classify_query(question)
        model_config = self.router.select_model(complexity)
        model = self.router.get_model_instance(model_config)

        logger.info(
            f"Routing '{question[:50]}...' → "
            f"{complexity.value} → {model_config.provider}/{model_config.model_id}"
        )

        return Agent(
            model=model,
            system_prompt=self.system_prompt,
            tools=ALL_TOOLS,
        )

    async def _process_question(self, question: str, user: str) -> str:
        """Run the agent on a question and return the response."""
        agent = self._build_strands_agent(question)

        try:
            result = agent(question)
            response = _clean_response(str(result))
            self._session_messages.append({
                "user": user,
                "question": question,
                "response": response,
            })
            return response
        except Exception as e:
            logger.error(f"Agent error: {e}")
            return "Sorry, I had a little trouble with that one. Can you ask me again?"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        """Start the agent — connect MQTT and begin processing loop."""
        logger.info("Naboo agent starting...")

        # Store event loop reference BEFORE starting MQTT (paho runs in its own thread)
        self._loop = asyncio.get_event_loop()

        self._mqtt = self._connect_mqtt()
        set_mqtt_client(self._mqtt)

        self._running = True
        logger.info("Naboo is ready. Listening for questions...")

        while self._running:
            try:
                item = await asyncio.wait_for(self._question_queue.get(), timeout=1.0)
                question = item["question"]
                user = item.get("user", "unknown")
                conversation_id = item.get("conversation_id")

                logger.info(f"Question from {user} (conv:{conversation_id}): {question}")
                response = await self._process_question(question, user)
                logger.info(f"Response: {response[:100]}...")

                # Publish answer — include conversation_id so HA component can match it
                if self._mqtt:
                    payload = {"text": response, "user": user, "response": response}
                    if conversation_id:
                        payload["conversation_id"] = conversation_id
                    self._mqtt.publish(ANSWER_TOPIC, json.dumps(payload))

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Processing error: {e}")

    async def stop(self):
        """Stop the agent and write session summary."""
        self._running = False

        # Write session summary to memory
        if self._session_messages:
            summary_lines = [f"- {m['user']}: {m['question'][:80]}" for m in self._session_messages]
            append_session_summary(
                f"Session with {len(self._session_messages)} messages:\n" + "\n".join(summary_lines)
            )
            logger.info("Session summary written to memory")

        if self._mqtt:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()

        logger.info("Naboo agent stopped.")

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
import time
from pathlib import Path
from typing import Optional

from naboo import telemetry

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
    get_camera_view,
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
    get_camera_view,
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

    Strands / local models may produce:
      - robot_speak("2 plus 2 is 4!")         ← plain string arg
      - robot_speak({"text": "..."})           ← JSON arg with parens
      - robot_speak {"text": "..."}            ← JSON arg without parens
      - [Use robot_speak "hello"]              ← bracket annotation format
      - A plain conversational response (ideal)
    """
    import re
    import json as _json

    text = text.strip()

    # ── 0. Strip model special tokens ──────────────────────────────────────
    text = re.sub(r'<\|im_end\|>|<\|im_start\|>|<\|endoftext\|>', '', text).strip()

    # ── 1. Full-response tool call: extract inner text ─────────────────────
    # Matches any of:  word("string")  word({"text":"..."})  word {"text":"..."}
    # Only applies when the ENTIRE response is a tool call

    # Plain string arg: robot_speak("Hello!")  or  robot_speak('Hello!')
    plain_str = re.match(r'^\w+\(["\'](.+?)["\']\)\s*$', text, re.DOTALL)
    if plain_str:
        return plain_str.group(1).strip()

    # Bracket annotation: [Use robot_speak "Hello!"] or [Use tool "text"]
    bracket_str = re.match(r'^\[Use \w+\s+["\'](.+?)["\']\]\s*$', text, re.DOTALL)
    if bracket_str:
        return bracket_str.group(1).strip()

    # JSON arg with or without parens: robot_speak({"text":"..."})
    json_arg = re.match(r'^\w+[\s(]+(\{.+\})[)\s]*$', text, re.DOTALL)
    if json_arg:
        try:
            data = _json.loads(json_arg.group(1))
            extracted = data.get("text") or data.get("message") or data.get("response")
            if extracted:
                return extracted.strip()
        except Exception:
            pass

    # ── 2. Inline tool calls mixed with normal text ────────────────────────
    # Remove bracket annotations: [Use robot_speak "..."] or [Use tool_name ...]
    text = re.sub(r'\[Use \w+[^\]]*\]', '', text)
    # Remove tool call lines: robot_speak("...") or robot_speak({"text":"..."}) at line start
    text = re.sub(r'^\w+\([^)]+\)\s*\n?', '', text, flags=re.MULTILINE)
    # Remove tool JSON without parens: robot_speak {"text":"..."} at line start
    text = re.sub(r'^\w+[\s(]+\{[^}]+\}[)\s]*\n?', '', text, flags=re.MULTILINE)
    # Remove leading/trailing quotes
    text = text.strip().strip('"')

    # ── 3. Strip trailing meta-commentary ─────────────────────────────────
    text = re.sub(
        r'\s*(How\'?s that( for (a|an) \w+)?|Is that helpful|Does that help|Hope that helps|Hope this helps|I found this information by[^.]*)[!?.]*\s*$',
        '', text, flags=re.IGNORECASE
    )

    # ── 4. Collapse whitespace ─────────────────────────────────────────────
    text = re.sub(r'\n{3,}', '\n\n', text)

    # ── 5. Strip common model-generated prefixes ──────────────────────────
    text = re.sub(r'^Robot\s+says\s*[:\-]\s*["\']?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^(Naboo\s+says|Naboo\s+responds?|Response)\s*[:\-]\s*["\']?', '', text, flags=re.IGNORECASE)

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
        self._stopped = False
        self._question_queue: asyncio.Queue = asyncio.Queue()
        self._session_messages: list = []
        # Map conversation_id → identified user (set when someone says "I am Ziggy")
        self._identified_users: dict = {}

    def _detect_user_introduction(self, text: str) -> Optional[str]:
        """
        Detect when someone identifies themselves (e.g. 'I am Ziggy').

        Returns the identified name, or None if no introduction detected.
        """
        import re
        # Patterns: "I'm X", "I am X", "my name is X", "it's X", "this is X"
        patterns = [
            r"(?:i'?m|i am|it'?s|this is|my name is)\s+([A-Za-z]+)",
        ]
        # Recognised family names and aliases
        FAMILY = {
            "ziggy": "Ziggy",
            "lev": "Lev",
            "dad": "Daddy", "daddy": "Daddy", "richard": "Daddy",
            "mum": "Mummy", "mummy": "Mummy", "vanessa": "Mummy",
        }
        text_lower = text.lower()
        for pattern in patterns:
            match = re.search(pattern, text_lower)
            if match:
                name = match.group(1).lower()
                if name in FAMILY:
                    return FAMILY[name]
        return None

    # ── MQTT ─────────────────────────────────────────────────────────────────

    def _connect_mqtt(self) -> mqtt.Client:
        """Connect to MQTT broker (local or AWS IoT Core)."""
        import uuid as _uuid
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="naboo-agent",  # Fixed ID so Mosquitto kicks stale connections on restart
            clean_session=True,
        )

        def _on_connect(c, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                logger.error(f"MQTT connect failed: {reason_code}")
                return
            logger.info(f"MQTT connected. Subscribing to {QUESTION_TOPIC}")
            c.subscribe(QUESTION_TOPIC, qos=1)
            logger.info(f"Subscribed to {QUESTION_TOPIC}")

        client.on_connect = _on_connect
        client.on_message = self._on_message

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

        client.loop_start()
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

    def _enrich_question(self, question: str) -> tuple[str, bool]:
        """
        Pre-fetch tool data for tool-backed queries and inject into the prompt.

        Returns (enriched_question, tools_disabled) where tools_disabled=True
        means the agent shouldn't use tools (data already injected).
        """
        import re
        q = question.lower()
        # ── Weather pre-fetch ─────────────────────────────────────────────────
        is_weather = (
            re.search(r'\b(weather|forecast|raining|sunny|cloudy|windy)\b', q)
            or re.search(r'\bweather like\b', q)
            or (re.search(r'\btemperature\b', q) and re.search(r'\b(today|tomorrow|tonight|now|in [A-Z]|like)\b', question))
        )
        if is_weather:
            location_match = re.search(
                r'\bin\s+([A-Za-z][A-Za-z ]{1,20}?)(?:\s+(?:at the moment|right now|today|tomorrow|tonight|at present)|\s*[\?,]|$)',
                question, re.IGNORECASE
            )
            location = location_match.group(1).strip() if location_match else "London"
            try:
                with telemetry.span("naboo.prefetch.weather", {"location": location}):
                    from naboo.tools.strands_tools import get_weather
                    weather_data = get_weather(location)
                enriched = f"{question}\n\n[Weather data: {weather_data}]"
                logger.info(f"Pre-fetched weather for '{location}': {weather_data}")
                return enriched, True
            except Exception as e:
                logger.warning(f"Weather pre-fetch failed: {e}")
        # ── Football fixture pre-fetch ────────────────────────────────────────
        if re.search(r'\b(next|playing|fixtures?|schedule|when)\b', q) and \
           re.search(r'\b(match|game|play(?:ing)?|kick.?off)\b', q):
            try:
                from naboo.tools.strands_tools import web_search
                team_match = re.search(
                    r'(?:is\s+)?([A-Z][A-Za-z ]{2,20}?)(?:\s+(?:playing|FC|AFC|United|City|Town|FC)\b|\'s?\s+next)',
                    question
                )
                if not team_match:
                    team_match = re.search(r'\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)*(?:\s+(?:FC|AFC|United|City|Town))?)\b', question)
                team = team_match.group(1).strip() if team_match else "Arsenal"
                with telemetry.span("naboo.prefetch.fixture", {"team": team}):
                    search_result = web_search(f"{team} next match fixture date 2025-26 season")
                enriched = f"{question}\n\n[Search results: {search_result}]"
                logger.info(f"Pre-fetched fixture info for {team}")
                return enriched, True
            except Exception as e:
                logger.warning(f"Fixture pre-fetch failed: {e}")
        # ── Vision pre-fetch ──────────────────────────────────────────────────
        is_vision = (
            re.search(r'\bwhat (can you|do you) see\b', q)
            or re.search(r'\bcan you see (me|us|anything|the)\b', q)
            or re.search(r'\blook around\b', q)
            or re.search(r'\bwhat.s (in the|in this) (room|kitchen|hallway|garden)\b', q)
            or re.search(r'\bshow me\b', q)
            or re.search(r'\buse.* camera\b', q)
            or re.search(r'\bwhat do you see\b', q)
            or re.search(r'\bwhat.s (around|ahead|behind|there)\b', q)
            or re.search(r'\bi\s*spy\b', q)
            or re.search(r'\bplay.* i\s*spy\b', q)
            or re.search(r'\blet.s play\b.*\b(spy|look|see)\b', q)
        )
        if is_vision:
            try:
                import httpx, base64
                camera_url = os.getenv("NABOO_CAMERA_URL", "")
                vision_url = os.getenv("NABOO_VISION_URL", "")
                if camera_url and vision_url:
                    with telemetry.span("naboo.prefetch.vision"):
                        cam_resp = httpx.get(camera_url, timeout=5.0)
                        cam_resp.raise_for_status()
                        image_b64 = base64.b64encode(cam_resp.content).decode("utf-8")
                        # Use a proper vision prompt - raw "play i spy" gets refused by VLM
                        is_ispy = re.search(r'\bi\s*spy\b', q) or re.search(r'\bplay.* i\s*spy\b', q)
                        vision_question = "List 5 specific objects you can see in this image" if is_ispy else question
                        vis_resp = httpx.post(
                            f"{vision_url}/vision",
                            json={"image_b64": image_b64, "question": vision_question, "max_tokens": 200},
                            timeout=30.0,
                        )
                        vis_resp.raise_for_status()
                        description = vis_resp.json().get("description", "")

                    # ── Game mode: I Spy ──────────────────────────────────
                    if re.search(r'\bi\s*spy\b', q) or re.search(r'\bplay.* i\s*spy\b', q):
                        enriched = (
                            f"You can see the following: {description}\n\n"
                            f"Play I Spy! Pick ONE specific object you can see in the description above. "
                            f"Say 'I spy with my little eye, something beginning with [first letter]!' "
                            f"Pick something a child could guess — not too hard, not too easy. "
                            f"Do NOT reveal what it is yet. Wait for them to guess. "
                            f"Keep it fun and playful!"
                        )
                    else:
                        enriched = f"{question}\n\n[Camera view: {description}]"

                    logger.info(f"Pre-fetched vision: {description[:80]}...")
                    return enriched, True
            except Exception as e:
                logger.warning(f"Vision pre-fetch failed: {e}")
        return question, False

    def _build_strands_agent(self, question: str, no_tools: bool = False,
                             conversation_id: str = "") -> tuple["Agent", dict]:
        """Build Strands agent with the right model for this question. Returns (agent, route_attrs)."""
        complexity = self.classifier.classify_query(question)

        # When data is pre-fetched we don't need Bedrock's search capability.
        # Cap at MODERATE (MLX 7b) — the injected context is already there.
        if no_tools and complexity in (QueryComplexity.COMPLEX, QueryComplexity.CURRENT_INFO):
            complexity = QueryComplexity.MODERATE

        model_config = self.router.select_model(complexity)
        model = self.router.get_model_instance(model_config)

        # Select tools based on interaction source and complexity:
        # - Pre-fetched queries → no tools (data already injected)
        # - SIMPLE queries → no tools (facts, math, greetings — no tools needed)
        # - HA voice pipeline (ha_*) → exclude robot_speak/robot_sound (HA handles TTS)
        # - Everything else → all tools
        if no_tools:
            tools = []
        elif complexity == QueryComplexity.SIMPLE:
            tools = []  # Simple queries don't need tools — saves ~9s of prompt overhead
        elif conversation_id.startswith("ha_"):
            tools = [t for t in ALL_TOOLS if t not in (robot_speak, robot_sound)]
        else:
            tools = ALL_TOOLS

        logger.info(
            f"Routing '{question[:50]}...' → "
            f"{complexity.value} → {model_config.provider}/{model_config.model_id}"
            + (" [no-tools, pre-fetched]" if no_tools else "")
            + (" [ha-voice, no speak/sound]" if conversation_id.startswith("ha_") and not no_tools else "")
        )

        route_attrs = {
            "complexity": complexity.value,
            "provider": model_config.provider,
            "model": model_config.model_id,
            "no_tools": str(no_tools),
            "source": "ha_voice" if conversation_id.startswith("ha_") else "standalone",
        }

        return Agent(
            model=model,
            system_prompt=self.system_prompt,
            tools=tools,
        ), route_attrs

    async def _call_mlx_direct(self, question: str) -> str:
        """Call MLX server directly via HTTP, bypassing Strands entirely.

        Used for no-tools queries where Strands adds 30-50s of overhead
        for zero benefit (no tool calling, no agent loop).
        """
        import httpx
        mlx_port = os.getenv("MLX_PORT", "11435")
        url = f"http://192.168.0.50:{mlx_port}/v1/chat/completions"
        model_id = os.getenv("MLX_MODEL_S1", "mlx-community/Qwen2.5-7B-Instruct-4bit")
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": question},
            ],
            "max_tokens": 256,
            "temperature": 0.7,
        }
        resp = httpx.post(url, json=payload, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    async def _process_question(self, question: str, user: str, conversation_id: str = "") -> str:
        """Run the agent on a question and return the response."""
        t0 = time.monotonic()

        with telemetry.span("naboo.question", {
            "user": user,
            "conversation_id": conversation_id,
            "question_len": len(question),
        }) as root_span:

            # Pre-fetch
            with telemetry.span("naboo.enrich") as enrich_span:
                enriched_question, no_tools = self._enrich_question(question)
                enrich_span.set_attribute("pre_fetched", str(no_tools))
                enrich_span.set_attribute("enriched", str(enriched_question != question))

            # Fast path: no tools needed → call MLX directly, skip Strands entirely
            # Strands adds 30-50s overhead even with no tools (metrics/telemetry flush)
            # Applies to: pre-fetched (vision) queries AND simple queries (greetings, facts, math)
            complexity = self.classifier.classify_query(enriched_question)
            use_direct = no_tools or complexity == QueryComplexity.SIMPLE
            if use_direct:
                try:
                    response = await self._call_mlx_direct(enriched_question)
                    response = _clean_response(response)
                    elapsed = time.monotonic() - t0
                    logger.info(f"Direct MLX response ({elapsed:.1f}s): {response[:80]}...")
                    root_span.set_attribute("response_len", len(response))
                    root_span.set_attribute("elapsed_s", f"{elapsed:.2f}")
                    root_span.set_attribute("path", "direct_mlx")
                    self._session_messages.append({
                        "user": user,
                        "question": question,
                        "response": response,
                    })
                    return response
                except Exception as e:
                    logger.warning(f"Direct MLX failed, falling back to Strands: {e}")

            # Route
            with telemetry.span("naboo.route") as route_span:
                agent, route_attrs = self._build_strands_agent(
                    enriched_question, no_tools=no_tools, conversation_id=conversation_id
                )
                for k, v in route_attrs.items():
                    route_span.set_attribute(k, v)

            # Run
            try:
                with telemetry.span("naboo.agent.run", route_attrs):
                    result = agent(enriched_question)

                response = _clean_response(str(result))
                elapsed = time.monotonic() - t0
                root_span.set_attribute("response_len", len(response))
                root_span.set_attribute("elapsed_s", f"{elapsed:.2f}")
                self._session_messages.append({
                    "user": user,
                    "question": question,
                    "response": response,
                })
                return response
            except Exception as e:
                logger.error(f"Agent error: {e}", exc_info=True)
                root_span.record_exception(e)
                return "Sorry, I had a little trouble with that one. Can you ask me again?"

    async def _warmup_mlx(self):
        """
        Send a trivial inference to keep the MLX text + vision models warm.

        Cold starts take 8-13s because macOS pages out weights under memory
        pressure. Fires at agent startup so the first real query is fast.
        Only runs during active hours (08:00–22:00).
        """
        from datetime import datetime
        hour = datetime.now().hour
        if not (8 <= hour <= 22):
            logger.info("MLX warmup skipped (outside active hours 08:00–22:00)")
            return

        import httpx

        # Warm text model
        mlx_host = os.getenv("MLX_HOST", "")
        if mlx_host:
            try:
                logger.info(f"MLX warmup: sending ping to {mlx_host}...")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    await client.post(
                        f"{mlx_host}/v1/chat/completions",
                        json={
                            "model": os.getenv("MLX_MODEL_S2", "mlx-community/Qwen2.5-7B-Instruct-4bit"),
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 5,
                        },
                    )
                logger.info("MLX warmup complete — model is hot")
            except Exception as e:
                logger.warning(f"MLX warmup failed (non-fatal): {e}")

        # Warm vision server — SKIP at startup to avoid GPU memory contention
        # with the MLX text model. Vision model warms on first actual vision request.
        # On 16GB M4, loading both models simultaneously causes memory thrashing
        # and makes the first text query take 25-40s instead of 1-3s.
        # vision_url = os.getenv("NABOO_VISION_URL", "")
        # camera_url = os.getenv("NABOO_CAMERA_URL", "")
        logger.info("Vision warmup skipped (deferred to first vision request to avoid GPU contention)")

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

        # Kick off background warmup so the first real query is always fast
        asyncio.ensure_future(self._warmup_mlx())

        while self._running:
            try:
                item = await asyncio.wait_for(self._question_queue.get(), timeout=1.0)
                question = item["question"]
                raw_user = item.get("user", "unknown")
                conversation_id = item.get("conversation_id")

                # Detect introductions: "I am Ziggy" → remember for this conversation
                introduced = self._detect_user_introduction(question)
                if introduced and conversation_id:
                    self._identified_users[conversation_id] = introduced
                    logger.info(f"User identified as {introduced} for conv:{conversation_id}")

                # Resolve user — prefer identified name over "unknown"
                user = self._identified_users.get(conversation_id, raw_user)
                if user == "unknown" and conversation_id in self._identified_users:
                    user = self._identified_users[conversation_id]

                logger.info(f"Question from {user} (conv:{conversation_id}): {question}")
                response = await self._process_question(question, user, conversation_id or "")
                logger.info(f"Response: {response[:100]}...")
                logger.info(f"Publishing answer for conv:{conversation_id}, text_len={len(response)}, starts_with={response[:30]!r}")

                # Publish answer — include conversation_id so HA component can match it
                if self._mqtt:
                    payload = {"text": response, "user": user, "response": response}
                    if conversation_id:
                        payload["conversation_id"] = conversation_id
                    self._mqtt.publish(ANSWER_TOPIC, json.dumps(payload))
                    logger.info(f"Published answer for conv:{conversation_id}")

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Processing error: {e}")

    async def stop(self):
        """Stop the agent and write session summary."""
        if self._stopped:
            return  # Guard against double-stop (two SIGTERMs)
        self._stopped = True
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

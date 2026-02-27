---
layout: page
title: "Chapter 3 — A Faster Brain"
permalink: /03-faster-brain/
nav_order: 3
---

# Chapter 3 — A Faster Brain

*"What would you do with a brain if you had one?"*

Naboo had a brain. But it was slow.

Not unusably slow — Ollama running Qwen 2.5 7B on a Mac mini M4 would answer in about 8–10 seconds. For a family robot answering questions from a six-year-old, that's an eternity. Ziggy doesn't wait 10 seconds. Ziggy asks another question, or wanders off, or starts doing something chaotic with Lev.

We needed faster.

---

## The Problem with Ollama

Ollama is brilliant for getting local models running quickly. But it's a generalised serving layer — it works on anything from a Raspberry Pi to a workstation. That generality comes at a cost on Apple Silicon.

The Mac mini M4 has a secret weapon: the **Neural Engine** and **unified memory**. Everything — CPU, GPU, Neural Engine — shares the same pool of fast memory. Apple's MLX framework is built from the ground up to exploit this. It doesn't abstract hardware; it leans into it.

| Model | Backend | Tokens/sec | Typical latency |
|-------|---------|-----------|----------------|
| Qwen 2.5 7B | Ollama | ~22 t/s | 8–10s |
| Qwen 2.5 7B | MLX | ~25 t/s | **3s** |
| Qwen 2.5 3B | Ollama | ~47 t/s | ~3s |
| Qwen 2.5 3B | MLX | ~55 t/s | ~1.5s |

The raw tokens/sec gap looks modest. But latency tells the real story. MLX delivers the **first token faster** and generates with less overhead. The 7B model goes from 10 seconds to 3 — a 3x improvement that makes the difference between Ziggy waiting and Ziggy asking again.

---

## mlx_lm.server

MLX ships with a built-in OpenAI-compatible API server: `mlx_lm.server`. One command, and you have a local inference endpoint that Strands agents can talk to exactly like it would talk to OpenAI.

```bash
mlx_lm.server \
  --model mlx-community/Qwen2.5-7B-Instruct-4bit \
  --host 0.0.0.0 \
  --port 11435
```

Naboo's model router already supported Bedrock and Ollama. Adding MLX was a small addition:

```python
elif config.provider == "mlx":
    from strands.models.openai import OpenAIModel
    return OpenAIModel(
        model_id=config.model_id,
        client_args={"base_url": f"{host}/v1", "api_key": "local"},
        params={"max_tokens": config.max_tokens},
    )
```

Set `MLX_HOST` in `.env`, and the router switches from Ollama to MLX automatically. No code changes needed.

---

## Persistent Service

We set `mlx_lm.server` up as a launchd service on the Mac mini so it survives reboots:

```xml
<!-- ~/Library/LaunchAgents/ai.naboo.mlx-server.plist -->
<key>Label</key>
<string>ai.naboo.mlx-server</string>
<key>RunAtLoad</key>
<true/>
<key>KeepAlive</key>
<true/>
```

Now the Mac mini boots, the MLX server starts, and Naboo's agent can connect whenever it's ready.

---

## What We Learned

**One model, one server.** We tried routing 3B and 7B queries to the same server, letting it swap models on-demand. Each swap took 15–20 seconds (loading weights from disk into unified memory). That's worse than just using 7B for everything. Now everything goes to 7B — consistently fast, no surprises.

**The test script was lying.** For weeks we thought we were testing the new agent. Turns out `test_e2e.py` had the old `.170` broker hardcoded. Every "passing test" was hitting the old Ollama agent on the Surface Pro 3. Lesson: always check your test target.

**Client ID conflicts break everything.** MQTT brokers enforce unique client IDs. Two agents with the same ID fight each other in an infinite reconnect loop — connect, get CONNACK, immediately get kicked, repeat. Add a UUID to your client ID. It costs nothing.

---

## End Result

Naboo now answers in 3 seconds for most questions. Weather pre-fetched from PirateWeather API, fixture results from a web search, simple facts from MLX 7B directly — all under 5 seconds. Fast enough that Ziggy doesn't lose interest.

The Scarecrow got a faster brain.

---

*Next: Chapter 4 — Eyes Open (camera, object detection, vision tools)*

---
layout: home
title: Naboo
---

> *"If I only had a brain…"*

Naboo is a family AI robot. They started life as a stock [mBot2](https://www.makeblock.com/pages/mbot2-steam-educational-robot-kit) — plastic wheels, sensors, a bit of pre-programmed wiggling. Then we gave them a brain.

This is the build log.

---

## The Story

- [Chapter 1 — Stock Robot](01-stock-robot/) — what we started with
- [Chapter 2 — Adding a Brain](02-adding-a-brain/) — Strands agents, dual-LLM routing, and why memory matters
- [Chapter 3 — A Faster Brain](03-faster-brain/) — MLX vs Ollama: 3x speedup on a Mac mini M4
- Chapter 4 — Eyes Open *(coming soon)*

---

## The Stack

| Layer | Technology |
|-------|-----------|
| Robot body | mBot2 (CyberPi / ESP32) |
| Agent framework | Strands Agents |
| Local LLM | MLX / Qwen 2.5 7B (Mac mini M4) |
| Cloud LLM | AWS Bedrock / Claude (fallback) |
| Voice in | Home Assistant wake word |
| Voice out | HA TTS — Ryan Cheerful |
| Vision | Camera + Claude Vision |
| Messaging | MQTT (Mosquitto) |

---

[View the code on GitHub](https://github.com/bagg3rs/naboo)

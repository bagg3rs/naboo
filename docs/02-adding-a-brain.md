---
layout: page
title: "Chapter 2 — Adding a Brain"
permalink: /02-adding-a-brain/
---

# Chapter 2 — Adding a Brain

*"If I only had a brain…"*

---

The Scarecrow already had a body. He could walk, gesture, frighten crows. What he couldn't do was think.

Naboo was in the same situation. The mBot2 had motors, sensors, LEDs, a speaker. It could follow lines, avoid obstacles, make sounds on command. But when Ziggy walked up and asked it a question, nothing happened.

Giving it a brain wasn't a single step. It was a series of decisions, each one building on the last.

---

## The Architecture

The brain is built on [Strands Agents](https://github.com/strands-agents/sdk-python) — an open-source agent framework from AWS that makes it straightforward to build LLM-powered agents with tools.

Naboo's agent sits on a small edge computer (Linux box, home network). It connects to the mBot2 hardware via MQTT, and to the outside world via AWS IoT Core.

```
┌─────────────────────────────────────────┐
│              Naboo Agent                │
│                                         │
│   ┌──────────┐    ┌──────────────────┐  │
│   │ System 1 │    │    System 2       │  │
│   │  (fast)  │    │   (reasoning)    │  │
│   │  Ollama  │    │  AWS Bedrock     │  │
│   │ ~1-2s    │    │  ~4-6s           │  │
│   └──────────┘    └──────────────────┘  │
│          │              │               │
│          └──────┬───────┘               │
│                 │ Router                │
│                 │                       │
│         ┌───────▼────────┐              │
│         │  Strands Agent │              │
│         │  + Tools       │              │
│         └───────┬────────┘              │
└─────────────────┼───────────────────────┘
                  │ MQTT
          ┌───────▼────────┐
          │  mBot2 / CyberPi│
          │  (the body)    │
          └────────────────┘
```

---

## The Two-Brain Problem

Kids expect fast responses. If Naboo takes 10 seconds to answer "what's my favourite football team?" the illusion breaks — it stops feeling like a real conversation and starts feeling like a loading screen.

But not everything can be answered quickly. Reasoning through a problem, looking something up, generating a creative story — these need more capable models that take longer.

The solution: **two models, routing between them**.

**System 1** (fast, local):
- Runs locally on the edge box via [Ollama](https://ollama.com/)
- Model: Qwen 2.5:3b (3 billion parameters, fits on modest hardware)
- Response time: ~1-2 seconds
- Handles: movement commands, simple questions, "what's my name?", greetings

**System 2** (smart, cloud):
- AWS Bedrock, Claude Haiku 4.5
- Response time: ~4-6 seconds  
- Handles: complex reasoning, stories, "why is the sky blue?", anything ambiguous

A lightweight classifier checks each incoming message and routes it. If it's confident the question is simple — local. If there's any doubt — cloud. Errors on the side of intelligence rather than speed.

```python
def classify_message(message: str) -> Literal["system1", "system2"]:
    """Route to fast local or smart cloud model."""
    # Simple pattern matching first (no LLM cost)
    if any(cmd in message.lower() for cmd in MOVEMENT_COMMANDS):
        return "system1"
    
    # Short, factual questions → local
    if len(message.split()) < 8 and "?" in message:
        return "system1"
    
    # Default to cloud for anything complex
    return "system2"
```

In practice this gets more sophisticated — but the principle holds. Most interactions with a 6-year-old are short and simple. Keep those fast.

---

## The Memory Problem

Once Naboo could answer questions, a new problem emerged.

Ziggy would ask: *"do you remember our conversation yesterday?"*

Naboo said no. Every time.

This is technically honest — without persistent memory, each session really does start fresh. But Ziggy didn't care about the technical explanation. He wanted a friend who remembered him.

The existing system had session logs — thousands of messages stored in JSON files. But nothing was reading them back. The conversations happened, got written to disk, and were never seen again.

The fix is a proper memory pipeline:

1. **At session end**: summarise what happened in 200-300 words, append to `memory/sessions/YYYY-MM-DD.md`
2. **At session start**: load the last 7 days of summaries + the family profile
3. **Inject into system prompt**: Naboo starts each session knowing what happened recently

```python
def load_memory_context(days_back: int = 7) -> str:
    """Load Naboo's memory context for system prompt injection."""
    sections = []
    
    # Long-term memory (curated)
    memory_file = MEMORY_DIR / "MEMORY.md"
    if memory_file.exists():
        sections.append(memory_file.read_text())
    
    # Family profiles (.local.md = private, gitignored)
    for name in ["ziggy", "lev"]:
        local = MEMORY_DIR / "family" / f"{name}.local.md"
        base = MEMORY_DIR / "family" / f"{name}.md"
        profile = local if local.exists() else base
        if profile.exists():
            sections.append(profile.read_text())
    
    # Recent sessions
    for i in range(days_back):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        session_file = MEMORY_DIR / "sessions" / f"{date}.md"
        if session_file.exists():
            sections.append(session_file.read_text())
    
    return "\n\n".join(sections)
```

The family profiles use a public/private split: the repo contains sanitised templates (first name, age, interests), while the machine running Naboo has `.local.md` files with the full detail — birthdays, school, grandparent names, current obsessions. Same pattern as `.env` files.

---

## The Conversation

The first time Ziggy asked "do you remember me?" after the memory system was wired up, Naboo said yes. She knew his name. She knew Arsenal were his team. She knew gold was his favourite colour.

It was a small thing technically. A few markdown files, some file reads at startup, a string appended to a system prompt.

Ziggy didn't know any of that. He just knew his robot remembered him.

That's the whole point.

---

*[← Chapter 1 — Stock Robot](../01-stock-robot/)* · *[Chapter 3 — Finding Her Voice →](../03-finding-her-voice/)*

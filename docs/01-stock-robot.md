---
layout: page
title: "Chapter 1 — Stock Robot"
permalink: /01-stock-robot/
---

# Chapter 1 — Stock Robot

*"If I only had a brain…"*

---

The mBot2 arrived in a box. Ziggy, aged 6, helped unpack it. It had wheels. It had sensors. It could follow a line on the floor, or stop when it detected an obstacle.

It did not have a brain.

Like the Scarecrow in Wizard of Oz, it could do things — it could move, it could sense, it could make sounds. But it couldn't *think*. It couldn't listen to a child ask a question and give a real answer. It couldn't navigate a room with purpose. It couldn't tell you the Arsenal score.

That felt like an opportunity.

---

## The Hardware

The mBot2 is a solid foundation:

- **CyberPi** — an ESP32-based microcontroller that handles sensors and motors
- **Ultrasonic sensors** — for obstacle detection
- **Encoder motors** — for precise movement
- **RGB LEDs** — for visual feedback
- **Speaker** — for audio output
- **Wi-Fi** — for connectivity

Out of the box, it runs MicroPython. You can program it in Python directly on the device, or via Bluetooth/USB. It's designed for education — so the API is clean, the docs are good, and it's genuinely hackable.

What it *doesn't* have: a camera, a microphone, internet connectivity in any meaningful sense, or any ability to understand natural language.

Those were the gaps. Those became the project.

---

## What Doesn't Change

One constraint from the start: **the kids use this thing**. Ziggy is 6. Lev is 2. Any system has to:

- Respond in under 3 seconds for simple commands
- Never say anything inappropriate
- Be honest when it doesn't know something
- Be fun, not just functional

That shaped every technical decision that followed.

---

*[Chapter 2 — Adding a Brain →](../02-adding-a-brain/)*

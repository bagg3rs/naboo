# Naboo Backlog

Ideas and future work. Not prioritised — just captured so they don't get lost.

---

## Architecture

### Deferred / lazy tool loading
**Source:** Discord #relevant-for-naboo, Mar 7 2026
**Context:** Currently all Strands tool definitions are loaded into context at agent init. This is fine at the current tool count (weather, fixtures, camera, speak) but will bloat the context window as more tools are added.
**Idea:** Only register tools that are relevant to the incoming query — e.g. use a lightweight classifier or keyword match to select a subset of tools before passing to the agent. Keeps the MLX model's attention focused and reduces prompt token overhead.
**Effort:** Low-medium — classify query → pick tool subset → pass to Strands agent.
**Worth doing when:** Tool count grows beyond ~6-8.

---

## Features / Ideas

*(add more here)*

# Why `run_agent` is an Agent

`run_agent` in [backend/app/agent/runner.py](../backend/app/agent/runner.py) is more than a pipeline or a single API call. Several properties together justify calling it an agent.

## 1. Perception → Reasoning → Action cycle

The review loop is the core signal. The system *perceives* user feedback (`await websocket.receive_json()`), *reasons* about it (passes corrections to Gemini in `apply_corrections`), then *acts* by revising the shopping list and re-presenting it. This repeats until a terminal condition is met. That sense-reason-act cycle is the defining structure of an agent.

## 2. Goal-directed multi-step planning with state

A simple API call produces one output. This function works toward a goal — a finalized shopping list — across multiple distinct steps:

```
categorize dishes → get ingredients → aggregate → review → deliver
```

It maintains `AgentState` across those steps, accumulating intermediate work (`serving_specs`, `dish_ingredients`, `shopping_list`) rather than computing everything in one shot. This is what distinguishes an agent from a pipeline.

## 3. Tool use

The agent doesn't call one model — it routes between different "tools" depending on context. In `get_all_dish_ingredients`, it decides which tool to invoke per dish:

- **Pure Python scaling** — for dishes with a known recipe (deterministic, no AI call)
- **Gemini call** — for beverages or dishes without a base recipe
- **Hardcoded stub** — for store-bought items

That per-dish routing logic — choosing the right tool for the job — is an agent behavior.

## 4. Parallel task execution

At delivery time, the agent schedules multiple independent tasks concurrently (`asyncio.gather`). A pure pipeline runs steps serially; an agent can reason about which actions are independent and run them in parallel.

## 5. Error handling and resilience

`gather(..., return_exceptions=True)` at delivery means individual steps can fail without crashing the whole run. The agent logs errors and continues, degrading gracefully. This reflects the agent property of *robustness under partial failure* — it keeps making progress toward its goal even when one tool fails.

## 6. Human-in-the-loop as a first-class interrupt

The review step isn't just "ask the user a question" — it's a *suspension point* where the agent yields control entirely, waits for external input, and then decides whether to continue or loop. This is the same model LangGraph's `interrupt()` primitive is built around, and it's a fundamental agent pattern. (The code even notes the migration path: `# LangGraph migration: replace this block with interrupt()`.)

## Summary

| Concept | Simple function | Pipeline | Agent |
|---|---|---|---|
| Output | Single return value | Sequential results | Goal achieved through iteration |
| State | None | Implicit | Explicit, accumulated |
| Control flow | Linear | Linear | Conditional, looping |
| Tools | One | Fixed sequence | Dynamically chosen |
| Human input | None | None | First-class interrupt |

A function is a call-and-return. A pipeline is sequential steps. An agent is a system that *reasons about what to do next* based on state and external input, uses multiple tools, and iterates toward a goal it didn't fully know how to reach at the start. `run_agent` does all of that.

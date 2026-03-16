# Repo Review: Chat Architecture, State Management & AI Prompts

Overview of potential improvements across three areas: chat architecture (WebSocket + streaming from Gemini), conversation state management, and AI prompts. Use this to decide what changes to make later.

---

## 1. Chat Architecture (WebSocket + Streaming from Gemini)

### What's Working Well

- **Single WebSocket endpoint** with a clear message contract (`stream_start` → `stream_chunk` → `stream_end`, plus agent and card types).
- **Session reload from DB on each turn** so you always work with the latest persisted state (with `agent_state` and `google_credentials` kept in memory).
- **Streaming is real**: `generate_response_stream()` yields Gemini chunks and the backend forwards them; the frontend appends to the last assistant message.
- **REST fallback** (`POST /api/chat`) for environments where WebSockets aren't viable.

### Improvements to Consider

- **No reconnection:** If the WebSocket drops (network blip, deploy, idle timeout), the client only has `onclose`; there's no auto-reconnect or "reconnecting…" state. Adding exponential-backoff reconnect and re-sending the last user message (or a "resume" token) would improve robustness.
- **Error handling and loading:** On `stream_start` you set `isLoading = true` implicitly (by starting a stream), but if the backend never sends `stream_end` (e.g. crash or timeout), the UI can stay in a loading state. Consider timeouts and/or a "stream failed" message type so the client can clear loading and show an error.
- **Single in-flight message:** The protocol assumes one request at a time. If the user sends a second message before `stream_end`, the backend still processes in order, but the frontend could get into a confusing state (e.g. two streams interleaved). Either document "one message at a time" and disable the send button while streaming, or add a `request_id` (or similar) so chunks are tied to the right message.
- **Large `event_data` on every `stream_start`:** You send the full `event_data` in `stream_start` and again in `event_data_update`. For large meal plans this is a lot of JSON. You could send a smaller "delta" or only changed fields if you hit performance issues.

---

## 2. Conversation State Management

### What's Working Well

- **Single source of truth:** `SessionData` (and DB) holds `event_data` + `conversation_history`; `apply_extraction()` is the single place that applies extraction results and drives stage transitions. That keeps state predictable.
- **Stage machine is clear:** `gathering` → `recipe_confirmation` → `selecting_output` → `agent_running` (and intercepts like "show menu confirm card" when moving to `recipe_confirmation`) are implemented in one place.
- **Transient fields:** `last_recipe_received`, `last_url_extraction_result`, `last_generated_recipes` are cleared at the start of `apply_extraction()`, so they're turn-scoped and don't leak.
- **Agent state separation:** `AgentState` is separate from `EventPlanningData`; the runner is pure steps + WebSocket I/O, which will make a LangGraph migration easier.

### Improvements to Consider

- **Stage transitions in two places:** Transitions happen both inside `apply_extraction()` (e.g. `is_complete` → `recipe_confirmation`) and in the WebSocket handler (e.g. after `confirm_menu`, after `_auto_generate_recipes` when `meal_plan.is_complete`). That's a bit harder to reason about. You could move all transition logic into `apply_extraction()` (or a small state machine module) and have the handler only call "apply extraction" or "apply user action" and then react to the resulting stage.
- **Intercept logic in the handler:** The "intercept gathering→recipe_confirmation and show menu_confirm_request" block (with `menu_confirm_shown_for_names`, etc.) is long and easy to break. Extracting it into a function like `maybe_show_menu_confirm(session) -> Optional[MenuConfirmPayload]` would make the handler easier to read and test.
- **DB session reload and in-memory agent state:** You reload session from DB each turn and reattach `agent_state` and `google_credentials` from the in-memory `session`. That's correct for not losing them, but if you scale to multiple workers, agent state isn't in the DB, so a different worker wouldn't have it. Not a problem for a single process; for multi-worker you'd eventually need to persist or re-run the agent.
- **REST vs WebSocket state path:** REST chat does extraction + `generate_response` and then saves; WebSocket does extraction + streaming + several possible side effects (cards, agent run). The two paths can drift. Keeping "apply extraction + decide what to do next" in a shared helper (used by both REST and WebSocket) would reduce divergence.

---

## 3. AI Prompts (Chat System Prompt, Extraction, Recipe Extraction)

### What's Working Well

- **Stage-aware system prompt:** The main chat prompt is structured by `conversation_stage` with clear "IF stage == X" sections, recipe-priority rules, and "RECIPE RECEIVED" / "RECIPE URL EXTRACTION RESULT" blocks. That gives the model clear rules per phase.
- **Structured extraction:** You use Gemini JSON mode with `ExtractionResult` and stage-specific extraction rules, which is the right replacement for the old regex approach (as in NOTES.md).
- **Concrete examples:** Extraction prompt includes many examples (e.g. beverage add, confirm suggested menu, reject-and-replace, multiple placeholders), which helps consistency.
- **Explicit "do not" rules:** You call out things like not outputting thinking, not re-opening the menu in recipe_confirmation, and not generating the shopping list in chat.

### Improvements to Consider

- **Length and structure:** The chat system prompt is long (~250+ lines). Breaking it into smaller sections (e.g. "gathering", "recipe_confirmation", "selecting_output") and loading them by stage would improve readability and make it easier to tune per stage. Same idea for the extraction prompt: it's one large f-string; splitting by stage or moving to a small template module would help.
- **Duplication between chat and extraction:** Some rules appear in both (e.g. "beverages as separate recipes", "no theme labels"). A single "authoritative" snippet or doc that both prompts reference (e.g. "Beverage rules: …") would reduce drift and mistakes.
- **Temperature:** Chat uses `temperature=1.2`, which is high for a structured, stateful flow. It can help creativity but may cause more off-script replies (e.g. skipping steps or mentioning things you told it not to). Trying 0.7–0.9 for chat (and keeping lower for extraction) might make behavior more consistent.
- **Extraction "Current known data":** The extractor gets `current_event_data` as JSON; if that blob gets very large, context can be dominated by it. Trimming to "summary for this stage" (e.g. meal plan names + key flags, not full ingredient lists) in the extraction call could help accuracy and cost.
- **Error recovery in prompts:** You tell the model what to do on URL extraction failure, but you could add one short "If the user seems confused or asks to start over, acknowledge and ask what they'd like to change" so recovery from confusion is explicit.

---

## Summary Table

| Area | Strength | Main improvement |
|------|----------|-------------------|
| **WebSocket / streaming** | Clear protocol, real streaming, DB reload each turn | Reconnection, timeout/error handling, and/or request IDs for multiple in-flight messages |
| **State management** | Single `apply_extraction()`, clear stages, transient fields cleared | Centralize all stage transitions; extract "menu confirm" intercept into a helper; align REST and WS paths |
| **Prompts** | Stage-aware, structured extraction, good examples | Shorter, modular prompts; shared rules for chat + extraction; lower chat temperature; trim extraction context |

---

*Written from a codebase review focused on chat architecture, conversation state, and AI prompts. Revisit this file when deciding what to prioritize.*

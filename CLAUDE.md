# Dinner Party Planner

## Compact instructions

When compacting, preserve: current task context, file paths being worked on, any errors/blockers. Drop: architectural explanations already in this file, completed work summaries.

## Tech Stack

- **Backend:** Python, FastAPI, Pydantic v2, `google-genai` SDK (NOT `google-generativeai`)
- **Frontend:** React 18 + TypeScript + Vite + Tailwind
- **AI:** `gemini-3-flash-preview` (main), `gemini-2.5-flash-lite` (fast). JSON mode for extraction, streaming for chat
- **Package management:** `uv` with `pyproject.toml`

## Dev Commands

- **Backend:** `cd backend && uv run uvicorn app.main:app --reload`
- **Frontend:** `cd frontend && npm run dev`
- **Tests:** `cd backend && uv run pytest` (add `-m smoke` for live Gemini API calls)
- **Lint:** `cd backend && uv run ruff check .`

## Conventions

- Absolute imports: `from app.models.event import ...` (NOT relative)
- Prompts: indent multi-line f-string content to match method body level
- Enums: prefer string enums (e.g. `QuantityUnit`)
- Backend port: 8000. Vite proxies `/api` and `/ws` to backend.

## Conversation Flow

`gathering` → `recipe_confirmation` → `selecting_output` → `agent_running`

- **Gathering**: two Gemini calls/turn (extraction JSON + streaming chat). Delta-based meal plan. `is_complete` requires all critical questions + confirmed meal plan.
- **Recipe confirmation**: per-dish recipe source tracking (`RecipeSourceType`: ai_default, user_url, user_upload, user_description). Endpoints: `POST /api/sessions/{id}/extract-recipe`, `POST /api/sessions/{id}/upload-recipe`.
- **Selecting output**: Google Tasks (OAuth, implemented) / Google Sheet (stub) / in-chat list. OAuth endpoints: `POST /api/auth/google/start`, `GET /api/auth/google/callback`.
- **Agent running**: `run_agent()` in `runner.py`. Steps: calculate_quantities → get_dish_ingredients (parallel) → aggregate → review loop → deliver.

Stage transitions + meal plan merging happen in `apply_extraction()` in `main.py`.

## Key Files

- `models/event.py` — EventPlanningData, ExtractionResult, RecipeSourceType, completion scoring
- `models/shopping.py` — GroceryCategory, DishCategory, QuantityUnit, RecipeIngredient, DishServingSpec, ShoppingList
- `services/ai_service.py` — GeminiService (chat, extraction, recipe extraction, agent Gemini calls)
- `services/quantity_engine.py` — per-person serving multiplier lookup by DishCategory
- `services/tasks_service.py` — TasksService (Google Tasks OAuth-backed list creation)
- `services/session_manager.py` — in-memory SessionData (stores event_data, history, agent_state, google_credentials)
- `agent/state.py` — AgentState, `agent/steps.py` — pure step functions, `agent/runner.py` — WS orchestrator

## Known Issues

- Sessions: in-memory only, no persistence
- `generate_response()` (non-streaming) exists for REST fallback only
- Google Sheet creation is a stub; Google Tasks is fully implemented with OAuth

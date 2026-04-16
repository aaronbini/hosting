# Food Event Planning Assistant - Architecture

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, Pydantic v2 |
| AI | Google Gemini via `google-genai` SDK |
| Database | PostgreSQL (SQLAlchemy async + asyncpg) |
| Auth | Google OAuth 2.0 + JWT session cookies |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Package mgmt | `uv` with `pyproject.toml` |

---

## Authentication

Users sign in via Google OAuth 2.0 (`/api/auth/login` → Google → `/api/auth/callback`). On successful callback the server:
1. Upserts a `User` row (keyed by `google_id`)
2. Issues a signed JWT as an HTTP-only cookie

All session and plan endpoints require this cookie. The JWT is validated in `auth/jwt.py`; the Google OAuth flow lives in `auth/google_login.py`.

---

## Database

PostgreSQL accessed via SQLAlchemy async (`db/database.py`). Three tables (`db/models.py`):

- **`users`** — `id` (UUID), `google_id`, `email`, `name`, `picture`
- **`app_sessions`** — `id`, `user_id` (FK), `event_data` (JSONB), `conversation_history` (JSONB), `stage`, `google_task_credentials` (JSONB), `last_updated`
- **`saved_plans`** — `id`, `user_id` (FK), `session_id` (FK nullable), `name`, `event_data` (JSONB), `shopping_list` (JSONB), `formatted_output`, `formatted_recipes_output`

`DbSessionManager` (`services/db_session_manager.py`) wraps all DB access. It marshals between the ORM rows and the in-memory `SessionData` runtime type so that existing agent step functions work unchanged. `save_session()` is called at each conversation turn to persist state.

---

## Conversation Flow

```
gathering → recipe_confirmation → selecting_output → agent_running
```

Stage transitions and meal plan merging are handled by `apply_extraction()` in `main.py`.

### Gathering
Two Gemini calls per turn:
1. **Extraction** (JSON mode) — pulls structured event data from the user's message
2. **Chat** (streaming) — generates the assistant's reply

The meal plan is tracked as a delta-based structure. `is_complete` requires all critical questions answered + a confirmed meal plan.

### Recipe Confirmation
Each dish has a `RecipeSourceType`: `ai_default`, `user_url`, `user_upload`, or `user_description`. Users can provide recipes via URL (`/api/sessions/{id}/extract-recipe`) or file upload (`/api/sessions/{id}/upload-recipe`).

### Selecting Output
The user chooses delivery method:
- **Google Tasks** — fully implemented with OAuth (`services/tasks_service.py`)
- **Google Sheets** — stub only
- **In-chat list** — always available

### Agent Running
`run_agent()` in `agent/runner.py` orchestrates the pipeline over WebSocket:

1. `calculate_quantities` — applies per-person serving multipliers (`services/quantity_engine.py`)
2. `get_dish_ingredients` — parallel Gemini calls per dish
3. `aggregate` — merges ingredients across dishes into a `ShoppingList`
4. **Review loop** — sends shopping list to user, repeats until approved
5. `deliver` — writes to Google Tasks (or returns in-chat list)

All step functions in `agent/steps.py` are pure (no side effects) to simplify testing.

---

## Key Services

| File | Responsibility |
|------|---------------|
| `services/ai_service.py` | `GeminiService` — chat, extraction, recipe extraction, agent calls |
| `services/quantity_engine.py` | Per-person serving multiplier lookup by `DishCategory` |
| `services/db_session_manager.py` | PostgreSQL-backed session CRUD |
| `services/session_manager.py` | `SessionData` in-memory runtime container |
| `services/tasks_service.py` | Google Tasks list creation via OAuth |
| `services/sheets_service.py` | Google Sheets stub |
| `services/plan_manager.py` | Saved plan CRUD |

---

## Data Models

- `models/event.py` — `EventPlanningData`, `ExtractionResult`, `RecipeSourceType`, completion scoring
- `models/shopping.py` — `GroceryCategory`, `DishCategory`, `QuantityUnit`, `RecipeIngredient`, `DishServingSpec`, `ShoppingList`
- `models/chat.py` — `ChatMessage`, `MessageRole`

---

## Future / Not Yet Implemented

- **Google Sheets output** — fully implemented (`sheets_service.py`): creates a formula-driven spreadsheet with Party Overview + Shopping List tabs, quantity formulas that scale with guest count edits, and per-item checkboxes
- **Async recipe upload processing** — see `ASYNC_UPLOAD_ARCHITECTURE.md`
- **RAG for recipe quality** — see `RAG_DESIGN.md`
- **Performance optimization** — batching recipe generation calls; see `PERFORMANCE_OPTIMIZATION.md`

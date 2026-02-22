# Testing Strategy

## Two-Track Approach

Testing this app is hard for two reasons:

1. **User input is unpredictable** — natural language conversations have nearly infinite variation
2. **AI model outputs are variable** — Gemini responses differ across runs

The answer is a two-track strategy:

- **Track A — `pytest`**: Test deterministic logic with no AI, no I/O. Fast, cheap, runs in CI.
- **Track B — Eval harness**: Test AI output quality. Runs against the live Gemini API. Not CI.

---

## Track A: pytest Test Suite

### Running Tests

```bash
cd backend
uv run pytest                    # all tests
uv run pytest tests/             # same
uv run pytest -v                 # verbose
uv run pytest -k test_apply      # filter by name
uv run pytest -m smoke           # smoke tests only (requires GOOGLE_API_KEY)
```

### Test Files

| File | What it tests |
|------|--------------|
| `tests/test_quantity_engine.py` | Serving size math per DishCategory |
| `tests/test_models.py` | Recipe, MealPlan, EventPlanningData logic |
| `tests/test_apply_extraction.py` | Stage transitions, recipe updates (highest ROI) |
| `tests/test_agent_steps.py` | Pure agent step functions |

### What Is NOT Tested by pytest

- Exact AI response wording
- Menu suggestion quality or creativity
- Whether Gemini correctly parses a prompt

These belong in the eval harness (Track B).

---

## Track B: Eval Harness

Located in `backend/evals/`. Requires `GOOGLE_API_KEY`.

```bash
cd backend
uv run python -m evals.run_evals
```

### What It Evaluates

| Task | Method | Metric |
|------|--------|--------|
| `extract_event_data` accuracy | Field-level comparison | Pass/fail per field |
| Ingredient generation quality | LLM-as-judge | Score 1–5 |
| Shopping list deduplication | Deterministic | No duplicate names |
| Conversation coherence | LLM-as-judge | Score 1–5 |

### When to Run

- Before releasing a new version
- After changing a system prompt
- After switching models

### Updating the Golden Dataset

Edit files in `evals/datasets/`. Add new cases when you observe a failure mode in production
that isn't covered. Keep examples focused — 20–30 cases beats 200 mediocre ones.

---

## Adding New Tests

When you fix a bug, add a test that would have caught it. The `test_apply_extraction.py` file
is the most valuable — if a stage transition breaks, a test here will catch it next time.

"""
Agent step functions.

Rules:
- Every function takes an AgentState and returns a (possibly modified) AgentState.
- No WebSocket calls, no HTTP calls, no direct user I/O of any kind.
- All external calls go through the ai_service or quantity_engine passed as args.
- This keeps the functions pure and trivially testable.

LangGraph migration: each function here becomes a node. The signature
    step(state: AgentState) -> AgentState
maps directly to a LangGraph node that receives/returns state updates.
"""

import asyncio
import logging
import math
from typing import TYPE_CHECKING

from app.agent.state import AgentStage, AgentState
from app.models.shopping import DishCategory
from app.services.quantity_engine import calculate_all_serving_specs

# Items that are always assumed available and should never appear on a shopping list.
NEVER_PURCHASE = frozenset(
    {
        "water",
        "tap water",
        "cold water",
        "hot water",
        "boiling water",
        "ice water",
    }
)

if TYPE_CHECKING:
    from app.services.ai_service import GeminiService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step 1 — Calculate serving specs
# ---------------------------------------------------------------------------


async def calculate_quantities(
    state: AgentState,
    ai_service: "GeminiService",
) -> AgentState:
    """
    Use Gemini to categorise each dish, then apply the lookup table to
    produce a DishServingSpec (serving counts) for each dish.

    Gemini call: categorise_dishes() — maps dish names to DishCategory.
    Lookup table: calculate_all_serving_specs() — applies per-person multipliers.
    """
    state.stage = AgentStage.CALCULATING_QUANTITIES
    logger.info(
        "[STEP 1] Starting calculate_quantities: %d dishes, %d adults, %d children",
        len(state.event_data.meal_plan),
        state.event_data.adult_count or 0,
        state.event_data.child_count or 0,
    )

    meal_plan = state.event_data.meal_plan
    adult_count = state.event_data.adult_count or 0
    child_count = state.event_data.child_count or 0

    # Ask Gemini to categorise each dish (one call, returns dict[str, DishCategory])
    dish_categories: dict[str, DishCategory] = await ai_service.categorise_dishes(meal_plan)

    state.serving_specs = calculate_all_serving_specs(
        meal_plan=meal_plan,
        dish_categories=dish_categories,
        adult_count=adult_count,
        child_count=child_count,
    )

    logger.info("calculate_quantities: %d specs produced", len(state.serving_specs))
    return state


# ---------------------------------------------------------------------------
# Step 2 — Get ingredients per dish (parallel)
# ---------------------------------------------------------------------------


async def get_all_dish_ingredients(
    state: AgentState,
    ai_service: "GeminiService",
) -> AgentState:
    """
    For each DishServingSpec, call Gemini to get a scaled ingredient list.
    All per-dish calls run concurrently.
    """
    state.stage = AgentStage.GETTING_INGREDIENTS
    logger.info(
        "[STEP 2] Starting get_all_dish_ingredients: fetching ingredients for %d dishes concurrently",
        len(state.serving_specs),
    )

    # Build a lookup of recipe sources by dish name (for user-provided recipes)
    recipe_source_map = {rs.dish_name.lower(): rs for rs in state.event_data.recipe_sources}

    tasks = [
        ai_service.get_dish_ingredients(
            spec,
            recipe_source=recipe_source_map.get(spec.dish_name.lower()),
        )
        for spec in state.serving_specs
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    dish_ingredients = []
    for spec, result in zip(state.serving_specs, results):
        if isinstance(result, Exception):
            logger.error("get_dish_ingredients failed for '%s': %s", spec.dish_name, result)
            # Skip the dish rather than aborting the whole agent run
            continue
        dish_ingredients.append(result)

    state.dish_ingredients = dish_ingredients
    logger.info("get_all_dish_ingredients: %d dishes processed", len(dish_ingredients))
    return state


# ---------------------------------------------------------------------------
# Step 3 — Aggregate ingredients across all dishes
# ---------------------------------------------------------------------------


async def aggregate_ingredients(
    state: AgentState,
    ai_service: "GeminiService",
) -> AgentState:
    """
    Pass all DishIngredients to Gemini for fuzzy deduplication and summing.
    Returns a single ShoppingList grouped by GroceryCategory.
    """
    state.stage = AgentStage.AGGREGATING
    logger.info(
        "[STEP 3] Starting aggregate_ingredients: deduplicating and summing %d dish ingredient sets",
        len(state.dish_ingredients),
    )

    shopping_list = await ai_service.aggregate_ingredients(state.dish_ingredients)

    # Remove items that are always available (water, etc.)
    before = len(shopping_list.items)
    shopping_list.items = [
        item for item in shopping_list.items if item.name.lower().strip() not in NEVER_PURCHASE
    ]
    removed = before - len(shopping_list.items)
    if removed:
        logger.info("aggregate_ingredients: filtered out %d never-purchase items", removed)

    shopping_list.build_grouped()

    state.shopping_list = shopping_list
    logger.info("aggregate_ingredients: %d unique items in shopping list", len(shopping_list.items))
    return state


# ---------------------------------------------------------------------------
# Step 4 — Apply user corrections
# ---------------------------------------------------------------------------


async def apply_corrections(
    state: AgentState,
    ai_service: "GeminiService",
) -> AgentState:
    """
    If the user provided corrections at the review checkpoint, pass the
    current shopping list + corrections to Gemini and return a revised list.

    If user_corrections is None or empty, this is a no-op.
    """
    state.stage = AgentStage.APPLYING_CORRECTIONS
    logger.info("[STEP 4] Starting apply_corrections")

    if not state.user_corrections or not state.user_corrections.strip():
        logger.info("apply_corrections: no corrections, skipping")
        return state

    revised = await ai_service.apply_shopping_list_corrections(
        shopping_list=state.shopping_list,
        corrections=state.user_corrections,
    )
    revised.build_grouped()
    state.shopping_list = revised
    logger.info("apply_corrections: shopping list revised")
    return state


# ---------------------------------------------------------------------------
# Step 5a — Create Google Sheet (stub)
# ---------------------------------------------------------------------------


async def create_google_sheet(state: AgentState) -> AgentState:
    """
    Stub: create a Google Sheet from the shopping list and return its URL.
    Pending Google Sheets API auth setup.
    """
    # TODO: implement once Google API credentials are configured
    logger.info("[STEP 5a] Starting create_google_sheet")
    state.google_sheet_url = None
    logger.info("[STEP 5a] create_google_sheet: stub — not yet implemented")
    return state


# ---------------------------------------------------------------------------
# Step 5b — Create Google Tasks list
# ---------------------------------------------------------------------------


async def create_google_tasks(state: AgentState, tasks_service=None) -> AgentState:
    """
    Create a Google Tasks checklist from the shopping list and return its URL.
    Requires a TasksService instance (built from session OAuth credentials).
    If tasks_service is None (no credentials yet), skips gracefully.
    """
    logger.info("[STEP 5b] Starting create_google_tasks")
    if not tasks_service:
        logger.warning("[STEP 5b] No TasksService provided — skipping (no Google credentials)")
        state.google_tasks_url = None
        return state

    title = f"Dinner Party Shopping - {state.event_data.event_date or 'Today'}"
    list_id = await tasks_service.create_shopping_list(state.shopping_list, title)
    state.google_tasks_url = f"https://tasks.google.com/tasks/lists/{list_id}"
    logger.info("[STEP 5b] create_google_tasks: task list created — %s", state.google_tasks_url)
    return state


# ---------------------------------------------------------------------------
# Step 5c — Format shopping list for in-chat display
# ---------------------------------------------------------------------------


async def format_chat_output(state: AgentState) -> AgentState:
    """
    Format the shopping list as a readable markdown string for display in chat.
    No Gemini call — pure formatting logic.
    """
    state.stage = AgentStage.DELIVERING
    logger.info("[STEP 5c] Starting format_chat_output")

    if not state.shopping_list:
        logger.warning("[STEP 5c] No shopping list available")
        state.formatted_chat_output = "No shopping list available."
        return state

    lines: list[str] = ["## Shopping List\n"]

    for category, items in state.shopping_list.grouped.items():
        lines.append(f"**{category.replace('_', ' ').title()}**")
        for item in items:
            # Always round up to the nearest whole number for shopping clarity
            qty = math.ceil(item.total_quantity)
            lines.append(f"- {item.name}: {qty} {item.unit.value}")
        lines.append("")

    state.formatted_chat_output = "\n".join(lines)
    return state

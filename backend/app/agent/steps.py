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
from typing import TYPE_CHECKING

from app.agent.state import AgentState, AgentStage
from app.models.shopping import DishCategory
from app.services.quantity_engine import calculate_all_serving_specs

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

    tasks = [
        ai_service.get_dish_ingredients(spec)
        for spec in state.serving_specs
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    dish_ingredients = []
    for spec, result in zip(state.serving_specs, results):
        if isinstance(result, Exception):
            logger.error(
                "get_dish_ingredients failed for '%s': %s",
                spec.dish_name, result
            )
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

    shopping_list = await ai_service.aggregate_ingredients(state.dish_ingredients)
    shopping_list.build_grouped()

    state.shopping_list = shopping_list
    logger.info(
        "aggregate_ingredients: %d unique items in shopping list",
        len(shopping_list.items)
    )
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
    state.google_sheet_url = None
    logger.info("create_google_sheet: stub — not yet implemented")
    return state


# ---------------------------------------------------------------------------
# Step 5b — Create Google Keep list (stub)
# ---------------------------------------------------------------------------

async def create_google_keep(state: AgentState) -> AgentState:
    """
    Stub: create a Google Keep checklist from the shopping list and return its URL.
    Pending Google Keep API auth setup.
    """
    # TODO: implement once Google API credentials are configured
    state.google_keep_url = None
    logger.info("create_google_keep: stub — not yet implemented")
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

    if not state.shopping_list:
        state.formatted_chat_output = "No shopping list available."
        return state

    lines: list[str] = ["## Shopping List\n"]

    for category, items in state.shopping_list.grouped.items():
        lines.append(f"**{category.replace('_', ' ').title()}**")
        for item in items:
            qty = item.total_quantity
            # Format quantity: show as int if whole number, else 1 decimal
            qty_str = str(int(qty)) if qty == int(qty) else f"{qty:.1f}"
            lines.append(f"- {item.name}: {qty_str} {item.unit.value}")
        lines.append("")

    state.formatted_chat_output = "\n".join(lines)
    return state

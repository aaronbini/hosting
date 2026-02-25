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
import datetime
import logging
import math
from typing import TYPE_CHECKING

from app.agent.state import AgentStage, AgentState, GoogleTasksResult
from app.models.event import PreparationMethod
from app.models.shopping import DishCategory, DishIngredients, DishServingSpec, RecipeIngredient, QuantityUnit
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
        len(state.event_data.meal_plan.recipes),
        state.event_data.adult_count or 0,
        state.event_data.child_count or 0,
    )

    # Extract dish names from MealPlan
    dish_names = [recipe.name for recipe in state.event_data.meal_plan.recipes]
    adult_count = state.event_data.adult_count or 0
    child_count = state.event_data.child_count or 0

    # Ask Gemini to categorise each dish (one call, returns dict[str, DishCategory])
    dish_categories: dict[str, DishCategory] = await ai_service.categorise_dishes(dish_names)

    state.serving_specs = calculate_all_serving_specs(
        meal_plan=dish_names,
        dish_categories=dish_categories,
        adult_count=adult_count,
        child_count=child_count,
    )

    logger.info("calculate_quantities: %d specs produced", len(state.serving_specs))
    return state


# ---------------------------------------------------------------------------
# Step 2 — Get ingredients per dish (parallel)
# ---------------------------------------------------------------------------


def _scale_recipe_in_python(recipe, spec: DishServingSpec) -> DishIngredients:
    """
    Scale a recipe's ingredient quantities using pure Python arithmetic.

    LLMs cannot reliably perform strict multiplication — they tend to substitute
    their own knowledge of "appropriate" quantities for a dish of a given size,
    bypassing the scale factor entirely. Pure Python guarantees the base recipe
    proportions are preserved exactly.

    scale_factor = total_servings / base_servings
    """
    base_servings = recipe.servings or 4
    factor = spec.total_servings / base_servings if base_servings > 0 else 1.0
    scaled_ingredients = []
    for ing in recipe.ingredients:
        scaled = dict(ing)
        scaled["quantity"] = round(ing["quantity"] * factor, 2)
        scaled_ingredients.append(RecipeIngredient(**scaled))
    return DishIngredients(
        dish_name=spec.dish_name,
        serving_spec=spec,
        ingredients=scaled_ingredients,
    )


async def get_all_dish_ingredients(
    state: AgentState,
    ai_service: "GeminiService",
) -> AgentState:
    """
    Produce a scaled ingredient list for every dish in the serving specs.

    Routing logic per dish:
    - Store-bought: single COUNT entry, no AI call.
    - Has a base recipe + is not a beverage: pure Python scaling — no Gemini
      call. This prevents the LLM from substituting its own quantity judgement
      and ignoring the scale factor, which caused systematic over-purchasing.
    - Beverage or no base recipe (rare fallback): Gemini call.
    """
    state.stage = AgentStage.GETTING_INGREDIENTS
    logger.info(
        "[STEP 2] Starting get_all_dish_ingredients: %d dishes",
        len(state.serving_specs),
    )

    BEVERAGE_CATEGORIES = {DishCategory.BEVERAGE_ALCOHOLIC, DishCategory.BEVERAGE_NONALCOHOLIC}

    recipe_map = {r.name.lower(): r for r in state.event_data.meal_plan.recipes}

    def _recipe_for(dish_name: str):
        r = recipe_map.get(dish_name.lower())
        return r if (r and r.ingredients) else None

    def _is_store_bought(dish_name: str) -> bool:
        r = recipe_map.get(dish_name.lower())
        return r is not None and r.preparation_method == PreparationMethod.STORE_BOUGHT

    dish_ingredients: list[DishIngredients] = []
    specs_for_gemini = []

    for spec in state.serving_specs:
        if _is_store_bought(spec.dish_name):
            logger.info("'%s' is store-bought — skipping AI call", spec.dish_name)
            dish_ingredients.append(
                DishIngredients(
                    dish_name=spec.dish_name,
                    serving_spec=spec,
                    ingredients=[
                        RecipeIngredient(
                            name=spec.dish_name,
                            quantity=1,
                            unit=QuantityUnit.COUNT,
                            grocery_category="other",
                        )
                    ],
                )
            )
        elif spec.dish_category not in BEVERAGE_CATEGORIES and _recipe_for(spec.dish_name):
            recipe = _recipe_for(spec.dish_name)
            base = recipe.servings or 4
            scaled = _scale_recipe_in_python(recipe, spec)
            logger.info(
                "'%s': Python-scaled %d ingredients by %.2fx (%s→%s servings)",
                spec.dish_name, len(scaled.ingredients),
                spec.total_servings / base, base, spec.total_servings,
            )
            dish_ingredients.append(scaled)
        else:
            # Beverage or no base recipe — use Gemini.
            specs_for_gemini.append(spec)

    if specs_for_gemini:
        tasks = [
            ai_service.get_dish_ingredients(
                spec,
                recipe=_recipe_for(spec.dish_name),
                dietary_restrictions=state.event_data.dietary_restrictions,
            )
            for spec in specs_for_gemini
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for spec, result in zip(specs_for_gemini, results):
            if isinstance(result, Exception):
                logger.error("get_dish_ingredients failed for '%s': %s", spec.dish_name, result)
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

    raw_date = state.event_data.event_date or datetime.date.today().isoformat()
    try:
        event_date = datetime.date.fromisoformat(raw_date).strftime("%m-%d-%Y")
    except ValueError:
        event_date = raw_date
    title = f"Dinner Party Shopping - {event_date}"
    await tasks_service.create_shopping_list(state.shopping_list, title)
    state.google_tasks = GoogleTasksResult(url="https://tasks.google.com", list_title=title)
    logger.info("[STEP 5b] create_google_tasks: task list %r created", title)
    return state


# ---------------------------------------------------------------------------
# Step 6 — Generate full recipes for homemade dishes
# ---------------------------------------------------------------------------

_BEVERAGE_CATEGORIES = frozenset(
    {DishCategory.BEVERAGE_ALCOHOLIC, DishCategory.BEVERAGE_NONALCOHOLIC}
)


async def generate_recipes(
    state: AgentState,
    ai_service: "GeminiService",
) -> AgentState:
    """
    Generate step-by-step cooking instructions for all homemade, non-beverage
    dishes. Uses the scaled ingredient lists from state.dish_ingredients so the
    instructions reference exact quantities already computed for the guest count.

    Formats results as a '## Recipes' markdown section stored in
    state.formatted_recipes_output. Store-bought items and beverages are skipped.
    """
    state.stage = AgentStage.GENERATING_RECIPES
    logger.info("[STEP 6] Starting generate_recipes")

    # Build a lookup of preparation_method per dish name
    recipe_map = {r.name.lower(): r for r in state.event_data.meal_plan.recipes}

    # Filter to homemade, non-beverage dishes only
    eligible = [
        d
        for d in state.dish_ingredients
        if d.serving_spec is not None
        and d.serving_spec.dish_category not in _BEVERAGE_CATEGORIES
        and recipe_map.get(d.dish_name.lower()) is not None
        and recipe_map[d.dish_name.lower()].preparation_method != PreparationMethod.STORE_BOUGHT
    ]

    if not eligible:
        logger.info("[STEP 6] No eligible homemade dishes — skipping recipe generation")
        state.formatted_recipes_output = None
        return state

    logger.info("[STEP 6] Generating recipes for %d dishes", len(eligible))

    dishes_input = [
        (d.dish_name, [i.model_dump(mode="json") for i in d.ingredients], d.serving_spec.total_servings)
        for d in eligible
    ]
    instructions_map = await ai_service.generate_recipe_instructions_batch(dishes_input)

    # Format markdown
    lines: list[str] = ["## Recipes\n"]
    for dish in eligible:
        instructions = instructions_map.get(dish.dish_name, [])
        total_servings = dish.serving_spec.total_servings if dish.serving_spec else "?"

        lines.append("---\n")
        lines.append(f"### {dish.dish_name}")
        lines.append(f"*Serves {total_servings}*\n")

        lines.append("**Ingredients**")
        for ing in dish.ingredients:
            qty = math.ceil(ing.quantity)
            lines.append(f"- {qty} {ing.unit.value} {ing.name}")
        lines.append("")

        if instructions:
            lines.append("**Instructions**")
            for i, step in enumerate(instructions, 1):
                lines.append(f"{i}. {step}")
        lines.append("")

    state.formatted_recipes_output = "\n".join(lines)
    logger.info("[STEP 6] generate_recipes: formatted %d recipes", len(eligible))
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

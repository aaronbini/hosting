"""
Tests for pure agent step functions in agent/steps.py.

format_chat_output has no external dependencies — tested directly.
Steps that call GeminiService are tested with a mock.
"""

from unittest.mock import AsyncMock

import pytest

from app.agent.state import AgentState, AgentStage
from app.agent.steps import format_chat_output, generate_recipes
from app.models.event import EventPlanningData, MealPlan, OutputFormat, PreparationMethod, Recipe, RecipeStatus
from app.models.shopping import (
    AggregatedIngredient,
    DishCategory,
    DishIngredients,
    DishServingSpec,
    GroceryCategory,
    QuantityUnit,
    RecipeIngredient,
    ShoppingList,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_items(**categories):
    """Build a list of AggregatedIngredients. Pass category=[(name, qty, unit), ...] pairs."""
    items = []
    for grocery_cat, entries in categories.items():
        cat = GroceryCategory(grocery_cat)
        for name, qty, unit in entries:
            items.append(
                AggregatedIngredient(
                    name=name,
                    total_quantity=qty,
                    unit=QuantityUnit(unit),
                    grocery_category=cat,
                    appears_in=["Test Dish"],
                )
            )
    return items


def _make_state(items=None) -> AgentState:
    sl = None
    if items is not None:
        sl = ShoppingList(
            meal_plan=["Pasta Carbonara"],
            adult_count=8,
            child_count=0,
            total_guests=8,
            items=items,
        )
        sl.build_grouped()
    return AgentState(
        event_data=EventPlanningData(adult_count=8),
        output_formats=[OutputFormat.IN_CHAT],
        shopping_list=sl,
    )


# ---------------------------------------------------------------------------
# format_chat_output
# ---------------------------------------------------------------------------


class TestFormatChatOutput:
    async def test_contains_shopping_list_header(self, sample_agent_state):
        result = await format_chat_output(sample_agent_state)
        assert "## Shopping List" in result.formatted_chat_output

    async def test_contains_item_names(self, sample_agent_state):
        result = await format_chat_output(sample_agent_state)
        output = result.formatted_chat_output.lower()
        assert "pasta" in output
        assert "eggs" in output

    async def test_quantities_rounded_up(self):
        items = _make_items(pantry=[("olive oil", 2.1, "cups")])
        state = _make_state(items)
        result = await format_chat_output(state)
        # 2.1 should ceil to 3
        assert " 3 " in result.formatted_chat_output

    async def test_whole_quantities_unchanged(self):
        items = _make_items(pantry=[("pasta", 2.0, "lbs")])
        state = _make_state(items)
        result = await format_chat_output(state)
        assert " 2 " in result.formatted_chat_output

    async def test_exactly_at_integer_not_rounded_up(self):
        # 4.0 should stay as 4 (math.ceil(4.0) == 4)
        items = _make_items(dairy=[("eggs", 4.0, "count")])
        state = _make_state(items)
        result = await format_chat_output(state)
        assert " 4 " in result.formatted_chat_output

    async def test_no_shopping_list_returns_message(self):
        state = _make_state(None)
        result = await format_chat_output(state)
        assert "No shopping list available" in result.formatted_chat_output

    async def test_stage_set_to_delivering(self, sample_agent_state):
        result = await format_chat_output(sample_agent_state)
        assert result.stage == AgentStage.DELIVERING

    async def test_categories_appear_as_headers(self):
        items = _make_items(
            pantry=[("pasta", 1.0, "lbs")],
            dairy=[("eggs", 6.0, "count")],
        )
        state = _make_state(items)
        result = await format_chat_output(state)
        output = result.formatted_chat_output
        # Category names appear in bold headers
        assert "**Pantry**" in output
        assert "**Dairy**" in output

    async def test_empty_shopping_list_no_items(self):
        state = _make_state([])  # empty list, not None
        result = await format_chat_output(state)
        # Should produce a header but no items
        assert "## Shopping List" in result.formatted_chat_output


# ---------------------------------------------------------------------------
# generate_recipes
# ---------------------------------------------------------------------------


def _make_dish_ingredients(
    dish_name: str,
    category: DishCategory = DishCategory.MAIN_PROTEIN,
    total_servings: int = 8,
) -> DishIngredients:
    spec = DishServingSpec(
        dish_name=dish_name,
        dish_category=category,
        adult_servings=float(total_servings),
        child_servings=0.0,
        total_servings=float(total_servings),
    )
    return DishIngredients(
        dish_name=dish_name,
        serving_spec=spec,
        ingredients=[
            RecipeIngredient(
                name="test ingredient",
                quantity=2.0,
                unit=QuantityUnit.LBS,
                grocery_category=GroceryCategory.PANTRY,
            )
        ],
    )


def _make_recipes_state(
    dishes: list[tuple[str, DishCategory, PreparationMethod]],
) -> AgentState:
    """Build an AgentState with meal plan recipes and dish_ingredients."""
    meal_plan = MealPlan()
    dish_ingredients = []
    for dish_name, category, prep_method in dishes:
        meal_plan.add_recipe(
            Recipe(
                name=dish_name,
                status=RecipeStatus.COMPLETE,
                preparation_method=prep_method,
                ingredients=[{"name": "test", "quantity": 1, "unit": "lbs", "grocery_category": "pantry"}],
            )
        )
        dish_ingredients.append(_make_dish_ingredients(dish_name, category))
    meal_plan.confirmed = True
    return AgentState(
        event_data=EventPlanningData(adult_count=8, meal_plan=meal_plan),
        output_formats=[OutputFormat.IN_CHAT],
        dish_ingredients=dish_ingredients,
    )


class TestGenerateRecipes:
    async def test_includes_homemade_dishes(self):
        state = _make_recipes_state([
            ("Pasta Carbonara", DishCategory.MAIN_PROTEIN, PreparationMethod.HOMEMADE),
        ])
        ai_service = AsyncMock()
        ai_service.generate_recipe_instructions_batch.return_value = {
            "Pasta Carbonara": ["Boil pasta.", "Mix eggs and cheese.", "Combine."]
        }
        result = await generate_recipes(state, ai_service)
        assert result.formatted_recipes_output is not None
        assert "Pasta Carbonara" in result.formatted_recipes_output
        assert "Boil pasta." in result.formatted_recipes_output
        ai_service.generate_recipe_instructions_batch.assert_called_once()

    async def test_recipes_header_in_output(self):
        state = _make_recipes_state([
            ("Tiramisu", DishCategory.DESSERT, PreparationMethod.HOMEMADE),
        ])
        ai_service = AsyncMock()
        ai_service.generate_recipe_instructions_batch.return_value = {
            "Tiramisu": ["Whip cream.", "Layer ladyfingers."]
        }
        result = await generate_recipes(state, ai_service)
        assert "## Recipes" in result.formatted_recipes_output

    async def test_skips_store_bought_dishes(self):
        state = _make_recipes_state([
            ("Store-bought Hummus", DishCategory.PASSED_APPETIZER, PreparationMethod.STORE_BOUGHT),
        ])
        ai_service = AsyncMock()
        result = await generate_recipes(state, ai_service)
        assert result.formatted_recipes_output is None
        ai_service.generate_recipe_instructions_batch.assert_not_called()

    async def test_skips_beverage_dishes(self):
        state = _make_recipes_state([
            ("Wine", DishCategory.BEVERAGE_ALCOHOLIC, PreparationMethod.HOMEMADE),
            ("Sparkling Water", DishCategory.BEVERAGE_NONALCOHOLIC, PreparationMethod.STORE_BOUGHT),
        ])
        ai_service = AsyncMock()
        result = await generate_recipes(state, ai_service)
        assert result.formatted_recipes_output is None
        ai_service.generate_recipe_instructions_batch.assert_not_called()

    async def test_no_eligible_dishes_returns_none(self):
        state = _make_recipes_state([
            ("Beer", DishCategory.BEVERAGE_ALCOHOLIC, PreparationMethod.STORE_BOUGHT),
            ("Pre-made Salad", DishCategory.SALAD, PreparationMethod.STORE_BOUGHT),
        ])
        ai_service = AsyncMock()
        result = await generate_recipes(state, ai_service)
        assert result.formatted_recipes_output is None

    async def test_mixed_dishes_only_includes_homemade(self):
        state = _make_recipes_state([
            ("Pasta", DishCategory.MAIN_PROTEIN, PreparationMethod.HOMEMADE),
            ("Wine", DishCategory.BEVERAGE_ALCOHOLIC, PreparationMethod.STORE_BOUGHT),
            ("Store Bread", DishCategory.BREAD, PreparationMethod.STORE_BOUGHT),
        ])
        ai_service = AsyncMock()
        ai_service.generate_recipe_instructions_batch.return_value = {
            "Pasta": ["Cook pasta."]
        }
        result = await generate_recipes(state, ai_service)
        assert result.formatted_recipes_output is not None
        assert "Pasta" in result.formatted_recipes_output
        assert "Wine" not in result.formatted_recipes_output
        # Verify only homemade dish was sent to AI
        call_args = ai_service.generate_recipe_instructions_batch.call_args[0][0]
        dish_names = [d[0] for d in call_args]
        assert dish_names == ["Pasta"]

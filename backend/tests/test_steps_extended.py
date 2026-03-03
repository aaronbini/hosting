"""
Tests for previously-untested step functions in agent/steps.py:
  - _scale_recipe_in_python  (pure Python, no mocks)
  - calculate_quantities     (mocked ai_service.categorise_dishes)
  - get_all_dish_ingredients (mocked ai_service, routing logic)
  - aggregate_ingredients    (mocked ai_service.aggregate_ingredients)
  - apply_corrections        (mocked ai_service.apply_shopping_list_corrections)
  - create_google_tasks      (mocked tasks_service)
"""

from unittest.mock import AsyncMock

from app.agent.state import AgentStage, AgentState
from app.agent.steps import (
    _scale_recipe_in_python,
    aggregate_ingredients,
    apply_corrections,
    calculate_quantities,
    create_google_tasks,
    get_all_dish_ingredients,
)
from app.models.event import (
    EventPlanningData,
    MealPlan,
    OutputFormat,
    PreparationMethod,
    Recipe,
    RecipeStatus,
)
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


def _make_spec(
    dish_name: str,
    category: DishCategory = DishCategory.MAIN_PROTEIN,
    total_servings: float = 8.0,
    adult_servings: float = 8.0,
    child_servings: float = 0.0,
) -> DishServingSpec:
    return DishServingSpec(
        dish_name=dish_name,
        dish_category=category,
        adult_servings=adult_servings,
        child_servings=child_servings,
        total_servings=total_servings,
    )


def _make_recipe(
    name: str,
    prep: PreparationMethod = PreparationMethod.HOMEMADE,
    servings: int = 4,
    ingredients: list | None = None,
    category: DishCategory = DishCategory.MAIN_PROTEIN,
) -> Recipe:
    if ingredients is None:
        ingredients = [
            {"name": "pasta", "quantity": 8.0, "unit": "oz", "grocery_category": "pantry"}
        ]
    return Recipe(
        name=name,
        status=RecipeStatus.COMPLETE,
        preparation_method=prep,
        servings=servings,
        ingredients=ingredients,
    )


def _make_state_with_specs(
    recipes: list[Recipe],
    specs: list[DishServingSpec],
) -> AgentState:
    plan = MealPlan()
    for r in recipes:
        plan.add_recipe(r)
    plan.confirmed = True
    return AgentState(
        event_data=EventPlanningData(adult_count=8, meal_plan=plan),
        output_formats=[OutputFormat.IN_CHAT],
        serving_specs=specs,
    )


def _make_shopping_list(extra_items: list[AggregatedIngredient] | None = None) -> ShoppingList:
    items = extra_items or [
        AggregatedIngredient(
            name="pasta",
            total_quantity=2.0,
            unit=QuantityUnit.LBS,
            grocery_category=GroceryCategory.PANTRY,
            appears_in=["Pasta Carbonara"],
        )
    ]
    sl = ShoppingList(
        meal_plan=["Pasta Carbonara"],
        adult_count=8,
        child_count=0,
        total_guests=8,
        items=items,
    )
    sl.build_grouped()
    return sl


# ---------------------------------------------------------------------------
# _scale_recipe_in_python
# ---------------------------------------------------------------------------


class TestScaleRecipeInPython:
    def test_doubles_quantities_for_double_servings(self):
        recipe = _make_recipe(
            "Pasta",
            servings=4,
            ingredients=[{"name": "pasta", "quantity": 8.0, "unit": "oz", "grocery_category": "pantry"}],
        )
        spec = _make_spec("Pasta", total_servings=8.0)  # 8/4 = 2x
        result = _scale_recipe_in_python(recipe, spec)
        assert result.ingredients[0].quantity == 16.0

    def test_halves_quantities_for_half_servings(self):
        recipe = _make_recipe(
            "Risotto",
            servings=8,
            ingredients=[{"name": "rice", "quantity": 24.0, "unit": "oz", "grocery_category": "pantry"}],
        )
        spec = _make_spec("Risotto", total_servings=4.0)  # 4/8 = 0.5x
        result = _scale_recipe_in_python(recipe, spec)
        assert result.ingredients[0].quantity == 12.0

    def test_scales_all_ingredients_by_same_factor(self):
        recipe = _make_recipe(
            "Soup",
            servings=4,
            ingredients=[
                {"name": "broth", "quantity": 16.0, "unit": "fl oz", "grocery_category": "pantry"},
                {"name": "carrots", "quantity": 4.0, "unit": "oz", "grocery_category": "produce"},
                {"name": "garlic", "quantity": 1.0, "unit": "head", "grocery_category": "produce"},
            ],
        )
        spec = _make_spec("Soup", total_servings=8.0)  # 2x factor
        result = _scale_recipe_in_python(recipe, spec)
        assert result.ingredients[0].quantity == 32.0   # broth
        assert result.ingredients[1].quantity == 8.0    # carrots
        assert result.ingredients[2].quantity == 2.0    # garlic

    def test_none_servings_defaults_to_four(self):
        """When recipe.servings is None, the or-4 guard defaults base to 4."""
        # model_construct bypasses Pydantic validation to test the defensive guard
        recipe = Recipe.model_construct(
            name="Mystery",
            status=RecipeStatus.COMPLETE,
            servings=None,
            preparation_method=PreparationMethod.HOMEMADE,
            ingredients=[{"name": "eggs", "quantity": 4.0, "unit": "count", "grocery_category": "dairy"}],
        )
        spec = _make_spec("Mystery", total_servings=8.0)  # 8/4 = 2x
        result = _scale_recipe_in_python(recipe, spec)
        assert result.ingredients[0].quantity == 8.0

    def test_zero_servings_treated_as_four_base(self):
        """servings=0 is falsy so `recipe.servings or 4` defaults to 4, giving 2x scale."""
        # model_construct bypasses the int validator that would reject 0 only in strict mode
        recipe = Recipe.model_construct(
            name="Odd",
            status=RecipeStatus.COMPLETE,
            servings=0,
            preparation_method=PreparationMethod.HOMEMADE,
            ingredients=[{"name": "butter", "quantity": 4.0, "unit": "oz", "grocery_category": "dairy"}],
        )
        spec = _make_spec("Odd", total_servings=8.0)
        result = _scale_recipe_in_python(recipe, spec)
        # servings=0 → or 4 → base=4, factor=8/4=2.0 → 4.0 * 2.0 = 8.0
        assert result.ingredients[0].quantity == 8.0

    def test_returns_dish_ingredients_with_correct_dish_name(self):
        recipe = _make_recipe("Tiramisu", servings=4)
        spec = _make_spec("Tiramisu", total_servings=4.0)
        result = _scale_recipe_in_python(recipe, spec)
        assert result.dish_name == "Tiramisu"

    def test_quantities_rounded_to_two_decimal_places(self):
        recipe = _make_recipe(
            "Sauce",
            servings=3,
            ingredients=[{"name": "oil", "quantity": 1.0, "unit": "fl oz", "grocery_category": "pantry"}],
        )
        spec = _make_spec("Sauce", total_servings=8.0)  # 8/3 = 2.666...
        result = _scale_recipe_in_python(recipe, spec)
        # Should be rounded to 2 decimal places
        assert result.ingredients[0].quantity == round(8.0 / 3, 2)


# ---------------------------------------------------------------------------
# calculate_quantities
# ---------------------------------------------------------------------------


class TestCalculateQuantities:
    async def test_calls_categorise_dishes_with_all_dish_names(self):
        plan = MealPlan()
        plan.add_recipe(_make_recipe("Pasta"))
        plan.add_recipe(_make_recipe("Salad"))
        plan.confirmed = True

        state = AgentState(
            event_data=EventPlanningData(adult_count=8, child_count=0, meal_plan=plan),
            output_formats=[OutputFormat.IN_CHAT],
        )
        ai_service = AsyncMock()
        ai_service.categorise_dishes.return_value = {
            "Pasta": DishCategory.MAIN_PROTEIN,
            "Salad": DishCategory.SALAD,
        }

        result = await calculate_quantities(state, ai_service)

        ai_service.categorise_dishes.assert_called_once()
        call_args = ai_service.categorise_dishes.call_args[0][0]
        assert "Pasta" in call_args
        assert "Salad" in call_args

    async def test_sets_serving_specs_on_state(self):
        plan = MealPlan()
        plan.add_recipe(_make_recipe("Pasta"))
        plan.confirmed = True

        state = AgentState(
            event_data=EventPlanningData(adult_count=8, child_count=0, meal_plan=plan),
            output_formats=[OutputFormat.IN_CHAT],
        )
        ai_service = AsyncMock()
        ai_service.categorise_dishes.return_value = {"Pasta": DishCategory.MAIN_PROTEIN}

        result = await calculate_quantities(state, ai_service)

        assert len(result.serving_specs) == 1
        assert result.serving_specs[0].dish_name == "Pasta"

    async def test_stage_set_to_calculating_quantities(self):
        plan = MealPlan()
        plan.add_recipe(_make_recipe("Pasta"))
        plan.confirmed = True

        state = AgentState(
            event_data=EventPlanningData(adult_count=4, meal_plan=plan),
            output_formats=[OutputFormat.IN_CHAT],
        )
        ai_service = AsyncMock()
        ai_service.categorise_dishes.return_value = {"Pasta": DishCategory.MAIN_PROTEIN}

        result = await calculate_quantities(state, ai_service)

        assert result.stage == AgentStage.CALCULATING_QUANTITIES


# ---------------------------------------------------------------------------
# get_all_dish_ingredients
# ---------------------------------------------------------------------------


class TestGetAllDishIngredients:
    async def test_store_bought_produces_count_entry_no_ai_call(self):
        recipe = _make_recipe("Hummus", prep=PreparationMethod.STORE_BOUGHT, ingredients=[])
        spec = _make_spec("Hummus", category=DishCategory.PASSED_APPETIZER)
        state = _make_state_with_specs([recipe], [spec])

        ai_service = AsyncMock()
        result = await get_all_dish_ingredients(state, ai_service)

        ai_service.get_dish_ingredients.assert_not_called()
        assert len(result.dish_ingredients) == 1
        ing = result.dish_ingredients[0].ingredients[0]
        assert ing.unit == QuantityUnit.COUNT
        assert ing.quantity == 1

    async def test_homemade_with_base_recipe_uses_python_scaling_no_ai_call(self):
        recipe = _make_recipe(
            "Pasta",
            prep=PreparationMethod.HOMEMADE,
            servings=4,
            ingredients=[{"name": "pasta", "quantity": 8.0, "unit": "oz", "grocery_category": "pantry"}],
            category=DishCategory.MAIN_PROTEIN,
        )
        spec = _make_spec("Pasta", category=DishCategory.MAIN_PROTEIN, total_servings=8.0)
        state = _make_state_with_specs([recipe], [spec])

        ai_service = AsyncMock()
        result = await get_all_dish_ingredients(state, ai_service)

        ai_service.get_dish_ingredients.assert_not_called()
        assert len(result.dish_ingredients) == 1
        # 8.0 oz × (8/4) = 16.0 oz
        assert result.dish_ingredients[0].ingredients[0].quantity == 16.0

    async def test_beverage_always_uses_gemini(self):
        # HOMEMADE beverage WITH stored ingredients — beverage category routes to Gemini
        # regardless of whether base ingredients exist (the BEVERAGE check comes first)
        recipe = _make_recipe(
            "Wine",
            prep=PreparationMethod.HOMEMADE,
            category=DishCategory.BEVERAGE_ALCOHOLIC,
            ingredients=[{"name": "wine", "quantity": 5.0, "unit": "fl oz", "grocery_category": "beverages"}],
        )
        spec = _make_spec("Wine", category=DishCategory.BEVERAGE_ALCOHOLIC, total_servings=8.0)
        state = _make_state_with_specs([recipe], [spec])

        ai_service = AsyncMock()
        ai_service.get_dish_ingredients.return_value = DishIngredients(
            dish_name="Wine",
            serving_spec=spec,
            ingredients=[RecipeIngredient(name="wine", quantity=40.0, unit=QuantityUnit.FL_OZ, grocery_category=GroceryCategory.BEVERAGES)],
        )

        result = await get_all_dish_ingredients(state, ai_service)

        ai_service.get_dish_ingredients.assert_called_once()

    async def test_homemade_without_base_ingredients_uses_gemini(self):
        """Homemade recipe with NO stored ingredients falls back to Gemini."""
        recipe = _make_recipe(
            "Mystery Dish",
            prep=PreparationMethod.HOMEMADE,
            ingredients=[],  # no stored ingredients
        )
        spec = _make_spec("Mystery Dish", category=DishCategory.STARCH_SIDE)
        state = _make_state_with_specs([recipe], [spec])

        ai_service = AsyncMock()
        ai_service.get_dish_ingredients.return_value = DishIngredients(
            dish_name="Mystery Dish",
            serving_spec=spec,
            ingredients=[],
        )

        result = await get_all_dish_ingredients(state, ai_service)

        ai_service.get_dish_ingredients.assert_called_once()

    async def test_gemini_failure_skips_dish_without_crashing(self):
        """If Gemini raises for a dish, that dish is skipped (not crashing the step)."""
        # HOMEMADE beverage without stored ingredients → goes to Gemini
        recipe = _make_recipe("Wine", prep=PreparationMethod.HOMEMADE, ingredients=[])
        spec = _make_spec("Wine", category=DishCategory.BEVERAGE_ALCOHOLIC)
        state = _make_state_with_specs([recipe], [spec])

        ai_service = AsyncMock()
        ai_service.get_dish_ingredients.side_effect = RuntimeError("API call failed")

        result = await get_all_dish_ingredients(state, ai_service)

        # No crash; dish is simply omitted
        assert len(result.dish_ingredients) == 0

    async def test_mixed_routing_store_bought_homemade_beverage(self):
        """All three routing branches exercised in one call."""
        store_recipe = _make_recipe("Hummus", prep=PreparationMethod.STORE_BOUGHT, ingredients=[])
        homemade_recipe = _make_recipe(
            "Pasta",
            prep=PreparationMethod.HOMEMADE,
            servings=4,
            ingredients=[{"name": "pasta", "quantity": 8.0, "unit": "oz", "grocery_category": "pantry"}],
        )
        # HOMEMADE beverage without stored ingredients → routed to Gemini via beverage category
        bev_recipe = _make_recipe("Wine", prep=PreparationMethod.HOMEMADE, ingredients=[])

        store_spec = _make_spec("Hummus", category=DishCategory.PASSED_APPETIZER)
        homemade_spec = _make_spec("Pasta", category=DishCategory.MAIN_PROTEIN, total_servings=8.0)
        bev_spec = _make_spec("Wine", category=DishCategory.BEVERAGE_ALCOHOLIC)

        state = _make_state_with_specs(
            [store_recipe, homemade_recipe, bev_recipe],
            [store_spec, homemade_spec, bev_spec],
        )

        bev_ingredients = DishIngredients(
            dish_name="Wine",
            serving_spec=bev_spec,
            ingredients=[RecipeIngredient(name="wine", quantity=40.0, unit=QuantityUnit.FL_OZ, grocery_category=GroceryCategory.BEVERAGES)],
        )
        ai_service = AsyncMock()
        ai_service.get_dish_ingredients.return_value = bev_ingredients

        result = await get_all_dish_ingredients(state, ai_service)

        # All three dishes present in output
        dish_names = {d.dish_name for d in result.dish_ingredients}
        assert "Hummus" in dish_names
        assert "Pasta" in dish_names
        assert "Wine" in dish_names

        # Gemini called only for beverage
        assert ai_service.get_dish_ingredients.call_count == 1

        # Store-bought is a COUNT item
        hummus_di = next(d for d in result.dish_ingredients if d.dish_name == "Hummus")
        assert hummus_di.ingredients[0].unit == QuantityUnit.COUNT

        # Homemade is Python-scaled: 8/4 = 2x → 16 oz
        pasta_di = next(d for d in result.dish_ingredients if d.dish_name == "Pasta")
        assert pasta_di.ingredients[0].quantity == 16.0


# ---------------------------------------------------------------------------
# aggregate_ingredients
# ---------------------------------------------------------------------------


class TestAggregateIngredients:
    async def test_calls_ai_service_aggregate(self):
        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            dish_ingredients=[],
        )
        expected_list = _make_shopping_list()
        ai_service = AsyncMock()
        ai_service.aggregate_ingredients.return_value = expected_list

        result = await aggregate_ingredients(state, ai_service)

        ai_service.aggregate_ingredients.assert_called_once_with(state.dish_ingredients)
        assert result.shopping_list is not None

    async def test_filters_never_purchase_items(self):
        """Water and similar items must be removed from the shopping list."""
        items = [
            AggregatedIngredient(
                name="water",
                total_quantity=1.0,
                unit=QuantityUnit.LITERS,
                grocery_category=GroceryCategory.BEVERAGES,
                appears_in=["Soup"],
            ),
            AggregatedIngredient(
                name="pasta",
                total_quantity=2.0,
                unit=QuantityUnit.LBS,
                grocery_category=GroceryCategory.PANTRY,
                appears_in=["Pasta"],
            ),
        ]
        shopping_list = ShoppingList(
            meal_plan=["Soup", "Pasta"],
            adult_count=8,
            child_count=0,
            total_guests=8,
            items=items,
        )

        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            dish_ingredients=[],
        )
        ai_service = AsyncMock()
        ai_service.aggregate_ingredients.return_value = shopping_list

        result = await aggregate_ingredients(state, ai_service)

        names = [item.name for item in result.shopping_list.items]
        assert "water" not in names
        assert "pasta" in names

    async def test_all_never_purchase_variants_removed(self):
        """Verify all NEVER_PURCHASE strings are filtered."""
        never_purchase = ["water", "tap water", "cold water", "hot water", "boiling water", "ice water"]
        items = [
            AggregatedIngredient(
                name=name,
                total_quantity=1.0,
                unit=QuantityUnit.LITERS,
                grocery_category=GroceryCategory.BEVERAGES,
                appears_in=["Test"],
            )
            for name in never_purchase
        ]
        shopping_list = ShoppingList(
            meal_plan=["Test"],
            adult_count=4,
            child_count=0,
            total_guests=4,
            items=items,
        )
        state = AgentState(
            event_data=EventPlanningData(adult_count=4),
            output_formats=[OutputFormat.IN_CHAT],
            dish_ingredients=[],
        )
        ai_service = AsyncMock()
        ai_service.aggregate_ingredients.return_value = shopping_list

        result = await aggregate_ingredients(state, ai_service)

        assert result.shopping_list.items == []

    async def test_grouped_dict_populated(self):
        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            dish_ingredients=[],
        )
        ai_service = AsyncMock()
        ai_service.aggregate_ingredients.return_value = _make_shopping_list()

        result = await aggregate_ingredients(state, ai_service)

        assert len(result.shopping_list.grouped) > 0

    async def test_stage_set_to_aggregating(self):
        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            dish_ingredients=[],
        )
        ai_service = AsyncMock()
        ai_service.aggregate_ingredients.return_value = _make_shopping_list()

        result = await aggregate_ingredients(state, ai_service)

        assert result.stage == AgentStage.AGGREGATING


# ---------------------------------------------------------------------------
# apply_corrections
# ---------------------------------------------------------------------------


class TestApplyCorrections:
    async def test_empty_corrections_is_noop(self):
        """No AI call when corrections is empty/None."""
        shopping_list = _make_shopping_list()
        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            shopping_list=shopping_list,
            user_corrections="",
        )
        ai_service = AsyncMock()

        result = await apply_corrections(state, ai_service)

        ai_service.apply_shopping_list_corrections.assert_not_called()
        # shopping list unchanged
        assert result.shopping_list is shopping_list

    async def test_none_corrections_is_noop(self):
        shopping_list = _make_shopping_list()
        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            shopping_list=shopping_list,
            user_corrections=None,
        )
        ai_service = AsyncMock()

        result = await apply_corrections(state, ai_service)

        ai_service.apply_shopping_list_corrections.assert_not_called()

    async def test_applies_corrections_via_ai_service(self):
        original_list = _make_shopping_list()
        revised_list = _make_shopping_list(extra_items=[
            AggregatedIngredient(
                name="revised pasta",
                total_quantity=1.0,
                unit=QuantityUnit.LBS,
                grocery_category=GroceryCategory.PANTRY,
                appears_in=["Pasta Carbonara"],
            )
        ])

        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            shopping_list=original_list,
            user_corrections="remove the olive oil",
        )
        ai_service = AsyncMock()
        ai_service.apply_shopping_list_corrections.return_value = revised_list

        result = await apply_corrections(state, ai_service)

        ai_service.apply_shopping_list_corrections.assert_called_once_with(
            shopping_list=original_list,
            corrections="remove the olive oil",
        )
        assert result.shopping_list is revised_list

    async def test_revised_list_has_grouped_populated(self):
        original_list = _make_shopping_list()
        revised_list = ShoppingList(
            meal_plan=["Pasta"],
            adult_count=8,
            child_count=0,
            total_guests=8,
            items=[
                AggregatedIngredient(
                    name="onion",
                    total_quantity=2.0,
                    unit=QuantityUnit.COUNT,
                    grocery_category=GroceryCategory.PRODUCE,
                    appears_in=["Pasta"],
                )
            ],
        )
        # grouped not yet built
        assert revised_list.grouped == {}

        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.IN_CHAT],
            shopping_list=original_list,
            user_corrections="add onion",
        )
        ai_service = AsyncMock()
        ai_service.apply_shopping_list_corrections.return_value = revised_list

        result = await apply_corrections(state, ai_service)

        assert len(result.shopping_list.grouped) > 0


# ---------------------------------------------------------------------------
# create_google_tasks
# ---------------------------------------------------------------------------


class TestCreateGoogleTasks:
    async def test_no_tasks_service_returns_early_with_none(self):
        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.GOOGLE_TASKS],
            shopping_list=_make_shopping_list(),
        )

        result = await create_google_tasks(state, tasks_service=None)

        assert result.google_tasks is None

    async def test_with_tasks_service_creates_list(self):
        state = AgentState(
            event_data=EventPlanningData(adult_count=8),
            output_formats=[OutputFormat.GOOGLE_TASKS],
            shopping_list=_make_shopping_list(),
        )
        tasks_service = AsyncMock()
        tasks_service.create_shopping_list = AsyncMock()

        result = await create_google_tasks(state, tasks_service=tasks_service)

        tasks_service.create_shopping_list.assert_called_once()
        assert result.google_tasks is not None
        assert result.google_tasks.url == "https://tasks.google.com"

    async def test_task_list_title_includes_event_date(self):
        state = AgentState(
            event_data=EventPlanningData(adult_count=8, event_date="2025-12-25"),
            output_formats=[OutputFormat.GOOGLE_TASKS],
            shopping_list=_make_shopping_list(),
        )
        tasks_service = AsyncMock()
        tasks_service.create_shopping_list = AsyncMock()

        result = await create_google_tasks(state, tasks_service=tasks_service)

        assert "12-25-2025" in result.google_tasks.list_title

    async def test_task_list_title_uses_today_when_no_date(self):
        state = AgentState(
            event_data=EventPlanningData(adult_count=8, event_date=None),
            output_formats=[OutputFormat.GOOGLE_TASKS],
            shopping_list=_make_shopping_list(),
        )
        tasks_service = AsyncMock()
        tasks_service.create_shopping_list = AsyncMock()

        result = await create_google_tasks(state, tasks_service=tasks_service)

        # Should still produce a title without crashing
        assert "Dinner Party Shopping" in result.google_tasks.list_title

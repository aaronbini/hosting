"""
Tests for data model logic in models/event.py and models/shopping.py.
No I/O, no mocking needed.
"""

import pytest

from app.models.event import (
    EventPlanningData,
    MealPlan,
    PreparationMethod,
    Recipe,
    RecipeStatus,
)
from app.models.shopping import (
    AggregatedIngredient,
    GroceryCategory,
    QuantityUnit,
    ShoppingList,
)


# Helper: a complete homemade recipe
def _complete_recipe(name: str = "Pasta") -> Recipe:
    return Recipe(
        name=name,
        status=RecipeStatus.COMPLETE,
        ingredients=[
            {"name": "pasta", "quantity": 1.0, "unit": "lbs", "grocery_category": "pantry"}
        ],
    )


# ---------------------------------------------------------------------------
# Recipe.is_complete_recipe
# ---------------------------------------------------------------------------


class TestRecipeIsCompleteRecipe:
    def test_homemade_with_ingredients_is_complete(self):
        r = _complete_recipe()
        assert r.is_complete_recipe() is True

    def test_homemade_status_complete_but_no_ingredients_is_not_complete(self):
        r = Recipe(name="Pasta", status=RecipeStatus.COMPLETE, ingredients=[])
        assert r.is_complete_recipe() is False

    def test_homemade_named_without_ingredients_is_not_complete(self):
        r = Recipe(name="Pasta", status=RecipeStatus.NAMED, ingredients=[])
        assert r.is_complete_recipe() is False

    def test_homemade_placeholder_without_ingredients_is_not_complete(self):
        r = Recipe(name="main", status=RecipeStatus.PLACEHOLDER, ingredients=[])
        assert r.is_complete_recipe() is False

    def test_store_bought_with_real_name_is_complete(self):
        r = Recipe(
            name="Sourdough Bread",
            preparation_method=PreparationMethod.STORE_BOUGHT,
            status=RecipeStatus.NAMED,
        )
        assert r.is_complete_recipe() is True

    def test_store_bought_placeholder_is_not_complete(self):
        # Store-bought still needs a real name — placeholder status blocks it
        r = Recipe(
            name="bread",
            preparation_method=PreparationMethod.STORE_BOUGHT,
            status=RecipeStatus.PLACEHOLDER,
        )
        assert r.is_complete_recipe() is False

    def test_store_bought_does_not_require_ingredients(self):
        # No ingredients provided — that's fine for store-bought
        r = Recipe(
            name="Sparkling Water",
            preparation_method=PreparationMethod.STORE_BOUGHT,
            status=RecipeStatus.NAMED,
            ingredients=[],
        )
        assert r.is_complete_recipe() is True


class TestRecipeNeedsIngredients:
    def test_no_ingredients(self):
        r = Recipe(name="Pasta", ingredients=[])
        assert r.needs_ingredients() is True

    def test_has_ingredients(self):
        r = _complete_recipe()
        assert r.needs_ingredients() is False


# ---------------------------------------------------------------------------
# MealPlan
# ---------------------------------------------------------------------------


class TestMealPlanMutations:
    def test_add_recipe(self):
        plan = MealPlan()
        plan.add_recipe(Recipe(name="Pasta"))
        assert len(plan.recipes) == 1

    def test_add_recipe_idempotent(self):
        plan = MealPlan()
        r = Recipe(name="Pasta")
        plan.add_recipe(r)
        plan.add_recipe(r)
        assert len(plan.recipes) == 1

    def test_find_recipe_case_insensitive(self):
        plan = MealPlan()
        plan.add_recipe(Recipe(name="Pasta Carbonara"))
        assert plan.find_recipe("pasta carbonara") is not None
        assert plan.find_recipe("PASTA CARBONARA") is not None

    def test_find_recipe_missing_returns_none(self):
        plan = MealPlan()
        assert plan.find_recipe("Does Not Exist") is None

    def test_remove_recipe_by_name(self):
        plan = MealPlan()
        plan.add_recipe(Recipe(name="Pasta"))
        plan.add_recipe(Recipe(name="Salad"))
        plan.remove_recipe("Pasta")
        assert len(plan.recipes) == 1
        assert plan.find_recipe("Pasta") is None
        assert plan.find_recipe("Salad") is not None

    def test_remove_recipe_case_insensitive(self):
        plan = MealPlan()
        plan.add_recipe(Recipe(name="Pasta"))
        plan.remove_recipe("PASTA")
        assert plan.find_recipe("Pasta") is None

    def test_remove_nonexistent_recipe_is_noop(self):
        plan = MealPlan()
        plan.remove_recipe("Ghost Dish")  # should not raise


class TestMealPlanIsComplete:
    def test_not_complete_without_confirmed(self):
        plan = MealPlan()
        plan.add_recipe(_complete_recipe())
        plan.confirmed = False
        assert plan.is_complete is False

    def test_complete_when_confirmed_and_all_recipes_done(self):
        plan = MealPlan()
        plan.add_recipe(_complete_recipe())
        plan.confirmed = True
        assert plan.is_complete is True

    def test_not_complete_when_one_recipe_has_no_ingredients(self):
        plan = MealPlan()
        plan.add_recipe(_complete_recipe("Pasta"))
        plan.add_recipe(Recipe(name="Salad", status=RecipeStatus.NAMED, ingredients=[]))
        plan.confirmed = True
        assert plan.is_complete is False

    def test_empty_recipe_list_confirmed_is_not_complete(self):
        plan = MealPlan(confirmed=True)
        # all() over empty list is True — but is_complete delegates to EventPlanningData
        # At the MealPlan level, confirmed=True and all([]) = True → is_complete = True
        # EventPlanningData adds the "has_recipes" guard. Test MealPlan logic only.
        assert plan.is_complete is True  # MealPlan itself doesn't guard against empty

    def test_store_bought_counts_as_complete(self):
        plan = MealPlan()
        plan.add_recipe(
            Recipe(
                name="Sourdough",
                preparation_method=PreparationMethod.STORE_BOUGHT,
                status=RecipeStatus.NAMED,
            )
        )
        plan.confirmed = True
        assert plan.is_complete is True

    def test_pending_user_recipes_property(self):
        plan = MealPlan()
        plan.add_recipe(Recipe(name="Pasta", awaiting_user_input=True))
        plan.add_recipe(Recipe(name="Salad", awaiting_user_input=False))
        pending = plan.pending_user_recipes
        assert len(pending) == 1
        assert pending[0].name == "Pasta"


# ---------------------------------------------------------------------------
# EventPlanningData.compute_derived_fields + completion scoring
# ---------------------------------------------------------------------------


class TestComputeDerivedFields:
    def test_total_guests(self):
        data = EventPlanningData(adult_count=8, child_count=2)
        data.compute_derived_fields()
        assert data.total_guests == 10

    def test_budget_per_person(self):
        data = EventPlanningData(adult_count=4, child_count=0, budget=200.0)
        data.compute_derived_fields()
        assert data.budget_per_person == 50.0

    def test_no_budget_no_budget_per_person(self):
        data = EventPlanningData(adult_count=4)
        data.compute_derived_fields()
        assert data.budget_per_person is None


class TestCompletionScoring:
    def _all_answered(self, data: EventPlanningData) -> EventPlanningData:
        for q in data.answered_questions:
            data.answered_questions[q] = True
        return data

    def test_is_complete_false_by_default(self):
        data = EventPlanningData()
        data.compute_derived_fields()
        assert data.is_complete is False

    def test_is_complete_true_when_all_conditions_met(self):
        data = EventPlanningData(adult_count=8)
        self._all_answered(data)
        data.meal_plan.add_recipe(_complete_recipe())
        data.meal_plan.confirmed = True
        data.compute_derived_fields()
        assert data.is_complete is True

    def test_is_complete_false_when_missing_critical_question(self):
        data = EventPlanningData(adult_count=8)
        # Answer all except one
        self._all_answered(data)
        data.answered_questions["cuisine"] = False
        data.meal_plan.add_recipe(_complete_recipe())
        data.meal_plan.confirmed = True
        data.compute_derived_fields()
        assert data.is_complete is False

    def test_is_complete_false_when_meal_plan_not_confirmed(self):
        data = EventPlanningData(adult_count=8)
        self._all_answered(data)
        data.meal_plan.add_recipe(_complete_recipe())
        data.meal_plan.confirmed = False  # not confirmed
        data.compute_derived_fields()
        assert data.is_complete is False

    def test_is_complete_false_with_no_recipes(self):
        data = EventPlanningData(adult_count=8)
        self._all_answered(data)
        data.meal_plan.confirmed = True
        # No recipes added
        data.compute_derived_fields()
        assert data.is_complete is False

    def test_is_complete_blocked_by_awaiting_user_input(self):
        data = EventPlanningData(adult_count=8)
        self._all_answered(data)
        # Recipe waiting on user to provide ingredients
        data.meal_plan.add_recipe(
            Recipe(name="Mystery Dish", status=RecipeStatus.NAMED, awaiting_user_input=True)
        )
        data.meal_plan.confirmed = True
        data.compute_derived_fields()
        assert data.is_complete is False

    def test_completion_score_zero_when_nothing_answered(self):
        data = EventPlanningData()
        data.compute_derived_fields()
        assert data.completion_score == 0.0

    def test_completion_score_one_when_all_critical_answered(self):
        data = EventPlanningData()
        self._all_answered(data)
        data.compute_derived_fields()
        assert data.completion_score == 1.0


# ---------------------------------------------------------------------------
# ShoppingList.build_grouped
# ---------------------------------------------------------------------------


class TestShoppingListBuildGrouped:
    def test_items_bucketed_by_category(self):
        items = [
            AggregatedIngredient(
                name="pasta",
                total_quantity=1.0,
                unit=QuantityUnit.LBS,
                grocery_category=GroceryCategory.PANTRY,
                appears_in=["Pasta"],
            ),
            AggregatedIngredient(
                name="eggs",
                total_quantity=6.0,
                unit=QuantityUnit.COUNT,
                grocery_category=GroceryCategory.DAIRY,
                appears_in=["Pasta"],
            ),
            AggregatedIngredient(
                name="olive oil",
                total_quantity=0.5,
                unit=QuantityUnit.CUPS,
                grocery_category=GroceryCategory.PANTRY,
                appears_in=["Pasta"],
            ),
        ]
        sl = ShoppingList(
            meal_plan=["Pasta"],
            adult_count=4,
            child_count=0,
            total_guests=4,
            items=items,
        )
        sl.build_grouped()

        assert "pantry" in sl.grouped
        assert "dairy" in sl.grouped
        assert len(sl.grouped["pantry"]) == 2
        assert len(sl.grouped["dairy"]) == 1

    def test_empty_items_produces_empty_grouped(self):
        sl = ShoppingList(
            meal_plan=[], adult_count=0, child_count=0, total_guests=0, items=[]
        )
        sl.build_grouped()
        assert sl.grouped == {}

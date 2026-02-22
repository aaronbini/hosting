"""
Tests for quantity_engine.py — pure serving-size math, no mocking needed.
"""

import pytest

from app.models.shopping import DishCategory
from app.services.quantity_engine import (
    ADULT_SERVINGS_PER_PERSON,
    CHILD_SERVINGS_PER_PERSON,
    calculate_all_serving_specs,
    calculate_dish_serving_spec,
)


class TestCalculateDishServingSpec:
    def test_main_protein_adults_only(self):
        spec = calculate_dish_serving_spec("Beef Ragu", DishCategory.MAIN_PROTEIN, 8, 0)
        assert spec.adult_servings == 10.0  # 8 * 1.25
        assert spec.child_servings == 0.0
        assert spec.total_servings == 10.0

    def test_main_protein_mixed_guests(self):
        spec = calculate_dish_serving_spec("Beef Ragu", DishCategory.MAIN_PROTEIN, 8, 2)
        assert spec.adult_servings == 10.0   # 8 * 1.25
        assert spec.child_servings == 1.5    # 2 * 0.75
        assert spec.total_servings == 11.5

    def test_alcoholic_beverage_children_get_zero(self):
        spec = calculate_dish_serving_spec("Wine", DishCategory.BEVERAGE_ALCOHOLIC, 8, 4)
        assert spec.child_servings == 0.0    # no alcohol for children
        assert spec.adult_servings == 20.0   # 8 * 2.5

    def test_nonalcoholic_beverage_children_same_multiplier_as_adults(self):
        spec = calculate_dish_serving_spec(
            "Sparkling Water", DishCategory.BEVERAGE_NONALCOHOLIC, 4, 4
        )
        assert spec.adult_servings == 12.0   # 4 * 3.0
        assert spec.child_servings == 12.0   # 4 * 3.0

    def test_dish_name_and_category_preserved(self):
        spec = calculate_dish_serving_spec("My Pasta", DishCategory.STARCH_SIDE, 4, 0)
        assert spec.dish_name == "My Pasta"
        assert spec.dish_category == DishCategory.STARCH_SIDE

    def test_zero_guests(self):
        spec = calculate_dish_serving_spec("Soup", DishCategory.VEGETABLE_SIDE, 0, 0)
        assert spec.adult_servings == 0.0
        assert spec.child_servings == 0.0
        assert spec.total_servings == 0.0

    def test_dessert_children_same_multiplier_as_adults(self):
        # Kids always want dessert — multiplier is 1.0 for both
        spec = calculate_dish_serving_spec("Tiramisu", DishCategory.DESSERT, 4, 4)
        assert spec.child_servings == 4.0    # 4 * 1.0

    def test_bread_adult_servings(self):
        spec = calculate_dish_serving_spec("Focaccia", DishCategory.BREAD, 8, 0)
        assert spec.adult_servings == 12.0   # 8 * 1.5

    def test_passed_appetizer_high_multiplier(self):
        # 3 pieces per adult
        spec = calculate_dish_serving_spec("Bruschetta", DishCategory.PASSED_APPETIZER, 10, 0)
        assert spec.adult_servings == 30.0   # 10 * 3.0

    @pytest.mark.parametrize(
        "category",
        [
            DishCategory.MAIN_PROTEIN,
            DishCategory.SECONDARY_PROTEIN,
            DishCategory.STARCH_SIDE,
            DishCategory.VEGETABLE_SIDE,
            DishCategory.SALAD,
            DishCategory.BREAD,
            DishCategory.DESSERT,
            DishCategory.PASSED_APPETIZER,
            DishCategory.BEVERAGE_ALCOHOLIC,
            DishCategory.BEVERAGE_NONALCOHOLIC,
        ],
    )
    def test_total_servings_is_sum_of_adult_and_child(self, category):
        spec = calculate_dish_serving_spec("Test Dish", category, 6, 3)
        assert spec.total_servings == round(spec.adult_servings + spec.child_servings, 2)


class TestAllCategoriesCovered:
    def test_every_category_has_adult_multiplier(self):
        for category in DishCategory:
            assert category in ADULT_SERVINGS_PER_PERSON, (
                f"Missing adult multiplier for {category}"
            )

    def test_every_category_has_child_multiplier(self):
        for category in DishCategory:
            assert category in CHILD_SERVINGS_PER_PERSON, (
                f"Missing child multiplier for {category}"
            )


class TestCalculateAllServingSpecs:
    def test_basic_multi_dish(self):
        specs = calculate_all_serving_specs(
            meal_plan=["Pasta", "Salad"],
            dish_categories={
                "Pasta": DishCategory.STARCH_SIDE,
                "Salad": DishCategory.SALAD,
            },
            adult_count=4,
            child_count=0,
        )
        assert len(specs) == 2
        assert specs[0].dish_name == "Pasta"
        assert specs[1].dish_name == "Salad"

    def test_order_preserved(self):
        dishes = ["Appetizer", "Main", "Dessert"]
        categories = {
            "Appetizer": DishCategory.PASSED_APPETIZER,
            "Main": DishCategory.MAIN_PROTEIN,
            "Dessert": DishCategory.DESSERT,
        }
        specs = calculate_all_serving_specs(dishes, categories, 6, 0)
        assert [s.dish_name for s in specs] == dishes

    def test_missing_category_defaults_to_starch_side(self):
        specs = calculate_all_serving_specs(
            meal_plan=["Mystery Dish"],
            dish_categories={},  # no mapping provided
            adult_count=4,
            child_count=0,
        )
        assert len(specs) == 1
        assert specs[0].dish_category == DishCategory.STARCH_SIDE

    def test_empty_meal_plan(self):
        specs = calculate_all_serving_specs([], {}, 8, 2)
        assert specs == []

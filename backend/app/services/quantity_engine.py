"""
Quantity calculation engine.

Responsibility: given a list of dish names + guest counts, produce a
DishServingSpec for each dish that tells the recipe-scaling AI how many
servings to calculate ingredients for.

Design:
- A lookup table owns the per-person serving multiplier for each DishCategory.
- Multipliers are separate for adults and children (children eat ~60% of an
  adult portion by default).
- A single Gemini call (in ai_service) then maps each dish name to a category,
  which is used here to look up the multiplier.
"""

from ..models.shopping import DishCategory, DishServingSpec

# ---------------------------------------------------------------------------
# Per-person serving multipliers
#
# Values represent "servings per person" for each dish category.
# These are intentionally conservative — it is better to slightly over-buy
# for a dinner party than to run short.
#
# Multipliers are tuned for a typical dinner-party/BBQ context.
# ---------------------------------------------------------------------------

ADULT_SERVINGS_PER_PERSON: dict[DishCategory, float] = {
    DishCategory.MAIN_PROTEIN: 1.0,  # generous primary protein
    DishCategory.SECONDARY_PROTEIN: 0.5,  # supplementary protein
    DishCategory.STARCH_SIDE: 1.0,
    DishCategory.VEGETABLE_SIDE: 1.0,
    DishCategory.SALAD: 1.0,  # people go back for salad
    DishCategory.BREAD: 1.0,  # rolls/slices, not loaves
    DishCategory.DESSERT: 1.0,  # most people take dessert
    DishCategory.PASSED_APPETIZER: 2.0,  # pieces per person
    DishCategory.BEVERAGE_ALCOHOLIC: 1.5,  # drinks per person
    DishCategory.BEVERAGE_NONALCOHOLIC: 1.5,  # glasses per person
}

# Children eat roughly 60% of an adult portion for food, same for non-alcoholic
# beverages; alcoholic beverages are 0 for children.
CHILD_SERVINGS_PER_PERSON: dict[DishCategory, float] = {
    DishCategory.MAIN_PROTEIN: 0.75,
    DishCategory.SECONDARY_PROTEIN: 0.50,
    DishCategory.STARCH_SIDE: 0.75,
    DishCategory.VEGETABLE_SIDE: 0.50,
    DishCategory.SALAD: 0.50,
    DishCategory.BREAD: 1.0,
    DishCategory.DESSERT: 1.0,  # kids always want dessert
    DishCategory.PASSED_APPETIZER: 1.0,
    DishCategory.BEVERAGE_ALCOHOLIC: 0.0,  # no alcohol for children
    DishCategory.BEVERAGE_NONALCOHOLIC: 1.0,
}


def calculate_dish_serving_spec(
    dish_name: str,
    dish_category: DishCategory,
    adult_count: int,
    child_count: int,
) -> DishServingSpec:
    """
    Calculate how many servings of a single dish are needed.

    Args:
        dish_name: Human-readable dish name (e.g. "pasta carbonara")
        dish_category: The DishCategory this dish belongs to
        adult_count: Number of adult guests
        child_count: Number of child guests

    Returns:
        DishServingSpec with adult_servings, child_servings, and total_servings
        ready to be passed to ai_service.get_dish_ingredients()
    """
    adult_multiplier = ADULT_SERVINGS_PER_PERSON[dish_category]
    child_multiplier = CHILD_SERVINGS_PER_PERSON[dish_category]

    adult_servings = round(adult_count * adult_multiplier, 2)
    child_servings = round(child_count * child_multiplier, 2)

    return DishServingSpec(
        dish_name=dish_name,
        dish_category=dish_category,
        adult_servings=adult_servings,
        child_servings=child_servings,
        total_servings=round(adult_servings + child_servings, 2),
    )


def calculate_all_serving_specs(
    meal_plan: list[str],
    dish_categories: dict[str, DishCategory],
    adult_count: int,
    child_count: int,
) -> list[DishServingSpec]:
    """
    Calculate serving specs for all dishes in the meal plan.

    Args:
        meal_plan: List of dish names
        dish_categories: Mapping of dish name → DishCategory (from Gemini categorisation call)
        adult_count: Number of adult guests
        child_count: Number of child guests

    Returns:
        List of DishServingSpec, one per dish
    """
    specs = []
    for dish in meal_plan:
        category = dish_categories.get(dish)
        if category is None:
            # Default to starch_side if categorisation failed — better than crashing
            category = DishCategory.STARCH_SIDE
        specs.append(calculate_dish_serving_spec(dish, category, adult_count, child_count))
    return specs

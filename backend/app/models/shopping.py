from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel


class GroceryCategory(str, Enum):
    PROTEINS = "proteins"
    PRODUCE = "produce"
    DAIRY = "dairy"
    PANTRY = "pantry"
    BAKERY = "bakery"
    BEVERAGES = "beverages"
    FROZEN = "frozen"
    CONDIMENTS = "condiments"
    OTHER = "other"


class DishCategory(str, Enum):
    MAIN_PROTEIN = "main_protein"
    SECONDARY_PROTEIN = "secondary_protein"
    STARCH_SIDE = "starch_side"
    VEGETABLE_SIDE = "vegetable_side"
    SALAD = "salad"
    BREAD = "bread"
    DESSERT = "dessert"
    PASSED_APPETIZER = "passed_appetizer"
    BEVERAGE_ALCOHOLIC = "beverage_alcoholic"
    BEVERAGE_NONALCOHOLIC = "beverage_nonalcoholic"


class QuantityUnit(str, Enum):
    # Weight
    OZ = "oz"
    LBS = "lbs"
    GRAMS = "grams"
    KG = "kg"
    # Volume
    TSP = "tsp"
    TBSP = "tbsp"
    CUPS = "cups"
    FL_OZ = "fl oz"
    PINTS = "pints"
    QUARTS = "quarts"
    GALLONS = "gallons"
    LITERS = "liters"
    ML = "ml"
    # Count
    COUNT = "count"
    DOZEN = "dozen"
    # Bulk
    BUNCH = "bunch"
    HEAD = "head"
    CLOVES = "cloves"
    SLICES = "slices"
    CANS = "cans"
    PACKAGES = "packages"


class RecipeIngredient(BaseModel):
    """A single ingredient in a recipe."""

    name: str
    quantity: float
    unit: QuantityUnit
    grocery_category: GroceryCategory
    notes: Optional[str] = None


class DishServingSpec(BaseModel):
    """How many servings of a dish are needed, based on lookup table + guest counts."""

    dish_name: str
    dish_category: DishCategory
    adult_servings: float
    child_servings: float
    total_servings: float


class DishIngredients(BaseModel):
    """All ingredients required to make a specific dish at a specific serving count."""

    dish_name: str
    serving_spec: Optional[DishServingSpec] = None
    ingredients: List[RecipeIngredient]


class AggregatedIngredient(BaseModel):
    """A single ingredient after deduplication and summing across all dishes."""

    name: str
    total_quantity: float
    unit: QuantityUnit
    grocery_category: GroceryCategory
    appears_in: List[str]  # list of dish names


class ShoppingList(BaseModel):
    """Final output: all ingredients aggregated, deduplicated, and grouped."""

    meal_plan: List[str]
    adult_count: int
    child_count: int
    total_guests: int
    items: List[AggregatedIngredient]
    # Grouped by GroceryCategory for display
    grouped: Dict[str, List[AggregatedIngredient]] = {}

    def build_grouped(self) -> None:
        """Populate grouped dict from items list."""
        result: Dict[str, List[AggregatedIngredient]] = {}
        for item in self.items:
            key = item.grocery_category.value
            result.setdefault(key, []).append(item)
        self.grouped = result

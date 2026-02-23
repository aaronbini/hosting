from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    GOOGLE_SHEET = "google_sheet"
    GOOGLE_TASKS = "google_tasks"
    IN_CHAT = "in_chat"


class ExtractedDietaryRestriction(BaseModel):
    type: str  # "vegetarian", "vegan", "gluten-free", etc.
    count: int  # how many guests


class RecipeSourceType(str, Enum):
    AI_DEFAULT = "ai_default"
    USER_URL = "user_url"
    USER_UPLOAD = "user_upload"
    USER_DESCRIPTION = "user_description"


class RecipeType(str, Enum):
    FOOD = "food"
    DRINK = "drink"


class PreparationMethod(str, Enum):
    STORE_BOUGHT = "store_bought"
    HOMEMADE = "homemade"


class RecipeStatus(str, Enum):
    """Lifecycle status of a recipe in the meal plan."""

    PLACEHOLDER = "placeholder"  # Generic name like "main", "side" - needs real name
    NAMED = "named"  # Has specific name - needs ingredients
    COMPLETE = "complete"  # Has name + ingredients


class Recipe(BaseModel):
    """Single recipe in the meal plan with full lifecycle tracking."""

    # Identity
    name: str  # Current name (may be placeholder like "main")
    status: RecipeStatus = RecipeStatus.NAMED

    # Ingredients
    ingredients: List[dict] = Field(
        default_factory=list, description="RecipeIngredient dicts"
    )  # List[RecipeIngredient] as dicts
    source_type: RecipeSourceType = RecipeSourceType.AI_DEFAULT
    recipe_type: RecipeType = RecipeType.FOOD
    preparation_method: PreparationMethod = PreparationMethod.HOMEMADE
    url: Optional[str] = None
    description: Optional[str] = None
    servings: int = Field(4, description="Base servings for this recipe")

    # User interaction state
    awaiting_user_input: bool = Field(
        False, description="True when user promised to provide this recipe"
    )

    def needs_ingredients(self) -> bool:
        """Recipe has no ingredients yet."""
        return len(self.ingredients) == 0

    def is_complete_recipe(self) -> bool:
        """Recipe has name and ingredients (or is store-bought and therefore needs no ingredients)."""
        if self.preparation_method == PreparationMethod.STORE_BOUGHT:
            # Store-bought items don't need an ingredient list — just a real name
            return self.status != RecipeStatus.PLACEHOLDER
        return self.status == RecipeStatus.COMPLETE and len(self.ingredients) > 0


class MealPlan(BaseModel):
    """The full meal plan with lifecycle tracking."""

    recipes: List[Recipe] = Field(default_factory=list)
    confirmed: bool = False  # User confirmed the full menu

    @property
    def pending_user_recipes(self) -> List[Recipe]:
        """Recipes where user promised to provide ingredients."""
        return [r for r in self.recipes if r.awaiting_user_input]

    @property
    def is_complete(self) -> bool:
        """All recipes have names and ingredients."""
        return self.confirmed and all(r.is_complete_recipe() for r in self.recipes)

    def find_recipe(self, name: str) -> Optional[Recipe]:
        """Find recipe by name (case-insensitive)."""
        name_lower = name.lower()
        return next((r for r in self.recipes if r.name.lower() == name_lower), None)

    def add_recipe(self, recipe: Recipe) -> None:
        """Add recipe if not already present."""
        if not self.find_recipe(recipe.name):
            self.recipes.append(recipe)

    def remove_recipe(self, name: str) -> None:
        """Remove recipe by name."""
        self.recipes = [r for r in self.recipes if r.name.lower() != name.lower()]


class RecipeUpdate(BaseModel):
    """Delta update for a single recipe during extraction."""

    recipe_name: str  # Name or placeholder to update
    action: Literal["add", "remove", "update"] = "update"

    # Optional updates (only set fields being changed)
    new_name: Optional[str] = None  # Rename (e.g., "main" → "Spaghetti Carbonara")
    status: Optional[RecipeStatus] = None
    awaiting_user_input: Optional[bool] = None
    ingredients: Optional[List[dict]] = None  # RecipeIngredient dicts
    source_type: Optional[RecipeSourceType] = None
    recipe_type: Optional[RecipeType] = None
    preparation_method: Optional[PreparationMethod] = None
    url: Optional[str] = None
    description: Optional[str] = None
    servings: Optional[int] = None


class ExtractionResult(BaseModel):
    # --- Gathering stage fields ---
    event_type: Optional[str] = None
    event_date: Optional[str] = None
    adult_count: Optional[int] = None
    child_count: Optional[int] = None
    meal_type: Optional[str] = None
    event_duration_hours: Optional[float] = None
    dietary_restrictions: Optional[List[ExtractedDietaryRestriction]] = None
    cuisine_preferences: Optional[List[str]] = None
    beverages_preferences: Optional[str] = None
    available_equipment: Optional[List[str]] = None
    budget: Optional[float] = None
    formality_level: Optional[str] = None

    # --- Meal plan updates (unified field replacing all the string lists) ---
    recipe_updates: Optional[List[RecipeUpdate]] = None
    meal_plan_confirmed: Optional[bool] = None

    # --- Output selection stage fields ---
    output_formats: Optional[List[str]] = None

    # --- Always present ---
    answered_questions: List[str] = []


class DietaryRestriction(BaseModel):
    """Represents a dietary restriction with count of people"""

    type: str  # e.g., "vegetarian", "gluten-free", "vegan", "kosher", "halal"
    count: int
    notes: Optional[str] = None


# Define conversation questions by category
CONVERSATION_QUESTIONS = {
    "critical": [
        {"id": "event_type", "question": "What type of event/meal?"},
        {"id": "guest_count", "question": "Total guest count?"},
        {"id": "guest_breakdown", "question": "Adult vs child breakdown?"},
        {"id": "dietary", "question": "Dietary restrictions?"},
        {"id": "cuisine", "question": "Cuisine preference?"},
        {"id": "meal_plan", "question": "Specific dishes/beverages for the menu?"},
    ]
}


class EventPlanningData(BaseModel):
    """Main data model for event planning - populated throughout conversation"""

    # Event basics
    event_type: Optional[str] = Field(None, description="e.g., 'dinner-party', 'wedding', 'bbq'")
    event_date: Optional[str] = Field(None, description="ISO format: YYYY-MM-DD")

    # Guest information
    adult_count: Optional[int] = Field(None, ge=1)
    child_count: Optional[int] = Field(None, ge=0)
    total_guests: Optional[int] = Field(None, description="Computed field")

    # Meal details
    meal_type: Optional[str] = Field(
        None, description="e.g., 'breakfast', 'lunch', 'dinner', 'brunch'"
    )
    event_duration_hours: Optional[float] = Field(None, gt=0)

    # Dietary and preferences
    dietary_restrictions: List[DietaryRestriction] = Field(default_factory=list)
    cuisine_preferences: List[str] = Field(
        default_factory=list, description="e.g., ['Italian', 'Asian', 'seafood-friendly']"
    )
    beverages_preferences: Optional[str] = Field(
        None, description="e.g., 'beer, wine, non-alcoholic'"
    )
    foods_to_avoid: List[str] = Field(default_factory=list)

    # Cooking equipment and aesthetics
    available_equipment: List[str] = Field(
        default_factory=list, description="e.g., ['grill', 'oven', 'stovetop']"
    )
    formality_level: Optional[str] = Field(
        None, description="e.g., 'casual', 'semi-formal', 'formal'"
    )

    # Budget
    budget: Optional[float] = Field(None, description="Budget in USD", ge=0)
    budget_per_person: Optional[float] = Field(None, description="Computed field")

    # Meal plan — recipes with full lifecycle tracking
    meal_plan: MealPlan = Field(default_factory=MealPlan)

    # Transient: result of the last recipe URL extraction attempt.
    # Set before AI response is generated so the AI can surface failures loudly.
    # Cleared at the start of each apply_extraction call.
    last_url_extraction_result: Optional[dict] = Field(
        None, description="Success/failure of the most recent URL recipe extraction"
    )

    # Transient: ingredient lists just auto-generated for AI_DEFAULT dishes this turn.
    # Each entry: {"dish": str, "ingredients": list[dict]}.
    # Cleared at the start of each apply_extraction call.
    last_generated_recipes: Optional[List[Dict]] = Field(
        None, description="Newly generated default recipes to present to the user this turn"
    )

    # Output format selection — set during selecting_output stage
    output_formats: List[OutputFormat] = Field(
        default_factory=list, description="How the user wants to receive the shopping list"
    )

    # Conversation stage
    conversation_stage: str = Field(
        default="gathering",
        description=(
            "'gathering' = collecting event info + meal plan, "
            "'recipe_confirmation' = confirming ingredients per dish, "
            "'selecting_output' = user picks output format(s), "
            "'agent_running' = agent is executing"
        ),
    )

    # Question tracking - which specific questions have been answered
    answered_questions: Dict[str, bool] = Field(
        default_factory=lambda: {
            q["id"]: False for category in CONVERSATION_QUESTIONS.values() for q in category
        },
        description="Track which specific questions have been answered",
    )

    # Completion tracking
    is_complete: bool = False
    completion_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="0-1 score indicating conversation completeness"
    )
    progress: Dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed progress breakdown by category (critical, important, optional)",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "dinner-party",
                "event_date": "2024-03-15",
                "adult_count": 8,
                "child_count": 2,
                "formality_level": "semi-formal",
                "meal_type": "dinner",
                "dietary_restrictions": [
                    {"type": "vegetarian", "count": 2},
                    {"type": "gluten-free", "count": 1},
                ],
                "cuisine_preferences": ["Italian", "seafood-friendly"],
                "beverages_preferences": "wine and beer",
                "available_equipment": ["oven", "stove"],
                "budget": 300,
            }
        }

    def compute_derived_fields(self):
        """Calculate total_guests, budget_per_person, and completion_score"""
        self.total_guests = (self.adult_count or 0) + (self.child_count or 0)

        if self.budget and self.total_guests and self.total_guests > 0:
            self.budget_per_person = self.budget / self.total_guests

        self._compute_completion_score()

    def _compute_completion_score(self):
        """
        Completion scoring:
        - 35% from non-meal critical questions (event_type, guest_count, guest_breakdown, dietary, cuisine)
        - 65% from meal plan items, food weighted 2x over drinks

        Per meal item: name = 20%, ingredients = 80%.
        Store-bought items get full ingredient score automatically (no ingredients needed).

        is_complete requires all 6 critical questions + confirmed meal plan + no pending recipes.
        """
        NON_MEAL_QUESTION_IDS = {"event_type", "guest_count", "guest_breakdown", "dietary", "cuisine"}
        non_meal_answered = sum(
            1 for qid in NON_MEAL_QUESTION_IDS if self.answered_questions.get(qid, False)
        )
        non_meal_score = non_meal_answered / len(NON_MEAL_QUESTION_IDS)

        # Meal plan score: each recipe weighted by type (food=1.0, drink=0.5)
        recipes = self.meal_plan.recipes
        meal_plan_score = 0.0
        if recipes:
            total_weight = 0.0
            weighted_sum = 0.0
            for recipe in recipes:
                item_weight = 1.0 if recipe.recipe_type == RecipeType.FOOD else 0.5
                name_score = 0.0 if recipe.status == RecipeStatus.PLACEHOLDER else 1.0
                if recipe.preparation_method == PreparationMethod.STORE_BOUGHT:
                    ingredient_score = 1.0
                else:
                    ingredient_score = 1.0 if len(recipe.ingredients) > 0 else 0.0
                item_score = 0.2 * name_score + 0.8 * ingredient_score
                total_weight += item_weight
                weighted_sum += item_weight * item_score
            meal_plan_score = weighted_sum / total_weight

        self.completion_score = 0.35 * non_meal_score + 0.65 * meal_plan_score

        # is_complete: all 6 critical questions + confirmed meal plan + no pending recipes
        all_critical_answered = all(
            self.answered_questions.get(q["id"], False)
            for q in CONVERSATION_QUESTIONS["critical"]
        )
        has_recipes = len(recipes) > 0
        has_unresolved_recipes = len(self.meal_plan.pending_user_recipes) > 0

        self.is_complete = (
            all_critical_answered
            and has_recipes
            and self.meal_plan.confirmed
            and not has_unresolved_recipes
        )

        self.progress = {
            "overall_score": round(self.completion_score, 2),
            "non_meal": {
                "answered": non_meal_answered,
                "total": len(NON_MEAL_QUESTION_IDS),
                "percentage": round(non_meal_score * 100, 1),
            },
            "meal_plan": {
                "score": round(meal_plan_score, 2),
                "recipe_count": len(recipes),
            },
            "is_complete": self.is_complete,
        }

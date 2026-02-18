from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    GOOGLE_SHEET = "google_sheet"
    GOOGLE_KEEP = "google_keep"
    IN_CHAT = "in_chat"


class ExtractedDietaryRestriction(BaseModel):
    type: str  # "vegetarian", "vegan", "gluten-free", etc.
    count: int  # how many guests


class RecipeSourceType(str, Enum):
    AI_DEFAULT = "ai_default"
    USER_URL = "user_url"
    USER_UPLOAD = "user_upload"
    USER_DESCRIPTION = "user_description"


class RecipeSource(BaseModel):
    """Tracks how a recipe was sourced and whether it's been confirmed."""

    dish_name: str
    source_type: RecipeSourceType = RecipeSourceType.AI_DEFAULT
    url: Optional[str] = None
    file_path: Optional[str] = None
    description: Optional[str] = None
    extracted_ingredients: Optional[List[dict]] = None  # RecipeIngredient dicts
    confirmed: bool = False


class RecipeConfirmation(BaseModel):
    """Extraction result for a single dish during recipe_confirmation stage."""

    dish_name: str
    confirmed: Optional[bool] = None
    source_type: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None  # URL pasted by user for this dish's recipe


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

    # --- Meal plan delta fields (gathering stage) ---
    meal_plan_additions: Optional[List[str]] = None
    meal_plan_removals: Optional[List[str]] = None
    meal_plan_confirmed: Optional[bool] = None
    # Gathering: dishes where user says "I have my own recipe for X"
    recipe_promise_additions: Optional[List[str]] = None
    # Gathering: dishes whose recipe promise is now resolved (description given, or "use default")
    recipe_promise_resolutions: Optional[List[str]] = None

    # --- Recipe confirmation stage fields ---
    recipe_confirmations: Optional[List[RecipeConfirmation]] = None
    recipes_confirmed: Optional[bool] = None
    # Set when the user indicates they have a file to upload for a specific dish.
    # Cleared on the next message. Used to pre-select the dish in the upload UI.
    pending_upload_dish: Optional[str] = None

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

    # Meal plan — specific dishes agreed upon during gathering
    meal_plan: List[str] = Field(
        default_factory=list,
        description="Specific dishes for the event, e.g. ['pasta carbonara', 'Caesar salad', 'garlic bread']",
    )

    # Transient: dish the user intends to upload a file for (cleared after one turn)
    pending_upload_dish: Optional[str] = Field(
        None, description="Dish name the user indicated they have a file to upload for"
    )

    # Dishes user claimed to have their own recipe for during gathering.
    # Must be empty before gathering stage can complete.
    recipe_promises: List[str] = Field(
        default_factory=list,
        description="Dishes with pending user-provided recipe (not yet collected)",
    )

    # Transient: result of the last recipe URL extraction attempt.
    # Set before AI response is generated so the AI can surface failures loudly.
    # Cleared at the start of each apply_extraction call.
    last_url_extraction_result: Optional[dict] = Field(
        None, description="Success/failure of the most recent URL recipe extraction"
    )

    # Recipe sources — populated during recipe_confirmation stage
    recipe_sources: List[RecipeSource] = Field(
        default_factory=list, description="Per-dish recipe provenance and extracted ingredients"
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
        Granular completion scoring based on question categories.

        Questions are organized by importance:
        - Critical (100% of score): Must have for basic recommendations
        - Optional (0% of score): Nice to have

        Completion threshold:
        - is_complete = True when ALL critical questions answered AND meal plan is set
        """
        # Count answered questions by category
        critical_answered = sum(
            1
            for q in CONVERSATION_QUESTIONS["critical"]
            if self.answered_questions.get(q["id"], False)
        )
        critical_total = len(CONVERSATION_QUESTIONS["critical"])

        # Calculate weighted scores
        critical_score = (critical_answered / critical_total * 1.0) if critical_total > 0 else 0.0

        self.completion_score = critical_score

        # Ready for suggestions when:
        # 1. ALL critical questions answered, AND
        # 2. Meal plan is set
        all_critical_answered = critical_answered == critical_total

        has_meal_plan = len(self.meal_plan) > 0
        has_unresolved_promises = len(self.recipe_promises) > 0
        self.is_complete = all_critical_answered and has_meal_plan and not has_unresolved_promises

        # Store detailed progress for UI
        self.progress = {
            "overall_score": round(self.completion_score, 2),
            "critical": {
                "answered": critical_answered,
                "total": critical_total,
                "percentage": round(
                    (critical_answered / critical_total * 100) if critical_total > 0 else 0, 1
                ),
            },
            "is_complete": self.is_complete,
        }

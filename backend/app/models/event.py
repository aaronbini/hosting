from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    GOOGLE_SHEET = "google_sheet"
    GOOGLE_KEEP = "google_keep"
    IN_CHAT = "in_chat"


class ExtractedDietaryRestriction(BaseModel):
    type: str   # "vegetarian", "vegan", "gluten-free", etc.
    count: int  # how many guests

class ExtractionResult(BaseModel):
    event_type: Optional[str] = None         # "dinner-party", "bbq", "wedding-reception"
    event_date: Optional[str] = None         # ISO format: YYYY-MM-DD
    adult_count: Optional[int] = None
    child_count: Optional[int] = None
    meal_type: Optional[str] = None          # "breakfast", "lunch", "dinner", "brunch"
    event_duration_hours: Optional[float] = None
    dietary_restrictions: Optional[List[ExtractedDietaryRestriction]] = None
    cuisine_preferences: Optional[List[str]] = None
    beverages_preferences: Optional[str] = None
    available_equipment: Optional[List[str]] = None
    budget: Optional[float] = None
    formality_level: Optional[str] = None
    answered_questions: List[str] = []       # question IDs addressed: "event_type", "guest_count", etc.

class DietaryRestriction(BaseModel):
    """Represents a dietary restriction with count of people"""
    type: str  # e.g., "vegetarian", "gluten-free", "vegan", "kosher", "halal"
    count: int
    notes: Optional[str] = None


# Define conversation questions by category
CONVERSATION_QUESTIONS = {
    "critical": [
        {"id": "event_type", "question": "What type of event?"},
        {"id": "event_date", "question": "When is the event?"},
        {"id": "guest_count", "question": "Total guest count?"},
        {"id": "meal_type", "question": "Meal type (breakfast/lunch/dinner)?"},
        {"id": "cuisine", "question": "Cuisine preference?"},
    ],
    "important": [
        {"id": "guest_breakdown", "question": "Adult vs child breakdown?"},
        {"id": "dietary", "question": "Dietary restrictions?"},
        {"id": "beverages", "question": "Beverage preferences?"},
        {"id": "equipment", "question": "Available cooking equipment?"},
    ],
    "optional": [
        {"id": "budget", "question": "Budget?"},
        {"id": "formality", "question": "Formality level?"},
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
    meal_type: Optional[str] = Field(None, description="e.g., 'breakfast', 'lunch', 'dinner', 'brunch'")
    event_duration_hours: Optional[float] = Field(None, gt=0)
    
    # Dietary and preferences
    dietary_restrictions: List[DietaryRestriction] = Field(default_factory=list)
    cuisine_preferences: List[str] = Field(default_factory=list, description="e.g., ['Italian', 'Asian', 'seafood-friendly']")
    beverages_preferences: Optional[str] = Field(None, description="e.g., 'beer, wine, non-alcoholic'")
    foods_to_avoid: List[str] = Field(default_factory=list)
    
    # Cooking equipment and aesthetics
    available_equipment: List[str] = Field(default_factory=list, description="e.g., ['grill', 'oven', 'stovetop']")
    formality_level: Optional[str] = Field(None, description="e.g., 'casual', 'semi-formal', 'formal'")
    
    # Budget
    budget: Optional[float] = Field(None, description="Budget in USD", ge=0)
    budget_per_person: Optional[float] = Field(None, description="Computed field")
    
    # Meal plan — specific dishes agreed upon during gathering
    meal_plan: List[str] = Field(
        default_factory=list,
        description="Specific dishes for the event, e.g. ['pasta carbonara', 'Caesar salad', 'garlic bread']"
    )

    # Output format selection — set during selecting_output stage
    output_formats: List[OutputFormat] = Field(
        default_factory=list,
        description="How the user wants to receive the shopping list"
    )

    # Conversation stage
    conversation_stage: str = Field(
        default="gathering",
        description=(
            "'gathering' = collecting event info + meal plan, "
            "'recipe_confirmation' = confirming ingredients per dish, "
            "'selecting_output' = user picks output format(s), "
            "'agent_running' = agent is executing"
        )
    )
    
    # Question tracking - which specific questions have been answered
    answered_questions: Dict[str, bool] = Field(
        default_factory=lambda: {
            q["id"]: False 
            for category in CONVERSATION_QUESTIONS.values() 
            for q in category
        },
        description="Track which specific questions have been answered"
    )
    
    # Completion tracking
    is_complete: bool = False
    completion_score: float = Field(default=0.0, ge=0.0, le=1.0, description="0-1 score indicating conversation completeness")
    progress: Dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed progress breakdown by category (critical, important, optional)"
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
                    {"type": "gluten-free", "count": 1}
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
        - Critical (40% of score): Must have for basic recommendations
        - Important (40% of score): Highly recommended
        - Optional (20% of score): Nice to have
        
        Completion threshold:
        - is_complete = True when ALL critical questions answered AND >= 50% important answered
        """
        # Count answered questions by category
        critical_answered = sum(
            1 for q in CONVERSATION_QUESTIONS["critical"]
            if self.answered_questions.get(q["id"], False)
        )
        critical_total = len(CONVERSATION_QUESTIONS["critical"])
        
        important_answered = sum(
            1 for q in CONVERSATION_QUESTIONS["important"]
            if self.answered_questions.get(q["id"], False)
        )
        important_total = len(CONVERSATION_QUESTIONS["important"])
        
        optional_answered = sum(
            1 for q in CONVERSATION_QUESTIONS["optional"]
            if self.answered_questions.get(q["id"], False)
        )
        optional_total = len(CONVERSATION_QUESTIONS["optional"])
        
        # Calculate weighted scores
        critical_score = (critical_answered / critical_total * 0.40) if critical_total > 0 else 0.0
        important_score = (important_answered / important_total * 0.40) if important_total > 0 else 0.0
        optional_score = (optional_answered / optional_total * 0.20) if optional_total > 0 else 0.0
        
        self.completion_score = critical_score + important_score + optional_score
        
        # Ready for suggestions when:
        # 1. ALL critical questions answered, AND
        # 2. At least 50% of important questions answered
        all_critical_answered = critical_answered == critical_total
        important_threshold_met = important_answered >= (important_total * 0.5)
        
        self.is_complete = all_critical_answered and important_threshold_met
        
        # Store detailed progress for UI
        self.progress = {
            "overall_score": round(self.completion_score, 2),
            "critical": {
                "answered": critical_answered,
                "total": critical_total,
                "percentage": round((critical_answered / critical_total * 100) if critical_total > 0 else 0, 1)
            },
            "important": {
                "answered": important_answered,
                "total": important_total,
                "percentage": round((important_answered / important_total * 100) if important_total > 0 else 0, 1)
            },
            "optional": {
                "answered": optional_answered,
                "total": optional_total,
                "percentage": round((optional_answered / optional_total * 100) if optional_total > 0 else 0, 1)
            },
            "is_complete": self.is_complete,
        }

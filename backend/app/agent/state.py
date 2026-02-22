"""
AgentState — the single object that flows through every agent step.

Design notes:
- Pydantic model so fields are validated and serialisable.
- On LangGraph migration: replace BaseModel with TypedDict, all fields become
  Optional with None defaults. The step function signatures stay identical.
- All fields default to None / empty so a fresh AgentState can be created
  with only the required inputs (event_data, output_formats).
"""

from typing import Optional

from pydantic import BaseModel, Field

from app.models.event import EventPlanningData, OutputFormat
from app.models.shopping import DishIngredients, DishServingSpec, ShoppingList


class AgentStage(str):
    """String constants for the agent's internal execution stage.

    Not an Enum so that LangGraph edge conditions can do simple string
    comparisons without importing the class.
    """

    IDLE = "idle"
    CALCULATING_QUANTITIES = "calculating_quantities"
    GETTING_INGREDIENTS = "getting_ingredients"
    AGGREGATING = "aggregating"
    AWAITING_REVIEW = "awaiting_review"
    APPLYING_CORRECTIONS = "applying_corrections"
    DELIVERING = "delivering"
    COMPLETE = "complete"
    ERROR = "error"


class AgentState(BaseModel):
    """
    Single state object passed between all agent steps.

    Steps take AgentState and return AgentState — they never write to
    WebSocket or any external resource directly. All I/O is handled by
    runner.py, which reads the state after each step and decides what to
    send to the client.

    LangGraph migration note: each field here maps 1-to-1 to a key in the
    TypedDict that LangGraph expects. The only structural change needed is
    replacing `class AgentState(BaseModel)` with
    `class AgentState(TypedDict, total=False)`.
    """

    # --- Inputs (required before agent starts) ---
    event_data: EventPlanningData
    output_formats: list[OutputFormat]

    # --- Internal execution state ---
    stage: str = AgentStage.IDLE
    error: Optional[str] = None

    # --- Step 1 output: serving specs per dish ---
    serving_specs: list[DishServingSpec] = Field(default_factory=list)

    # --- Step 2 output: ingredients per dish ---
    dish_ingredients: list[DishIngredients] = Field(default_factory=list)

    # --- Step 3 output: aggregated shopping list ---
    shopping_list: Optional[ShoppingList] = None

    # --- Step 5 input: corrections from human review ---
    # Free-text corrections the user typed during the review checkpoint.
    # apply_corrections() reads this and produces a revised shopping_list.
    user_corrections: Optional[str] = None

    # --- Step 7 outputs ---
    google_sheet_url: Optional[str] = None
    google_tasks_url: Optional[str] = None
    formatted_chat_output: Optional[str] = None

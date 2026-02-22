"""
Shared fixtures for all test modules.
"""

import pytest

from app.agent.state import AgentState
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
    GroceryCategory,
    QuantityUnit,
    ShoppingList,
)
from app.services.session_manager import SessionData


# ---------------------------------------------------------------------------
# Session / EventPlanningData fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_session() -> SessionData:
    """A brand-new session in the gathering stage."""
    return SessionData("test-session")


@pytest.fixture
def fully_answered_event_data() -> EventPlanningData:
    """EventPlanningData with all critical questions answered."""
    data = EventPlanningData(event_type="dinner-party", adult_count=8, child_count=0)
    for q in data.answered_questions:
        data.answered_questions[q] = True
    return data


@pytest.fixture
def complete_meal_plan() -> MealPlan:
    """A confirmed meal plan where every recipe is fully complete."""
    plan = MealPlan()
    plan.add_recipe(
        Recipe(
            name="Pasta Carbonara",
            status=RecipeStatus.COMPLETE,
            ingredients=[
                {"name": "pasta", "quantity": 1.0, "unit": "lbs", "grocery_category": "pantry"}
            ],
        )
    )
    plan.confirmed = True
    return plan


# ---------------------------------------------------------------------------
# ShoppingList / AgentState fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_shopping_list() -> ShoppingList:
    """A small shopping list covering multiple grocery categories."""
    items = [
        AggregatedIngredient(
            name="pasta",
            total_quantity=2.5,
            unit=QuantityUnit.LBS,
            grocery_category=GroceryCategory.PANTRY,
            appears_in=["Pasta Carbonara"],
        ),
        AggregatedIngredient(
            name="eggs",
            total_quantity=12.0,
            unit=QuantityUnit.COUNT,
            grocery_category=GroceryCategory.DAIRY,
            appears_in=["Pasta Carbonara"],
        ),
        AggregatedIngredient(
            name="cherry tomatoes",
            total_quantity=1.0,
            unit=QuantityUnit.LBS,
            grocery_category=GroceryCategory.PRODUCE,
            appears_in=["Salad"],
        ),
    ]
    sl = ShoppingList(
        meal_plan=["Pasta Carbonara", "Salad"],
        adult_count=8,
        child_count=0,
        total_guests=8,
        items=items,
    )
    sl.build_grouped()
    return sl


@pytest.fixture
def sample_agent_state(sample_shopping_list: ShoppingList) -> AgentState:
    """An AgentState ready for the delivery step."""
    return AgentState(
        event_data=EventPlanningData(adult_count=8, child_count=0),
        output_formats=[OutputFormat.IN_CHAT],
        shopping_list=sample_shopping_list,
    )

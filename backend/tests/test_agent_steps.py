"""
Tests for pure agent step functions in agent/steps.py.

format_chat_output has no external dependencies — tested directly.
Steps that call GeminiService are tested with a mock.
"""

import pytest

from app.agent.state import AgentState, AgentStage
from app.agent.steps import format_chat_output
from app.models.event import EventPlanningData, OutputFormat
from app.models.shopping import (
    AggregatedIngredient,
    GroceryCategory,
    QuantityUnit,
    ShoppingList,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_items(**categories):
    """Build a list of AggregatedIngredients. Pass category=[(name, qty, unit), ...] pairs."""
    items = []
    for grocery_cat, entries in categories.items():
        cat = GroceryCategory(grocery_cat)
        for name, qty, unit in entries:
            items.append(
                AggregatedIngredient(
                    name=name,
                    total_quantity=qty,
                    unit=QuantityUnit(unit),
                    grocery_category=cat,
                    appears_in=["Test Dish"],
                )
            )
    return items


def _make_state(items=None) -> AgentState:
    sl = None
    if items is not None:
        sl = ShoppingList(
            meal_plan=["Pasta Carbonara"],
            adult_count=8,
            child_count=0,
            total_guests=8,
            items=items,
        )
        sl.build_grouped()
    return AgentState(
        event_data=EventPlanningData(adult_count=8),
        output_formats=[OutputFormat.IN_CHAT],
        shopping_list=sl,
    )


# ---------------------------------------------------------------------------
# format_chat_output
# ---------------------------------------------------------------------------


class TestFormatChatOutput:
    async def test_contains_shopping_list_header(self, sample_agent_state):
        result = await format_chat_output(sample_agent_state)
        assert "## Shopping List" in result.formatted_chat_output

    async def test_contains_item_names(self, sample_agent_state):
        result = await format_chat_output(sample_agent_state)
        output = result.formatted_chat_output.lower()
        assert "pasta" in output
        assert "eggs" in output

    async def test_quantities_rounded_up(self):
        items = _make_items(pantry=[("olive oil", 2.1, "cups")])
        state = _make_state(items)
        result = await format_chat_output(state)
        # 2.1 should ceil to 3
        assert " 3 " in result.formatted_chat_output

    async def test_whole_quantities_unchanged(self):
        items = _make_items(pantry=[("pasta", 2.0, "lbs")])
        state = _make_state(items)
        result = await format_chat_output(state)
        assert " 2 " in result.formatted_chat_output

    async def test_exactly_at_integer_not_rounded_up(self):
        # 4.0 should stay as 4 (math.ceil(4.0) == 4)
        items = _make_items(dairy=[("eggs", 4.0, "count")])
        state = _make_state(items)
        result = await format_chat_output(state)
        assert " 4 " in result.formatted_chat_output

    async def test_no_shopping_list_returns_message(self):
        state = _make_state(None)
        result = await format_chat_output(state)
        assert "No shopping list available" in result.formatted_chat_output

    async def test_stage_set_to_delivering(self, sample_agent_state):
        result = await format_chat_output(sample_agent_state)
        assert result.stage == AgentStage.DELIVERING

    async def test_categories_appear_as_headers(self):
        items = _make_items(
            pantry=[("pasta", 1.0, "lbs")],
            dairy=[("eggs", 6.0, "count")],
        )
        state = _make_state(items)
        result = await format_chat_output(state)
        output = result.formatted_chat_output
        # Category names appear in bold headers
        assert "**Pantry**" in output
        assert "**Dairy**" in output

    async def test_empty_shopping_list_no_items(self):
        state = _make_state([])  # empty list, not None
        result = await format_chat_output(state)
        # Should produce a header but no items
        assert "## Shopping List" in result.formatted_chat_output

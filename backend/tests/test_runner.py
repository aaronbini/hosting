"""
Tests for agent/runner.py — run_agent() orchestration.

The WebSocket and all step functions are mocked so no real I/O occurs.
Covers: step sequencing, review loop variants, excluded items, cached
shopping list, output format routing, delivery failure recovery, and
fatal error handling.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.runner import run_agent
from app.agent.state import AgentStage, AgentState
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


def _make_websocket(approve_immediately: bool = True) -> AsyncMock:
    """Return a mock WebSocket. Defaults to approving the review immediately."""
    ws = AsyncMock()
    if approve_immediately:
        ws.receive_json.return_value = {"type": "approve"}
    return ws


def _make_shopping_list() -> ShoppingList:
    items = [
        AggregatedIngredient(
            name="pasta",
            total_quantity=2.0,
            unit=QuantityUnit.LBS,
            grocery_category=GroceryCategory.PANTRY,
            appears_in=["Pasta Carbonara"],
        )
    ]
    sl = ShoppingList(
        meal_plan=["Pasta Carbonara"],
        adult_count=8,
        child_count=0,
        total_guests=8,
        items=items,
    )
    sl.build_grouped()
    return sl


def _make_event_data(adult_count: int = 8, child_count: int = 0) -> EventPlanningData:
    return EventPlanningData(adult_count=adult_count, child_count=child_count)


def _passthrough(state, *args, **kwargs):
    """Step side_effect that returns state unchanged."""
    return state


def _inject_shopping_list(state, *args, **kwargs):
    """Step side_effect that sets a shopping list on state."""
    state.shopping_list = _make_shopping_list()
    return state


def _sent_types(ws: AsyncMock) -> list[str]:
    """Return the list of WebSocket message types sent."""
    return [json.loads(c.args[0])["type"] for c in ws.send_text.call_args_list]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRunAgentHappyPath:
    async def test_sends_agent_complete_on_success(self):
        ws = _make_websocket()
        event_data = _make_event_data()

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            await run_agent(ws, event_data, [OutputFormat.IN_CHAT], AsyncMock())

        assert "agent_complete" in _sent_types(ws)

    async def test_sends_progress_then_review_then_complete(self):
        """Message sequence must include progress → review → complete."""
        ws = _make_websocket()
        event_data = _make_event_data()

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            await run_agent(ws, event_data, [OutputFormat.IN_CHAT], AsyncMock())

        types = _sent_types(ws)
        # At least 3 progress messages (one per computation step + delivery)
        assert types.count("agent_progress") >= 3
        assert "agent_review" in types
        assert "agent_complete" in types
        # review comes before complete
        assert types.index("agent_review") < types.index("agent_complete")

    async def test_final_state_stage_is_complete(self):
        ws = _make_websocket()
        event_data = _make_event_data()

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            state = await run_agent(ws, event_data, [OutputFormat.IN_CHAT], AsyncMock())

        assert state.stage == AgentStage.COMPLETE

    async def test_backfills_guest_counts_on_shopping_list(self):
        """adult_count / child_count from event_data must be written to shopping_list."""
        ws = _make_websocket()
        event_data = _make_event_data(adult_count=6, child_count=3)

        # aggregate returns a list with zeroed counts (as the real function does)
        bare_list = ShoppingList(
            meal_plan=["Pasta"],
            adult_count=0,
            child_count=0,
            total_guests=0,
            items=[],
        )

        async def inject_bare(state, *_):
            state.shopping_list = bare_list
            return state

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=inject_bare)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            state = await run_agent(ws, event_data, [OutputFormat.IN_CHAT], AsyncMock())

        assert state.shopping_list.adult_count == 6
        assert state.shopping_list.child_count == 3
        # total_guests = event_data.total_guests or 0; total_guests isn't computed
        # unless compute_derived_fields() is called, so it remains 0 here
        assert state.shopping_list.total_guests == 0


# ---------------------------------------------------------------------------
# Review loop
# ---------------------------------------------------------------------------


class TestRunAgentReviewLoop:
    async def test_explicit_approve_breaks_loop_immediately(self):
        """{"type": "approve"} should exit after one receive_json."""
        ws = _make_websocket()
        ws.receive_json.return_value = {"type": "approve"}
        apply_mock = AsyncMock()

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.apply_corrections", apply_mock),
        ):
            await run_agent(ws, event_data := _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        assert ws.receive_json.call_count == 1
        apply_mock.assert_not_called()

    async def test_empty_message_treated_as_approval(self):
        """Empty data string should break the loop without apply_corrections."""
        ws = _make_websocket()
        ws.receive_json.return_value = {"type": "message", "data": ""}
        apply_mock = AsyncMock()

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.apply_corrections", apply_mock),
        ):
            await run_agent(_make_event_data(), _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        apply_mock.assert_not_called()

    async def test_corrections_then_approval(self):
        """Correction message → apply_corrections → re-present → approve."""
        ws = _make_websocket()
        ws.receive_json.side_effect = [
            {"type": "message", "data": "remove the olive oil"},
            {"type": "approve"},
        ]
        apply_mock = AsyncMock(side_effect=_passthrough)

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.apply_corrections", apply_mock),
        ):
            await run_agent(ws, _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        assert ws.receive_json.call_count == 2
        apply_mock.assert_called_once()
        # agent_review sent twice: before each receive_json
        assert _sent_types(ws).count("agent_review") == 2

    async def test_excluded_items_removed_from_shopping_list(self):
        """Items listed in excluded_items are filtered before delivery."""
        ws = _make_websocket()
        ws.receive_json.return_value = {
            "type": "approve",
            "excluded_items": ["pasta"],
        }

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            state = await run_agent(ws, _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        remaining_names = [item.name for item in state.shopping_list.items]
        assert "pasta" not in remaining_names

    async def test_excluded_items_case_insensitive(self):
        ws = _make_websocket()
        ws.receive_json.return_value = {
            "type": "approve",
            "excluded_items": ["PASTA"],  # uppercase
        }

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            state = await run_agent(ws, _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        remaining_names = [item.name for item in state.shopping_list.items]
        assert "pasta" not in remaining_names


# ---------------------------------------------------------------------------
# Cached shopping list (existing_state)
# ---------------------------------------------------------------------------


class TestRunAgentCachedShoppingList:
    async def test_skips_calculation_steps_when_cached(self):
        """When existing_state has a shopping list, steps 1-3 are skipped."""
        event_data = _make_event_data()
        existing_state = AgentState(
            event_data=event_data,
            output_formats=[OutputFormat.IN_CHAT],
            shopping_list=_make_shopping_list(),
        )
        calc = AsyncMock()
        get_ing = AsyncMock()
        agg = AsyncMock()

        with (
            patch("app.agent.runner.calculate_quantities", calc),
            patch("app.agent.runner.get_all_dish_ingredients", get_ing),
            patch("app.agent.runner.aggregate_ingredients", agg),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            await run_agent(
                _make_websocket(),
                event_data,
                [OutputFormat.IN_CHAT],
                AsyncMock(),
                existing_state=existing_state,
            )

        calc.assert_not_called()
        get_ing.assert_not_called()
        agg.assert_not_called()

    async def test_skips_review_loop_when_cached(self):
        """With existing_state, no review loop — receive_json is never called."""
        event_data = _make_event_data()
        ws = _make_websocket()
        existing_state = AgentState(
            event_data=event_data,
            output_formats=[OutputFormat.IN_CHAT],
            shopping_list=_make_shopping_list(),
        )

        with (
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            await run_agent(
                ws,
                event_data,
                [OutputFormat.IN_CHAT],
                AsyncMock(),
                existing_state=existing_state,
            )

        ws.receive_json.assert_not_called()

    async def test_output_formats_updated_from_new_request(self):
        """The new output_formats list replaces the one on existing_state."""
        event_data = _make_event_data()
        existing_state = AgentState(
            event_data=event_data,
            output_formats=[OutputFormat.IN_CHAT],
            shopping_list=_make_shopping_list(),
        )
        tasks_mock = AsyncMock(side_effect=_passthrough)

        with (
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.create_google_tasks", tasks_mock),
        ):
            await run_agent(
                _make_websocket(),
                event_data,
                [OutputFormat.GOOGLE_TASKS],  # new format
                AsyncMock(),
                existing_state=existing_state,
                tasks_service=MagicMock(),
            )

        tasks_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Output format routing
# ---------------------------------------------------------------------------


class TestRunAgentOutputFormats:
    async def test_google_tasks_called_when_format_selected(self):
        ws = _make_websocket()
        tasks_mock = AsyncMock(side_effect=_passthrough)

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.create_google_tasks", tasks_mock),
        ):
            await run_agent(
                ws,
                _make_event_data(),
                [OutputFormat.GOOGLE_TASKS],
                AsyncMock(),
                tasks_service=MagicMock(),
            )

        tasks_mock.assert_called_once()

    async def test_google_tasks_not_called_when_format_not_selected(self):
        ws = _make_websocket()
        tasks_mock = AsyncMock(side_effect=_passthrough)

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.create_google_tasks", tasks_mock),
        ):
            await run_agent(ws, _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        tasks_mock.assert_not_called()

    async def test_google_sheet_called_when_format_selected(self):
        ws = _make_websocket()
        sheet_mock = AsyncMock(side_effect=_passthrough)

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.create_google_sheet", sheet_mock),
        ):
            await run_agent(
                ws,
                _make_event_data(),
                [OutputFormat.GOOGLE_SHEET],
                AsyncMock(),
                sheets_service=MagicMock(),
            )

        sheet_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestRunAgentErrorHandling:
    async def test_delivery_failure_does_not_crash_agent(self):
        """If one delivery task raises, agent_complete is still sent."""
        ws = _make_websocket()

        async def failing_format(state):
            raise RuntimeError("format step exploded")

        with (
            patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.get_all_dish_ingredients", AsyncMock(side_effect=_passthrough)),
            patch("app.agent.runner.aggregate_ingredients", AsyncMock(side_effect=_inject_shopping_list)),
            patch("app.agent.runner.format_chat_output", AsyncMock(side_effect=failing_format)),
            patch("app.agent.runner.generate_recipes", AsyncMock(side_effect=_passthrough)),
        ):
            state = await run_agent(ws, _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        types = _sent_types(ws)
        assert "agent_complete" in types
        assert "agent_error" not in types

    async def test_fatal_step_error_sends_agent_error(self):
        """If a pre-delivery step raises, agent_error is sent and stage=ERROR."""
        ws = _make_websocket()

        async def crash(state, *_):
            raise RuntimeError("catastrophic failure")

        with patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=crash)):
            state = await run_agent(ws, _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        assert state.stage == AgentStage.ERROR
        assert state.error is not None
        types = _sent_types(ws)
        assert "agent_error" in types
        assert "agent_complete" not in types

    async def test_fatal_error_records_error_message(self):
        """state.error should contain the exception message."""
        ws = _make_websocket()

        async def crash(state, *_):
            raise RuntimeError("disk full")

        with patch("app.agent.runner.calculate_quantities", AsyncMock(side_effect=crash)):
            state = await run_agent(ws, _make_event_data(), [OutputFormat.IN_CHAT], AsyncMock())

        assert "disk full" in state.error

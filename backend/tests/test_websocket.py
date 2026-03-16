"""
WebSocket integration tests — stage transition flows.

Uses Starlette's TestClient with DB and AI service mocked.
No real Gemini calls, no real database needed.

Each test exercises a specific message type or stage transition in the
/ws/chat/{session_id} handler in app/main.py.
"""

import uuid
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.agent.state import AgentState
from app.main import app
from app.models.event import (
    EventPlanningData,
    ExtractionResult,
    OutputFormat,
    PreparationMethod,
    Recipe,
    RecipeStatus,
)
from app.services.session_manager import SessionData

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FAKE_USER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
FAKE_SESSION_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
FAKE_TOKEN = "fake.jwt.token"
WS_PATH = f"/ws/chat/{FAKE_SESSION_ID}"
COOKIES = {"access_token": FAKE_TOKEN}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row():
    """Fake DB row whose user_id matches FAKE_USER_ID."""
    row = MagicMock()
    row.user_id = uuid.UUID(FAKE_USER_ID)
    return row


def _make_session(stage: str = "gathering", recipes: list | None = None) -> SessionData:
    session = SessionData(FAKE_SESSION_ID)
    session.event_data.conversation_stage = stage
    if recipes:
        for r in recipes:
            session.event_data.meal_plan.add_recipe(r)
    return session


@asynccontextmanager
async def _fake_session_factory():
    """Stand-in for async_session_factory — no real DB connection needed."""
    yield MagicMock()


def _mock_ai_svc(extraction: ExtractionResult | None = None) -> MagicMock:
    """Minimal mock GeminiService for the WebSocket handler."""
    svc = MagicMock()
    svc.extract_event_data = AsyncMock(return_value=extraction or ExtractionResult())

    async def _stream(*_args, **_kwargs):
        yield "Hi!"

    svc.generate_response_stream = MagicMock(side_effect=_stream)
    return svc


def _collect(ws, *, stop_on: tuple[str, ...]) -> list[dict]:
    """Receive JSON messages from ws until one of the stop_on types is seen."""
    msgs: list[dict] = []
    while True:
        msg = ws.receive_json()
        msgs.append(msg)
        if msg.get("type") in stop_on:
            break
    return msgs


def _types(msgs: list[dict]) -> list[str]:
    return [m["type"] for m in msgs]


@contextmanager
def _ws_patched(session: SessionData, ai_svc: MagicMock | None = None):
    """
    Activate all patches required for the WS handler to run against in-memory
    fakes rather than a real DB or Gemini API. Yields (TestClient, db_mgr_mock).
    """
    db_mgr = MagicMock()
    db_mgr.get_session_row = AsyncMock(return_value=_make_row())
    # Always return the SAME session object so in-place mutations persist
    # across the per-message reload that happens at the top of the while loop.
    db_mgr._row_to_session_data = MagicMock(return_value=session)
    db_mgr.save_session = AsyncMock()

    patches = [
        patch("app.main.decode_access_token_raw", return_value=FAKE_USER_ID),
        patch("app.main.async_session_factory", _fake_session_factory),
        patch("app.main.db_session_manager", db_mgr),
        patch("app.main.ai_service", ai_svc or _mock_ai_svc()),
        patch("app.main._auto_generate_recipes", AsyncMock(return_value=False)),
    ]
    for p in patches:
        p.start()
    try:
        with TestClient(app) as client:
            yield client, db_mgr
    finally:
        for p in reversed(patches):
            p.stop()


# ===========================================================================
# Test classes
# ===========================================================================


class TestConfirmMenu:
    """
    confirm_menu message — transitions gathering → recipe_confirmation,
    auto-generates ingredients, and sends stream + optional recipe_confirm_request.
    """

    def test_transitions_stage_to_recipe_confirmation(self):
        session = _make_session(
            stage="gathering",
            recipes=[Recipe(name="Pasta", preparation_method=PreparationMethod.HOMEMADE)],
        )
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "confirm_menu", "data": []})
                # Consume until the handler finishes this turn
                _collect(ws, stop_on=("recipe_confirm_request", "stream_end"))

        assert session.event_data.conversation_stage == "recipe_confirmation"

    def test_sends_stream_start_chunk_end(self):
        session = _make_session(
            stage="gathering",
            recipes=[Recipe(name="Pasta", preparation_method=PreparationMethod.HOMEMADE)],
        )
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "confirm_menu", "data": []})
                msgs = _collect(ws, stop_on=("recipe_confirm_request", "stream_end"))

        types = _types(msgs)
        assert "stream_start" in types
        assert "stream_chunk" in types
        assert "stream_end" in types

    def test_sends_recipe_confirm_request_when_no_own_recipes(self):
        """
        When the user doesn't claim any own recipes (data=[]), all are
        AI-generated and recipe_confirm_request is sent immediately.
        """
        session = _make_session(
            stage="gathering",
            recipes=[Recipe(name="Pasta", preparation_method=PreparationMethod.HOMEMADE)],
        )
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "confirm_menu", "data": []})
                msgs = _collect(ws, stop_on=("recipe_confirm_request",))

        assert any(m["type"] == "recipe_confirm_request" for m in msgs)

    def test_no_recipe_confirm_request_when_user_provides_own_recipes(self):
        """
        When the user claims own recipes (n_own > 0, n_ai = 0), the server
        should NOT send recipe_confirm_request (user must upload first).
        """
        session = _make_session(
            stage="gathering",
            recipes=[Recipe(name="Pasta", preparation_method=PreparationMethod.HOMEMADE)],
        )
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                # "Pasta" is in the own-recipe list → awaiting_user_input=True
                ws.send_json({"type": "confirm_menu", "data": ["Pasta"]})
                msgs = _collect(ws, stop_on=("stream_end",))

        assert not any(m["type"] == "recipe_confirm_request" for m in msgs)

    def test_auto_generate_recipes_called(self):
        """_auto_generate_recipes should be invoked once on confirm_menu."""
        session = _make_session(
            stage="gathering",
            recipes=[Recipe(name="Pasta", preparation_method=PreparationMethod.HOMEMADE)],
        )
        auto_gen_mock = AsyncMock(return_value=False)
        with _ws_patched(session, ai_svc=_mock_ai_svc()) as (client, _):
            with patch("app.main._auto_generate_recipes", auto_gen_mock):
                with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                    ws.send_json({"type": "confirm_menu", "data": []})
                    _collect(ws, stop_on=("recipe_confirm_request", "stream_end"))

        assert auto_gen_mock.called


class TestConfirmRecipes:
    """
    confirm_recipes message — transitions recipe_confirmation → selecting_output
    and sends the output_selection card.
    """

    def test_advances_to_selecting_output(self):
        session = _make_session(stage="recipe_confirmation")
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "confirm_recipes", "data": None})
                _collect(ws, stop_on=("output_selection",))

        assert session.event_data.conversation_stage == "selecting_output"

    def test_sends_event_data_update_and_output_selection(self):
        session = _make_session(stage="recipe_confirmation")
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "confirm_recipes", "data": None})
                msgs = _collect(ws, stop_on=("output_selection",))

        types = _types(msgs)
        assert "event_data_update" in types
        assert "output_selection" in types

    def test_output_selection_card_has_three_options(self):
        session = _make_session(stage="recipe_confirmation")
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "confirm_recipes", "data": None})
                msgs = _collect(ws, stop_on=("output_selection",))

        card = next(m for m in msgs if m["type"] == "output_selection")
        values = [opt["value"] for opt in card["options"]]
        assert "google_tasks" in values
        assert "in_chat" in values


class TestSelectOutputs:
    """
    select_outputs message — sets output_formats, triggers run_agent,
    and sends event_data_update before and after the agent.
    """

    def test_triggers_run_agent(self):
        session = _make_session(stage="selecting_output")
        run_agent_mock = AsyncMock(
            return_value=AgentState(
                event_data=EventPlanningData(),
                output_formats=[OutputFormat.IN_CHAT],
            )
        )
        with _ws_patched(session) as (client, _):
            with patch("app.main.run_agent", run_agent_mock):
                with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                    ws.send_json({"type": "select_outputs", "data": ["in_chat"]})
                    # Two event_data_update messages: before + after run_agent
                    ws.receive_json()
                    ws.receive_json()

        assert run_agent_mock.called

    def test_stage_becomes_complete_after_agent(self):
        session = _make_session(stage="selecting_output")
        run_agent_mock = AsyncMock(
            return_value=AgentState(
                event_data=EventPlanningData(),
                output_formats=[OutputFormat.IN_CHAT],
            )
        )
        with _ws_patched(session) as (client, _):
            with patch("app.main.run_agent", run_agent_mock):
                with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                    ws.send_json({"type": "select_outputs", "data": ["in_chat"]})
                    ws.receive_json()
                    ws.receive_json()

        assert session.event_data.conversation_stage == "complete"

    def test_sends_event_data_update_before_and_after_agent(self):
        session = _make_session(stage="selecting_output")
        run_agent_mock = AsyncMock(
            return_value=AgentState(
                event_data=EventPlanningData(),
                output_formats=[OutputFormat.IN_CHAT],
            )
        )
        with _ws_patched(session) as (client, _):
            with patch("app.main.run_agent", run_agent_mock):
                with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                    ws.send_json({"type": "select_outputs", "data": ["in_chat"]})
                    msg1 = ws.receive_json()
                    msg2 = ws.receive_json()

        assert msg1["type"] == "event_data_update"
        assert msg2["type"] == "event_data_update"

    def test_invalid_format_is_ignored(self):
        """An unrecognised output format → no stage transition, run_agent not called."""
        session = _make_session(stage="selecting_output")
        run_agent_mock = AsyncMock(
            return_value=AgentState(
                event_data=EventPlanningData(),
                output_formats=[],
            )
        )
        with _ws_patched(session) as (client, _):
            with patch("app.main.run_agent", run_agent_mock):
                with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                    ws.send_json({"type": "select_outputs", "data": ["not_a_real_format"]})

        assert not run_agent_mock.called
        assert session.event_data.conversation_stage == "selecting_output"


class TestGatheringMessageFlow:
    """Regular 'message' type handling in the gathering stage."""

    def test_invalid_message_type_returns_error(self):
        session = _make_session(stage="gathering")
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "not_a_real_type", "data": "hi"})
                msg = ws.receive_json()

        assert msg["type"] == "error"

    def test_empty_message_data_returns_error(self):
        """message type with empty data string is invalid."""
        session = _make_session(stage="gathering")
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "message", "data": ""})
                msg = ws.receive_json()

        assert msg["type"] == "error"

    def test_regular_message_streams_response(self):
        session = _make_session(stage="gathering")
        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "message", "data": "Hello"})
                msgs = _collect(ws, stop_on=("stream_end",))

        types = _types(msgs)
        assert "stream_start" in types
        assert "stream_chunk" in types
        assert "stream_end" in types

    def test_menu_confirm_request_sent_when_all_recipes_named(self):
        """
        After a streaming response in gathering, if all recipes are named
        (not placeholders, not awaiting user input) and the card hasn't been
        shown yet, the server sends menu_confirm_request.
        """
        recipes = [
            Recipe(
                name="Pasta Carbonara",
                status=RecipeStatus.NAMED,
                preparation_method=PreparationMethod.HOMEMADE,
            )
        ]
        session = _make_session(stage="gathering", recipes=recipes)
        # menu_confirm_clicked defaults to False

        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "message", "data": "That menu looks great"})
                msgs = _collect(ws, stop_on=("stream_end",))
                # menu_confirm_request comes after stream_end in this flow
                extra = ws.receive_json()
                msgs.append(extra)

        assert any(m["type"] == "menu_confirm_request" for m in msgs)

    def test_menu_confirm_request_not_resent_if_already_shown(self):
        """menu_confirm_request is only sent once: suppressed when menu_confirm_clicked=True."""
        recipes = [
            Recipe(
                name="Pasta Carbonara",
                status=RecipeStatus.NAMED,
                preparation_method=PreparationMethod.HOMEMADE,
            )
        ]
        session = _make_session(stage="gathering", recipes=recipes)
        # Simulate already shown: set shown names to match current recipe names
        session.event_data.meal_plan.menu_confirm_shown_for_names = [r.name for r in recipes]

        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "message", "data": "Still thinking"})
                msgs = _collect(ws, stop_on=("stream_end",))

        assert not any(m["type"] == "menu_confirm_request" for m in msgs)


class TestRecipeConfirmationFlow:
    """Message handling in recipe_confirmation stage."""

    def test_recipe_confirm_request_sent_when_no_pending_recipes(self):
        """
        In recipe_confirmation, after a streaming response, if no recipe has
        awaiting_user_input=True and the plan isn't confirmed, the server sends
        recipe_confirm_request to prompt the user.
        """
        recipes = [
            Recipe(
                name="Pasta Carbonara",
                status=RecipeStatus.COMPLETE,
                preparation_method=PreparationMethod.HOMEMADE,
                ingredients=[{"name": "pasta", "quantity": 1.0, "unit": "lbs", "grocery_category": "pantry"}],
            )
        ]
        session = _make_session(stage="recipe_confirmation", recipes=recipes)
        session.event_data.meal_plan.confirmed = False

        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "message", "data": "Ingredients look right"})
                msgs = _collect(ws, stop_on=("stream_end",))
                # recipe_confirm_request is sent after stream_end
                extra = ws.receive_json()
                msgs.append(extra)

        assert any(m["type"] == "recipe_confirm_request" for m in msgs)

    def test_recipe_confirm_request_suppressed_when_recipe_pending(self):
        """recipe_confirm_request should NOT be sent while a recipe awaits user input."""
        recipes = [
            Recipe(
                name="Pasta Carbonara",
                status=RecipeStatus.NAMED,
                preparation_method=PreparationMethod.HOMEMADE,
                awaiting_user_input=True,
            )
        ]
        session = _make_session(stage="recipe_confirmation", recipes=recipes)

        with _ws_patched(session) as (client, _):
            with client.websocket_connect(WS_PATH, cookies=COOKIES) as ws:
                ws.send_json({"type": "message", "data": "Still uploading"})
                msgs = _collect(ws, stop_on=("stream_end",))

        assert not any(m["type"] == "recipe_confirm_request" for m in msgs)

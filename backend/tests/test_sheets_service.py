"""
Tests for SheetsService and the create_google_sheet agent step.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.state import AgentState
from app.agent.steps import create_google_sheet
from app.models.event import EventPlanningData, OutputFormat
from app.models.shopping import (
    AggregatedIngredient,
    DishCategory,
    DishServingSpec,
    GroceryCategory,
    QuantityUnit,
    ShoppingList,
)
from app.services.sheets_service import (
    SheetsService,
    _bg_color,
    _bold,
    _checkboxes,
    _col_width,
    _freeze,
)


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _make_shopping_list(adult_count: int = 8, child_count: int = 0) -> ShoppingList:
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
    ]
    sl = ShoppingList(
        meal_plan=["Pasta Carbonara"],
        adult_count=adult_count,
        child_count=child_count,
        total_guests=adult_count + child_count,
        items=items,
    )
    sl.build_grouped()
    return sl


def _make_state(
    adult_count: int = 8,
    child_count: int = 0,
    event_date: str = "2025-12-01",
    with_shopping_list: bool = True,
    with_serving_specs: bool = True,
) -> AgentState:
    sl = _make_shopping_list(adult_count, child_count) if with_shopping_list else None
    serving_specs = (
        [
            DishServingSpec(
                dish_name="Pasta Carbonara",
                dish_category=DishCategory.MAIN_PROTEIN,
                adult_servings=float(adult_count),
                child_servings=float(child_count) * 0.75,
                total_servings=float(adult_count) + float(child_count) * 0.75,
            )
        ]
        if with_serving_specs
        else []
    )
    return AgentState(
        event_data=EventPlanningData(
            adult_count=adult_count,
            child_count=child_count,
            event_date=event_date,
            cuisine_preferences=["Italian"],
        ),
        output_formats=[OutputFormat.GOOGLE_SHEET],
        shopping_list=sl,
        serving_specs=serving_specs,
    )


def _make_mock_sheets_api():
    """Return (mock_build, mock_service) configured with realistic responses."""
    mock_service = MagicMock()
    mock_spreadsheets = mock_service.spreadsheets.return_value

    # spreadsheets().create().execute() response
    mock_spreadsheets.create.return_value.execute.return_value = {
        "spreadsheetId": "test-spreadsheet-id",
        "sheets": [
            {"properties": {"sheetId": 0}},
            {"properties": {"sheetId": 1}},
        ],
    }

    # spreadsheets().values().batchUpdate().execute()
    mock_spreadsheets.values.return_value.batchUpdate.return_value.execute.return_value = {}

    # spreadsheets().batchUpdate().execute()
    mock_spreadsheets.batchUpdate.return_value.execute.return_value = {}

    return mock_service


# ---------------------------------------------------------------------------
# SheetsService.from_token_dict
# ---------------------------------------------------------------------------


class TestFromTokenDict:
    def test_valid_dict_returns_instance(self):
        token_dict = {
            "token": "access_token",
            "refresh_token": "refresh_token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client_id",
            "client_secret": "client_secret",
            "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
        }
        with patch("google.oauth2.credentials.Credentials"):
            service = SheetsService.from_token_dict(token_dict)
        assert isinstance(service, SheetsService)

    def test_empty_dict_returns_none(self):
        # Patching Credentials to raise so we exercise the except path
        with patch(
            "google.oauth2.credentials.Credentials", side_effect=Exception("bad creds")
        ):
            result = SheetsService.from_token_dict({})
        assert result is None


# ---------------------------------------------------------------------------
# SheetsService._create_sheet_sync — API call structure
# ---------------------------------------------------------------------------


class TestCreateSheetSync:
    def _run(self, state):
        """Run _create_sheet_sync synchronously with a mocked Sheets API."""
        mock_service = _make_mock_sheets_api()
        with patch(
            "app.services.sheets_service.SheetsService._build_service",
            return_value=mock_service,
        ):
            svc = SheetsService(credentials=MagicMock())
            return svc._create_sheet_sync(state, "Dinner Party - 12-01-2025"), mock_service

    def test_returns_spreadsheet_url(self):
        state = _make_state()
        url, _ = self._run(state)
        assert url == "https://docs.google.com/spreadsheets/d/test-spreadsheet-id"

    def test_create_called_with_two_sheets(self):
        state = _make_state()
        _, mock_service = self._run(state)
        create_call = mock_service.spreadsheets.return_value.create.call_args
        body = create_call[1]["body"]
        assert body["properties"]["title"] == "Dinner Party - 12-01-2025"
        assert len(body["sheets"]) == 2
        sheet_titles = [s["properties"]["title"] for s in body["sheets"]]
        assert "Party Overview" in sheet_titles
        assert "Shopping List" in sheet_titles

    def test_values_batch_update_called_for_both_tabs(self):
        state = _make_state()
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        batch_call = values_mock.batchUpdate.call_args
        body = batch_call[1]["body"]
        assert body["valueInputOption"] == "USER_ENTERED"
        ranges = [entry["range"] for entry in body["data"]]
        assert any("Party Overview" in r for r in ranges)
        assert any("Shopping List" in r for r in ranges)

    def test_formatting_batch_update_called(self):
        state = _make_state()
        _, mock_service = self._run(state)
        format_mock = mock_service.spreadsheets.return_value.batchUpdate
        assert format_mock.called
        body = format_mock.call_args[1]["body"]
        assert len(body["requests"]) > 0

    def test_party_overview_contains_guest_count(self):
        state = _make_state(adult_count=10, child_count=2)
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        overview_entry = next(
            e for e in body["data"] if "Party Overview" in e["range"]
        )
        flat = [cell for row in overview_entry["values"] for cell in row]
        assert 10 in flat  # adults
        assert 2 in flat   # children

    def test_party_overview_contains_effective_guests_formula(self):
        state = _make_state()
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        overview_entry = next(
            e for e in body["data"] if "Party Overview" in e["range"]
        )
        flat = [cell for row in overview_entry["values"] for cell in row if cell]
        assert "=B4+B5*B6" in flat

    def test_party_overview_contains_child_serving_factor(self):
        state = _make_state()
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        overview_entry = next(
            e for e in body["data"] if "Party Overview" in e["range"]
        )
        flat = [cell for row in overview_entry["values"] for cell in row if cell]
        assert 0.75 in flat

    def test_shopping_list_quantities_are_formulas(self):
        state = _make_state(adult_count=8)
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        shopping_entry = next(
            e for e in body["data"] if "Shopping List" in e["range"]
        )
        formulas = [
            cell
            for row in shopping_entry["values"]
            for cell in row
            if isinstance(cell, str) and cell.startswith("=ROUND(")
        ]
        assert len(formulas) > 0
        for formula in formulas:
            assert "'Party Overview'!B7" in formula
            assert "/ 8.0" in formula  # original_weighted_total: 8 adults + 0 children

    def test_shopping_list_formula_uses_weighted_denominator(self):
        # 8 adults + 2 children → weighted = 8 + 2*0.75 = 9.5
        state = _make_state(adult_count=8, child_count=2)
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        shopping_entry = next(
            e for e in body["data"] if "Shopping List" in e["range"]
        )
        formulas = [
            cell
            for row in shopping_entry["values"]
            for cell in row
            if isinstance(cell, str) and cell.startswith("=ROUND(")
        ]
        assert len(formulas) > 0
        for formula in formulas:
            assert "/ 9.5" in formula

    def test_shopping_list_banner_references_party_overview(self):
        state = _make_state(adult_count=8)
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        shopping_entry = next(
            e for e in body["data"] if "Shopping List" in e["range"]
        )
        banner = shopping_entry["values"][0][0]
        assert "Party Overview" in banner

    def test_no_shopping_list_does_not_crash(self):
        state = _make_state(with_shopping_list=False)
        url, _ = self._run(state)
        assert url.startswith("https://docs.google.com/spreadsheets/d/")

    def test_party_overview_does_not_include_meal_plan(self):
        state = _make_state(with_serving_specs=True)
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        overview_entry = next(
            e for e in body["data"] if "Party Overview" in e["range"]
        )
        flat = [cell for row in overview_entry["values"] for cell in row if cell]
        assert "MEAL PLAN" not in flat

    def test_party_overview_includes_cuisine(self):
        state = _make_state()
        _, mock_service = self._run(state)
        values_mock = mock_service.spreadsheets.return_value.values.return_value
        body = values_mock.batchUpdate.call_args[1]["body"]
        overview_entry = next(
            e for e in body["data"] if "Party Overview" in e["range"]
        )
        flat = [cell for row in overview_entry["values"] for cell in row if cell]
        assert "Italian" in flat


# ---------------------------------------------------------------------------
# create_google_sheet step
# ---------------------------------------------------------------------------


class TestCreateGoogleSheetStep:
    async def test_no_service_sets_url_to_none(self):
        state = _make_state()
        result = await create_google_sheet(state, sheets_service=None)
        assert result.google_sheet_url is None

    async def test_with_service_sets_url(self):
        state = _make_state()
        mock_svc = AsyncMock()
        mock_svc.create_party_sheet.return_value = (
            "https://docs.google.com/spreadsheets/d/abc123"
        )
        result = await create_google_sheet(state, sheets_service=mock_svc)
        assert result.google_sheet_url == "https://docs.google.com/spreadsheets/d/abc123"
        mock_svc.create_party_sheet.assert_called_once()

    async def test_title_uses_event_date(self):
        state = _make_state(event_date="2025-06-15")
        mock_svc = AsyncMock()
        mock_svc.create_party_sheet.return_value = "https://docs.google.com/spreadsheets/d/x"
        await create_google_sheet(state, sheets_service=mock_svc)
        title_arg = mock_svc.create_party_sheet.call_args[0][1]
        assert "06-15-2025" in title_arg

    async def test_title_fallback_when_no_date(self):
        state = _make_state()
        state.event_data.event_date = None
        mock_svc = AsyncMock()
        mock_svc.create_party_sheet.return_value = "https://docs.google.com/spreadsheets/d/x"
        await create_google_sheet(state, sheets_service=mock_svc)
        title_arg = mock_svc.create_party_sheet.call_args[0][1]
        # Falls back to today's date — just assert it's a non-empty string
        assert isinstance(title_arg, str) and len(title_arg) > 0


# ---------------------------------------------------------------------------
# Formatting helpers — pure function tests
# ---------------------------------------------------------------------------


class TestFormattingHelpers:
    def test_bold_structure(self):
        req = _bold(0, 1, 2, 0, 3)
        assert req["repeatCell"]["range"]["sheetId"] == 0
        assert req["repeatCell"]["range"]["startRowIndex"] == 1
        assert req["repeatCell"]["range"]["endRowIndex"] == 2
        assert req["repeatCell"]["cell"]["userEnteredFormat"]["textFormat"]["bold"] is True
        assert "userEnteredFormat.textFormat.bold" in req["repeatCell"]["fields"]

    def test_freeze_structure(self):
        req = _freeze(1, 2, 0)
        props = req["updateSheetProperties"]["properties"]
        assert props["sheetId"] == 1
        assert props["gridProperties"]["frozenRowCount"] == 2
        assert props["gridProperties"]["frozenColumnCount"] == 0

    def test_col_width_structure(self):
        req = _col_width(0, 1, 2, 200)
        r = req["updateDimensionProperties"]["range"]
        assert r["sheetId"] == 0
        assert r["startIndex"] == 1
        assert r["endIndex"] == 2
        assert req["updateDimensionProperties"]["properties"]["pixelSize"] == 200

    def test_checkboxes_one_per_row(self):
        reqs = _checkboxes(1, [5, 7, 9], 4)
        assert len(reqs) == 3
        row_indices = [r["setDataValidation"]["range"]["startRowIndex"] for r in reqs]
        assert row_indices == [5, 7, 9]
        for req in reqs:
            assert req["setDataValidation"]["rule"]["condition"]["type"] == "BOOLEAN"

    def test_checkboxes_empty_list(self):
        assert _checkboxes(0, [], 4) == []

    def test_bg_color_structure(self):
        req = _bg_color(0, 2, 3, 0, 5, 0.9, 0.9, 0.9)
        cell_fmt = req["repeatCell"]["cell"]["userEnteredFormat"]
        assert cell_fmt["backgroundColor"]["red"] == pytest.approx(0.9)
        assert cell_fmt["backgroundColor"]["green"] == pytest.approx(0.9)
        assert cell_fmt["backgroundColor"]["blue"] == pytest.approx(0.9)

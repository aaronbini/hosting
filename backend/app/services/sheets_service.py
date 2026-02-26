"""
SheetsService — creates a formula-driven Google Sheet from party planning data.

Structure:
  Tab 1 — Party Overview:  event details, editable guest-count cells
  Tab 2 — Shopping List:   quantities formula-driven off effective guest count in Tab 1

Party Overview key cells:
  B4 — Adults (editable)
  B5 — Children (editable)
  B6 — Child Serving Factor (editable, default 0.75; children eat this fraction
        of an adult serving)
  B7 — Effective Guests = =B4+B5*B6  ← Shopping List formulas reference this

Changing Adults, Children, or Child Serving Factor automatically rescales all
shopping quantities to the correct weighted total.

The Google Sheets API is synchronous, so all calls are wrapped in
asyncio.to_thread to avoid blocking the event loop.
"""

CHILD_SERVING_FACTOR = 0.75

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.agent.state import AgentState


class SheetsService:
    def __init__(self, credentials) -> None:
        """
        credentials: google.oauth2.credentials.Credentials
        The service is instantiated per-request from session OAuth tokens.
        """
        self._credentials = credentials

    def _build_service(self):
        """Build the Sheets API service client (sync — call inside to_thread)."""
        from googleapiclient.discovery import build

        return build("sheets", "v4", credentials=self._credentials)

    async def create_party_sheet(self, state: "AgentState", title: str) -> str:
        """
        Create a Google Sheet with Party Overview and Shopping List tabs.
        Returns the spreadsheet URL.
        """
        url = await asyncio.to_thread(self._create_sheet_sync, state, title)
        logger.info("SheetsService: spreadsheet created at %s", url)
        return url

    def _create_sheet_sync(self, state: "AgentState", title: str) -> str:
        """Synchronous implementation — runs in a thread pool."""
        service = self._build_service()
        event_data = state.event_data
        shopping_list = state.shopping_list
        serving_specs = state.serving_specs or []

        # Guest counts — shopping_list is backfilled from event_data in the runner
        adult_count = shopping_list.adult_count if shopping_list else (event_data.adult_count or 0)
        child_count = shopping_list.child_count if shopping_list else (event_data.child_count or 0)
        # Weighted denominator: children count as a fraction of an adult serving.
        # This must match the formula in B7 of Party Overview so scaling is consistent.
        original_weighted_total = max(
            1, adult_count + child_count * CHILD_SERVING_FACTOR
        )

        # ------------------------------------------------------------------ #
        # 1. Create spreadsheet with two sheets
        # ------------------------------------------------------------------ #
        spreadsheet = (
            service.spreadsheets()
            .create(
                body={
                    "properties": {"title": title},
                    "sheets": [
                        {"properties": {"title": "Party Overview", "sheetId": 0, "index": 0}},
                        {"properties": {"title": "Shopping List", "sheetId": 1, "index": 1}},
                    ],
                }
            )
            .execute()
        )

        spreadsheet_id = spreadsheet["spreadsheetId"]
        sheet0_id = spreadsheet["sheets"][0]["properties"]["sheetId"]
        sheet1_id = spreadsheet["sheets"][1]["properties"]["sheetId"]
        logger.debug("Created spreadsheet id=%s", spreadsheet_id)

        # ------------------------------------------------------------------ #
        # 2. Build Party Overview values
        # ------------------------------------------------------------------ #
        cuisine = (
            ", ".join(event_data.cuisine_preferences) if event_data.cuisine_preferences else ""
        )
        dietary_parts = []
        for d in event_data.dietary_restrictions or []:
            if hasattr(d, "type"):
                dietary_parts.append(f"{getattr(d, 'count', '')} {d.type}".strip())
            elif isinstance(d, dict):
                dietary_parts.append(f"{d.get('count', '')} {d.get('type', '')}".strip())
        dietary = "; ".join(dietary_parts) or "None"

        overview_values = [
            [title],                                                          # A1
            [],                                                               # A2
            ["Event Date", event_data.event_date or ""],                      # A3
            ["Adults", adult_count],                                          # A4 — editable
            ["Children", child_count],                                        # A5 — editable
            ["Child Serving Factor", CHILD_SERVING_FACTOR],                   # A6 — editable
            ["Effective Guests", "=B4+B5*B6"],                                # A7 — formula
            [],                                                               # A8
            ["Cuisine", cuisine],                                             # A9
            ["Dietary Notes", dietary],                                       # A10
        ]

        # ------------------------------------------------------------------ #
        # 3. Build Shopping List values
        # ------------------------------------------------------------------ #
        shopping_values = [
            [
                "Shopping List — quantities scale with 'Party Overview' guest count"
            ],
            [],
        ]

        checkbox_rows: list[int] = []
        current_row = 2  # 0-indexed; rows 0+1 = banner + blank

        for category, items in (shopping_list.grouped if shopping_list else {}).items():
            if not items:
                continue

            category_label = category.replace("_", " ").upper()
            shopping_values.append([f"── {category_label} ──", "", "", "", ""])
            current_row += 1

            shopping_values.append(["Ingredient", "Quantity", "Unit", "Used In", "Already Have?"])
            current_row += 1

            for item in items:
                formula = (
                    f"=ROUND({item.total_quantity} * 'Party Overview'!B7"
                    f" / {original_weighted_total}, 2)"
                )
                appears_in = ", ".join(item.appears_in)
                shopping_values.append([item.name, formula, item.unit.value, appears_in, False])
                checkbox_rows.append(current_row)
                current_row += 1

            shopping_values.append([])
            current_row += 1

        # ------------------------------------------------------------------ #
        # 4. Write all values in one batch call
        # ------------------------------------------------------------------ #
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": "Party Overview!A1", "values": overview_values},
                    {"range": "Shopping List!A1", "values": shopping_values},
                ],
            },
        ).execute()

        # ------------------------------------------------------------------ #
        # 5. Formatting requests
        # ------------------------------------------------------------------ #
        requests: list[dict] = []

        # Party Overview
        requests.append(_bold(sheet0_id, 0, 1, 0, 1))          # A1: title
        requests.append(_bold(sheet0_id, 2, 10, 0, 1))         # A3:A10: labels
        requests.append(_freeze(sheet0_id, 1, 0))
        requests.append(_col_width(sheet0_id, 0, 1, 220))
        requests.append(_col_width(sheet0_id, 1, 2, 300))

        # Shopping List
        requests.append(_bold(sheet1_id, 0, 1, 0, 1))          # A1: banner
        requests.append(_freeze(sheet1_id, 1, 0))
        requests.append(_col_width(sheet1_id, 0, 1, 220))      # Ingredient
        requests.append(_col_width(sheet1_id, 1, 2, 90))       # Quantity
        requests.append(_col_width(sheet1_id, 2, 3, 90))       # Unit
        requests.append(_col_width(sheet1_id, 3, 4, 250))      # Used In
        requests.append(_col_width(sheet1_id, 4, 5, 110))      # Already Have?

        # Category section headers and column sub-headers
        for row_idx, row in enumerate(shopping_values):
            if not row or not isinstance(row[0], str):
                continue
            if row[0].startswith("──"):
                requests.append(_bold(sheet1_id, row_idx, row_idx + 1, 0, 5))
                requests.append(_bg_color(sheet1_id, row_idx, row_idx + 1, 0, 5, 0.9, 0.9, 0.9))
            elif row[0] == "Ingredient":
                requests.append(_bold(sheet1_id, row_idx, row_idx + 1, 0, 5))

        # Checkboxes — one request per ingredient row
        requests.extend(_checkboxes(sheet1_id, checkbox_rows, 4))

        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()

        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"

    @staticmethod
    def from_token_dict(token_dict: dict) -> Optional["SheetsService"]:
        """
        Build a SheetsService from the serialized token dict stored on the session.
        Returns None if the dict is missing required fields.
        """
        try:
            from google.oauth2.credentials import Credentials

            creds = Credentials(
                token=token_dict.get("token"),
                refresh_token=token_dict.get("refresh_token"),
                token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=token_dict.get("client_id"),
                client_secret=token_dict.get("client_secret"),
                scopes=token_dict.get("scopes"),
            )
            return SheetsService(creds)
        except Exception as exc:
            logger.error("SheetsService.from_token_dict failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Formatting helpers — each returns a Sheets API request dict
# ---------------------------------------------------------------------------


def _bold(sheet_id: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": r0,
                "endRowIndex": r1,
                "startColumnIndex": c0,
                "endColumnIndex": c1,
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }
    }


def _freeze(sheet_id: int, rows: int, cols: int) -> dict:
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": rows,
                    "frozenColumnCount": cols,
                },
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    }


def _col_width(sheet_id: int, c0: int, c1: int, px: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": c0,
                "endIndex": c1,
            },
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }
    }


def _checkboxes(sheet_id: int, row_indices: list[int], col: int) -> list[dict]:
    """Return one checkbox validation request per row index."""
    return [
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": r,
                    "endRowIndex": r + 1,
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1,
                },
                "rule": {
                    "condition": {"type": "BOOLEAN"},
                    "showCustomUi": True,
                },
            }
        }
        for r in row_indices
    ]


def _bg_color(
    sheet_id: int, r0: int, r1: int, c0: int, c1: int, red: float, green: float, blue: float
) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": r0,
                "endRowIndex": r1,
                "startColumnIndex": c0,
                "endColumnIndex": c1,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": {"red": red, "green": green, "blue": blue}
                }
            },
            "fields": "userEnteredFormat.backgroundColor",
        }
    }

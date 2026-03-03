"""
Tests for recipe extraction REST endpoints:
  - POST /api/sessions/{session_id}/extract-recipe
  - POST /api/sessions/{session_id}/upload-recipe

Auth, DB, and AI service are all mocked via dependency overrides and patch.
"""

import uuid
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from app.auth.jwt import get_current_user
from app.db.database import get_db
from app.main import app
from app.models.event import PreparationMethod, Recipe, RecipeSourceType, RecipeStatus
from app.models.shopping import GroceryCategory, QuantityUnit, RecipeIngredient
from app.services.session_manager import SessionData

# ---------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------

FAKE_SESSION_ID = "aaaaaaaa-0000-0000-0000-000000000000"


def _fake_user():
    user = MagicMock()
    user.id = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000000")
    return user


def _make_session(recipe_name: str = "Pasta Carbonara", status: RecipeStatus = RecipeStatus.NAMED) -> SessionData:
    session = SessionData(FAKE_SESSION_ID)
    session.event_data.meal_plan.add_recipe(
        Recipe(
            name=recipe_name,
            status=status,
            preparation_method=PreparationMethod.HOMEMADE,
        )
    )
    return session


def _sample_ingredients() -> list[RecipeIngredient]:
    return [
        RecipeIngredient(
            name="pasta",
            quantity=8.0,
            unit=QuantityUnit.OZ,
            grocery_category=GroceryCategory.PANTRY,
        ),
        RecipeIngredient(
            name="eggs",
            quantity=4.0,
            unit=QuantityUnit.COUNT,
            grocery_category=GroceryCategory.DAIRY,
        ),
    ]


@pytest.fixture(autouse=True)
def override_auth_deps():
    """Bypass JWT cookie auth and DB connection for all tests in this module."""

    async def _get_db():
        yield MagicMock()

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_db] = _get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /extract-recipe
# ---------------------------------------------------------------------------


class TestExtractRecipeEndpoint:
    def test_missing_url_returns_400(self, client):
        with patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"dish_name": "Pasta Carbonara"},  # no url
            )
        assert resp.status_code == 400

    def test_missing_dish_name_returns_400(self, client):
        with patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://example.com/recipe"},  # no dish_name
            )
        assert resp.status_code == 400

    def test_http_403_on_url_returns_success_false(self, client):
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_url = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "forbidden", request=MagicMock(), response=MagicMock(status_code=403)
            )
        )

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())),
            patch("app.main.ai_service", mock_ai),
        ):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://paywalled.com/recipe", "dish_name": "Pasta Carbonara"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "403" in body["message"] or "blocked" in body["message"].lower()

    def test_http_404_on_url_returns_success_false(self, client):
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_url = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "not found", request=MagicMock(), response=MagicMock(status_code=404)
            )
        )

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())),
            patch("app.main.ai_service", mock_ai),
        ):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://example.com/gone", "dish_name": "Pasta Carbonara"},
            )

        body = resp.json()
        assert body["success"] is False
        assert "404" in body["message"] or "not found" in body["message"].lower()

    def test_network_error_returns_success_false(self, client):
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_url = AsyncMock(
            side_effect=httpx.RequestError("timeout", request=MagicMock())
        )

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())),
            patch("app.main.ai_service", mock_ai),
        ):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://unreachable.example", "dish_name": "Pasta Carbonara"},
            )

        body = resp.json()
        assert body["success"] is False
        assert "network" in body["message"].lower() or "timeout" in body["message"].lower()

    def test_no_ingredients_found_returns_success_false(self, client):
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_url = AsyncMock(return_value=[])  # empty

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())),
            patch("app.main.ai_service", mock_ai),
        ):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://example.com/recipe", "dish_name": "Pasta Carbonara"},
            )

        body = resp.json()
        assert body["success"] is False
        assert body["ingredients"] == []

    def test_recipe_not_in_meal_plan_returns_404(self, client):
        session = _make_session("Risotto")  # has Risotto, not Pasta
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_url = AsyncMock(return_value=_sample_ingredients())

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://example.com/recipe", "dish_name": "Pasta Carbonara"},
            )

        assert resp.status_code == 404

    def test_success_returns_ingredients_and_success_true(self, client):
        session = _make_session()
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_url = AsyncMock(return_value=_sample_ingredients())

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://example.com/recipe", "dish_name": "Pasta Carbonara"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["ingredients"]) == 2
        assert body["dish_name"] == "Pasta Carbonara"

    def test_success_updates_recipe_source_type(self, client):
        session = _make_session()
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_url = AsyncMock(return_value=_sample_ingredients())

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://example.com/recipe", "dish_name": "Pasta Carbonara"},
            )

        recipe = session.event_data.meal_plan.find_recipe("Pasta Carbonara")
        assert recipe.source_type == RecipeSourceType.USER_URL
        assert recipe.status == RecipeStatus.COMPLETE
        assert recipe.awaiting_user_input is False

    def test_ai_service_unavailable_returns_503(self, client):
        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())),
            patch("app.main.ai_service", None),
        ):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/extract-recipe",
                json={"url": "https://example.com/recipe", "dish_name": "Pasta Carbonara"},
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /upload-recipe
# ---------------------------------------------------------------------------


class TestUploadRecipeEndpoint:
    def _post_file(self, client, session_id: str, dish_name: str, content: bytes = b"recipe text", content_type: str = "text/plain"):
        return client.post(
            f"/api/sessions/{session_id}/upload-recipe",
            params={"dish_name": dish_name},
            files={"file": ("recipe.txt", BytesIO(content), content_type)},
        )

    def test_unsupported_mime_type_returns_400(self, client):
        with patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())):
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/upload-recipe",
                params={"dish_name": "Pasta Carbonara"},
                files={"file": ("recipe.docx", BytesIO(b"content"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["detail"]

    def test_recipe_not_in_meal_plan_returns_404(self, client):
        session = _make_session("Risotto")
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(return_value=("Risotto", _sample_ingredients()))

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
        ):
            resp = self._post_file(client, FAKE_SESSION_ID, "Pasta Carbonara")

        assert resp.status_code == 404

    def test_named_recipe_ingredients_updated(self, client):
        session = _make_session("Pasta Carbonara", status=RecipeStatus.NAMED)
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(return_value=(None, _sample_ingredients()))

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            resp = self._post_file(client, FAKE_SESSION_ID, "Pasta Carbonara")

        assert resp.status_code == 200
        recipe = session.event_data.meal_plan.find_recipe("Pasta Carbonara")
        assert recipe.status == RecipeStatus.COMPLETE
        assert recipe.source_type == RecipeSourceType.USER_UPLOAD
        assert recipe.awaiting_user_input is False

    def test_placeholder_recipe_name_updated_from_extraction(self, client):
        """When recipe is PLACEHOLDER and AI extracts a name, rename it."""
        session = _make_session("main", status=RecipeStatus.PLACEHOLDER)
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(return_value=("Pasta Carbonara", _sample_ingredients()))

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            resp = self._post_file(client, FAKE_SESSION_ID, "main")

        assert resp.status_code == 200
        body = resp.json()
        assert body["dish_name"] == "Pasta Carbonara"
        # Original placeholder renamed
        assert session.event_data.meal_plan.find_recipe("Pasta Carbonara") is not None

    def test_named_recipe_keeps_original_name_even_if_ai_returns_different(self, client):
        """NAMED recipe should not be renamed even if AI returns a different name."""
        session = _make_session("Pasta Carbonara", status=RecipeStatus.NAMED)
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(
            return_value=("Completely Different Name", _sample_ingredients())
        )

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            resp = self._post_file(client, FAKE_SESSION_ID, "Pasta Carbonara")

        assert resp.status_code == 200
        body = resp.json()
        assert body["dish_name"] == "Pasta Carbonara"

    def test_empty_ingredients_sets_named_status(self, client):
        """If AI extracts no ingredients, status stays NAMED (not COMPLETE)."""
        session = _make_session("Pasta Carbonara", status=RecipeStatus.NAMED)
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(return_value=(None, []))  # empty

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            self._post_file(client, FAKE_SESSION_ID, "Pasta Carbonara")

        recipe = session.event_data.meal_plan.find_recipe("Pasta Carbonara")
        assert recipe.status == RecipeStatus.NAMED

    def test_nonempty_ingredients_sets_complete_status(self, client):
        session = _make_session("Pasta Carbonara", status=RecipeStatus.NAMED)
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(return_value=(None, _sample_ingredients()))

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            self._post_file(client, FAKE_SESSION_ID, "Pasta Carbonara")

        recipe = session.event_data.meal_plan.find_recipe("Pasta Carbonara")
        assert recipe.status == RecipeStatus.COMPLETE

    def test_awaiting_user_input_cleared_on_success(self, client):
        session = _make_session("Pasta Carbonara", status=RecipeStatus.NAMED)
        recipe = session.event_data.meal_plan.find_recipe("Pasta Carbonara")
        recipe.awaiting_user_input = True

        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(return_value=(None, _sample_ingredients()))

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            self._post_file(client, FAKE_SESSION_ID, "Pasta Carbonara")

        assert recipe.awaiting_user_input is False

    def test_pdf_mime_type_accepted(self, client):
        session = _make_session()
        mock_ai = MagicMock()
        mock_ai.extract_recipe_from_file = AsyncMock(return_value=(None, _sample_ingredients()))

        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=session)),
            patch("app.main.ai_service", mock_ai),
            patch("app.main.db_session_manager") as mock_db_mgr,
        ):
            mock_db_mgr.save_session = AsyncMock()
            resp = client.post(
                f"/api/sessions/{FAKE_SESSION_ID}/upload-recipe",
                params={"dish_name": "Pasta Carbonara"},
                files={"file": ("recipe.pdf", BytesIO(b"%PDF content"), "application/pdf")},
            )

        assert resp.status_code == 200

    def test_ai_service_unavailable_returns_503(self, client):
        with (
            patch("app.main._require_session_owner", new=AsyncMock(return_value=_make_session())),
            patch("app.main.ai_service", None),
        ):
            resp = self._post_file(client, FAKE_SESSION_ID, "Pasta Carbonara")

        assert resp.status_code == 503

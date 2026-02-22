"""
Tests for apply_extraction() in main.py — the highest-value test file.

apply_extraction() drives all stage transitions and meal plan mutations.
Tests here would have caught every stage-transition bug seen in production.
No mocking needed: build ExtractionResult fixtures and call the function directly.
"""

import pytest

from app.main import apply_extraction
from app.models.event import (
    ExtractionResult,
    MealPlan,
    OutputFormat,
    PreparationMethod,
    Recipe,
    RecipeSourceType,
    RecipeStatus,
    RecipeUpdate,
)
from app.services.session_manager import SessionData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session(stage: str = "gathering") -> SessionData:
    session = SessionData("test-session")
    session.event_data.conversation_stage = stage
    return session


def _answer_all(session: SessionData) -> SessionData:
    """Mark every critical question as answered."""
    for q in session.event_data.answered_questions:
        session.event_data.answered_questions[q] = True
    session.event_data.adult_count = 8
    return session


def _complete_recipe(name: str = "Pasta") -> Recipe:
    return Recipe(
        name=name,
        status=RecipeStatus.COMPLETE,
        ingredients=[
            {"name": "pasta", "quantity": 1.0, "unit": "lbs", "grocery_category": "pantry"}
        ],
    )


# ---------------------------------------------------------------------------
# Recipe mutations
# ---------------------------------------------------------------------------


class TestRecipeUpdates:
    def test_add_recipe(self):
        session = make_session()
        extraction = ExtractionResult(
            recipe_updates=[RecipeUpdate(recipe_name="Pasta Carbonara", action="add")]
        )
        apply_extraction(session, extraction)
        assert session.event_data.meal_plan.find_recipe("Pasta Carbonara") is not None

    def test_add_recipe_idempotent(self):
        session = make_session()
        extraction = ExtractionResult(
            recipe_updates=[RecipeUpdate(recipe_name="Pasta", action="add")]
        )
        apply_extraction(session, extraction)
        apply_extraction(session, extraction)  # second call should not duplicate
        assert len(session.event_data.meal_plan.recipes) == 1

    def test_remove_recipe(self):
        session = make_session()
        session.event_data.meal_plan.add_recipe(Recipe(name="Pasta"))
        extraction = ExtractionResult(
            recipe_updates=[RecipeUpdate(recipe_name="Pasta", action="remove")]
        )
        apply_extraction(session, extraction)
        assert session.event_data.meal_plan.find_recipe("Pasta") is None

    def test_rename_placeholder_via_update(self):
        """Reproduces the 'main → wrong dish name' bug fix."""
        session = make_session()
        session.event_data.meal_plan.add_recipe(
            Recipe(name="main", status=RecipeStatus.PLACEHOLDER)
        )
        extraction = ExtractionResult(
            recipe_updates=[
                RecipeUpdate(
                    recipe_name="main",
                    action="update",
                    new_name="Spaghetti Carbonara",
                    status=RecipeStatus.NAMED,
                )
            ]
        )
        apply_extraction(session, extraction)
        assert session.event_data.meal_plan.find_recipe("main") is None
        assert session.event_data.meal_plan.find_recipe("Spaghetti Carbonara") is not None

    def test_update_source_type(self):
        session = make_session()
        session.event_data.meal_plan.add_recipe(Recipe(name="Pasta"))
        extraction = ExtractionResult(
            recipe_updates=[
                RecipeUpdate(
                    recipe_name="Pasta",
                    action="update",
                    source_type=RecipeSourceType.USER_URL,
                    url="https://example.com/recipe",
                )
            ]
        )
        apply_extraction(session, extraction)
        recipe = session.event_data.meal_plan.find_recipe("Pasta")
        assert recipe.source_type == RecipeSourceType.USER_URL
        assert recipe.url == "https://example.com/recipe"

    def test_update_nonexistent_recipe_is_noop(self):
        """Updating a recipe that doesn't exist should not crash."""
        session = make_session()
        extraction = ExtractionResult(
            recipe_updates=[
                RecipeUpdate(recipe_name="Does Not Exist", action="update", status=RecipeStatus.COMPLETE)
            ]
        )
        apply_extraction(session, extraction)  # should not raise


# ---------------------------------------------------------------------------
# Meal plan confirmation
# ---------------------------------------------------------------------------


class TestMealPlanConfirmed:
    def test_confirmed_sets_flag(self):
        session = make_session()
        session.event_data.meal_plan.add_recipe(Recipe(name="Pasta"))
        extraction = ExtractionResult(meal_plan_confirmed=True)
        apply_extraction(session, extraction)
        assert session.event_data.meal_plan.confirmed is True

    def test_confirmed_marks_meal_plan_question(self):
        session = make_session()
        session.event_data.meal_plan.add_recipe(Recipe(name="Pasta"))
        extraction = ExtractionResult(meal_plan_confirmed=True)
        apply_extraction(session, extraction)
        assert session.event_data.answered_questions.get("meal_plan") is True

    def test_confirmed_false_does_not_set_flag(self):
        session = make_session()
        extraction = ExtractionResult(meal_plan_confirmed=False)
        apply_extraction(session, extraction)
        assert session.event_data.meal_plan.confirmed is False


# ---------------------------------------------------------------------------
# Answered questions tracking
# ---------------------------------------------------------------------------


class TestAnsweredQuestions:
    def test_marks_specified_questions_as_answered(self):
        session = make_session()
        extraction = ExtractionResult(answered_questions=["event_type", "guest_count"])
        apply_extraction(session, extraction)
        assert session.event_data.answered_questions["event_type"] is True
        assert session.event_data.answered_questions["guest_count"] is True

    def test_unspecified_questions_remain_unanswered(self):
        session = make_session()
        extraction = ExtractionResult(answered_questions=["event_type"])
        apply_extraction(session, extraction)
        assert session.event_data.answered_questions["dietary"] is False

    def test_unknown_question_id_is_ignored(self):
        session = make_session()
        extraction = ExtractionResult(answered_questions=["not_a_real_question"])
        apply_extraction(session, extraction)  # should not raise


# ---------------------------------------------------------------------------
# Stage transitions
# ---------------------------------------------------------------------------


class TestStageTransitions:
    def test_gathering_transitions_to_recipe_confirmation_when_complete(self):
        session = _answer_all(make_session("gathering"))
        session.event_data.meal_plan.add_recipe(_complete_recipe())
        extraction = ExtractionResult(
            meal_plan_confirmed=True,
            answered_questions=["meal_plan"],
        )
        apply_extraction(session, extraction)
        assert session.event_data.conversation_stage == "recipe_confirmation"

    def test_gathering_does_not_transition_if_questions_incomplete(self):
        session = make_session("gathering")  # no questions answered
        session.event_data.meal_plan.add_recipe(_complete_recipe())
        extraction = ExtractionResult(meal_plan_confirmed=True)
        apply_extraction(session, extraction)
        assert session.event_data.conversation_stage == "gathering"

    def test_gathering_blocked_by_awaiting_user_input_recipe(self):
        """Reproduces the stage-stuck bug: pending recipe blocks transition."""
        session = _answer_all(make_session("gathering"))
        session.event_data.meal_plan.add_recipe(
            Recipe(name="main", status=RecipeStatus.NAMED, awaiting_user_input=True)
        )
        extraction = ExtractionResult(
            meal_plan_confirmed=True,
            answered_questions=["meal_plan"],
        )
        apply_extraction(session, extraction)
        assert session.event_data.conversation_stage == "gathering"

    def test_recipe_confirmation_transitions_to_selecting_output(self):
        """Reproduces the store-bought blocking bug."""
        session = make_session("recipe_confirmation")
        session.event_data.meal_plan.add_recipe(
            Recipe(
                name="Sourdough",
                preparation_method=PreparationMethod.STORE_BOUGHT,
                status=RecipeStatus.NAMED,
            )
        )
        session.event_data.meal_plan.confirmed = True
        extraction = ExtractionResult()
        apply_extraction(session, extraction)
        assert session.event_data.conversation_stage == "selecting_output"

    def test_recipe_confirmation_does_not_advance_if_incomplete_recipe(self):
        session = make_session("recipe_confirmation")
        session.event_data.meal_plan.add_recipe(
            Recipe(name="Pasta", status=RecipeStatus.NAMED, ingredients=[])
        )
        session.event_data.meal_plan.confirmed = True
        extraction = ExtractionResult()
        apply_extraction(session, extraction)
        assert session.event_data.conversation_stage == "recipe_confirmation"

    def test_selecting_output_transitions_to_agent_running(self):
        session = make_session("selecting_output")
        extraction = ExtractionResult(output_formats=["in_chat"])
        apply_extraction(session, extraction)
        assert session.event_data.conversation_stage == "agent_running"
        assert OutputFormat.IN_CHAT in session.event_data.output_formats

    def test_selecting_output_no_formats_does_not_advance(self):
        session = make_session("selecting_output")
        extraction = ExtractionResult()
        apply_extraction(session, extraction)
        assert session.event_data.conversation_stage == "selecting_output"

    def test_invalid_output_format_ignored(self):
        session = make_session("selecting_output")
        extraction = ExtractionResult(output_formats=["not_a_real_format"])
        apply_extraction(session, extraction)
        # Stage should not advance (no valid formats were parsed)
        assert session.event_data.conversation_stage == "selecting_output"


# ---------------------------------------------------------------------------
# Output format parsing
# ---------------------------------------------------------------------------


class TestOutputFormats:
    def test_valid_formats_parsed(self):
        session = make_session("selecting_output")
        extraction = ExtractionResult(output_formats=["in_chat", "google_tasks"])
        apply_extraction(session, extraction)
        assert OutputFormat.IN_CHAT in session.event_data.output_formats
        assert OutputFormat.GOOGLE_TASKS in session.event_data.output_formats

    def test_invalid_format_filtered_out(self):
        session = make_session("selecting_output")
        extraction = ExtractionResult(output_formats=["in_chat", "fax_machine"])
        apply_extraction(session, extraction)
        assert len(session.event_data.output_formats) == 1
        assert OutputFormat.IN_CHAT in session.event_data.output_formats


# ---------------------------------------------------------------------------
# Transient fields cleared each turn
# ---------------------------------------------------------------------------


class TestTransientFieldsCleared:
    def test_last_url_extraction_result_cleared(self):
        session = make_session()
        session.event_data.last_url_extraction_result = {"success": True}
        apply_extraction(session, ExtractionResult())
        assert session.event_data.last_url_extraction_result is None

    def test_last_generated_recipes_cleared(self):
        session = make_session()
        session.event_data.last_generated_recipes = [{"dish": "Pasta", "ingredients": []}]
        apply_extraction(session, ExtractionResult())
        assert session.event_data.last_generated_recipes is None

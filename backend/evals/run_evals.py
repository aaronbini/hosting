"""
Eval harness for AI output quality.

Runs against the live Gemini API — requires GOOGLE_API_KEY.
NOT part of the pytest test suite. Run manually before releases or after prompt changes.

Usage:
    cd backend
    uv run python -m evals.run_evals
    uv run python -m evals.run_evals --only extraction
    uv run python -m evals.run_evals --only conversation
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

DATASETS_DIR = Path(__file__).parent / "datasets"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    score: float  # 0.0–1.0
    details: str
    errors: list[str] = field(default_factory=list)


@dataclass
class EvalSummary:
    category: str
    results: list[EvalResult]

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def avg_score(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.score for r in self.results) / len(self.results)


# ---------------------------------------------------------------------------
# Extraction eval — deterministic field-level checks
# ---------------------------------------------------------------------------


def _field_match(actual: dict, expected: dict) -> tuple[int, int]:
    """
    Compare expected fields against actual extracted fields.
    Returns (matched, total) for the fields in expected.
    """
    matched = 0
    total = 0
    for key, exp_val in expected.items():
        total += 1
        act_val = actual.get(key)
        if act_val is None:
            continue
        # Numeric: allow ±1 tolerance (e.g., "about 8" might extract as 8 or 10)
        if isinstance(exp_val, (int, float)) and isinstance(act_val, (int, float)):
            if abs(act_val - exp_val) <= 1:
                matched += 1
        # Bool: exact match
        elif isinstance(exp_val, bool):
            if act_val == exp_val:
                matched += 1
        # List of strings: check all expected values present (case-insensitive)
        elif isinstance(exp_val, list) and all(isinstance(v, str) for v in exp_val):
            act_list = [str(v).lower() for v in (act_val if isinstance(act_val, list) else [])]
            if all(v.lower() in act_list for v in exp_val):
                matched += 1
        # List of dicts (e.g., dietary_restrictions): check each expected entry present
        elif isinstance(exp_val, list) and all(isinstance(v, dict) for v in exp_val):
            act_list = act_val if isinstance(act_val, list) else []
            all_found = True
            for exp_item in exp_val:
                found = any(
                    all(
                        str(act_item.get(k, "")).lower() == str(v).lower()
                        for k, v in exp_item.items()
                        if k != "count"  # count is fuzzy
                    )
                    for act_item in act_list
                )
                if not found:
                    all_found = False
                    break
            if all_found:
                matched += 1
        # String: case-insensitive substring match
        elif isinstance(exp_val, str) and isinstance(act_val, str):
            if exp_val.lower() in act_val.lower() or act_val.lower() in exp_val.lower():
                matched += 1
    return matched, total


async def run_extraction_evals(ai_service) -> EvalSummary:
    """Run all extraction test cases and return results."""
    cases = json.loads((DATASETS_DIR / "extraction_cases.json").read_text())
    results = []

    for case in cases:
        case_id = case["id"]
        try:
            from app.models.event import EventPlanningData

            event_data = EventPlanningData()
            # Set context if provided
            if "context_recipes" in case:
                from app.models.event import Recipe
                for name in case["context_recipes"]:
                    event_data.meal_plan.add_recipe(Recipe(name=name))

            extraction = await ai_service.extract_event_data(
                user_message=case["input"],
                event_data=event_data,
                conversation_history=[],
            )
            actual = extraction.model_dump(exclude_none=True)

            errors = []
            matched = 0
            total = 0

            # Check standard field expectations
            if "expected" in case:
                m, t = _field_match(actual, case["expected"])
                matched += m
                total += t

            # Check recipe_updates shape
            if "expected_recipe_updates" in case:
                updates = actual.get("recipe_updates") or []
                for exp_upd in case["expected_recipe_updates"]:
                    found = any(
                        upd.get("action") == exp_upd["action"]
                        and exp_upd["recipe_name"].lower() in (upd.get("recipe_name") or "").lower()
                        for upd in updates
                    )
                    total += 1
                    if found:
                        matched += 1
                    else:
                        errors.append(f"Missing recipe update: {exp_upd}")

            score = matched / total if total > 0 else 0.0
            passed = score >= 0.8  # 80% field match = pass
            details = f"{matched}/{total} fields matched"
            results.append(EvalResult(case_id, passed, score, details, errors))

        except Exception as e:
            results.append(
                EvalResult(case_id, False, 0.0, "Exception during eval", [str(e)])
            )

    return EvalSummary("extraction", results)


# ---------------------------------------------------------------------------
# Conversation eval — LLM-as-judge
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """You are evaluating a dinner party planning chatbot response.

Rubric:
5 = Excellent: specific, on-topic, moves the conversation forward meaningfully
4 = Good: on-topic, helpful, minor gaps
3 = Acceptable: on-topic but generic or slightly off-focus
2 = Poor: partially relevant but misses key requirement
1 = Bad: off-topic, confusing, or breaks the task

Evaluation criteria for this specific case:
{rubric}

User message: {user_message}

Chatbot response to evaluate:
{response}

First explain your reasoning in 1-2 sentences.
Then output exactly: SCORE: <number>
"""


async def _judge_response_text(ai_service, user_message: str, response: str, rubric: str) -> int:
    """Use Gemini as a judge via text generation. Returns score 1-5."""
    from google.genai import types

    prompt = JUDGE_PROMPT.format(
        rubric=rubric, user_message=user_message, response=response
    )
    result = await ai_service.client.aio.models.generate_content(
        model=ai_service.fast_model_name,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=types.GenerateContentConfig(temperature=0.0),
    )
    text = result.text or ""
    # Parse "SCORE: N" from the response
    for line in reversed(text.splitlines()):
        if "SCORE:" in line:
            try:
                return int(line.split("SCORE:")[-1].strip())
            except ValueError:
                pass
    return 3  # default if parsing fails


async def run_conversation_evals(ai_service) -> EvalSummary:
    """Run conversation quality evaluations using LLM-as-judge."""
    cases = json.loads((DATASETS_DIR / "conversation_cases.json").read_text())
    results = []

    for case in cases:
        case_id = case["id"]
        try:
            from app.models.event import EventPlanningData

            event_data = EventPlanningData()
            # Apply context fields
            ctx = case.get("context", {})
            for key, val in ctx.items():
                if key == "conversation_stage":
                    event_data.conversation_stage = val
                elif key == "adult_count" and val is not None:
                    event_data.adult_count = val
                elif key == "cuisine_preferences" and val:
                    event_data.cuisine_preferences = val
                elif key == "last_url_extraction_result":
                    event_data.last_url_extraction_result = val
                elif key == "last_generated_recipes":
                    event_data.last_generated_recipes = val

            # Generate the actual chatbot response
            response = await ai_service.generate_response(
                user_message=case["user_message"],
                event_data=event_data,
                conversation_history=[],
            )

            # Judge the response
            score = await _judge_response_text(
                ai_service,
                user_message=case["user_message"],
                response=response,
                rubric=case["rubric"],
            )

            passed = score >= 3
            results.append(
                EvalResult(
                    case_id,
                    passed,
                    score / 5.0,
                    f"Judge score: {score}/5",
                )
            )

        except Exception as e:
            results.append(
                EvalResult(case_id, False, 0.0, "Exception during eval", [str(e)])
            )

    return EvalSummary("conversation", results)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_summary(summary: EvalSummary) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {summary.category.upper()} EVALS")
    print(f"{'=' * 60}")
    print(f"  Pass rate: {summary.pass_rate:.0%}  |  Avg score: {summary.avg_score:.2f}")
    print()
    for r in summary.results:
        icon = "✓" if r.passed else "✗"
        print(f"  {icon} [{r.case_id}]  {r.details}")
        for err in r.errors:
            print(f"      → {err}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(only: str | None = None) -> int:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY not set. Evals require a live Gemini API key.")
        return 1

    from app.services.ai_service import GeminiService

    ai_service = GeminiService(api_key=api_key)
    summaries: list[EvalSummary] = []

    if only in (None, "extraction"):
        print("Running extraction evals...")
        summaries.append(await run_extraction_evals(ai_service))

    if only in (None, "conversation"):
        print("Running conversation evals...")
        summaries.append(await run_conversation_evals(ai_service))

    for summary in summaries:
        print_summary(summary)

    # Exit with non-zero if any category is below 70% pass rate
    failed = any(s.pass_rate < 0.70 for s in summaries)
    if failed:
        print("⚠  One or more eval categories below 70% pass rate.")
    else:
        print("✓  All eval categories passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run AI output quality evals")
    parser.add_argument(
        "--only",
        choices=["extraction", "conversation"],
        help="Run only one category of evals",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(only=args.only)))

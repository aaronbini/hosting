"""
Agent runner — WebSocket-aware orchestrator.

Responsibility:
- Owns the execution sequence and all WebSocket communication.
- Calls the pure step functions from steps.py in order.
- Sends progress messages to the client between steps.
- Implements the human-in-the-loop review loop (steps 4-5): repeats until
  the user approves the shopping list without requesting further changes.

LangGraph migration: the sequential logic here becomes a graph definition
with edges between nodes. The WebSocket send calls move to a streaming
callback / interrupt handler. The review loop becomes a cycle edge that
routes back to apply_corrections when corrections are provided, or forward
to delivery when the user approves.
"""

import asyncio
import json
import logging
from fastapi import WebSocket

from app.agent.state import AgentState, AgentStage
from app.agent.steps import (
    calculate_quantities,
    get_all_dish_ingredients,
    aggregate_ingredients,
    apply_corrections,
    create_google_sheet,
    create_google_keep,
    format_chat_output,
)
from app.models.event import EventPlanningData, OutputFormat
from app.services.ai_service import GeminiService

logger = logging.getLogger(__name__)


async def _send(websocket: WebSocket, message: dict) -> None:
    """Send a JSON message over the WebSocket."""
    await websocket.send_text(json.dumps(message))


async def run_agent(
    websocket: WebSocket,
    event_data: EventPlanningData,
    output_formats: list[OutputFormat],
    ai_service: GeminiService,
) -> None:
    """
    Run the full agent pipeline for a session.

    Execution flow:
        1. calculate_quantities      → DishServingSpec per dish
        2. get_all_dish_ingredients  → DishIngredients per dish (parallel)
        3. aggregate_ingredients     → ShoppingList
        4. → client: agent_review + shopping list        ┐
        5. ← client: user approval / corrections          │ loop until approved
        6. apply_corrections → revised ShoppingList       │
           → client: agent_review + revised list          ┘
        7. In parallel:
               create_google_sheet   → sheet URL  [stub]
               create_google_keep    → keep URL   [stub]
               format_chat_output    → markdown string
        8. → client: agent_complete + results
    """
    state = AgentState(event_data=event_data, output_formats=output_formats)

    try:
        # ------------------------------------------------------------------ #
        # Step 1: Calculate serving specs
        # ------------------------------------------------------------------ #
        await _send(websocket, {
            "type": "agent_progress",
            "stage": AgentStage.CALCULATING_QUANTITIES,
            "message": "Calculating quantities for your meal plan...",
        })
        state = await calculate_quantities(state, ai_service)

        # ------------------------------------------------------------------ #
        # Step 2: Get ingredients per dish
        # ------------------------------------------------------------------ #
        await _send(websocket, {
            "type": "agent_progress",
            "stage": AgentStage.GETTING_INGREDIENTS,
            "message": f"Getting ingredients for {len(state.serving_specs)} dishes...",
        })
        state = await get_all_dish_ingredients(state, ai_service)

        # ------------------------------------------------------------------ #
        # Step 3: Aggregate
        # ------------------------------------------------------------------ #
        await _send(websocket, {
            "type": "agent_progress",
            "stage": AgentStage.AGGREGATING,
            "message": "Building your shopping list...",
        })
        state = await aggregate_ingredients(state, ai_service)

        # Back-fill guest counts that aggregate_ingredients leaves as 0
        if state.shopping_list:
            state.shopping_list.adult_count = event_data.adult_count or 0
            state.shopping_list.child_count = event_data.child_count or 0
            state.shopping_list.total_guests = (event_data.total_guests or 0)

        # ------------------------------------------------------------------ #
        # Steps 4-6: Review loop — repeat until user approves
        # ------------------------------------------------------------------ #
        while True:
            state.stage = AgentStage.AWAITING_REVIEW
            await _send(websocket, {
                "type": "agent_review",
                "stage": AgentStage.AWAITING_REVIEW,
                "shopping_list": state.shopping_list.model_dump() if state.shopping_list else None,
                "message": (
                    "Here's your shopping list! Review it and let me know if anything "
                    "needs adjusting, or type 'looks good' to proceed."
                ),
            })

            # Wait for the client to send back approval or corrections.
            # LangGraph migration: replace this block with interrupt().
            review_msg = await websocket.receive_json()
            corrections = review_msg.get("corrections", "").strip()

            if not corrections:
                # User approved — exit the review loop
                break

            # Apply corrections and loop back to re-present the list
            state.user_corrections = corrections
            await _send(websocket, {
                "type": "agent_progress",
                "stage": AgentStage.APPLYING_CORRECTIONS,
                "message": "Applying your corrections...",
            })
            state = await apply_corrections(state, ai_service)

        # ------------------------------------------------------------------ #
        # Step 7: Deliver in parallel
        # ------------------------------------------------------------------ #
        await _send(websocket, {
            "type": "agent_progress",
            "stage": AgentStage.DELIVERING,
            "message": "Preparing your outputs...",
        })

        delivery_tasks = [format_chat_output(state)]

        if OutputFormat.GOOGLE_SHEET in output_formats:
            delivery_tasks.append(create_google_sheet(state))
        if OutputFormat.GOOGLE_KEEP in output_formats:
            delivery_tasks.append(create_google_keep(state))

        results = await asyncio.gather(*delivery_tasks, return_exceptions=True)

        # Merge results back into state (gather returns in task order)
        for result in results:
            if isinstance(result, Exception):
                logger.error("Delivery step failed: %s", result)
                continue
            state = result

        # ------------------------------------------------------------------ #
        # Step 8: Done
        # ------------------------------------------------------------------ #
        state.stage = AgentStage.COMPLETE
        await _send(websocket, {
            "type": "agent_complete",
            "stage": AgentStage.COMPLETE,
            "formatted_output": state.formatted_chat_output,
            "google_sheet_url": state.google_sheet_url,
            "google_keep_url": state.google_keep_url,
        })

    except Exception as exc:
        logger.exception("Agent run failed: %s", exc)
        state.stage = AgentStage.ERROR
        state.error = str(exc)
        await _send(websocket, {
            "type": "agent_error",
            "stage": AgentStage.ERROR,
            "message": f"Something went wrong during planning: {exc}",
        })

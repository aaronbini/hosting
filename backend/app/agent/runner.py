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
from typing import Optional

from fastapi import WebSocket

from app.agent.state import AgentStage, AgentState
from app.agent.steps import (
    aggregate_ingredients,
    apply_corrections,
    calculate_quantities,
    create_google_sheet,
    create_google_tasks,
    format_chat_output,
    generate_recipes,
    get_all_dish_ingredients,
)
from app.models.event import EventPlanningData, OutputFormat
from app.services.ai_service import GeminiService

logger = logging.getLogger(__name__)


async def _send(websocket: WebSocket, message: dict) -> None:
    """Send a JSON message over the WebSocket."""
    try:
        await websocket.send_text(json.dumps(message))
    except Exception as e:
        logger.error(
            "Failed to send WebSocket message of type '%s': %s",
            message.get("type", "unknown"),
            e,
            exc_info=True,
        )


async def run_agent(
    websocket: WebSocket,
    event_data: EventPlanningData,
    output_formats: list[OutputFormat],
    ai_service: GeminiService,
    existing_state: Optional[AgentState] = None,
    tasks_service=None,
    sheets_service=None,
) -> AgentState:
    """
    Run the full agent pipeline for a session.

    If ``existing_state`` already contains an approved shopping list (i.e. the
    agent ran before and the user is requesting additional output formats), steps
    1-3 and the review loop are skipped — delivery runs immediately with the
    cached list.

    Execution flow (first run):
        1. calculate_quantities      → DishServingSpec per dish
        2. get_all_dish_ingredients  → DishIngredients per dish (parallel)
        3. aggregate_ingredients     → ShoppingList
        4. → client: agent_review + shopping list         ┐
        5. ← client: user approval / corrections          │ loop until approved
        6. apply_corrections → revised ShoppingList       │
           → client: agent_review + revised list          ┘
        7. In parallel:
               create_google_sheet   → sheet URL  [stub]
               create_google_keep    → keep URL   [stub]
               format_chat_output    → markdown string
        8. → client: agent_complete + results
    """
    # Reuse the previous shopping list if available; otherwise start fresh.
    if existing_state and existing_state.shopping_list:
        logger.info(
            "Reusing cached shopping list (%d items) for new output formats: %s",
            len(existing_state.shopping_list.items),
            output_formats,
        )
        state = existing_state.model_copy(update={"output_formats": output_formats})
        skip_to_delivery = True
    else:
        state = AgentState(event_data=event_data, output_formats=output_formats)
        skip_to_delivery = False

    try:
        if not skip_to_delivery:
            # ------------------------------------------------------------------ #
            # Step 1: Calculate serving specs
            # ------------------------------------------------------------------ #
            await _send(
                websocket,
                {
                    "type": "agent_progress",
                    "stage": AgentStage.CALCULATING_QUANTITIES,
                    "message": "Calculating quantities for your meal plan...",
                },
            )
            state = await calculate_quantities(state, ai_service)

            # ------------------------------------------------------------------ #
            # Step 2: Get ingredients per dish
            # ------------------------------------------------------------------ #
            await _send(
                websocket,
                {
                    "type": "agent_progress",
                    "stage": AgentStage.GETTING_INGREDIENTS,
                    "message": f"Getting ingredients for {len(state.serving_specs)} dishes...",
                },
            )
            state = await get_all_dish_ingredients(state, ai_service)

            # ------------------------------------------------------------------ #
            # Step 3: Aggregate
            # ------------------------------------------------------------------ #
            await _send(
                websocket,
                {
                    "type": "agent_progress",
                    "stage": AgentStage.AGGREGATING,
                    "message": "Building your shopping list...",
                },
            )
            state = await aggregate_ingredients(state, ai_service)
            logger.info(
                "aggregate_ingredients completed, shopping_list has %d items",
                len(state.shopping_list.items) if state.shopping_list else 0,
            )

            # Back-fill guest counts that aggregate_ingredients leaves as 0
            if state.shopping_list:
                logger.info(
                    "Backfilling guest counts: %d adults, %d children, %d total",
                    event_data.adult_count or 0,
                    event_data.child_count or 0,
                    event_data.total_guests or 0,
                )
                state.shopping_list.adult_count = event_data.adult_count or 0
                state.shopping_list.child_count = event_data.child_count or 0
                state.shopping_list.total_guests = event_data.total_guests or 0
            else:
                logger.warning("No shopping list to backfill")

            # ------------------------------------------------------------------ #
            # Steps 4-6: Review loop — repeat until user approves
            # ------------------------------------------------------------------ #
            logger.info("Entering review loop")
            while True:
                state.stage = AgentStage.AWAITING_REVIEW
                logger.info("Sending agent_review message with shopping list")
                await _send(
                    websocket,
                    {
                        "type": "agent_review",
                        "stage": AgentStage.AWAITING_REVIEW,
                        "shopping_list": state.shopping_list.model_dump()
                        if state.shopping_list
                        else None,
                        "message": (
                            "Here's your shopping list! Review it and let me know if anything "
                            "needs adjusting, or click 'Approve' below to proceed."
                        ),
                    },
                )
                logger.info("agent_review message sent successfully")

                # Wait for the client to send back approval or corrections.
                # Supports two message formats:
                #   {"type": "approve"}                       — explicit approval button
                #   {"type": "message", "data": "..."}        — standard chat input (corrections)
                #   {"corrections": "..."}                     — legacy dedicated format
                # LangGraph migration: replace this block with interrupt().
                review_msg = await websocket.receive_json()

                if review_msg.get("type") == "approve":
                    excluded = review_msg.get("excluded_items", [])
                    if excluded and state.shopping_list:
                        excluded_set = {name.lower() for name in excluded}
                        state.shopping_list.items = [
                            item for item in state.shopping_list.items
                            if item.name.lower() not in excluded_set
                        ]
                        state.shopping_list.build_grouped()
                        logger.info(
                            "Removed %d excluded items; %d items remain",
                            len(excluded),
                            len(state.shopping_list.items),
                        )
                    break

                corrections = (
                    review_msg.get("corrections") or review_msg.get("data") or ""
                ).strip()

                if not corrections:
                    # Empty message — treat as approval
                    break

                # Apply corrections and loop back to re-present the list
                state.user_corrections = corrections
                await _send(
                    websocket,
                    {
                        "type": "agent_progress",
                        "stage": AgentStage.APPLYING_CORRECTIONS,
                        "message": "Applying your corrections...",
                    },
                )
                state = await apply_corrections(state, ai_service)

        # ------------------------------------------------------------------ #
        # Step 7: Deliver in parallel
        # ------------------------------------------------------------------ #
        await _send(
            websocket,
            {
                "type": "agent_progress",
                "stage": AgentStage.DELIVERING,
                "message": "Preparing your outputs...",
            },
        )

        delivery_tasks = [
            format_chat_output(state),
            generate_recipes(state, ai_service),
        ]

        if OutputFormat.GOOGLE_SHEET in output_formats:
            delivery_tasks.append(create_google_sheet(state, sheets_service))
        if OutputFormat.GOOGLE_TASKS in output_formats:
            delivery_tasks.append(create_google_tasks(state, tasks_service))

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
        await _send(
            websocket,
            {
                "type": "agent_complete",
                "stage": AgentStage.COMPLETE,
                "formatted_output": state.formatted_chat_output,
                "formatted_recipes_output": state.formatted_recipes_output,
                "google_sheet_url": state.google_sheet_url,
                "google_tasks": state.google_tasks.model_dump() if state.google_tasks else None,
            },
        )

    except Exception as exc:
        logger.exception("Agent run failed: %s", exc)
        state.stage = AgentStage.ERROR
        state.error = str(exc)
        await _send(
            websocket,
            {
                "type": "agent_error",
                "stage": AgentStage.ERROR,
                "message": f"Something went wrong during planning: {exc}",
            },
        )

    return state

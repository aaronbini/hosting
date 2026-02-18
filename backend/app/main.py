import logging
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware

from app.models.chat import ChatRequest, ChatResponse, MessageRole
from app.models.event import ExtractionResult, OutputFormat, RecipeSource, RecipeSourceType
from app.services.ai_service import GeminiService
from app.services.session_manager import SessionData, session_manager

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize services
# TODO: Make this configurable/injectable for testing
try:
    ai_service = GeminiService()
except ValueError as e:
    logger.error(f"Failed to initialize AI service: {e}")
    ai_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle context manager"""
    logger.info("Application starting up")
    yield
    logger.info("Application shutting down")
    # TODO: Cleanup persistent storage connections here


# Create FastAPI app
app = FastAPI(
    title="Food Event Planning Assistant",
    description="AI-powered assistant for planning food events",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Post-extraction processing (shared by REST and WebSocket handlers)
# ============================================================================


def apply_extraction(session: SessionData, extraction: ExtractionResult) -> None:
    """
    Apply an ExtractionResult to a session's event data.

    Handles:
    - Standard field updates
    - Meal plan delta merging (additions/removals)
    - Answered question tracking
    - Completion recomputation
    - Stage transitions
    """
    event_data = session.event_data

    # Clear transient fields that are only valid for one turn
    event_data.last_url_extraction_result = None

    # 1. Apply standard extracted fields (exclude special fields)
    exclude_fields = {
        "answered_questions",
        "meal_plan_additions",
        "meal_plan_removals",
        "meal_plan_confirmed",
        "recipe_promise_additions",
        "recipe_promise_resolutions",
        "recipe_confirmations",
        "recipes_confirmed",
        "pending_upload_dish",
        "output_formats",
    }
    extracted_data = extraction.model_dump(exclude_none=True, exclude=exclude_fields)
    if extracted_data:
        session.update_event_data(extracted_data)

    # 2. Meal plan delta merge
    if extraction.meal_plan_additions:
        existing_lower = [d.lower() for d in event_data.meal_plan]
        for dish in extraction.meal_plan_additions:
            if dish.lower() not in existing_lower:
                event_data.meal_plan.append(dish)
                existing_lower.append(dish.lower())

    if extraction.meal_plan_removals:
        removals_lower = [r.lower() for r in extraction.meal_plan_removals]
        event_data.meal_plan = [d for d in event_data.meal_plan if d.lower() not in removals_lower]

    # 3. Recipe promises — track dishes user claims to have their own recipe for
    if extraction.recipe_promise_additions:
        existing_lower = [p.lower() for p in event_data.recipe_promises]
        for dish in extraction.recipe_promise_additions:
            if dish.lower() not in existing_lower:
                event_data.recipe_promises.append(dish)
                existing_lower.append(dish.lower())

    if extraction.recipe_promise_resolutions:
        res_lower = {r.lower() for r in extraction.recipe_promise_resolutions}
        event_data.recipe_promises = [
            p for p in event_data.recipe_promises if p.lower() not in res_lower
        ]

    # 4. Mark answered questions
    for question_id in extraction.answered_questions:
        if question_id in event_data.answered_questions:
            event_data.answered_questions[question_id] = True

    # 5. Handle meal_plan_confirmed — only mark meal_plan answered when explicitly confirmed
    if extraction.meal_plan_confirmed and len(event_data.meal_plan) > 0:
        event_data.answered_questions["meal_plan"] = True

    # 6. Pending upload dish — persist while the recipe promise is still unresolved.
    # If the extraction sets a new value, use it. If it's null but the current dish
    # still has an unresolved promise, keep showing the panel.
    if extraction.pending_upload_dish is not None:
        event_data.pending_upload_dish = extraction.pending_upload_dish
    elif event_data.pending_upload_dish:
        dish_lower = event_data.pending_upload_dish.lower()
        still_pending = any(p.lower() == dish_lower for p in event_data.recipe_promises)
        if not still_pending:
            event_data.pending_upload_dish = None
        # else: keep it set so the panel remains visible

    # 6. Recipe confirmations — update source tracking per dish
    if extraction.recipe_confirmations:
        for rc in extraction.recipe_confirmations:
            for rs in event_data.recipe_sources:
                if rs.dish_name.lower() == rc.dish_name.lower():
                    if rc.confirmed is not None:
                        rs.confirmed = rc.confirmed
                    if rc.source_type:
                        rs.source_type = RecipeSourceType(rc.source_type)
                    if rc.description:
                        rs.description = rc.description

    # 7. Output format selection
    if extraction.output_formats:
        event_data.output_formats = [
            OutputFormat(f)
            for f in extraction.output_formats
            if f in [e.value for e in OutputFormat]
        ]

    # 7. Recompute completion score
    event_data.compute_derived_fields()

    # 8. Stage transitions
    if event_data.conversation_stage == "gathering" and event_data.is_complete:
        event_data.conversation_stage = "recipe_confirmation"
        # Initialize recipe_sources for each dish
        event_data.recipe_sources = [RecipeSource(dish_name=dish) for dish in event_data.meal_plan]

    elif event_data.conversation_stage == "recipe_confirmation":
        if extraction.recipes_confirmed or all(rs.confirmed for rs in event_data.recipe_sources):
            event_data.conversation_stage = "selecting_output"

    elif event_data.conversation_stage == "selecting_output":
        if len(event_data.output_formats) > 0:
            event_data.conversation_stage = "agent_running"


# ============================================================================
# REST Endpoints
# ============================================================================


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "ai_service_ready": ai_service is not None,
    }


@app.post("/api/sessions")
async def create_session():
    """Create a new conversation session"""
    session_id = session_manager.create_session()
    return {
        "session_id": session_id,
        "message": "Session created. Let's start planning your event!",
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session info"""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return session.to_dict()


@app.post("/api/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    """REST endpoint for chat (alternative to WebSocket)"""

    # Get or create session
    session = session_manager.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check AI service
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")

    # Add user message to history
    session.add_message(MessageRole.USER, request.message)

    last_assistant = next(
        (
            m.content
            for m in reversed(session.conversation_history)
            if m.role == MessageRole.ASSISTANT
        ),
        None,
    )
    extraction = ai_service.extract_event_data(request.message, session.event_data, last_assistant)
    apply_extraction(session, extraction)

    # Generate AI response
    ai_response = ai_service.generate_response(
        request.message, session.event_data, session.conversation_history
    )

    # Add AI response to history
    session.add_message(MessageRole.ASSISTANT, ai_response)

    # Return response
    return ChatResponse(
        session_id=request.session_id,
        message=ai_response,
        completion_score=session.event_data.completion_score,
        is_complete=session.event_data.is_complete,
        event_data=session.event_data.model_dump(),
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session"""
    if not session_manager.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    return {"message": "Session deleted"}


# ============================================================================
# WebSocket Endpoints
# ============================================================================


@app.websocket("/ws/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time chat

    Message format from client:
    {
        "type": "message",
        "data": "user message text"
    }

    Server response:
    {
        "type": "response" | "error",
        "data": {
            "message": "assistant response",
            "completion_score": 0.6,
            "is_complete": false,
            "event_data": {...}
        }
    }
    """

    # Verify session exists
    session = session_manager.get_session(session_id)
    if not session:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found")
        return

    await websocket.accept()
    logger.info(f"WebSocket connection established for session {session_id}")

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()

            msg_type = data.get("type")
            msg_data = data.get("data")

            if msg_type != "message" or not msg_data:
                await websocket.send_json(
                    {"type": "error", "data": {"error": "Invalid message format"}}
                )
                continue

            # Check AI service
            if not ai_service:
                await websocket.send_json(
                    {"type": "error", "data": {"error": "AI service not available"}}
                )
                continue

            try:
                # Add user message
                session.add_message(MessageRole.USER, msg_data)

                last_assistant = next(
                    (
                        m.content
                        for m in reversed(session.conversation_history)
                        if m.role == MessageRole.ASSISTANT
                    ),
                    None,
                )
                extraction = ai_service.extract_event_data(
                    msg_data, session.event_data, last_assistant
                )
                apply_extraction(session, extraction)

                # Auto-extract any recipe URL provided in this message.
                # Must run before AI response so the AI can surface failures/successes.
                for rc in extraction.recipe_confirmations or []:
                    if rc.url:
                        try:
                            ingredients = await ai_service.extract_recipe_from_url(rc.url)
                            if not ingredients:
                                raise ValueError("No ingredient list found on that page")
                            for rs in session.event_data.recipe_sources:
                                if rs.dish_name.lower() == rc.dish_name.lower():
                                    rs.source_type = RecipeSourceType.USER_URL
                                    rs.url = rc.url
                                    rs.extracted_ingredients = [i.model_dump() for i in ingredients]
                                    rs.confirmed = True
                            # Resolve the promise if it exists
                            session.event_data.recipe_promises = [
                                p
                                for p in session.event_data.recipe_promises
                                if p.lower() != rc.dish_name.lower()
                            ]
                            session.event_data.last_url_extraction_result = {
                                "dish": rc.dish_name,
                                "success": True,
                                "ingredient_count": len(ingredients),
                            }
                        except Exception as url_err:
                            logger.warning(
                                "URL extraction failed for '%s': %s", rc.dish_name, url_err
                            )
                            session.event_data.last_url_extraction_result = {
                                "dish": rc.dish_name,
                                "success": False,
                                "error": str(url_err),
                            }

                # If we just transitioned to agent_running, hand off to the agent
                if session.event_data.conversation_stage == "agent_running":
                    from app.agent.runner import run_agent

                    session.agent_state = await run_agent(
                        websocket,
                        session.event_data,
                        session.event_data.output_formats,
                        ai_service,
                        existing_state=session.agent_state,
                    )
                    continue

                # Send metadata immediately
                await websocket.send_json(
                    {
                        "type": "stream_start",
                        "data": {
                            "completion_score": session.event_data.completion_score,
                            "is_complete": session.event_data.is_complete,
                            "event_data": session.event_data.model_dump(),
                        },
                    }
                )

                # Stream AI response in chunks
                full_response = []
                for chunk in ai_service.generate_response_stream(
                    msg_data, session.event_data, session.conversation_history
                ):
                    await websocket.send_json({"type": "stream_chunk", "data": {"text": chunk}})
                    full_response.append(chunk)

                # Signal done and save complete message to history
                session.add_message(MessageRole.ASSISTANT, "".join(full_response))
                await websocket.send_json({"type": "stream_end"})

            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await websocket.send_json(
                    {"type": "error", "data": {"error": "Failed to process message"}}
                )

    except Exception as e:
        logger.error(f"WebSocket error: {e}")

    finally:
        logger.info(f"WebSocket connection closed for session {session_id}")


# ============================================================================
# Recipe Extraction Endpoints
# ============================================================================


@app.post("/api/sessions/{session_id}/extract-recipe")
async def extract_recipe_from_url(session_id: str, body: dict):
    """Extract ingredients from a recipe URL."""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")

    url = body.get("url")
    dish_name = body.get("dish_name")
    if not url or not dish_name:
        raise HTTPException(status_code=400, detail="url and dish_name are required")

    FALLBACK_MSG = (
        "Try taking a screenshot of the recipe and uploading it instead, "
        "or describe the key ingredients in the chat."
    )

    try:
        ingredients = await ai_service.extract_recipe_from_url(url)
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        if status_code == 403:
            detail = f"That site blocked access (403 Forbidden). {FALLBACK_MSG}"
        elif status_code == 404:
            detail = f"URL not found (404). Double-check the link. {FALLBACK_MSG}"
        else:
            detail = f"Failed to fetch the URL (HTTP {status_code}). {FALLBACK_MSG}"
        return {"dish_name": dish_name, "ingredients": [], "success": False, "message": detail}
    except httpx.RequestError:
        detail = f"Could not reach that URL (network error or timeout). {FALLBACK_MSG}"
        return {"dish_name": dish_name, "ingredients": [], "success": False, "message": detail}
    except Exception as e:
        logger.error(f"extract_recipe_from_url failed: {e}")
        detail = f"Something went wrong extracting the recipe. {FALLBACK_MSG}"
        return {"dish_name": dish_name, "ingredients": [], "success": False, "message": detail}

    if not ingredients:
        detail = (
            "Couldn't find an ingredient list on that page — it may require a login, "
            f"use a non-standard format, or just not contain a recipe. {FALLBACK_MSG}"
        )
        return {"dish_name": dish_name, "ingredients": [], "success": False, "message": detail}

    # Update the recipe source for this dish
    for rs in session.event_data.recipe_sources:
        if rs.dish_name.lower() == dish_name.lower():
            rs.source_type = RecipeSourceType.USER_URL
            rs.url = url
            rs.extracted_ingredients = [i.model_dump() for i in ingredients]
            break

    return {
        "dish_name": dish_name,
        "ingredients": [i.model_dump() for i in ingredients],
        "success": True,
        "message": None,
    }


@app.post("/api/sessions/{session_id}/upload-recipe")
async def upload_recipe(
    session_id: str,
    dish_name: str,
    file: UploadFile = File(...),
):
    """Extract ingredients from an uploaded recipe file (PDF, TXT, JPG, PNG)."""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")

    allowed_types = {
        "application/pdf",
        "text/plain",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: {', '.join(allowed_types)}",
        )

    content = await file.read()
    ingredients = await ai_service.extract_recipe_from_file(content, file.content_type)

    # Update the recipe source and resolve any gathering-stage promise for this dish
    for rs in session.event_data.recipe_sources:
        if rs.dish_name.lower() == dish_name.lower():
            rs.source_type = RecipeSourceType.USER_UPLOAD
            rs.extracted_ingredients = [i.model_dump() for i in ingredients]
            rs.confirmed = True
            break

    session.event_data.recipe_promises = [
        p for p in session.event_data.recipe_promises if p.lower() != dish_name.lower()
    ]

    return {
        "dish_name": dish_name,
        "ingredients": [i.model_dump() for i in ingredients],
    }


# ============================================================================
# Debug Endpoints (TODO: Protect with auth in production)
# ============================================================================


@app.get("/debug/sessions")
async def debug_list_sessions():
    """Debug endpoint to list all active sessions"""
    # TODO: Add authentication/authorization
    return {
        "active_sessions": len(session_manager.sessions),
        "sessions": session_manager.list_active_sessions(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

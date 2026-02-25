import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.models.chat import ChatRequest, ChatResponse, MessageRole
from app.models.event import (
    ExtractionResult,
    OutputFormat,
    PreparationMethod,
    RecipeSourceType,
    RecipeStatus,
    RecipeType,
)
from app.services.ai_service import GeminiService
from app.services.session_manager import SessionData, session_manager

# Load environment variables (override=True ensures .env wins over any shell env vars)
load_dotenv(override=True)

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
# Google OAuth constants (used by helpers and endpoints below)
# ============================================================================

GOOGLE_TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
_OAUTH_REDIRECT_URI = os.getenv(
    "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback"
)

# ============================================================================
# Shared helpers
# ============================================================================


def _build_oauth_flow():
    """Build a Google OAuth Flow from environment config."""
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        {
            "web": {
                "client_id": _OAUTH_CLIENT_ID,
                "client_secret": _OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=[GOOGLE_TASKS_SCOPE],
        redirect_uri=_OAUTH_REDIRECT_URI,
    )


# ============================================================================
# Post-extraction processing (shared by REST and WebSocket handlers)
# ============================================================================


def apply_extraction(session: SessionData, extraction: ExtractionResult) -> None:
    """
    Apply an ExtractionResult to a session's event data.

    Handles:
    - Standard field updates
    - Recipe updates (add/remove/update recipes in meal plan)
    - Answered question tracking
    - Completion recomputation
    - Stage transitions
    """
    event_data = session.event_data

    # Clear transient fields that are only valid for one turn
    event_data.last_url_extraction_result = None
    event_data.last_generated_recipes = None

    # 1. Apply standard extracted fields (exclude special fields)
    exclude_fields = {"answered_questions", "recipe_updates", "meal_plan_confirmed", "output_formats"}
    extracted_data = extraction.model_dump(exclude_none=True, exclude=exclude_fields)
    if extracted_data:
        session.update_event_data(extracted_data)

    # 2. Apply recipe updates to meal plan
    if extraction.recipe_updates:
        from app.models.event import Recipe, RecipeStatus

        for update in extraction.recipe_updates:
            if update.action == "add":
                # Add new recipe if not already present
                if not event_data.meal_plan.find_recipe(update.recipe_name):
                    event_data.meal_plan.add_recipe(
                        Recipe(
                            name=update.recipe_name,
                            status=update.status or RecipeStatus.NAMED,
                            awaiting_user_input=update.awaiting_user_input or False,
                        )
                    )

            elif update.action == "remove":
                event_data.meal_plan.remove_recipe(update.recipe_name)

            elif update.action == "update":
                recipe = event_data.meal_plan.find_recipe(update.recipe_name)
                if recipe:
                    # Extract non-None fields from update, excluding meta fields
                    updates = update.model_dump(exclude_none=True, exclude={"recipe_name", "action"})
                    # Handle rename: new_name → name
                    if "new_name" in updates:
                        updates["name"] = updates.pop("new_name")

                    # Apply updates using model_copy
                    idx = event_data.meal_plan.recipes.index(recipe)
                    event_data.meal_plan.recipes[idx] = recipe.model_copy(update=updates)

    # 3. Mark answered questions
    for question_id in extraction.answered_questions:
        if question_id in event_data.answered_questions:
            event_data.answered_questions[question_id] = True

    # 4. Handle meal_plan_confirmed
    if extraction.meal_plan_confirmed:
        event_data.meal_plan.confirmed = True
        if len(event_data.meal_plan.recipes) > 0:
            event_data.answered_questions["meal_plan"] = True

    # 5. Output format selection
    if extraction.output_formats:
        event_data.output_formats = [
            OutputFormat(f) for f in extraction.output_formats if f in [e.value for e in OutputFormat]
        ]

    # 6. Recompute completion score
    event_data.compute_derived_fields()

    # 7. Stage transitions
    if event_data.conversation_stage == "gathering" and event_data.is_complete:
        event_data.conversation_stage = "recipe_confirmation"
    elif event_data.conversation_stage == "recipe_confirmation":
        if event_data.meal_plan.is_complete:
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
    extraction = await ai_service.extract_event_data(
        request.message, session.event_data, last_assistant
    )
    apply_extraction(session, extraction)

    # Generate AI response
    ai_response = await ai_service.generate_response(
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
                logger.info(
                    "📨 USER MESSAGE (session=%s, stage=%s): %s",
                    session_id[:8],
                    session.event_data.conversation_stage,
                    msg_data[:100],
                )

                last_assistant = next(
                    (
                        m.content
                        for m in reversed(session.conversation_history)
                        if m.role == MessageRole.ASSISTANT
                    ),
                    None,
                )
                extraction = await ai_service.extract_event_data(
                    msg_data, session.event_data, last_assistant
                )
                logger.info(
                    "🔄 APPLYING EXTRACTION (stage=%s): %d recipe_updates, meal_plan_confirmed=%s",
                    session.event_data.conversation_stage,
                    len(extraction.recipe_updates) if extraction.recipe_updates else 0,
                    extraction.meal_plan_confirmed,
                )
                apply_extraction(session, extraction)

                # Handle URL/description extraction if provided in recipe_updates
                if extraction.recipe_updates:
                    for update in extraction.recipe_updates:
                        if update.url:
                            try:
                                logger.info(
                                    "Extracting recipe from URL for '%s': %s", update.recipe_name, update.url
                                )
                                ingredients = await ai_service.extract_recipe_from_url(update.url)
                                if not ingredients:
                                    raise ValueError("No ingredient list found on that page")
                                # Update the recipe
                                recipe = session.event_data.meal_plan.find_recipe(update.recipe_name)
                                if recipe:
                                    recipe.ingredients = [i.model_dump() for i in ingredients]
                                    recipe.source_type = RecipeSourceType.USER_URL
                                    recipe.url = update.url
                                    recipe.status = RecipeStatus.COMPLETE
                                    recipe.awaiting_user_input = False
                                session.event_data.last_url_extraction_result = {
                                    "dish": update.recipe_name,
                                    "success": True,
                                    "ingredient_count": len(ingredients),
                                }
                            except Exception as url_err:
                                logger.warning(
                                    "URL extraction failed for '%s': %s", update.recipe_name, url_err
                                )
                                session.event_data.last_url_extraction_result = {
                                    "dish": update.recipe_name,
                                    "success": False,
                                    "error": str(url_err),
                                }
                        elif update.description:
                            try:
                                logger.info(
                                    "Extracting recipe from description for '%s': %s", update.recipe_name, update.description[:100]
                                )
                                ingredients = await ai_service.extract_recipe_from_description(
                                    update.description
                                )
                                recipe = session.event_data.meal_plan.find_recipe(update.recipe_name)
                                if recipe:
                                    recipe.ingredients = [i.model_dump() for i in ingredients]
                                    recipe.source_type = RecipeSourceType.USER_DESCRIPTION
                                    recipe.description = update.description
                                    recipe.status = RecipeStatus.COMPLETE
                                    recipe.awaiting_user_input = False
                            except Exception as desc_err:
                                logger.warning(
                                    "Description extraction failed for '%s': %s",
                                    update.recipe_name,
                                    desc_err,
                                )

                # During recipe_confirmation, auto-generate default ingredient lists for
                # any AI_DEFAULT recipes that haven't been generated yet.
                # EXCLUDE beverages and store-bought items - they don't need recipe extraction.
                if session.event_data.conversation_stage == "recipe_confirmation":
                    recipes_needing_ingredients = [
                        r
                        for r in session.event_data.meal_plan.recipes
                        if r.source_type == RecipeSourceType.AI_DEFAULT
                        and r.needs_ingredients()
                        and r.status != RecipeStatus.PLACEHOLDER  # Skip placeholders — model will invent wrong dish
                        and not (r.recipe_type == RecipeType.DRINK and r.preparation_method == PreparationMethod.STORE_BOUGHT)
                        and r.preparation_method != PreparationMethod.STORE_BOUGHT  # Also skip store-bought food
                    ]
                    if recipes_needing_ingredients:
                        logger.info(
                            "Auto-generating default ingredients for %d AI_DEFAULT recipes (batched)",
                            len(recipes_needing_ingredients),
                        )
                        results = await ai_service.generate_default_recipes_batch(
                            [r.name for r in recipes_needing_ingredients]
                        )
                        newly_generated = []
                        for recipe, ingredients in zip(recipes_needing_ingredients, results):
                            recipe.ingredients = [i.model_dump() for i in ingredients]
                            recipe.status = RecipeStatus.COMPLETE
                            newly_generated.append(
                                {"dish": recipe.name, "ingredients": recipe.ingredients}
                            )
                        session.event_data.last_generated_recipes = newly_generated

                # If we just transitioned to agent_running, hand off to the agent
                if session.event_data.conversation_stage == "agent_running":
                    from app.agent.runner import run_agent
                    from app.models.event import OutputFormat
                    from app.services.tasks_service import TasksService

                    # If Google Tasks was selected but the user hasn't connected yet,
                    # notify the frontend (so it shows the OAuth button) and wait.
                    needs_google_auth = (
                        _OAUTH_CLIENT_ID
                        and _OAUTH_CLIENT_SECRET
                        and OutputFormat.GOOGLE_TASKS in session.event_data.output_formats
                        and not session.google_credentials
                    )
                    if needs_google_auth:
                        await websocket.send_json({
                            "type": "stream_start",
                            "data": {
                                "completion_score": session.event_data.completion_score,
                                "is_complete": session.event_data.is_complete,
                                "event_data": session.event_data.model_dump(),
                            },
                        })
                        await websocket.send_json({"type": "stream_end"})
                        continue

                    tasks_service = None
                    if _OAUTH_CLIENT_ID and _OAUTH_CLIENT_SECRET and OutputFormat.GOOGLE_TASKS in session.event_data.output_formats:
                        tasks_service = TasksService.from_token_dict(session.google_credentials)

                    session.agent_state = await run_agent(
                        websocket,
                        session.event_data,
                        session.event_data.output_formats,
                        ai_service,
                        existing_state=session.agent_state,
                        tasks_service=tasks_service,
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
                async for chunk in ai_service.generate_response_stream(
                    msg_data, session.event_data, session.conversation_history
                ):
                    await websocket.send_json({"type": "stream_chunk", "data": {"text": chunk}})
                    full_response.append(chunk)

                # Signal done and save complete message to history
                complete_response = "".join(full_response)
                session.add_message(MessageRole.ASSISTANT, complete_response)
                logger.info(
                    "💬 ASSISTANT RESPONSE (session=%s): %s",
                    session_id[:8],
                    complete_response[:100] + "..." if len(complete_response) > 100 else complete_response,
                )
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

    recipe = session.event_data.meal_plan.find_recipe(dish_name)
    if not recipe:
        raise HTTPException(status_code=404, detail=f"Recipe '{dish_name}' not found in meal plan")

    recipe.source_type = RecipeSourceType.USER_URL
    recipe.url = url
    recipe.ingredients = [i.model_dump() for i in ingredients]
    recipe.status = RecipeStatus.COMPLETE
    recipe.awaiting_user_input = False

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
    extracted_dish_name, ingredients = await ai_service.extract_recipe_from_file(
        content, file.content_type
    )

    recipe = session.event_data.meal_plan.find_recipe(dish_name)
    if not recipe:
        raise HTTPException(status_code=404, detail=f"Recipe '{dish_name}' not found in meal plan")

    final_dish_name = dish_name

    # If this is a placeholder and we extracted an actual dish name, rename it
    if recipe.status == RecipeStatus.PLACEHOLDER and extracted_dish_name:
        recipe.name = extracted_dish_name
        final_dish_name = extracted_dish_name
        logger.info(f"Replaced placeholder '{dish_name}' with extracted '{extracted_dish_name}'")

    # Update recipe with ingredients and source info
    recipe.ingredients = [i.model_dump() for i in ingredients]
    recipe.source_type = RecipeSourceType.USER_UPLOAD
    recipe.awaiting_user_input = False
    # Set status to COMPLETE only if we got ingredients
    recipe.status = RecipeStatus.COMPLETE if len(ingredients) > 0 else RecipeStatus.NAMED

    return {
        "dish_name": final_dish_name,
        "ingredients": [i.model_dump() for i in ingredients],
    }


# ============================================================================
# Google OAuth Endpoints
# ============================================================================


@app.get("/api/auth/google/start")
async def google_auth_start(session_id: str):
    """
    Generate a Google OAuth authorization URL for the given session.
    The frontend opens this URL in a popup to begin the OAuth flow.
    Returns 503 if OAuth credentials are not configured.
    """
    if not _OAUTH_CLIENT_ID or not _OAUTH_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET.",
        )

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    flow = _build_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=session_id,
        prompt="consent",
    )
    return {"auth_url": auth_url}


@app.get("/api/auth/google/callback", response_class=HTMLResponse)
async def google_auth_callback(code: str, state: str):
    """
    OAuth callback — exchanges the authorization code for tokens and stores
    them on the session identified by the `state` parameter (session_id).
    Returns an HTML page that closes the popup window.
    """
    session = session_manager.get_session(state)
    if not session:
        return HTMLResponse("<script>window.close();</script>", status_code=400)

    try:
        flow = _build_oauth_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        session.google_credentials = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else [GOOGLE_TASKS_SCOPE],
        }
        logger.info("Google OAuth complete for session %s", state)
    except Exception as exc:
        logger.error("Google OAuth callback failed: %s", exc)
        return HTMLResponse(
            "<script>window.opener?.postMessage('google_auth_error','*');window.close();</script>",
            status_code=200,
        )

    return HTMLResponse(
        "<script>window.opener?.postMessage('google_auth_complete','*');window.close();</script>"
    )


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

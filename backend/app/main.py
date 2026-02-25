from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google_login import build_login_flow, is_login_configured
from app.auth.jwt import create_access_token, decode_access_token_raw, get_current_user
from app.db.database import async_session_factory, engine, get_db
from app.db.models import User
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
from app.services.db_session_manager import db_session_manager
from app.services.plan_manager import plan_manager
from app.services.session_manager import SessionData

# Load environment variables (override=True ensures .env wins over any shell env vars)
load_dotenv(override=True)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5174")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize services
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
    await engine.dispose()


# Create FastAPI app
app = FastAPI(
    title="Food Event Planning Assistant",
    description="AI-powered assistant for planning food events",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware — explicit origins required when credentials: 'include' is used
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Google OAuth constants — Tasks OAuth (popup, offline, tasks scope)
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
    """Build a Google OAuth Flow for the Tasks permission (existing flow)."""
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
    # If any recipes were added or removed this turn, reset confirmation — the user
    # needs to see and approve the updated ingredient list before we can proceed.
    if extraction.recipe_updates:
        if any(u.action in ("add", "remove") for u in extraction.recipe_updates):
            event_data.meal_plan.confirmed = False
            event_data.answered_questions["meal_plan"] = False

    if extraction.meal_plan_confirmed:
        event_data.meal_plan.confirmed = True
        if len(event_data.meal_plan.recipes) > 0:
            event_data.answered_questions["meal_plan"] = True

    # 5. Output format selection — only honoured once the user is actually choosing
    # an output format. Ignoring it in earlier stages prevents the AI from
    # accidentally extracting output_formats during recipe_confirmation and
    # short-circuiting the selecting_output stage entirely.
    if extraction.output_formats and event_data.conversation_stage == "selecting_output":
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
# Auth helpers
# ============================================================================


async def _upsert_user(db: AsyncSession, google_id: str, email: str, name: str, picture: Optional[str]) -> User:
    """Insert or update a User row based on google_id."""
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(google_id=google_id, email=email, name=name, picture=picture)
        db.add(user)
    else:
        user.email = email
        user.name = name
        user.picture = picture
    await db.commit()
    await db.refresh(user)
    return user


async def _require_session_owner(
    session_id: str,
    current_user: User,
    db: AsyncSession,
) -> SessionData:
    """Fetch a session and verify ownership. Returns SessionData or raises HTTP 403/404."""
    row = await db_session_manager.get_session_row(session_id, db)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    session = db_session_manager._row_to_session_data(row)
    return session


# ============================================================================
# REST Endpoints — Health
# ============================================================================


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "ai_service_ready": ai_service is not None,
    }


# ============================================================================
# Login OAuth Endpoints
# ============================================================================


@app.get("/api/auth/login")
async def login():
    """Redirect the browser to Google's OAuth consent page for login."""
    if not is_login_configured():
        raise HTTPException(
            status_code=503,
            detail="Google login OAuth is not configured.",
        )
    flow = build_login_flow()
    auth_url, state = flow.authorization_url(access_type="online", prompt="select_account")
    response = RedirectResponse(url=auth_url, status_code=302)
    # Store state in a short-lived httpOnly cookie for CSRF verification on callback
    response.set_cookie("login_state", state, httponly=True, max_age=600, samesite="lax")
    return response


@app.get("/api/auth/callback")
async def auth_callback(
    request: Request,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """OAuth callback — exchange code for tokens, upsert user, set JWT cookie."""
    stored_state = request.cookies.get("login_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    flow = build_login_flow()
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
    except Exception as exc:
        logger.error("Login OAuth token exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail="OAuth token exchange failed")

    # Fetch user info from Google
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
            )
            resp.raise_for_status()
            user_info = resp.json()
    except Exception as exc:
        logger.error("Failed to fetch Google user info: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to retrieve user info from Google")

    google_id = user_info.get("sub")
    email = user_info.get("email", "")
    name = user_info.get("name", email)
    picture = user_info.get("picture")

    if not google_id:
        raise HTTPException(status_code=502, detail="Google did not return a user ID")

    user = await _upsert_user(db, google_id=google_id, email=email, name=name, picture=picture)
    token = create_access_token(str(user.id))

    response = RedirectResponse(url=FRONTEND_URL, status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )
    response.delete_cookie("login_state")
    return response


@app.get("/api/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's info."""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "name": current_user.name,
        "picture": current_user.picture,
    }


@app.post("/api/auth/logout")
async def logout():
    """Clear the auth cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("access_token")
    return response


# ============================================================================
# Session Endpoints
# ============================================================================


@app.post("/api/sessions")
async def create_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation session"""
    session_id = await db_session_manager.create_session(current_user.id, db)
    return {
        "session_id": session_id,
        "message": "Session created. Let's start planning your event!",
    }


@app.get("/api/sessions")
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all sessions for the current user (lightweight summary, ordered by most recent)."""
    sessions = await db_session_manager.list_user_sessions_summary(current_user.id, db)
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full session info including conversation history."""
    session = await _require_session_owner(session_id, current_user, db)
    data = session.to_dict()
    data["conversation_history"] = [
        {"role": m.role.value, "content": m.content}
        for m in session.conversation_history
    ]
    return data


@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """REST endpoint for chat (alternative to WebSocket)"""
    session = await _require_session_owner(request.session_id, current_user, db)

    if not ai_service:
        raise HTTPException(status_code=503, detail="AI service not available")

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

    ai_response = await ai_service.generate_response(
        request.message, session.event_data, session.conversation_history
    )
    session.add_message(MessageRole.ASSISTANT, ai_response)

    await db_session_manager.save_session(session, db)

    return ChatResponse(
        session_id=request.session_id,
        message=ai_response,
        completion_score=session.event_data.completion_score,
        is_complete=session.event_data.is_complete,
        event_data=session.event_data.model_dump(),
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a session"""
    await _require_session_owner(session_id, current_user, db)
    await db_session_manager.delete_session(session_id, db)
    return {"message": "Session deleted"}


# ============================================================================
# WebSocket Endpoint
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
    # --- Auth: validate JWT from cookie ---
    token = websocket.cookies.get("access_token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated")
        return

    user_id_str = decode_access_token_raw(token)
    if not user_id_str:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return

    # --- Load session and verify ownership ---
    async with async_session_factory() as db:
        row = await db_session_manager.get_session_row(session_id, db)
        if row is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found")
            return
        try:
            if str(row.user_id) != user_id_str:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Forbidden")
                return
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Forbidden")
            return
        session = db_session_manager._row_to_session_data(row)

    await websocket.accept()
    logger.info(f"WebSocket connection established for session {session_id}")

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()

            msg_type = data.get("type")
            msg_data = data.get("data")

            if msg_type not in ("message", "select_outputs") or (msg_type == "message" and not msg_data):
                await websocket.send_json(
                    {"type": "error", "data": {"error": "Invalid message format"}}
                )
                continue

            # Check AI service (only needed for regular messages)
            if msg_type == "message" and not ai_service:
                await websocket.send_json(
                    {"type": "error", "data": {"error": "AI service not available"}}
                )
                continue

            try:
                if msg_type == "select_outputs":
                    # Direct output format selection — bypass AI extraction entirely
                    formats = msg_data if isinstance(msg_data, list) else []
                    valid = [
                        OutputFormat(f)
                        for f in formats
                        if f in [e.value for e in OutputFormat]
                    ]
                    if valid:
                        session.event_data.output_formats = valid
                        session.event_data.conversation_stage = "agent_running"
                        logger.info(
                            "🎯 OUTPUT SELECTED (session=%s): %s",
                            session_id[:8],
                            [f.value for f in valid],
                        )
                else:
                    # Regular message processing
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
                                    recipe = session.event_data.meal_plan.find_recipe(update.recipe_name)
                                    if recipe:
                                        recipe.ingredients = [i.model_dump(mode="json") for i in ingredients]
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
                                        recipe.ingredients = [i.model_dump(mode="json") for i in ingredients]
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
                    if session.event_data.conversation_stage == "recipe_confirmation":
                        recipes_needing_ingredients = [
                            r
                            for r in session.event_data.meal_plan.recipes
                            if r.needs_ingredients()
                            and not r.awaiting_user_input
                            and r.status != RecipeStatus.PLACEHOLDER
                            and not (r.recipe_type == RecipeType.DRINK and r.preparation_method == PreparationMethod.STORE_BOUGHT)
                            and r.preparation_method != PreparationMethod.STORE_BOUGHT
                        ]
                        if recipes_needing_ingredients:
                            logger.info(
                                "Auto-generating default ingredients for %d recipes (batched)",
                                len(recipes_needing_ingredients),
                            )
                            results = await ai_service.generate_default_recipes_batch(
                                [r.name for r in recipes_needing_ingredients]
                            )
                            newly_generated = []
                            for recipe, ingredients in zip(recipes_needing_ingredients, results):
                                recipe.ingredients = [i.model_dump(mode="json") for i in ingredients]
                                recipe.status = RecipeStatus.COMPLETE
                                newly_generated.append(
                                    {"dish": recipe.name, "ingredients": recipe.ingredients}
                                )
                            session.event_data.last_generated_recipes = newly_generated
                            session.event_data.compute_derived_fields()

                # If we just transitioned to agent_running, hand off to the agent
                if session.event_data.conversation_stage == "agent_running":
                    from app.agent.runner import run_agent
                    from app.services.tasks_service import TasksService

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
                        # Save before continuing (output_formats may have changed)
                        async with async_session_factory() as db:
                            await db_session_manager.save_session(session, db)
                        continue

                    await websocket.send_json({
                        "type": "event_data_update",
                        "data": {
                            "completion_score": session.event_data.completion_score,
                            "is_complete": session.event_data.is_complete,
                            "event_data": session.event_data.model_dump(),
                        },
                    })

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

                    session.event_data.conversation_stage = "complete"

                    # Auto-save a plan snapshot now that the agent has finished
                    if session.agent_state and session.agent_state.shopping_list is not None:
                        try:
                            async with async_session_factory() as plan_db:
                                await plan_manager.save_plan(
                                    user_id=uuid.UUID(user_id_str),
                                    session_id=uuid.UUID(session_id),
                                    event_data=session.event_data,
                                    agent_state=session.agent_state,
                                    db=plan_db,
                                )
                        except Exception as plan_err:
                            logger.error("Failed to auto-save plan: %s", plan_err)

                    await websocket.send_json({
                        "type": "event_data_update",
                        "data": {
                            "completion_score": session.event_data.completion_score,
                            "is_complete": session.event_data.is_complete,
                            "event_data": session.event_data.model_dump(),
                        },
                    })
                    async with async_session_factory() as db:
                        await db_session_manager.save_session(session, db)
                    continue

                # When stage is selecting_output, send structured options card
                if session.event_data.conversation_stage == "selecting_output":
                    await websocket.send_json({
                        "type": "event_data_update",
                        "data": {
                            "completion_score": session.event_data.completion_score,
                            "is_complete": session.event_data.is_complete,
                            "event_data": session.event_data.model_dump(),
                        },
                    })
                    await websocket.send_json({
                        "type": "output_selection",
                        "options": [
                            {
                                "value": "google_sheet",
                                "label": "Google Sheet",
                                "description": "Formula-driven spreadsheet, quantities auto-adjust",
                            },
                            {
                                "value": "google_tasks",
                                "label": "Google Tasks",
                                "description": "Checklist format, great for shopping on your phone",
                            },
                            {
                                "value": "in_chat",
                                "label": "In-chat list",
                                "description": "Formatted list right here in the conversation",
                            },
                        ],
                    })
                    async with async_session_factory() as db:
                        await db_session_manager.save_session(session, db)
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

                complete_response = "".join(full_response)
                session.add_message(MessageRole.ASSISTANT, complete_response)
                logger.info(
                    "💬 ASSISTANT RESPONSE (session=%s): %s",
                    session_id[:8],
                    complete_response[:100] + "..." if len(complete_response) > 100 else complete_response,
                )
                await websocket.send_json({"type": "stream_end"})

                # Persist session state after each message round-trip
                async with async_session_factory() as db:
                    await db_session_manager.save_session(session, db)

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
async def extract_recipe_from_url(
    session_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Extract ingredients from a recipe URL."""
    session = await _require_session_owner(session_id, current_user, db)
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
    recipe.ingredients = [i.model_dump(mode="json") for i in ingredients]
    recipe.status = RecipeStatus.COMPLETE
    recipe.awaiting_user_input = False

    await db_session_manager.save_session(session, db)

    return {
        "dish_name": dish_name,
        "ingredients": [i.model_dump(mode="json") for i in ingredients],
        "success": True,
        "message": None,
    }


@app.post("/api/sessions/{session_id}/upload-recipe")
async def upload_recipe(
    session_id: str,
    dish_name: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Extract ingredients from an uploaded recipe file (PDF, TXT, JPG, PNG)."""
    session = await _require_session_owner(session_id, current_user, db)
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

    if recipe.status == RecipeStatus.PLACEHOLDER and extracted_dish_name:
        recipe.name = extracted_dish_name
        final_dish_name = extracted_dish_name
        logger.info(f"Replaced placeholder '{dish_name}' with extracted '{extracted_dish_name}'")

    recipe.ingredients = [i.model_dump(mode="json") for i in ingredients]
    recipe.source_type = RecipeSourceType.USER_UPLOAD
    recipe.awaiting_user_input = False
    recipe.status = RecipeStatus.COMPLETE if len(ingredients) > 0 else RecipeStatus.NAMED

    await db_session_manager.save_session(session, db)

    return {
        "dish_name": final_dish_name,
        "ingredients": [i.model_dump(mode="json") for i in ingredients],
    }


# ============================================================================
# Google OAuth Endpoints — Tasks (popup-based, separate from login)
# ============================================================================


@app.get("/api/auth/google/start")
async def google_auth_start(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a Google OAuth authorization URL for Tasks access.
    The frontend opens this URL in a popup to begin the OAuth flow.
    """
    if not _OAUTH_CLIENT_ID or not _OAUTH_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET.",
        )

    await _require_session_owner(session_id, current_user, db)

    flow = _build_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=session_id,
        prompt="consent",
    )
    return {"auth_url": auth_url}


@app.get("/api/auth/google/callback", response_class=HTMLResponse)
async def google_auth_callback(
    code: str,
    state: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    OAuth callback — exchanges the authorization code for Tasks tokens and stores
    them on the session identified by the `state` parameter (session_id).
    Returns an HTML page that closes the popup window.
    """
    try:
        session = await _require_session_owner(state, current_user, db)
    except HTTPException:
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
        await db_session_manager.save_session(session, db)
        logger.info("Google Tasks OAuth complete for session %s", state)
    except Exception as exc:
        logger.error("Google Tasks OAuth callback failed: %s", exc)
        return HTMLResponse(
            "<script>window.opener?.postMessage('google_auth_error','*');window.close();</script>",
            status_code=200,
        )

    return HTMLResponse(
        "<script>window.opener?.postMessage('google_auth_complete','*');window.close();</script>"
    )


# ============================================================================
# Saved Plans Endpoints
# ============================================================================


@app.get("/api/plans")
async def list_plans(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all saved plans for the current user (lightweight summary)."""
    plans = await plan_manager.list_user_plans(current_user.id, db)
    return {"plans": plans}


@app.get("/api/plans/{plan_id}")
async def get_plan(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get full detail for a saved plan."""
    row = await plan_manager.get_plan(plan_id, db)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {
        "id": str(row.id),
        "name": row.name,
        "created_at": row.created_at.isoformat(),
        "event_data": row.event_data,
        "shopping_list": row.shopping_list,
        "formatted_output": row.formatted_output,
        "formatted_recipes_output": row.formatted_recipes_output,
    }


@app.delete("/api/plans/{plan_id}")
async def delete_plan(
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved plan."""
    row = await plan_manager.get_plan(plan_id, db)
    if row is None or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Plan not found")
    await plan_manager.delete_plan(plan_id, db)
    return {"ok": True}


# ============================================================================
# Debug Endpoints
# ============================================================================


@app.get("/debug/sessions")
async def debug_list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Debug endpoint — lists the current user's sessions."""
    sessions = await db_session_manager.list_user_sessions(current_user.id, db)
    return {
        "user_id": str(current_user.id),
        "session_count": len(sessions),
        "sessions": sessions,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

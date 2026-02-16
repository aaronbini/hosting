import os
import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.services.session_manager import session_manager
from app.services.ai_service import GeminiService
from app.models.chat import MessageRole, ChatRequest, ChatResponse

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
    lifespan=lifespan
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
        raise HTTPException(
            status_code=503,
            detail="AI service not available"
        )
    
    # Add user message to history
    session.add_message(MessageRole.USER, request.message)
        
    extraction = ai_service.extract_event_data(request.message, session.event_data)
    extracted_data = extraction.model_dump(exclude_none=True, exclude={"answered_questions"})
    answered_questions = extraction.answered_questions

    # Update event data with extracted information
    if extracted_data:
        session.update_event_data(extracted_data)
    
    # Mark answered questions
    for question_id in answered_questions:
        session.event_data.answered_questions[question_id] = True
    
    # Recompute completion score with updated question answers
    session.event_data.compute_derived_fields()
    
    # Generate AI response
    ai_response = ai_service.generate_response(
        request.message,
        session.event_data,
        session.conversation_history
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
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": "Invalid message format"}
                })
                continue
            
            # Check AI service
            if not ai_service:
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": "AI service not available"}
                })
                continue
            
            try:
                # Add user message
                session.add_message(MessageRole.USER, msg_data)
                                
                extraction = ai_service.extract_event_data(msg_data, session.event_data)
                extracted_data = extraction.model_dump(exclude_none=True, exclude={"answered_questions"})
                answered_questions = extraction.answered_questions

                if extracted_data:
                    session.update_event_data(extracted_data)
                
                # Mark answered questions
                for question_id in answered_questions:
                    session.event_data.answered_questions[question_id] = True
                
                # Recompute completion score with updated question answers
                session.event_data.compute_derived_fields()
                
                # Send metadata immediately
                await websocket.send_json({
                    "type": "stream_start",
                    "data": {
                        "completion_score": session.event_data.completion_score,
                        "is_complete": session.event_data.is_complete,
                        "event_data": session.event_data.model_dump(),
                    }
                })

                # Stream AI response in chunks
                full_response = []
                for chunk in ai_service.generate_response_stream(
                    msg_data,
                    session.event_data,
                    session.conversation_history
                ):
                    await websocket.send_json({
                        "type": "stream_chunk",
                        "data": {"text": chunk}
                    })
                    full_response.append(chunk)

                # Signal done and save complete message to history
                session.add_message(MessageRole.ASSISTANT, "".join(full_response))
                await websocket.send_json({"type": "stream_end"})
                
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                await websocket.send_json({
                    "type": "error",
                    "data": {"error": "Failed to process message"}
                })
    
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    
    finally:
        logger.info(f"WebSocket connection closed for session {session_id}")


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

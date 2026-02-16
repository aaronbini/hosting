from typing import Optional
from pydantic import BaseModel
from enum import Enum


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessage(BaseModel):
    """Individual chat message"""
    role: MessageRole
    content: str


class ChatRequest(BaseModel):
    """Request body for chat endpoint"""
    session_id: str
    message: str
    ai_provider: Optional[str] = "gemini"  # TODO: Support multiple providers


class ChatResponse(BaseModel):
    """Response body for chat endpoint"""
    session_id: str
    message: str
    completion_score: float
    is_complete: bool
    event_data: Optional[dict] = None  # Current event planning data

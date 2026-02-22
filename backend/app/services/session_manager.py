import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from app.models.chat import ChatMessage, MessageRole
from app.models.event import EventPlanningData

if TYPE_CHECKING:
    from app.agent.state import AgentState


class SessionData:
    """Container for a user session's conversation and event data"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.created_at = datetime.now()
        self.last_updated = datetime.now()
        self.conversation_history: list[ChatMessage] = []
        self.event_data = EventPlanningData()
        self.agent_state: Optional["AgentState"] = None  # cached after agent completes
        self.google_credentials: Optional[dict] = None  # serialized OAuth token dict

    def add_message(self, role: MessageRole, content: str):
        """Add a message to conversation history"""
        self.conversation_history.append(ChatMessage(role=role, content=content))
        self.last_updated = datetime.now()

    def update_event_data(self, data_dict: dict):
        """Update event planning data and recompute fields"""
        # Update model fields
        for key, value in data_dict.items():
            if hasattr(self.event_data, key):
                setattr(self.event_data, key, value)

        # Recompute derived fields
        self.event_data.compute_derived_fields()

    def to_dict(self):
        """Convert session to dictionary for serialization"""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "event_data": self.event_data.model_dump(),
            "conversation_length": len(self.conversation_history),
            "google_connected": self.google_credentials is not None,
        }


class SessionManager:
    """
    Manages user sessions and conversation state

    TODO: Replace in-memory storage with persistent datastore (Redis, PostgreSQL, etc.)
    Current implementation stores everything in memory, so data is lost on restart.
    For production, integrate with:
    - Redis for session caching
    - PostgreSQL for persistent storage
    - Implement session expiration (e.g., 24 hours)
    """

    def __init__(self):
        self.sessions: dict[str, SessionData] = {}

    def create_session(self) -> str:
        """Create a new session and return session ID"""
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = SessionData(session_id)
        return session_id

    def get_session(self, session_id: str) -> Optional[SessionData]:
        """Retrieve an existing session"""
        return self.sessions.get(session_id)

    def session_exists(self, session_id: str) -> bool:
        """Check if session exists"""
        return session_id in self.sessions

    def delete_session(self, session_id: str) -> bool:
        """Delete a session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False

    def list_active_sessions(self) -> list[dict]:
        """Get list of all active sessions (debug/admin only)"""
        # TODO: Add authentication/authorization before exposing this in production
        return [session.to_dict() for session in self.sessions.values()]


# Global session manager instance
# TODO: Make this thread-safe and handle concurrent access
session_manager = SessionManager()

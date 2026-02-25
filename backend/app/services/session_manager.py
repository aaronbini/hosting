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

    # List fields that should be merged (union) rather than replaced on update.
    _MERGE_LIST_FIELDS = {"cuisine_preferences", "dietary_restrictions", "available_equipment"}

    def update_event_data(self, data_dict: dict):
        """Update event planning data and recompute fields"""
        for key, value in data_dict.items():
            if not hasattr(self.event_data, key):
                continue
            if key in self._MERGE_LIST_FIELDS and isinstance(value, list):
                existing = getattr(self.event_data, key) or []
                if existing and isinstance(existing[0], str):
                    # Simple string lists — dedupe while preserving order
                    merged = list(dict.fromkeys(existing + value))
                else:
                    # Pydantic objects (e.g. DietaryRestriction) — append new entries
                    merged = existing + [v for v in value if v not in existing]
                setattr(self.event_data, key, merged)
            else:
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



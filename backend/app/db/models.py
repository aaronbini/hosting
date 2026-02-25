from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    google_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    picture: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    sessions: Mapped[list[AppSession]] = relationship(back_populates="user")
    saved_plans: Mapped[list[SavedPlan]] = relationship(back_populates="user")


class AppSession(Base):
    __tablename__ = "app_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    conversation_history: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    google_task_credentials: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    stage: Mapped[str] = mapped_column(String, default="gathering")

    user: Mapped[User] = relationship(back_populates="sessions")


class SavedPlan(Base):
    __tablename__ = "saved_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_sessions.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    shopping_list: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    formatted_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    formatted_recipes_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="saved_plans")

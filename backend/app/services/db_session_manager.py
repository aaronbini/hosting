from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSession
from app.models.chat import ChatMessage, MessageRole
from app.models.event import EventPlanningData
from app.services.session_manager import SessionData


class DbSessionManager:
    """DB-backed session manager. Uses PostgreSQL via SQLAlchemy async.

    Returns SessionData objects (the existing in-memory runtime type) so that
    all existing step functions and the WS runner work without modification.
    """

    async def create_session(self, user_id: uuid.UUID, db: AsyncSession) -> str:
        row = AppSession(
            id=uuid.uuid4(),
            user_id=user_id,
            event_data={},
            conversation_history=[],
        )
        db.add(row)
        await db.commit()
        return str(row.id)

    async def get_session(self, session_id: str, db: AsyncSession) -> Optional[SessionData]:
        row = await self._get_row(session_id, db)
        if row is None:
            return None
        return self._row_to_session_data(row)

    async def get_session_row(
        self, session_id: str, db: AsyncSession
    ) -> Optional[AppSession]:
        """Return the raw ORM row (used for ownership checks)."""
        return await self._get_row(session_id, db)

    async def save_session(self, session: SessionData, db: AsyncSession) -> None:
        row = await self._get_row(session.session_id, db)
        if row is None:
            return
        row.event_data = session.event_data.model_dump(mode="json")
        row.conversation_history = [
            m.model_dump(mode="json") for m in session.conversation_history
        ]
        row.google_task_credentials = session.google_credentials
        row.stage = session.event_data.conversation_stage
        row.last_updated = datetime.utcnow()
        await db.commit()

    async def delete_session(self, session_id: str, db: AsyncSession) -> bool:
        row = await self._get_row(session_id, db)
        if row is None:
            return False
        await db.delete(row)
        await db.commit()
        return True

    async def list_user_sessions(
        self, user_id: uuid.UUID, db: AsyncSession
    ) -> list[dict]:
        result = await db.execute(
            select(AppSession).where(AppSession.user_id == user_id)
        )
        rows = result.scalars().all()
        return [self._row_to_session_data(r).to_dict() for r in rows]

    async def list_user_sessions_summary(
        self, user_id: uuid.UUID, db: AsyncSession
    ) -> list[dict]:
        """Lightweight listing — returns session_id, stage, timestamps only."""
        result = await db.execute(
            select(AppSession)
            .where(AppSession.user_id == user_id)
            .order_by(AppSession.last_updated.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "session_id": str(r.id),
                "stage": r.stage or "gathering",
                "last_updated": (r.last_updated or r.created_at).isoformat(),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_row(
        self, session_id: str, db: AsyncSession
    ) -> Optional[AppSession]:
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return None
        result = await db.execute(select(AppSession).where(AppSession.id == sid))
        return result.scalar_one_or_none()

    def _row_to_session_data(self, row: AppSession) -> SessionData:
        session = SessionData(str(row.id))
        session.created_at = row.created_at
        session.last_updated = row.last_updated

        if row.event_data:
            try:
                session.event_data = EventPlanningData.model_validate(row.event_data)
            except Exception:
                session.event_data = EventPlanningData()

        if row.conversation_history:
            session.conversation_history = [
                ChatMessage(role=MessageRole(m["role"]), content=m["content"])
                for m in row.conversation_history
            ]

        session.google_credentials = row.google_task_credentials
        return session


db_session_manager = DbSessionManager()

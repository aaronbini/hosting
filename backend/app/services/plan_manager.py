from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SavedPlan
from app.models.event import EventPlanningData

if TYPE_CHECKING:
    from app.agent.state import AgentState


def _plan_name(event_data: EventPlanningData) -> str:
    parts = []
    if event_data.event_type:
        parts.append(event_data.event_type.replace("_", " ").title())
    if event_data.total_guests:
        parts.append(f"for {event_data.total_guests}")
    if event_data.event_date:
        parts.append(f"· {event_data.event_date}")
    return " ".join(parts) or "Event Plan"


class PlanManager:
    async def save_plan(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        event_data: EventPlanningData,
        agent_state: AgentState,
        db: AsyncSession,
    ) -> SavedPlan:
        row = SavedPlan(
            id=uuid.uuid4(),
            user_id=user_id,
            session_id=session_id,
            name=_plan_name(event_data),
            event_data=event_data.model_dump(mode="json"),
            shopping_list=agent_state.shopping_list.model_dump(mode="json")
            if agent_state.shopping_list is not None
            else None,
            formatted_output=agent_state.formatted_chat_output,
            formatted_recipes_output=agent_state.formatted_recipes_output,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    async def list_user_plans(
        self, user_id: uuid.UUID, db: AsyncSession
    ) -> list[dict]:
        result = await db.execute(
            select(SavedPlan)
            .where(SavedPlan.user_id == user_id)
            .order_by(SavedPlan.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "name": r.name,
                "created_at": r.created_at.isoformat(),
                "event_data": {
                    "total_guests": r.event_data.get("total_guests"),
                    "event_type": r.event_data.get("event_type"),
                    "event_date": r.event_data.get("event_date"),
                    "meal_type": r.event_data.get("meal_type"),
                    "meal_plan": r.event_data.get("meal_plan"),
                },
            }
            for r in rows
        ]

    async def get_plan(
        self, plan_id: str, db: AsyncSession
    ) -> Optional[SavedPlan]:
        try:
            pid = uuid.UUID(plan_id)
        except ValueError:
            return None
        result = await db.execute(select(SavedPlan).where(SavedPlan.id == pid))
        return result.scalar_one_or_none()

    async def delete_plan(self, plan_id: str, db: AsyncSession) -> bool:
        row = await self.get_plan(plan_id, db)
        if row is None:
            return False
        await db.delete(row)
        await db.commit()
        return True


plan_manager = PlanManager()

"""
TasksService — creates a Google Tasks checklist from a ShoppingList.

Structure in Google Tasks:
  Task List: "Dinner Party Shopping - YYYY-MM-DD"
    ── PROTEINS ──          ← parent task (category header)
      chicken breast: 4 lbs  ← child task
      salmon fillet: 2 lbs
    ── PRODUCE ──
      garlic: 3 heads
      ...

The Google Tasks API is synchronous, so all calls are wrapped in
asyncio.to_thread to avoid blocking the event loop.
"""

import asyncio
import logging
import math
from typing import Optional

from app.models.shopping import ShoppingList

logger = logging.getLogger(__name__)


class TasksService:
    def __init__(self, credentials) -> None:
        """
        credentials: google.oauth2.credentials.Credentials
        The service is instantiated per-request from session OAuth tokens.
        """
        self._credentials = credentials

    def _build_service(self):
        """Build the Tasks API service client (sync — call inside to_thread)."""
        from googleapiclient.discovery import build

        return build("tasks", "v1", credentials=self._credentials)

    async def create_shopping_list(
        self, shopping_list: ShoppingList, title: str
    ) -> str:
        """
        Create a Google Tasks list populated with shopping items grouped by
        grocery category. Returns the task list ID.
        """
        list_id = await asyncio.to_thread(
            self._create_list_sync, shopping_list, title
        )
        logger.info("TasksService: task list created with id=%s", list_id)
        return list_id

    def _create_list_sync(self, shopping_list: ShoppingList, title: str) -> str:
        """Synchronous implementation — runs in a thread pool."""
        service = self._build_service()

        # 1. Create the task list
        task_list = service.tasklists().insert(body={"title": title}).execute()
        list_id: str = task_list["id"]
        logger.debug("Created task list id=%s title=%r", list_id, title)

        # 2. Populate tasks by category
        for category, items in shopping_list.grouped.items():
            if not items:
                continue

            # Category header as a parent task
            header_label = f"── {category.replace('_', ' ').upper()} ──"
            header_task = (
                service.tasks()
                .insert(tasklist=list_id, body={"title": header_label})
                .execute()
            )
            header_id: str = header_task["id"]

            # Ingredient child tasks
            for item in items:
                qty = math.ceil(item.total_quantity)
                child_title = f"{item.name}: {qty} {item.unit.value}"
                service.tasks().insert(
                    tasklist=list_id,
                    body={"title": child_title},
                    parent=header_id,
                ).execute()

        return list_id

    @staticmethod
    def from_token_dict(token_dict: dict) -> Optional["TasksService"]:
        """
        Build a TasksService from the serialized token dict stored on the session.
        Returns None if the dict is missing required fields.
        """
        try:
            from google.oauth2.credentials import Credentials

            creds = Credentials(
                token=token_dict.get("token"),
                refresh_token=token_dict.get("refresh_token"),
                token_uri=token_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=token_dict.get("client_id"),
                client_secret=token_dict.get("client_secret"),
                scopes=token_dict.get("scopes"),
            )
            return TasksService(creds)
        except Exception as exc:
            logger.error("TasksService.from_token_dict failed: %s", exc)
            return None

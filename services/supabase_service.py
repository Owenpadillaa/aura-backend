"""Supabase integration.

Wraps the synchronous supabase-py client in async functions
using asyncio.to_thread to keep FastAPI's event loop unblocked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client

from config.settings import settings
from models.schemas import FluidTask, HabitEntry, HabitSummary

# Initialize synchronous Supabase client
_client: Client = create_client(settings.supabase_url, settings.supabase_key)


async def get_fluid_tasks() -> list[FluidTask]:
    """Fetch the user's configured fluid tasks (habits to schedule).

    Returns:
        List of FluidTask objects ordered by priority descending.
    """
    def _query() -> list[dict[str, Any]]:
        result = (
            _client.table("fluid_tasks")
            .select("*")
            .order("priority", desc=True)
            .execute()
        )
        return result.data or []

    rows = await asyncio.to_thread(_query)

    return [
        FluidTask(
            name=row["name"],
            duration_minutes=row["duration_minutes"],
            priority=row.get("priority", 1),
        )
        for row in rows
    ]


async def get_habit_summary() -> HabitSummary:
    """Fetch habit streaks and completion rates from Supabase.

    Queries the `habits` table and computes:
    - Current streak (consecutive days completed)
    - 7-day completion rate
    - Last completion timestamp

    Returns:
        HabitSummary with all tracked habits.
    """
    def _query() -> list[dict[str, Any]]:
        result = _client.table("habits").select("*").execute()
        return result.data or []

    rows = await asyncio.to_thread(_query)

    habits: list[HabitEntry] = []
    now = datetime.now(timezone.utc)

    for row in rows:
        last_completed = None
        if row.get("last_completed"):
            last_completed = datetime.fromisoformat(row["last_completed"])

        habits.append(HabitEntry(
            name=row["name"],
            streak=row.get("streak", 0),
            last_completed=last_completed,
            completion_rate_7d=row.get("completion_rate_7d", 0.0),
        ))

    return HabitSummary(habits=habits, generated_at=now)


async def log_voice_transcript(transcript: str, metadata: dict[str, Any] | None = None) -> None:
    """Store a voice transcript in the brain_vault table.

    Args:
        transcript: The raw voice-to-text transcript.
        metadata: Optional extra data (parsed ideas, expenses, etc.).
    """
    def _insert() -> None:
        _client.table("brain_vault").insert({
            "transcript": transcript,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    await asyncio.to_thread(_insert)


async def add_expense(name: str, amount: float, category: str) -> dict[str, Any]:
    """Insert an expense into the expenses table.

    Returns:
        The inserted row as a dict.
    """
    def _insert() -> dict[str, Any]:
        result = _client.table("expenses").insert({
            "name": name,
            "amount": amount,
            "category": category,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return result.data[0] if result.data else {}

    return await asyncio.to_thread(_insert)


async def get_expenses() -> list[dict[str, Any]]:
    """Fetch all expenses ordered by most recent.

    Returns:
        List of expense dicts.
    """
    def _query() -> list[dict[str, Any]]:
        result = (
            _client.table("expenses")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    return await asyncio.to_thread(_query)


async def delete_expense(expense_id: str) -> bool:
    """Delete an expense by ID.

    Returns:
        True if deleted successfully.
    """
    def _delete() -> bool:
        result = (
            _client.table("expenses")
            .delete()
            .eq("id", expense_id)
            .execute()
        )
        return len(result.data or []) > 0

    return await asyncio.to_thread(_delete)

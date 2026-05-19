"""Supabase integration.

Wraps the synchronous supabase-py client in async functions
using asyncio.to_thread to keep FastAPI's event loop unblocked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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


# ── Calendar Events (user-created, stored in Supabase) ───────


async def create_calendar_event(
    title: str,
    start: str,
    end: str,
    flexibility: str = "fluid",
    recurrence_rule: str | None = None,
    recurrence_days: list[int] | None = None,
    recurrence_end_date: str | None = None,
) -> dict[str, Any]:
    """Create a calendar event in Supabase.

    Returns:
        The inserted row as a dict.
    """
    def _insert() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "start": start,
            "end": end,
            "flexibility": flexibility,
        }
        if recurrence_rule:
            payload["recurrence_rule"] = recurrence_rule
        if recurrence_days is not None:
            payload["recurrence_days"] = recurrence_days
        if recurrence_end_date:
            payload["recurrence_end_date"] = recurrence_end_date
        result = _client.table("calendar_events").insert(payload).execute()
        return result.data[0] if result.data else {}

    return await asyncio.to_thread(_insert)


async def get_calendar_events(
    date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch calendar events, optionally filtered by date range.

    Recurring events are expanded into individual instances for the queried range.

    Args:
        date: ISO date string (YYYY-MM-DD). Single-day filter.
        start_date: ISO date string for range start (inclusive).
        end_date: ISO date string for range end (inclusive).

    Returns:
        List of event dicts.
    """
    def _query() -> list[dict[str, Any]]:
        q = _client.table("calendar_events").select("*")
        if date:
            q = q.gte("start", f"{date}T00:00:00").lte("start", f"{date}T23:59:59")
        q = q.order("start")
        result = q.execute()
        return result.data or []

    all_rows = await asyncio.to_thread(_query)

    # Separate recurring and non-recurring events
    non_recurring: list[dict[str, Any]] = []
    recurring_templates: list[dict[str, Any]] = []

    for row in all_rows:
        if row.get("recurrence_rule"):
            recurring_templates.append(row)
        else:
            non_recurring.append(row)

    # If no date range query, just filter non-recurring by date and return
    if not date and not start_date:
        return non_recurring

    # Determine the expansion range
    if date:
        range_start = datetime.fromisoformat(f"{date}T00:00:00")
        range_end = datetime.fromisoformat(f"{date}T23:59:59")
    else:
        range_start = datetime.fromisoformat(f"{start_date}T00:00:00") if start_date else datetime.fromisoformat(f"{date}T00:00:00")
        range_end = datetime.fromisoformat(f"{end_date}T23:59:59") if end_date else range_start

    # Expand recurring events into instances
    expanded: list[dict[str, Any]] = []

    for template in recurring_templates:
        rule = template.get("recurrence_rule")
        days = template.get("recurrence_days") or []
        end_recurrence = template.get("recurrence_end_date")

        template_start = datetime.fromisoformat(template["start"])
        template_end = datetime.fromisoformat(template["end"])
        event_duration = template_end - template_start

        # Parse recurrence end boundary
        rec_end = None
        if end_recurrence:
            rec_end = datetime.fromisoformat(f"{end_recurrence}T23:59:59")

        if rule == "weekly":
            # Iterate each day in the range
            current = range_start.replace(hour=0, minute=0, second=0, microsecond=0)
            range_end_day = range_end.replace(hour=23, minute=59, second=59, microsecond=0)

            while current <= range_end_day:
                # Python weekday: 0=Mon..6=Sun — matches our frontend convention
                weekday = current.weekday()

                if weekday in days:
                    # Check if this instance is past the recurrence end
                    if rec_end and current > rec_end:
                        current += timedelta(days=1)
                        continue

                    # Check if this instance is before the template start
                    if current.date() < template_start.date():
                        current += timedelta(days=1)
                        continue

                    instance_start = current.replace(
                        hour=template_start.hour,
                        minute=template_start.minute,
                        second=0, microsecond=0,
                    )
                    instance_end = instance_start + event_duration

                    expanded.append({
                        **template,
                        "start": instance_start.isoformat(),
                        "end": instance_end.isoformat(),
                        "recurrence_parent_id": template["id"],
                    })

                current += timedelta(days=1)

    return non_recurring + expanded


async def delete_calendar_event(event_id: str) -> bool:
    """Delete a calendar event by ID.

    Returns:
        True if deleted successfully.
    """
    def _delete() -> bool:
        result = (
            _client.table("calendar_events")
            .delete()
            .eq("id", event_id)
            .execute()
        )
        return len(result.data or []) > 0

    return await asyncio.to_thread(_delete)

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
            id=row.get("id"),
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


# ── Push Subscriptions ────────────────────────────────────


async def save_push_subscription(subscription: dict[str, Any]) -> dict[str, Any]:
    """Upsert a push subscription by endpoint."""
    endpoint = subscription.get("endpoint", "")
    keys = subscription.get("keys", {})

    def _upsert() -> dict[str, Any]:
        result = _client.table("push_subscriptions").upsert({
            "endpoint": endpoint,
            "p256dh": keys.get("p256dh", ""),
            "auth_key": keys.get("auth", ""),
        }, on_conflict="endpoint").execute()
        return result.data[0] if result.data else {}

    return await asyncio.to_thread(_upsert)


async def get_all_push_subscriptions() -> list[dict[str, Any]]:
    """Get all push subscriptions formatted for pywebpush."""
    def _query() -> list[dict[str, Any]]:
        result = _client.table("push_subscriptions").select("*").execute()
        rows = result.data or []
        return [
            {
                "endpoint": row["endpoint"],
                "keys": {
                    "p256dh": row["p256dh"],
                    "auth": row["auth_key"],
                },
            }
            for row in rows
        ]

    return await asyncio.to_thread(_query)


async def delete_push_subscription(endpoint: str) -> bool:
    """Delete a push subscription by endpoint."""
    def _delete() -> bool:
        result = (
            _client.table("push_subscriptions")
            .delete()
            .eq("endpoint", endpoint)
            .execute()
        )
        return len(result.data or []) > 0

    return await asyncio.to_thread(_delete)


# ── Date-Filtered Expenses ────────────────────────────────


async def get_expenses_by_date_range(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Fetch expenses within a date range.

    Args:
        start_date: ISO datetime string (inclusive).
        end_date: ISO datetime string (inclusive).
    """
    def _query() -> list[dict[str, Any]]:
        result = (
            _client.table("expenses")
            .select("*")
            .gte("created_at", start_date)
            .lte("created_at", end_date)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    return await asyncio.to_thread(_query)


async def get_weekly_expenses_total() -> float:
    """Sum of all expenses from the last 7 days."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    rows = await get_expenses_by_date_range(week_ago, now.isoformat())
    return sum(float(r.get("amount", 0)) for r in rows)


async def get_daily_expense_totals() -> list[dict[str, Any]]:
    """Get daily expense totals for the last 7 days.

    Returns:
        List of 7 dicts: [{"date": "2026-05-18", "total": 12.50, "day": "Sun"}, ...]
    """
    from datetime import datetime, timedelta, timezone

    def _query() -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat()
        result = (
            _client.table("expenses")
            .select("*")
            .gte("created_at", week_ago)
            .execute()
        )
        return result.data or []

    rows = await asyncio.to_thread(_query)

    # Group by date
    totals: dict[str, float] = {}
    for row in rows:
        date_str = row.get("created_at", "")[:10]  # YYYY-MM-DD
        amount = float(row.get("amount", 0))
        totals[date_str] = totals.get(date_str, 0) + amount

    # Build 7-day array
    now = datetime.now(timezone.utc)
    days = []
    for i in range(6, -1, -1):
        d = now - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        days.append({
            "date": date_str,
            "total": round(totals.get(date_str, 0), 2),
            "day": d.strftime("%a")[0],
            "is_today": i == 0,
        })

    return days


# ── Habit Completion ─────────────────────────────────────


async def complete_habit(habit_id: str) -> dict[str, Any]:
    """Mark a habit as completed today.

    Increments streak if last_completed was yesterday, resets if older.
    """
    def _update() -> dict[str, Any]:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        today = now.date()

        # Get current habit
        result = _client.table("habits").select("*").eq("id", habit_id).execute()
        if not result.data:
            return {}
        habit = result.data[0]

        # Calculate new streak
        streak = habit.get("streak", 0)
        last = habit.get("last_completed")
        if last:
            last_date = datetime.fromisoformat(last).date()
            if last_date == today:
                # Already completed today, no change
                return habit
            elif last_date == (today - timedelta(days=1)):
                streak += 1
            else:
                streak = 1
        else:
            streak = 1

        update_result = _client.table("habits").update({
            "streak": streak,
            "last_completed": now.isoformat(),
        }).eq("id", habit_id).execute()

        return update_result.data[0] if update_result.data else {}

    return await asyncio.to_thread(_update)


# ── User Settings ─────────────────────────────────────────


async def get_user_setting(key: str) -> Any:
    """Get a user setting by key."""
    def _query() -> Any:
        result = _client.table("user_settings").select("*").eq("key", key).execute()
        if result.data:
            return result.data[0].get("value")
        return None

    return await asyncio.to_thread(_query)


async def set_user_setting(key: str, value: Any) -> dict[str, Any]:
    """Upsert a user setting by key."""
    def _upsert() -> dict[str, Any]:
        result = _client.table("user_settings").upsert({
            "key": key,
            "value": value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="key").execute()
        return result.data[0] if result.data else {}

    return await asyncio.to_thread(_upsert)

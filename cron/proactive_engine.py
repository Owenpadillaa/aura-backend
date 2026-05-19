"""Proactive Cron Engine — Push Notification Generator.

Gathers context from calendar, budget, and habits, generates AI
notifications via Gemini, and sends them as Web Push notifications.

Runs as an asyncio.Task started in FastAPI's lifespan event.
Survives Render free tier sleep cycles via external cron-job.org ping
to /api/health.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from services.calendar_service import fetch_events
from services.gemini_service import chat_notification
from services.supabase_service import (
    get_calendar_events,
    get_habit_summary,
    get_expenses_by_date_range,
    get_weekly_expenses_total,
    get_user_setting,
    log_voice_transcript,
)
from services.push_service import send_push_to_all

logger = logging.getLogger("aura.cron")

_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None

# Deduplication: track last send time and type
_last_send_time: datetime | None = None
_last_send_type: str | None = None


def _get_notification_type(hour: int) -> str | None:
    """Determine notification type based on current hour (UTC).

    We use a flexible window — Render may sleep, so we catch anything
    that lands in the general time zone.
    """
    if 7 <= hour < 9:
        return "morning"
    elif 9 <= hour < 21:
        return "hourly"
    elif 21 <= hour < 23:
        return "evening"
    return None  # Sleep window


def _should_send(ntype: str) -> bool:
    """Check deduplication — prevent duplicate sends."""
    global _last_send_time, _last_send_type
    now = datetime.now(timezone.utc)

    if _last_send_time is None:
        return True

    elapsed = (now - _last_send_time).total_seconds()

    # Morning/evening: only once per day
    if ntype in ("morning", "evening"):
        if _last_send_type == ntype and elapsed < 43200:  # 12 hours
            return False
        return True

    # Hourly: at least 90 min between sends
    if ntype == "hourly" and elapsed < 5400:
        return False

    return True


async def _gather_context() -> str:
    """Gather all user context into a single text block for the AI."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    day_name = now.strftime("%A")

    parts: list[str] = [
        f"Current time: {now.strftime('%I:%M %p')} UTC, {day_name}",
    ]

    # Calendar events for today (Google Calendar + Supabase recurring)
    try:
        google_events = fetch_events(date=today)
        if google_events:
            cal_lines = ["Calendar today:"]
            for e in google_events:
                cal_lines.append(f"  {e.start.strftime('%I:%M %p')}–{e.end.strftime('%I:%M %p')}: {e.title}")
            parts.append("\n".join(cal_lines))
    except Exception:
        pass

    try:
        supabase_events = await get_calendar_events(date=today)
        if supabase_events:
            cal_lines = ["Recurring/custom events today:"]
            for e in supabase_events:
                start_t = e["start"][11:16] if len(e["start"]) > 16 else "?"
                end_t = e["end"][11:16] if len(e["end"]) > 16 else "?"
                cal_lines.append(f"  {start_t}–{end_t}: {e['title']}")
            parts.append("\n".join(cal_lines))
    except Exception:
        pass

    # Habits / streaks
    try:
        habit_summary = await get_habit_summary()
        if habit_summary.habits:
            habit_lines = ["Habits:"]
            for h in habit_summary.habits:
                streak_str = f"{h.streak}d streak" if h.streak > 0 else "no streak"
                habit_lines.append(f"  {h.name}: {streak_str}")
            parts.append("\n".join(habit_lines))
    except Exception:
        pass

    # Today's expenses
    try:
        today_start = f"{today}T00:00:00+00:00"
        today_end = f"{today}T23:59:59+00:00"
        today_expenses = await get_expenses_by_date_range(today_start, today_end)
        if today_expenses:
            total_today = sum(float(e.get("amount", 0)) for e in today_expenses)
            parts.append(f"Spent today: ${total_today:.2f} ({len(today_expenses)} transactions)")
        else:
            parts.append("No expenses logged today.")
    except Exception:
        pass

    # Weekly spending
    try:
        weekly_total = await get_weekly_expenses_total()
        parts.append(f"Spent this week: ${weekly_total:.2f}")
    except Exception:
        pass

    # Budget info (earnings + daily budget)
    try:
        earnings_data = await get_user_setting("earnings")
        if earnings_data and isinstance(earnings_data, dict):
            amount = float(earnings_data.get("amount", 0))
            period = earnings_data.get("period", "weekly")
            weekly_amount = amount
            if period == "biweekly":
                weekly_amount = amount / 2
            elif period == "monthly":
                weekly_amount = amount / 4.33

            weekly_total = await get_weekly_expenses_total()
            remaining = weekly_amount - weekly_total
            day_of_week = now.weekday()  # 0=Mon
            days_left = max(7 - day_of_week, 1)
            daily_budget = remaining / days_left

            parts.append(f"Weekly earnings: ${weekly_amount:.2f}")
            parts.append(f"Remaining this week: ${remaining:.2f}")
            parts.append(f"Daily spending limit: ${daily_budget:.2f}")
    except Exception:
        pass

    # User name
    try:
        name = await get_user_setting("user_name")
        if name:
            parts.append(f"User name: {name}")
    except Exception:
        pass

    return "\n".join(parts)


async def _run_proactive_check() -> None:
    """Execute a single proactive check cycle."""
    now = datetime.now(timezone.utc)
    hour = now.hour

    ntype = _get_notification_type(hour)
    if ntype is None:
        return  # Sleep window

    if not _should_send(ntype):
        logger.info(f"Proactive engine: skipping {ntype} (dedup)")
        return

    try:
        logger.info(f"Proactive engine: generating {ntype} notification...")

        context = await _gather_context()
        message = chat_notification(context=context, notification_type=ntype)

        logger.info(f"Proactive engine: {ntype} message ({len(message)} chars): {message[:80]}...")

        title_map = {
            "morning": "Good Morning",
            "hourly": "Aura",
            "evening": "Evening Recap",
        }
        title = title_map.get(ntype, "Aura")

        # Send push notification
        result = await send_push_to_all(title=title, body=message[:150], tag=f"aura-{ntype}")

        # Update dedup tracking
        global _last_send_time, _last_send_type
        _last_send_time = datetime.now(timezone.utc)
        _last_send_type = ntype

        # Log to brain_vault
        await log_voice_transcript(
            transcript=message,
            metadata={"source": "proactive_engine", "type": ntype, "push_result": result},
        )

        logger.info(f"Proactive engine: {ntype} sent ({result})")

    except Exception as exc:
        logger.error(f"Proactive engine error ({ntype}): {exc}", exc_info=True)


async def _engine_loop(stop_event: asyncio.Event) -> None:
    """Main loop — runs every 30 minutes until stopped."""
    logger.info("Proactive engine started. Running every 1800 seconds.")

    while not stop_event.is_set():
        await _run_proactive_check()

        # Wait 30 min OR until stop signal
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1800)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("Proactive engine stopped.")


def start_proactive_engine() -> None:
    """Start the background proactive engine task."""
    global _task, _stop_event
    if _task is not None and not _task.done():
        logger.warning("Proactive engine already running.")
        return

    _stop_event = asyncio.Event()
    _task = asyncio.create_task(_engine_loop(_stop_event))


async def stop_proactive_engine() -> None:
    """Gracefully stop the background engine."""
    global _task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _task is not None:
        await _task
        _task = None
        _stop_event = None

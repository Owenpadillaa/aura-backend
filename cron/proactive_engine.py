"""Proactive Cron Engine — Hourly Background Worker.

Reads the user's Google Calendar, formats upcoming events, and sends
them to Gemini 1.5 Flash for proactive scheduling recommendations.

Runs as an asyncio.Task started in FastAPI's lifespan event.
Survives Render free tier sleep cycles via external cron-job.org ping
to /api/health.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from services.calendar_service import fetch_events
from services.gemini_service import chat
from services.supabase_service import log_voice_transcript

logger = logging.getLogger("aura.cron")

_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _format_events_for_gemini(events: list) -> str:
    """Format calendar events into a prompt-ready string."""
    if not events:
        return "No events scheduled for today."

    lines = ["### Today's Schedule:"]
    for e in events:
        start_str = e.start.strftime("%I:%M %p")
        end_str = e.end.strftime("%I:%M %p")
        tag = "[FIXED]" if e.flexibility.value == "fixed" else "[FLUID]"
        lines.append(f"- {start_str}–{end_str} {tag} {e.title}")

    # Detect whitespace gaps
    lines.append("\n### Analysis Request:")
    lines.append(
        "Look at this schedule. If there is an upcoming exam or deadline "
        "and a massive chunk of open white space today, formulate an aggressive, "
        "conversational recommendation to use that free time. Be specific about "
        "what to do and when. If the schedule is packed, acknowledge it briefly "
        "and suggest one small optimization."
    )

    return "\n".join(lines)


async def _run_proactive_check() -> None:
    """Execute a single proactive check cycle."""
    try:
        logger.info("Proactive engine: fetching today's events...")
        events = fetch_events()

        if not events:
            logger.info("Proactive engine: no events today. Skipping.")
            return

        prompt = _format_events_for_gemini(events)
        logger.info("Proactive engine: sending to Gemini...")
        recommendation = chat(prompt)

        logger.info(f"Proactive engine: recommendation received ({len(recommendation)} chars)")

        # Log the recommendation to Supabase
        await log_voice_transcript(
            transcript=recommendation,
            metadata={"source": "proactive_engine", "event_count": len(events)},
        )

    except Exception as exc:
        logger.error(f"Proactive engine error: {exc}", exc_info=True)


async def _engine_loop(stop_event: asyncio.Event) -> None:
    """Main loop — runs every hour until stopped."""
    logger.info("Proactive engine started. Running every 3600 seconds.")

    while not stop_event.is_set():
        await _run_proactive_check()

        # Wait 1 hour OR until stop signal, whichever comes first
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600)
            break  # Stop event was set
        except asyncio.TimeoutError:
            pass  # Timeout reached, run again

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

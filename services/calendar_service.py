"""Google Calendar integration.

Authenticates via OAuth 2.0 env vars (no JSON key file).
Fetches events and classifies them as fixed or fluid.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config.settings import settings
from models.schemas import CalendarEvent, Flexibility


# ── Keywords that signal a fixed commitment ──────────────────
FIXED_KEYWORDS = frozenset({
    "exam", "midterm", "final", "interview", "class", "lecture",
    "lab", "seminar", "workshop", "presentation", "defense",
    "appointment", "meeting", "standup", "review",
})


def _classify_event(summary: str, transparency: str) -> Flexibility:
    """Heuristic: opaque (busy) events with fixed keywords → FIXED."""
    if transparency == "transparent":
        return Flexibility.FLUID

    title_lower = summary.lower()
    if any(kw in title_lower for kw in FIXED_KEYWORDS):
        return Flexibility.FIXED

    # Default opaque events to fixed (classes, meetings, etc.)
    return Flexibility.FIXED


def _build_credentials() -> Credentials:
    """Build Google OAuth2 credentials from environment variables."""
    return Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
    )


def fetch_events(
    date: Optional[str] = None,
    calendar_id: Optional[str] = None,
) -> list[CalendarEvent]:
    """Fetch events for a specific date from Google Calendar.

    Args:
        date: ISO date string (YYYY-MM-DD). Defaults to today.
        calendar_id: Google Calendar ID. Defaults to settings value.

    Returns:
        List of CalendarEvent objects classified as fixed or fluid.
    """
    cal_id = calendar_id or settings.google_calendar_id

    # Parse target date
    if date:
        target = datetime.fromisoformat(date)
    else:
        target = datetime.now()

    # Time bounds for the day
    time_min = target.replace(hour=0, minute=0, second=0, microsecond=0)
    time_max = time_min + timedelta(days=1)

    # Build Google Calendar API client
    creds = _build_credentials()
    service = build("calendar", "v3", credentials=creds)

    # Fetch events
    result = service.events().list(
        calendarId=cal_id,
        timeMin=time_min.isoformat() + "Z",
        timeMax=time_max.isoformat() + "Z",
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events: list[CalendarEvent] = []
    for item in result.get("items", []):
        summary = item.get("summary", "Untitled")
        transparency = item.get("transparency", "opaque")

        # Parse start/end — handle both dateTime and date (all-day)
        start_raw = item["start"].get("dateTime", item["start"].get("date"))
        end_raw = item["end"].get("dateTime", item["end"].get("date"))

        events.append(CalendarEvent(
            id=item["id"],
            title=summary,
            start=datetime.fromisoformat(start_raw.replace("Z", "+00:00")),
            end=datetime.fromisoformat(end_raw.replace("Z", "+00:00")),
            flexibility=_classify_event(summary, transparency),
        ))

    return events

"""Aura Backend — FastAPI Application.

Main entry point. Wires up all routes, middleware, and background tasks.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.settings import settings
from models.schemas import (
    BudgetResponse,
    CalendarImportUrlRequest,
    ChatRequest,
    ChatResponse,
    CreateEventRequest,
    ExpenseEntry,
    ExpenseListResponse,
    HealthResponse,
    OptimizedSchedule,
)
from scheduler import optimize_day
from services.calendar_service import fetch_events, import_from_url
from services.gemini_service import chat, get_budget_recommendation
from services.supabase_service import (
    add_expense,
    create_calendar_event,
    delete_calendar_event,
    delete_expense,
    get_calendar_events,
    get_expenses,
    get_fluid_tasks,
    get_habit_summary,
)
from cron import start_proactive_engine, stop_proactive_engine

# ── Logging ──────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("aura")


# ── Lifespan (startup/shutdown) ──────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background tasks on app startup, clean up on shutdown."""
    logger.info("Aura backend starting up...")
    start_proactive_engine()
    yield
    logger.info("Aura backend shutting down...")
    await stop_proactive_engine()


# ── App Instance ─────────────────────────────────────────────

app = FastAPI(
    title="Aura — Personal Growth Assistant",
    description="Voice-first, proactive calendar and lifestyle assistant.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow the frontend dev server and production domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ───────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint. Also serves as Render keep-alive ping target."""
    return HealthResponse(status="ok", timestamp=datetime.now(timezone.utc))


@app.get("/api/calendar/sync")
async def calendar_sync(
    date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD). Defaults to today."),
):
    """Fetch and return Google Calendar events for a given date."""
    try:
        events = fetch_events(date=date)
        return {
            "events": [e.model_dump(mode="json") for e in events],
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error(f"Calendar sync error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to sync calendar: {str(exc)}"},
        )


@app.post("/api/calendar/events")
async def create_event_endpoint(req: CreateEventRequest):
    """Create a new calendar event (stored in Supabase)."""
    try:
        row = await create_calendar_event(
            title=req.title,
            start=req.start.isoformat(),
            end=req.end.isoformat(),
            flexibility=req.flexibility.value if hasattr(req.flexibility, 'value') else str(req.flexibility),
            recurrence_rule=req.recurrence_rule,
            recurrence_days=req.recurrence_days,
            recurrence_end_date=req.recurrence_end_date,
        )
        return row
    except Exception as exc:
        logger.error(f"Create event error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to create event: {str(exc)}"},
        )


@app.get("/api/calendar/events")
async def list_calendar_events(
    date: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Get calendar events, optionally filtered by date or date range.
    Recurring events are expanded into instances for the queried range."""
    try:
        rows = await get_calendar_events(date=date, start_date=start_date, end_date=end_date)
        return {"events": rows}
    except Exception as exc:
        logger.error(f"List events error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch events: {str(exc)}"},
        )


@app.delete("/api/calendar/events/{event_id}")
async def remove_calendar_event(event_id: str):
    """Delete a calendar event by ID."""
    try:
        deleted = await delete_calendar_event(event_id)
        if deleted:
            return {"deleted": True}
        return JSONResponse(status_code=404, content={"error": "Event not found"})
    except Exception as exc:
        logger.error(f"Delete event error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to delete event: {str(exc)}"},
        )


@app.post("/api/calendar/import-url")
async def import_calendar_url(req: CalendarImportUrlRequest):
    """Import events from a public ICS calendar URL."""
    try:
        events = await import_from_url(req.url)
        return {
            "events": [e.model_dump(mode="json") for e in events],
            "count": len(events),
        }
    except Exception as exc:
        logger.error(f"Import URL error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to import calendar: {str(exc)}"},
        )


@app.get("/api/calendar/google/connect")
async def google_calendar_connect():
    """Return Google Calendar OAuth URL (placeholder for future OAuth flow)."""
    return {"auth_url": None, "connected": False, "message": "Google Calendar uses env-based auth"}


@app.get("/api/calendar/google/status")
async def google_calendar_status():
    """Check Google Calendar connection status."""
    try:
        events = fetch_events()
        return {"connected": True, "event_count": len(events)}
    except Exception:
        return {"connected": False}


@app.get("/api/schedule/optimize")
async def schedule_optimize(
    date: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD). Defaults to today."),
):
    """Run the fluid rescheduler on a day's events."""
    try:
        # Fetch calendar events
        events = fetch_events(date=date)

        # Fetch configured fluid tasks from Supabase
        fluid_tasks = await get_fluid_tasks()

        # Run the optimization algorithm
        schedule = optimize_day(
            events=events,
            fluid_tasks=fluid_tasks,
            target_date=date,
            day_start_hour=settings.day_start_hour,
            day_end_hour=settings.day_end_hour,
        )

        return schedule.model_dump(mode="json")

    except Exception as exc:
        logger.error(f"Schedule optimize error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to optimize schedule: {str(exc)}"},
        )


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """Send a text message to Gemini 1.5 Flash and handle event creation."""
    try:
        response_text = chat(
            message=request.message,
            context=request.context,
        )

        # Parse and execute any CREATE_EVENT commands from the AI response
        import re
        event_pattern = r'\[CREATE_EVENT\|([^|]+)\|([^|]+)\|([^|]+)\|([^\]]+)\]'
        events_created = []
        cleaned_response = response_text

        for match in re.finditer(event_pattern, response_text):
            title, start_str, end_str, flexibility = match.groups()
            try:
                row = await create_calendar_event(
                    title=title.strip(),
                    start=start_str.strip(),
                    end=end_str.strip(),
                    flexibility=flexibility.strip(),
                )
                events_created.append(title.strip())
            except Exception as e:
                logger.error(f"Failed to create event from AI: {e}")

        # Remove the [CREATE_EVENT|...] markers from the response
        cleaned_response = re.sub(event_pattern, '', cleaned_response).strip()

        # Add confirmation if events were created
        if events_created:
            event_list = ", ".join(f'"{e}"' for e in events_created)
            cleaned_response += f"\n\nAdded {event_list} to your calendar."

        return ChatResponse(response=cleaned_response)
    except Exception as exc:
        logger.error(f"Chat error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Chat failed: {str(exc)}"},
        )


@app.get("/api/habits/summary")
async def habits_summary():
    """Return habit streaks and completion rates from Supabase."""
    try:
        summary = await get_habit_summary()
        return summary.model_dump(mode="json")
    except Exception as exc:
        logger.error(f"Habits summary error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch habits: {str(exc)}"},
        )


# ── Expenses / Budget ────────────────────────────────────────

@app.post("/api/expenses")
async def create_expense(entry: ExpenseEntry):
    """Log a new expense."""
    try:
        row = await add_expense(
            name=entry.name,
            amount=entry.amount,
            category=entry.category,
        )
        return row
    except Exception as exc:
        logger.error(f"Add expense error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to add expense: {str(exc)}"},
        )


@app.get("/api/expenses", response_model=ExpenseListResponse)
async def list_expenses():
    """Get all expenses, most recent first."""
    try:
        rows = await get_expenses()
        total = sum(float(r.get("amount", 0)) for r in rows)
        return ExpenseListResponse(
            expenses=rows,
            total=total,
            generated_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.error(f"List expenses error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch expenses: {str(exc)}"},
        )


@app.delete("/api/expenses/{expense_id}")
async def remove_expense(expense_id: str):
    """Delete an expense by ID."""
    try:
        deleted = await delete_expense(expense_id)
        if deleted:
            return {"deleted": True}
        return JSONResponse(status_code=404, content={"error": "Expense not found"})
    except Exception as exc:
        logger.error(f"Delete expense error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to delete expense: {str(exc)}"},
        )


@app.post("/api/budget/recommend", response_model=BudgetResponse)
async def budget_recommend(weekly_earnings: float, expenses: list[dict]):
    """Get AI budget recommendations based on earnings and expenses."""
    try:
        import json

        raw = get_budget_recommendation(weekly_earnings, expenses)

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])
        cleaned = cleaned.strip()

        data = json.loads(cleaned)
        return BudgetResponse(
            recommendations=data.get("recommendations", []),
            pie_data=data.get("pie_data", []),
            summary=data.get("summary", ""),
            generated_at=datetime.now(timezone.utc),
        )
    except Exception as exc:
        logger.error(f"Budget recommend error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Budget recommendation failed: {str(exc)}"},
        )


# ── Push Notifications ────────────────────────────────────────

# In-memory store for push subscriptions (use Supabase in production)
_push_subscriptions: list[dict] = []


@app.get("/api/push/vapid-key")
async def get_vapid_public_key():
    """Return the VAPID public key for push subscription."""
    if not settings.vapid_public_key:
        return JSONResponse(
            status_code=404,
            content={"error": "Push notifications not configured"},
        )
    return {"publicKey": settings.vapid_public_key}


@app.post("/api/push/subscribe")
async def store_push_subscription(subscription: dict):
    """Store a push notification subscription."""
    # Avoid duplicates
    endpoint = subscription.get("endpoint", "")
    _push_subscriptions[:] = [s for s in _push_subscriptions if s.get("endpoint") != endpoint]
    _push_subscriptions.append(subscription)
    logger.info(f"Push subscription stored: {endpoint[:50]}...")
    return {"status": "subscribed"}


@app.post("/api/push/send")
async def send_push_notification(payload: dict):
    """Send a push notification to all subscribers."""
    if not settings.vapid_private_key:
        return JSONResponse(
            status_code=400,
            content={"error": "Push notifications not configured"},
        )

    try:
        from pywebpush import webpush, WebPushException

        title = payload.get("title", "Aura")
        body = payload.get("body", "")
        sent = 0
        failed = []

        for sub in _push_subscriptions:
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps({"title": title, "body": body}),
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_email},
                )
                sent += 1
            except WebPushException:
                failed.append(sub.get("endpoint", ""))

        # Remove failed subscriptions
        _push_subscriptions[:] = [
            s for s in _push_subscriptions
            if s.get("endpoint") not in failed
        ]

        return {"sent": sent, "failed": len(failed)}
    except ImportError:
        return JSONResponse(
            status_code=500,
            content={"error": "pywebpush not installed"},
        )
    except Exception as exc:
        logger.error(f"Push send error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to send push: {str(exc)}"},
        )


# ── Entrypoint ───────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=settings.app_env == "development",
    )

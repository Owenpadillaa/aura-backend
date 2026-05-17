"""Aura Backend — FastAPI Application.

Main entry point. Wires up all routes, middleware, and background tasks.
"""

from __future__ import annotations

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
    ChatRequest,
    ChatResponse,
    ExpenseEntry,
    ExpenseListResponse,
    HealthResponse,
    OptimizedSchedule,
)
from scheduler import optimize_day
from services.calendar_service import fetch_events
from services.gemini_service import chat, get_budget_recommendation
from services.supabase_service import add_expense, get_expenses, get_fluid_tasks, get_habit_summary
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
    """Send a text message to Gemini 1.5 Flash."""
    try:
        response_text = chat(
            message=request.message,
            context=request.context,
        )
        return ChatResponse(response=response_text)
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


# ── Entrypoint ───────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=settings.app_env == "development",
    )

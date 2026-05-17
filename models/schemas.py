"""Pydantic schemas for Aura API request/response models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enumerations ─────────────────────────────────────────────

class Flexibility(str, Enum):
    """Whether an event can be moved by the scheduler."""
    FIXED = "fixed"
    FLUID = "fluid"


# ── Calendar ─────────────────────────────────────────────────

class CalendarEvent(BaseModel):
    """A single calendar event fetched from Google Calendar."""
    id: str
    title: str
    start: datetime
    end: datetime
    flexibility: Flexibility = Flexibility.FIXED
    source_calendar: Optional[str] = None


# ── Scheduler ────────────────────────────────────────────────

class FluidTask(BaseModel):
    """A health/habit task that can be placed into open gaps."""
    name: str
    duration_minutes: int = Field(gt=0)
    priority: int = Field(default=1, ge=1, le=10, description="Higher = scheduled first")


class ScheduleItem(BaseModel):
    """A single item in the optimized daily schedule."""
    name: str
    start: datetime
    end: datetime
    flexibility: Flexibility
    placed: bool = True
    reason: Optional[str] = None


class OptimizedSchedule(BaseModel):
    """Full optimized schedule for a single day."""
    date: str
    schedule: list[ScheduleItem]
    unplaced: list[ScheduleItem] = []
    generated_at: datetime


# ── Chat ─────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Request body for the /api/chat endpoint."""
    message: str = Field(min_length=1, max_length=4000)
    context: Optional[str] = Field(default=None, max_length=8000, description="Optional schedule/habit context")


class ChatResponse(BaseModel):
    """Response from the Gemini chat endpoint."""
    response: str
    model: str = "gemini-2.0-flash"


# ── Habits ───────────────────────────────────────────────────

class HabitEntry(BaseModel):
    """A single habit with its streak and completion metrics."""
    name: str
    streak: int = 0
    last_completed: Optional[datetime] = None
    completion_rate_7d: float = Field(ge=0.0, le=1.0)


class HabitSummary(BaseModel):
    """Aggregate habit summary response."""
    habits: list[HabitEntry]
    generated_at: datetime


# ── Expenses / Budget ────────────────────────────────────────

EXPENSE_CATEGORIES = [
    "Food", "Transport", "Entertainment", "Bills",
    "Shopping", "Health", "Education", "Other",
]


class ExpenseEntry(BaseModel):
    """A single expense the user logged."""
    name: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)
    category: str = Field(description="One of: " + ", ".join(EXPENSE_CATEGORIES))


class ExpenseRecord(ExpenseEntry):
    """An expense as stored in Supabase (with id + timestamp)."""
    id: str
    created_at: datetime


class ExpenseListResponse(BaseModel):
    """Response for GET /api/expenses."""
    expenses: list[ExpenseRecord]
    total: float
    generated_at: datetime


class BudgetRecommendation(BaseModel):
    """A single category recommendation from Gemini."""
    category: str
    suggested_amount: float
    reasoning: str


class PieSlice(BaseModel):
    """A slice of the spending pie chart."""
    category: str
    amount: float
    color: str


class BudgetResponse(BaseModel):
    """Response for POST /api/budget/recommend."""
    recommendations: list[BudgetRecommendation]
    pie_data: list[PieSlice]
    summary: str
    generated_at: datetime


# ── Health ───────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    timestamp: datetime

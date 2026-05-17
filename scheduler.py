"""Aura Timeline-Shifting Scheduler — Pure Algorithm Module.

Takes fixed calendar events and fluid habit tasks, extracts open gaps
between fixed blocks, and greedily places fluid tasks by priority.

This module has NO external I/O. It takes structured data in and returns
structured data out. The calling service handles persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from models.schemas import (
    CalendarEvent,
    Flexibility,
    FluidTask,
    OptimizedSchedule,
    ScheduleItem,
)


# ── Internal data structures ─────────────────────────────────

@dataclass
class OpenGap:
    """A time window between fixed events where fluid tasks can be placed."""
    start: datetime
    end: datetime

    @property
    def duration_minutes(self) -> int:
        delta = (self.end - self.start).total_seconds() / 60
        return max(0, int(delta))


@dataclass
class _PlacementResult:
    """Result of attempting to place a single fluid task."""
    task: FluidTask
    placed: bool
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    reason: Optional[str] = None


# ── Core Algorithm ───────────────────────────────────────────

def _extract_gaps(
    fixed_events: list[CalendarEvent],
    day_start: datetime,
    day_end: datetime,
) -> list[OpenGap]:
    """Walk through sorted fixed events and extract open gaps.

    Uses max(cursor, event.end) to handle late-running events.
    If a fixed event overran its slot, subsequent gaps shrink accordingly.

    Args:
        fixed_events: Sorted list of fixed events (by start time ascending).
        day_start: Start of the scheduling window (e.g., 06:00).
        day_end: End of the scheduling window (e.g., 23:00).

    Returns:
        List of OpenGap objects representing available time windows.
    """
    gaps: list[OpenGap] = []
    cursor = day_start

    for event in fixed_events:
        # If the event starts after the cursor, there's a gap
        if event.start > cursor:
            gaps.append(OpenGap(start=cursor, end=event.start))

        # Advance cursor — max handles late-running events
        cursor = max(cursor, event.end)

    # Final gap after the last fixed event
    if cursor < day_end:
        gaps.append(OpenGap(start=cursor, end=day_end))

    # Filter out zero-length gaps
    return [g for g in gaps if g.duration_minutes > 0]


def _place_fluid_tasks(
    fluid_tasks: list[FluidTask],
    gaps: list[OpenGap],
) -> list[_PlacementResult]:
    """Greedy first-fit placement by priority.

    Higher-priority tasks get first pick of available gaps.
    Each placed task shrinks the gap from the front.

    Args:
        fluid_tasks: Sorted by priority descending.
        gaps: Available time windows (mutated in place).

    Returns:
        List of PlacementResult objects.
    """
    results: list[_PlacementResult] = []

    for task in fluid_tasks:
        placed = False

        for gap in gaps:
            if gap.duration_minutes >= task.duration_minutes:
                # Place the task at the start of this gap
                task_start = gap.start
                task_end = gap.start + timedelta(minutes=task.duration_minutes)

                results.append(_PlacementResult(
                    task=task,
                    placed=True,
                    start=task_start,
                    end=task_end,
                ))

                # Shrink the gap from the front
                gap.start = task_end
                placed = True
                break

        if not placed:
            results.append(_PlacementResult(
                task=task,
                placed=False,
                reason="no gap large enough",
            ))

    return results


# ── Public API ───────────────────────────────────────────────

def optimize_day(
    events: list[CalendarEvent],
    fluid_tasks: list[FluidTask],
    target_date: Optional[str] = None,
    day_start_hour: int = 6,
    day_end_hour: int = 23,
) -> OptimizedSchedule:
    """Optimize a single day's schedule.

    Fixed events stay locked. Fluid tasks are placed into open gaps
    by priority using greedy first-fit.

    Args:
        events: All calendar events for the day.
        fluid_tasks: Habit tasks to place (workouts, meals, etc.).
        target_date: ISO date string. Defaults to today.
        day_start_hour: Start of scheduling window (24h format).
        day_end_hour: End of scheduling window (24h format).

    Returns:
        OptimizedSchedule with placed items, unplaced items, and metadata.
    """
    # Determine the target date
    if target_date:
        base_date = datetime.fromisoformat(target_date)
    else:
        base_date = datetime.now()

    day_start = base_date.replace(
        hour=day_start_hour, minute=0, second=0, microsecond=0
    )
    day_end = base_date.replace(
        hour=day_end_hour, minute=0, second=0, microsecond=0
    )

    # Step 1: Separate fixed and fluid events
    fixed_events = sorted(
        [e for e in events if e.flexibility == Flexibility.FIXED],
        key=lambda e: e.start,
    )

    # Step 2: Extract open gaps
    gaps = _extract_gaps(fixed_events, day_start, day_end)

    # Step 3: Sort fluid tasks by priority (highest first)
    sorted_tasks = sorted(fluid_tasks, key=lambda t: t.priority, reverse=True)

    # Step 4: Place fluid tasks
    placements = _place_fluid_tasks(sorted_tasks, gaps)

    # Step 5: Build the composite schedule
    schedule_items: list[ScheduleItem] = []

    # Add fixed events
    for event in fixed_events:
        schedule_items.append(ScheduleItem(
            name=event.title,
            start=event.start,
            end=event.end,
            flexibility=Flexibility.FIXED,
        ))

    # Add placed fluid tasks
    unplaced: list[ScheduleItem] = []
    for placement in placements:
        if placement.placed:
            schedule_items.append(ScheduleItem(
                name=placement.task.name,
                start=placement.start,
                end=placement.end,
                flexibility=Flexibility.FLUID,
            ))
        else:
            unplaced.append(ScheduleItem(
                name=placement.task.name,
                start=day_start,  # placeholder
                end=day_start,    # placeholder
                flexibility=Flexibility.FLUID,
                placed=False,
                reason=placement.reason,
            ))

    # Sort final schedule by start time
    schedule_items.sort(key=lambda item: item.start)

    return OptimizedSchedule(
        date=base_date.strftime("%Y-%m-%d"),
        schedule=schedule_items,
        unplaced=unplaced,
        generated_at=datetime.now(),
    )

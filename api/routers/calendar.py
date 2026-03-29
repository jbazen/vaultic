"""Financial calendar router.

Manages user financial events — tax deadlines, estimated payments, budget meetings,
paydays, and custom events. Supports full-day and timed events.

Endpoints (specific routes before parameterized — per CLAUDE.md):
  GET  /api/calendar/upcoming    — next N days (default 14); used by Dashboard + Sage
  POST /api/calendar/seed        — seed standard tax/meeting events for this year + next (idempotent)
  GET  /api/calendar             — events in a date range (?from=YYYY-MM-DD&to=YYYY-MM-DD)
  POST /api/calendar             — create a custom event
  PATCH /api/calendar/{event_id} — update an event
  DELETE /api/calendar/{event_id}— soft-delete an event
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.database import get_db
from api.dependencies import get_current_user

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


# ── Request models ─────────────────────────────────────────────────────────────

class CreateEventBody(BaseModel):
    """Fields for creating a new financial calendar event."""
    title: str
    description: Optional[str] = None
    start_dt: str               # "YYYY-MM-DD" for all-day, "YYYY-MM-DDTHH:MM:SS" for timed
    end_dt: Optional[str] = None
    all_day: bool = True
    event_type: str = "custom"  # tax_deadline | estimated_tax | payday | budget_meeting | statement_drop | custom
    recurring: str = "none"     # none | weekly | monthly | quarterly | annually
    reminder_days_before: int = 3


class UpdateEventBody(BaseModel):
    """Partial update — only supplied fields are changed."""
    title: Optional[str] = None
    description: Optional[str] = None
    start_dt: Optional[str] = None
    end_dt: Optional[str] = None
    all_day: Optional[bool] = None
    event_type: Optional[str] = None
    recurring: Optional[str] = None
    reminder_days_before: Optional[int] = None


# ── Seed helpers ───────────────────────────────────────────────────────────────

def _adjust_for_weekend(d: date) -> date:
    """If d falls on Saturday or Sunday, move to the following Monday."""
    if d.weekday() == 5:   # Saturday → Monday
        return d + timedelta(days=2)
    if d.weekday() == 6:   # Sunday → Monday
        return d + timedelta(days=1)
    return d


def _first_saturday(year: int, month: int) -> date:
    """Return the first Saturday of the given month."""
    first = date(year, month, 1)
    days_to_sat = (5 - first.weekday()) % 7   # 5 = Saturday; 0 if already Saturday
    return first + timedelta(days=days_to_sat)


def _standard_events(year: int, username: str) -> list[dict]:
    """Build the standard set of financial events for a given year.

    Returns a list of dicts ready to INSERT into financial_events. Each event
    is uniquely identified by (username, title, start_dt, auto_generated=1) for
    idempotency checks in the seed endpoint.
    """
    events = []

    # ── Federal tax filing deadline — April 15 ──────────────────────────────
    tax_due = _adjust_for_weekend(date(year, 4, 15))
    events.append({
        "username": username,
        "title": f"{year} Federal Tax Filing Deadline",
        "description": "Form 1040 due. File Form 4868 for a 6-month extension.",
        "start_dt": tax_due.isoformat(),
        "end_dt": None,
        "all_day": 1,
        "event_type": "tax_deadline",
        "recurring": "annually",
        "reminder_days_before": 14,
    })

    # ── 1040-ES quarterly estimated tax payments ─────────────────────────────
    quarters = [
        (f"{year} Q1 Estimated Tax (1040-ES)", date(year, 4, 15)),
        (f"{year} Q2 Estimated Tax (1040-ES)", date(year, 6, 15)),
        (f"{year} Q3 Estimated Tax (1040-ES)", date(year, 9, 15)),
        (f"{year} Q4 Estimated Tax (1040-ES)", date(year + 1, 1, 15)),
    ]
    for q_title, q_date in quarters:
        q_due = _adjust_for_weekend(q_date)
        events.append({
            "username": username,
            "title": q_title,
            "description": "IRS Form 1040-ES quarterly estimated income tax payment due.",
            "start_dt": q_due.isoformat(),
            "end_dt": None,
            "all_day": 1,
            "event_type": "estimated_tax",
            "recurring": "quarterly",
            "reminder_days_before": 7,
        })

    # ── Monthly budget meetings — first Saturday of each month ───────────────
    for month in range(1, 13):
        sat = _first_saturday(year, month)
        events.append({
            "username": username,
            "title": "Budget Meeting",
            "description": "Monthly zero-based budget review and fund financials update.",
            "start_dt": f"{sat.isoformat()}T10:00:00",
            "end_dt": f"{sat.isoformat()}T11:00:00",
            "all_day": 0,
            "event_type": "budget_meeting",
            "recurring": "monthly",
            "reminder_days_before": 3,
        })

    return events


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/upcoming")
def get_upcoming(days: int = 14, user: str = Depends(get_current_user)):
    """Return the next N days of active events for the current user.

    Used by the Dashboard calendar widget and the Sage get_upcoming_events tool.
    Adds a `days_until` field (integer) to each event for display convenience.
    """
    today = date.today()
    until = (today + timedelta(days=days)).isoformat()
    today_str = today.isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, title, description, start_dt, end_dt, all_day,
                      event_type, recurring, reminder_days_before
               FROM financial_events
               WHERE username = ? AND is_active = 1
                 AND DATE(start_dt) BETWEEN ? AND ?
               ORDER BY start_dt""",
            (user, today_str, until),
        ).fetchall()
    result = []
    for r in rows:
        ev = dict(r)
        try:
            ev["days_until"] = (date.fromisoformat(r["start_dt"][:10]) - today).days
        except ValueError:
            ev["days_until"] = None
        result.append(ev)
    return result


@router.post("/seed")
def seed_events(user: str = Depends(get_current_user)):
    """Seed standard financial events for the current year and next year.

    Safe to call multiple times — skips events that already exist (matched by
    username + title + start_dt + auto_generated=1).
    Returns the count of newly inserted events.
    """
    today = date.today()
    inserted = 0
    with get_db() as conn:
        for year in [today.year, today.year + 1]:
            for ev in _standard_events(year, user):
                # Idempotency: skip if this exact auto-generated event already exists
                exists = conn.execute(
                    """SELECT 1 FROM financial_events
                       WHERE username = ? AND title = ? AND start_dt = ? AND auto_generated = 1""",
                    (user, ev["title"], ev["start_dt"]),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """INSERT INTO financial_events
                         (username, title, description, start_dt, end_dt, all_day,
                          event_type, recurring, reminder_days_before, auto_generated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                    (
                        ev["username"], ev["title"], ev["description"],
                        ev["start_dt"], ev["end_dt"], ev["all_day"],
                        ev["event_type"], ev["recurring"], ev["reminder_days_before"],
                    ),
                )
                inserted += 1
    return {"ok": True, "inserted": inserted}


@router.get("")
def list_events(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    user: str = Depends(get_current_user),
):
    """List events in a date range. Returns all active events if no range given."""
    with get_db() as conn:
        if from_date and to_date:
            rows = conn.execute(
                """SELECT * FROM financial_events
                   WHERE username = ? AND is_active = 1
                     AND DATE(start_dt) BETWEEN ? AND ?
                   ORDER BY start_dt""",
                (user, from_date, to_date),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM financial_events
                   WHERE username = ? AND is_active = 1
                   ORDER BY start_dt""",
                (user,),
            ).fetchall()
    return [dict(r) for r in rows]


@router.post("")
def create_event(body: CreateEventBody, user: str = Depends(get_current_user)):
    """Create a new calendar event. Returns the new event's id."""
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO financial_events
                 (username, title, description, start_dt, end_dt, all_day,
                  event_type, recurring, reminder_days_before)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user, body.title, body.description,
                body.start_dt, body.end_dt,
                1 if body.all_day else 0,
                body.event_type, body.recurring,
                body.reminder_days_before,
            ),
        )
    return {"ok": True, "id": cur.lastrowid}


@router.patch("/{event_id}")
def update_event(
    event_id: int,
    body: UpdateEventBody,
    user: str = Depends(get_current_user),
):
    """Update an existing event. Only supplied (non-None) fields are changed."""
    with get_db() as conn:
        ev = conn.execute(
            "SELECT id FROM financial_events WHERE id = ? AND username = ? AND is_active = 1",
            (event_id, user),
        ).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="Event not found")

        # Build SET clause from non-None fields only
        raw = body.model_dump()
        updates = {k: v for k, v in raw.items() if v is not None}
        if "all_day" in updates:
            updates["all_day"] = 1 if updates["all_day"] else 0
        if not updates:
            return {"ok": True}

        # Column names come from the Pydantic model — safe to use in SET clause
        cols = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE financial_events SET {cols} WHERE id = ?",
            (*updates.values(), event_id),
        )
    return {"ok": True}


@router.delete("/{event_id}")
def delete_event(event_id: int, user: str = Depends(get_current_user)):
    """Soft-delete an event (sets is_active=0). The row is preserved for audit."""
    with get_db() as conn:
        ev = conn.execute(
            "SELECT id FROM financial_events WHERE id = ? AND username = ? AND is_active = 1",
            (event_id, user),
        ).fetchone()
        if not ev:
            raise HTTPException(status_code=404, detail="Event not found")
        conn.execute(
            "UPDATE financial_events SET is_active = 0 WHERE id = ?",
            (event_id,),
        )
    return {"ok": True}

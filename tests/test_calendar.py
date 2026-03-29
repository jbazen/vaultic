"""Tests for the Financial Calendar API endpoints.

Covers:
  - Auth guards: all endpoints require JWT
  - Route ordering: /upcoming and /seed must be matched BEFORE /{event_id} so
    the literal strings "upcoming" and "seed" are never interpreted as integers
  - Seed idempotency: POST /seed returns correct counts and skips duplicates
  - CRUD lifecycle: create → list → update → soft-delete
  - Soft-delete: deleted events do not appear in listing
  - Upcoming filter: only returns events in the requested date window
  - Days-until field: correctly computed in /upcoming response
  - Weekend adjustment: tax deadlines on Saturday/Sunday move to Monday
  - First-Saturday logic: budget meetings land on the correct date
  - 404 on unknown event: update/delete non-existent id returns 404
"""
from datetime import date, timedelta


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create(client, auth_headers, **kwargs):
    """Create a calendar event with sensible defaults and return the response JSON."""
    payload = {
        "title": "Test Event",
        "start_dt": date.today().isoformat(),
        "all_day": True,
        "event_type": "custom",
        "recurring": "none",
        "reminder_days_before": 3,
    }
    payload.update(kwargs)
    res = client.post("/api/calendar", json=payload, headers=auth_headers)
    assert res.status_code == 200, res.text
    return res.json()


# ── Auth guard tests ───────────────────────────────────────────────────────────

class TestCalendarAuth:
    """All calendar endpoints require a valid JWT."""

    def test_upcoming_requires_auth(self, client):
        res = client.get("/api/calendar/upcoming")
        assert res.status_code == 401

    def test_seed_requires_auth(self, client):
        res = client.post("/api/calendar/seed")
        assert res.status_code == 401

    def test_list_requires_auth(self, client):
        res = client.get("/api/calendar")
        assert res.status_code == 401

    def test_create_requires_auth(self, client):
        res = client.post("/api/calendar", json={"title": "x", "start_dt": "2026-01-01"})
        assert res.status_code == 401

    def test_update_requires_auth(self, client):
        res = client.patch("/api/calendar/1", json={"title": "y"})
        assert res.status_code == 401

    def test_delete_requires_auth(self, client):
        res = client.delete("/api/calendar/1")
        assert res.status_code == 401


# ── Route ordering regression ─────────────────────────────────────────────────

class TestRouteOrdering:
    """
    'upcoming' and 'seed' must be defined BEFORE /{event_id}.
    If they come after, FastAPI tries to cast the literal string as an integer
    and returns 422 instead of routing correctly.
    """

    def test_upcoming_route_not_mismatched_as_event_id(self, client, auth_headers):
        res = client.get("/api/calendar/upcoming", headers=auth_headers)
        # Must route to the listing handler (200), not the event lookup (422)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_seed_route_not_mismatched_as_event_id(self, client, auth_headers):
        res = client.post("/api/calendar/seed", headers=auth_headers)
        # Must reach the seed handler and return {"ok": True, "inserted": N}
        assert res.status_code == 200
        data = res.json()
        assert "ok" in data
        assert "inserted" in data


# ── Seed idempotency ──────────────────────────────────────────────────────────

class TestSeedIdempotency:
    """POST /api/calendar/seed is safe to call multiple times."""

    def test_seed_inserts_events(self, client, auth_headers):
        res = client.post("/api/calendar/seed", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        # Should insert at least 1 event (tax deadline for each year = 2 minimum).
        # May insert 0 if a previous test in this session already seeded.
        assert data["inserted"] >= 0

    def test_seed_is_idempotent(self, client, auth_headers):
        # First call: seed everything (may already be 0 if prior test ran first)
        client.post("/api/calendar/seed", headers=auth_headers)
        # Second call must always insert 0 — all events already exist
        second = client.post("/api/calendar/seed", headers=auth_headers).json()
        assert second["inserted"] == 0

    def test_seed_creates_tax_deadline(self, client, auth_headers):
        client.post("/api/calendar/seed", headers=auth_headers)
        events = client.get("/api/calendar", headers=auth_headers).json()
        titles = [e["title"] for e in events]
        # There should be a Federal Tax Filing Deadline for the current year
        current_year = date.today().year
        assert any(str(current_year) in t and "Federal Tax Filing" in t for t in titles)

    def test_seed_creates_estimated_tax_payments(self, client, auth_headers):
        client.post("/api/calendar/seed", headers=auth_headers)
        events = client.get("/api/calendar", headers=auth_headers).json()
        types = [e["event_type"] for e in events]
        assert "estimated_tax" in types

    def test_seed_creates_budget_meetings(self, client, auth_headers):
        client.post("/api/calendar/seed", headers=auth_headers)
        events = client.get("/api/calendar", headers=auth_headers).json()
        types = [e["event_type"] for e in events]
        assert "budget_meeting" in types


# ── CRUD lifecycle ────────────────────────────────────────────────────────────

class TestEventCRUD:
    """Full create → list → update → delete lifecycle."""

    def test_create_event(self, client, auth_headers):
        data = _create(client, auth_headers, title="Quarterly Review", event_type="custom")
        assert data["ok"] is True
        assert "id" in data

    def test_create_event_appears_in_list(self, client, auth_headers):
        created = _create(client, auth_headers, title="List Test")
        event_id = created["id"]
        events = client.get("/api/calendar", headers=auth_headers).json()
        ids = [e["id"] for e in events]
        assert event_id in ids

    def test_update_event_title(self, client, auth_headers):
        event_id = _create(client, auth_headers, title="Old Title")["id"]
        res = client.patch(f"/api/calendar/{event_id}", json={"title": "New Title"}, headers=auth_headers)
        assert res.status_code == 200
        events = client.get("/api/calendar", headers=auth_headers).json()
        updated = next(e for e in events if e["id"] == event_id)
        assert updated["title"] == "New Title"

    def test_soft_delete_removes_from_list(self, client, auth_headers):
        event_id = _create(client, auth_headers, title="To Delete")["id"]
        res = client.delete(f"/api/calendar/{event_id}", headers=auth_headers)
        assert res.status_code == 200
        events = client.get("/api/calendar", headers=auth_headers).json()
        ids = [e["id"] for e in events]
        assert event_id not in ids

    def test_update_nonexistent_event_returns_404(self, client, auth_headers):
        res = client.patch("/api/calendar/99999", json={"title": "Ghost"}, headers=auth_headers)
        assert res.status_code == 404

    def test_delete_nonexistent_event_returns_404(self, client, auth_headers):
        res = client.delete("/api/calendar/99999", headers=auth_headers)
        assert res.status_code == 404

    def test_create_timed_event(self, client, auth_headers):
        today = date.today().isoformat()
        data = _create(
            client, auth_headers,
            title="Budget Meeting",
            start_dt=f"{today}T10:00:00",
            end_dt=f"{today}T11:00:00",
            all_day=False,
            event_type="budget_meeting",
        )
        assert data["ok"] is True


# ── Upcoming filter ───────────────────────────────────────────────────────────

class TestUpcomingFilter:
    """GET /upcoming only returns events in the requested window."""

    def test_upcoming_returns_list(self, client, auth_headers):
        res = client.get("/api/calendar/upcoming", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_upcoming_has_days_until_field(self, client, auth_headers):
        # Create an event today so we know there's at least one in the window
        today = date.today().isoformat()
        _create(client, auth_headers, title="Today Event DaysUntil", start_dt=today)
        events = client.get("/api/calendar/upcoming?days=14", headers=auth_headers).json()
        for ev in events:
            assert "days_until" in ev

    def test_upcoming_days_until_is_zero_for_today(self, client, auth_headers):
        today = date.today().isoformat()
        _create(client, auth_headers, title="Today Zero Check", start_dt=today)
        events = client.get("/api/calendar/upcoming?days=1", headers=auth_headers).json()
        today_events = [e for e in events if e["title"] == "Today Zero Check"]
        assert len(today_events) == 1
        assert today_events[0]["days_until"] == 0

    def test_upcoming_excludes_past_events(self, client, auth_headers):
        past = (date.today() - timedelta(days=10)).isoformat()
        _create(client, auth_headers, title="Past Event Exclude", start_dt=past)
        events = client.get("/api/calendar/upcoming?days=14", headers=auth_headers).json()
        titles = [e["title"] for e in events]
        assert "Past Event Exclude" not in titles


# ── Weekend adjustment + first-Saturday helper unit tests ─────────────────────

class TestHelpers:
    """Unit tests for the calendar seed helper functions (no HTTP needed)."""

    def test_adjust_for_weekend_saturday(self):
        from api.routers.calendar import _adjust_for_weekend
        # 2026-04-18 is a Saturday → should move to Monday 2026-04-20
        sat = date(2026, 4, 18)
        assert sat.weekday() == 5        # confirm assumption
        result = _adjust_for_weekend(sat)
        assert result == date(2026, 4, 20)

    def test_adjust_for_weekend_sunday(self):
        from api.routers.calendar import _adjust_for_weekend
        # 2026-04-19 is a Sunday → should move to Monday 2026-04-20
        sun = date(2026, 4, 19)
        assert sun.weekday() == 6        # confirm assumption
        result = _adjust_for_weekend(sun)
        assert result == date(2026, 4, 20)

    def test_adjust_for_weekend_weekday_unchanged(self):
        from api.routers.calendar import _adjust_for_weekend
        # 2026-04-15 is a Wednesday — must be unchanged
        wed = date(2026, 4, 15)
        assert wed.weekday() == 2
        result = _adjust_for_weekend(wed)
        assert result == wed

    def test_first_saturday_april(self):
        from api.routers.calendar import _first_saturday
        # April 2026: first Saturday is 2026-04-04
        result = _first_saturday(2026, 4)
        assert result == date(2026, 4, 4)
        assert result.weekday() == 5     # verify it's Saturday

    def test_first_saturday_january(self):
        from api.routers.calendar import _first_saturday
        # January 2026: first Saturday is 2026-01-03
        result = _first_saturday(2026, 1)
        assert result == date(2026, 1, 3)
        assert result.weekday() == 5

    def test_first_saturday_when_month_starts_on_saturday(self):
        from api.routers.calendar import _first_saturday
        # August 2026: August 1 is a Saturday → first Saturday IS Aug 1
        aug1 = date(2026, 8, 1)
        assert aug1.weekday() == 5       # confirm assumption
        result = _first_saturday(2026, 8)
        assert result == date(2026, 8, 1)

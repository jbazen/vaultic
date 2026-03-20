"""Unit tests for the Google Sheets fund-financials CSV parser.

Tests parse the actual CSV structure without making network requests.
The CSV format is:
  Row 0: blank, "TO START", blank, blank, blank, "Oct-15", blank, blank, blank, "Nov-15", ...
  Row 1: blank, "HEATHER", "JASON", "TOTAL", blank, "Saved", "Spent", "Balance", blank, "Saved", ...
  Data:  fund name, heather_total, jason_total, combined_total, blank, saved, spent, balance, ...
  Section headers: "CAPITAL ONE 360:", blank, blank, blank, ... (all blank after col 0)
"""
from unittest.mock import patch, MagicMock

import pytest

from api.routers.sheet import _parse_dollar, _month_sort_key


# ── Pure-function unit tests ───────────────────────────────────────────────────

class TestParseDollar:
    def test_plain_integer(self):
        assert _parse_dollar("1000") == 1000.0

    def test_dollar_sign_and_commas(self):
        assert _parse_dollar("$1,234.56") == 1234.56

    def test_parentheses_negative(self):
        assert _parse_dollar("(500.00)") == -500.0

    def test_blank_returns_none(self):
        assert _parse_dollar("") is None
        assert _parse_dollar("  ") is None

    def test_dash_returns_none(self):
        assert _parse_dollar("-") is None
        assert _parse_dollar("—") is None

    def test_zero(self):
        assert _parse_dollar("0.00") == 0.0

    def test_spaces_around_value(self):
        # Google Sheets CSV sometimes pads values with spaces
        assert _parse_dollar(" 57,769.72 ") == pytest.approx(57769.72)


class TestMonthSortKey:
    def test_chronological_order(self):
        months = ["Mar-26", "Oct-15", "Jan-26", "Dec-25"]
        sorted_months = sorted(months, key=_month_sort_key)
        assert sorted_months == ["Oct-15", "Dec-25", "Jan-26", "Mar-26"]

    def test_year_boundary(self):
        assert _month_sort_key("Dec-25") < _month_sort_key("Jan-26")

    def test_invalid_label(self):
        assert _month_sort_key("invalid") == 0


# ── Integration tests: full CSV parse via mocked HTTP ─────────────────────────

# Minimal CSV matching the actual sheet structure.
# Cols: name | HEATHER | JASON | TOTAL | blank | Oct-24 Saved | Spent | Balance | blank | Nov-24 Saved | Spent | Balance
MOCK_CSV = """,TO START,,,,,Sep-24,,,,Oct-24,,,,Nov-24,,,
,HEATHER,JASON,TOTAL,,Saved,Spent,Balance,,Saved,Spent,Balance,,Saved,Spent,Balance
CAPITAL ONE 360:,,,,,,,,,,,,,,,,,
Vacation Fund," 5,000.00 "," 3,000.00 "," 8,000.00 ",, 400.00 , 0.00 ," 8,000.00 ",, 500.00 , 0.00 ," 8,500.00 ",, 200.00 , 100.00 ," 8,600.00 "
Holiday Gifts," 1,200.00 "," 800.00 "," 2,000.00 ",, 100.00 , 0.00 ," 2,000.00 ",, 100.00 , 0.00 ," 2,100.00 ",, 50.00 , 200.00 ," 1,950.00 "
"""


def _make_mock_response(csv_text: str):
    mock_resp = MagicMock()
    mock_resp.text = csv_text
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


class TestSheetEndpoint:
    def test_returns_expected_structure(self, client, auth_headers):
        with patch("api.routers.sheet.requests.get", return_value=_make_mock_response(MOCK_CSV)):
            resp = client.get("/api/sheet/fund-financials", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert "months" in data
        assert "categories" in data
        assert set(data["months"]) == {"Sep-24", "Oct-24", "Nov-24"}
        # Section header "CAPITAL ONE 360:" excluded; 2 fund rows remain
        assert len(data["categories"]) == 2

    def test_category_values(self, client, auth_headers):
        with patch("api.routers.sheet.requests.get", return_value=_make_mock_response(MOCK_CSV)):
            resp = client.get("/api/sheet/fund-financials", headers=auth_headers)
        data = resp.json()
        vacation = next(c for c in data["categories"] if c["name"] == "Vacation Fund")

        assert vacation["heather"] == pytest.approx(5000.0)
        assert vacation["jason"]   == pytest.approx(3000.0)
        assert vacation["total"]   == pytest.approx(8000.0)
        assert vacation["monthly"]["Nov-24"] == pytest.approx(8600.0)
        assert vacation["monthly"]["Oct-24"] == pytest.approx(8500.0)
        assert vacation["monthly"]["Sep-24"] == pytest.approx(8000.0)

    def test_holiday_gifts_values(self, client, auth_headers):
        with patch("api.routers.sheet.requests.get", return_value=_make_mock_response(MOCK_CSV)):
            resp = client.get("/api/sheet/fund-financials", headers=auth_headers)
        data = resp.json()
        gifts = next(c for c in data["categories"] if c["name"] == "Holiday Gifts")

        assert gifts["total"] == pytest.approx(2000.0)
        # Nov-24: 50 saved, 200 spent → balance dropped to 1950
        assert gifts["monthly"]["Nov-24"] == pytest.approx(1950.0)

    def test_months_are_chronological(self, client, auth_headers):
        with patch("api.routers.sheet.requests.get", return_value=_make_mock_response(MOCK_CSV)):
            resp = client.get("/api/sheet/fund-financials", headers=auth_headers)
        months = resp.json()["months"]
        sort_keys = [_month_sort_key(m) for m in months]
        assert sort_keys == sorted(sort_keys), "Months must be in chronological order"

    def test_requires_auth(self, client):
        resp = client.get("/api/sheet/fund-financials")
        assert resp.status_code == 401

    def test_upstream_error_returns_502(self, client, auth_headers):
        import requests as req_lib
        with patch("api.routers.sheet.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = client.get("/api/sheet/fund-financials", headers=auth_headers)
        assert resp.status_code == 502

    def test_empty_csv_returns_502(self, client, auth_headers):
        with patch("api.routers.sheet.requests.get", return_value=_make_mock_response("a,b\n")):
            resp = client.get("/api/sheet/fund-financials", headers=auth_headers)
        assert resp.status_code == 502

"""Tests for PDF ingestion and save endpoints, including _salvage_json recovery."""
import io
import json
import pytest
from unittest.mock import patch, MagicMock

from api.routers.pdf import _salvage_json


FAKE_PDF_BYTES = b"%PDF-1.4 fake pdf content"


# ---------------------------------------------------------------------------
# _salvage_json unit tests
# ---------------------------------------------------------------------------

class TestSalvageJson:
    def test_parses_complete_array(self):
        data = [{"name": "IRA", "value": 85000}, {"name": "529", "value": 12000}]
        assert _salvage_json(json.dumps(data)) == data

    def test_recovers_from_truncated_array(self):
        # Simulate a response cut off in the middle of the second object
        raw = '[{"name": "IRA", "value": 85000}, {"name": "529", "val'
        result = _salvage_json(raw)
        assert len(result) == 1
        assert result[0]["name"] == "IRA"

    def test_returns_empty_list_for_garbage_input(self):
        assert _salvage_json("not json at all") == []

    def test_handles_nested_objects_correctly(self):
        # Holdings inside entries should not confuse the depth counter
        raw = '[{"name": "Parker IRA", "holdings": [{"name": "Large-Cap", "value": 50000}], "value": 85000}]'
        result = _salvage_json(raw)
        assert len(result) == 1
        assert result[0]["holdings"][0]["name"] == "Large-Cap"

    def test_handles_strings_with_braces(self):
        # Braces inside string values should not affect depth tracking
        raw = '[{"name": "Fund {A}", "value": 1000}]'
        result = _salvage_json(raw)
        assert len(result) == 1
        assert result[0]["name"] == "Fund {A}"


def _make_pdf_file(content=FAKE_PDF_BYTES):
    return ("test.pdf", io.BytesIO(content), "application/pdf")


class TestPDFIngest:
    def test_ingest_requires_auth(self, client):
        res = client.post("/api/pdf/ingest",
                          files={"file": _make_pdf_file()})
        assert res.status_code == 401

    def test_ingest_returns_parsed_entries(self, client, auth_headers):
        fake_entries = [
            {
                "name": "Parker IRA",
                "category": "invested",
                "value": 85000,
                "notes": "Traditional IRA",
                "activity_summary": {
                    "beginning_balance": 90000,
                    "beginning_date": "1/1/2026",
                    "net_change": -5000,
                    "ending_balance": 85000,
                    "ending_date": "3/1/2026",
                    "twr_pct": -5.5,
                },
                "holdings": [
                    {"name": "Large-Cap Growth", "value": 50000, "asset_class": "equities", "pct_assets": 58.8},
                ],
            },
            {"name": "College Fund", "category": "invested", "value": 12000, "notes": "529 plan"},
        ]

        with patch("pdfplumber.open") as mock_pdf, \
             patch("api.routers.pdf.anthropic.Anthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):

            # Mock pdfplumber
            mock_page = MagicMock()
            mock_page.extract_text.return_value = "Account: Parker IRA $85,000\nCollege Fund $12,000"
            mock_pdf.return_value.__enter__.return_value.pages = [mock_page]

            # Mock Anthropic response
            mock_block = MagicMock()
            import json
            mock_block.text = json.dumps(fake_entries)
            mock_resp = MagicMock()
            mock_resp.content = [mock_block]
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            res = client.post("/api/pdf/ingest",
                              headers=auth_headers,
                              files={"file": _make_pdf_file()})

        assert res.status_code == 200
        data = res.json()
        key = "parsed" if "parsed" in data else "entries"
        assert isinstance(data[key], list)
        assert len(data[key]) == 2

    def test_ingest_rejects_non_pdf(self, client, auth_headers):
        res = client.post("/api/pdf/ingest",
                          headers=auth_headers,
                          files={"file": ("test.txt", io.BytesIO(b"not a pdf"), "text/plain")})
        assert res.status_code in (400, 422)

    def test_ingest_rejects_oversized_file(self, client, auth_headers):
        big_content = b"%PDF-1.4 " + b"x" * (21 * 1024 * 1024)  # 21MB
        res = client.post("/api/pdf/ingest",
                          headers=auth_headers,
                          files={"file": ("big.pdf", io.BytesIO(big_content), "application/pdf")})
        assert res.status_code in (400, 413)

    def test_ingest_accepts_file_over_1mb(self, client, auth_headers):
        """FastAPI limit is 20MB. Files between 1MB and 20MB must be accepted by the API.
        (nginx is configured separately with client_max_body_size 25m — that's nginx, not FastAPI.)
        """
        # 2MB PDF — well within the 20MB FastAPI limit
        content = b"%PDF-1.4 " + b"A" * (2 * 1024 * 1024)

        fake_entries = [{"name": "Large PDF Account", "category": "invested", "value": 50000}]

        with patch("pdfplumber.open") as mock_pdf, \
             patch("api.routers.pdf.anthropic.Anthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):

            mock_page = MagicMock()
            mock_page.extract_text.return_value = "Account: $50,000"
            mock_pdf.return_value.__enter__.return_value.pages = [mock_page]

            mock_block = MagicMock()
            mock_block.text = json.dumps(fake_entries)
            mock_resp = MagicMock()
            mock_resp.content = [mock_block]
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            res = client.post("/api/pdf/ingest",
                              headers=auth_headers,
                              files={"file": ("large.pdf", io.BytesIO(content), "application/pdf")})

        # Should not be rejected for size (413) — only rejected if > 20MB
        assert res.status_code != 413, "API should accept PDFs up to 20MB (nginx handles 25MB limit)"
        assert res.status_code == 200


class TestPDFSave:
    def test_save_requires_auth(self, client):
        res = client.post("/api/pdf/save", json={"entries": []})
        assert res.status_code == 401

    def test_save_valid_entries(self, client, auth_headers):
        entries = [
            {"name": "Parker IRA Unique", "category": "invested", "value": 85000.0, "notes": ""},
        ]
        res = client.post("/api/pdf/save", headers=auth_headers,
                          json={"entries": entries})
        assert res.status_code == 200

        # Verify it appears in manual entries
        manual = client.get("/api/manual", headers=auth_headers).json()
        assert any(e["name"] == "Parker IRA Unique" for e in manual)

    def test_save_empty_entries_returns_ok(self, client, auth_headers):
        res = client.post("/api/pdf/save", headers=auth_headers,
                          json={"entries": []})
        assert res.status_code == 200

    def test_save_invalid_category_sanitized_or_rejected(self, client, auth_headers):
        entries = [{"name": "Bad Category Entry", "category": "not_valid", "value": 100.0}]
        res = client.post("/api/pdf/save", headers=auth_headers,
                          json={"entries": entries})
        # pdf/save does not validate category — manual/post does. PDF save stores as-is.
        assert res.status_code in (200, 400)

    def test_save_prevents_duplicate_on_reimport(self, client, auth_headers):
        """Re-importing the same PDF should replace the old entry, not add a duplicate."""
        entry = {"name": "DupTest Account", "category": "invested", "value": 100000.0}

        # Save once
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry]})
        # Save again with updated value
        entry["value"] = 110000.0
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry]})

        manual = client.get("/api/manual", headers=auth_headers).json()
        matches = [e for e in manual if e["name"] == "DupTest Account"]
        assert len(matches) == 1, "Re-import should replace, not duplicate"
        assert matches[0]["value"] == 110000.0

    def test_save_stores_holdings(self, client, auth_headers):
        """Holdings rows should be saved and returned with the manual entry."""
        entries = [{
            "name": "Holdings Test Account",
            "category": "invested",
            "value": 50000.0,
            "notes": "",
            "holdings": [
                {"name": "Large-Cap Growth", "value": 30000.0, "asset_class": "equities", "pct_assets": 60.0},
                {"name": "Fixed Income", "value": 20000.0, "asset_class": "fixed_income", "pct_assets": 40.0},
            ],
        }]
        res = client.post("/api/pdf/save", headers=auth_headers, json={"entries": entries})
        assert res.status_code == 200

        manual = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in manual if e["name"] == "Holdings Test Account"), None)
        assert entry is not None
        assert len(entry.get("holdings", [])) == 2
        names = {h["name"] for h in entry["holdings"]}
        assert "Large-Cap Growth" in names
        assert "Fixed Income" in names

    def test_save_stores_activity_summary(self, client, auth_headers):
        """Activity summary from PDF should be stored and returned as activity_summary."""
        entries = [{
            "name": "Activity Summary Account",
            "category": "invested",
            "value": 586114.75,
            "activity_summary": {
                "beginning_balance": 598640.01,
                "beginning_date": "1/1/2026",
                "additions_withdrawals": 2800.00,
                "net_change": -15325.26,
                "ending_balance": 586114.75,
                "ending_date": "3/16/2026",
                "twr_pct": -2.54,
            },
        }]
        res = client.post("/api/pdf/save", headers=auth_headers, json={"entries": entries})
        assert res.status_code == 200

        manual = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in manual if e["name"] == "Activity Summary Account"), None)
        assert entry is not None
        summary = entry.get("activity_summary")
        assert summary is not None
        assert summary["beginning_balance"] == 598640.01
        assert summary["twr_pct"] == -2.54

    # ------------------------------------------------------------------
    # Account matching / de-duplication
    # ------------------------------------------------------------------

    def test_tier1_match_by_account_number(self, client, auth_headers):
        """Tier 1: second import with same account_number replaces (not duplicates) the entry."""
        entry = {
            "name": "Tier1 IRA",
            "category": "invested",
            "value": 100000.0,
            "activity_summary": {
                "account_number": "B37999001",
                "institution": "Test Brokerage",
                "account_holder": "John Test",
                "period_end": "2026-01-31",
            },
        }
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry]})

        # Re-import with updated value
        entry["value"] = 105000.0
        entry["activity_summary"]["period_end"] = "2026-02-28"
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry]})

        manual = client.get("/api/manual", headers=auth_headers).json()
        matches = [e for e in manual if "Tier1 IRA" in e["name"] or e.get("account_number") == "B37999001"]
        assert len(matches) == 1, "Tier 1 re-import must replace, not duplicate"
        assert matches[0]["value"] == 105000.0

    def test_tier3_false_positive_prevention(self, client, auth_headers):
        """
        Tier 3 guard: when two accounts share the same institution+holder+category,
        a single save() call must NOT let the second account steal/overwrite the first.
        Root cause of the IRA Rollover / Roth IRA collision at Parker Financial.
        """
        # Two accounts — same institution, same holder, same category, different names/numbers.
        entries = [
            {
                "name": "Parker Roth IRA",
                "category": "invested",
                "value": 50000.0,
                "activity_summary": {
                    "account_number": "TIER3A001",
                    "institution": "Parker Financial",
                    "account_holder": "Heather Test",
                    "period_end": "2026-01-31",
                },
            },
            {
                "name": "Parker IRA Rollover",
                "category": "invested",
                "value": 150000.0,
                "activity_summary": {
                    "account_number": "TIER3B002",
                    "institution": "Parker Financial",
                    "account_holder": "Heather Test",
                    "period_end": "2026-01-31",
                },
            },
        ]
        res = client.post("/api/pdf/save", headers=auth_headers, json={"entries": entries})
        assert res.status_code == 200

        manual = client.get("/api/manual", headers=auth_headers).json()
        roth = [e for e in manual if e["name"] == "Parker Roth IRA"]
        rollover = [e for e in manual if e["name"] == "Parker IRA Rollover"]
        assert len(roth) == 1, "Roth IRA must survive as a separate entry"
        assert len(rollover) == 1, "IRA Rollover must be created as a separate entry"
        assert roth[0]["value"] == 50000.0
        assert rollover[0]["value"] == 150000.0

    def test_is_historical_uses_snapshot_date_not_entered_at(self, client, auth_headers):
        """
        is_historical must be based solely on the snapshot table, NOT entered_at.
        If a user manually restores an entry (entered_at = today) and then imports a
        statement from 2 months ago, that import should NOT be blocked as 'historical'.
        It is historical only if a *newer* snapshot already exists.
        """
        from datetime import date, timedelta

        future_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        past_date = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")

        # First import: future-dated statement — establishes a newer snapshot
        entry_future = {
            "name": "Historical Test Account",
            "category": "invested",
            "value": 120000.0,
            "activity_summary": {
                "account_number": "HIST001",
                "institution": "Test Bank",
                "account_holder": "John Test",
                "period_end": future_date,
            },
        }
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry_future]})

        # Second import: older statement — should be treated as historical (snapshot only)
        entry_past = {
            "name": "Historical Test Account",
            "category": "invested",
            "value": 100000.0,  # older, lower value
            "activity_summary": {
                "account_number": "HIST001",
                "institution": "Test Bank",
                "account_holder": "John Test",
                "period_end": past_date,
            },
        }
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry_past]})

        # Current value should remain the newer (future) value, not be overwritten by old import
        manual = client.get("/api/manual", headers=auth_headers).json()
        matches = [e for e in manual if e.get("account_number") == "HIST001"]
        assert len(matches) == 1
        assert matches[0]["value"] == 120000.0, "Older import must not overwrite current balance"

    def test_force_holdings_on_empty_entry(self, client, auth_headers):
        """
        force_holdings: when an entry was manually restored (no holdings) and a
        historical PDF for that account is imported, holdings MUST be written even
        though the import is historical (older than current snapshot date).
        """
        from datetime import date, timedelta

        future_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        past_date = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")

        # Seed a current snapshot without holdings to simulate a manually-restored entry
        entry_no_holdings = {
            "name": "Force Holdings Account",
            "category": "invested",
            "value": 80000.0,
            "activity_summary": {
                "account_number": "FH001",
                "institution": "Test Financial",
                "account_holder": "Jane Test",
                "period_end": future_date,
            },
            "holdings": [],  # no holdings
        }
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry_no_holdings]})

        # Now import an older statement with holdings — should write holdings (force_holdings)
        entry_with_holdings = {
            "name": "Force Holdings Account",
            "category": "invested",
            "value": 75000.0,
            "activity_summary": {
                "account_number": "FH001",
                "institution": "Test Financial",
                "account_holder": "Jane Test",
                "period_end": past_date,
            },
            "holdings": [
                {"name": "Large-Cap Fund", "value": 50000.0, "asset_class": "equities"},
                {"name": "Bond Fund", "value": 25000.0, "asset_class": "fixed_income"},
            ],
        }
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry_with_holdings]})

        manual = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in manual if e.get("account_number") == "FH001"), None)
        assert entry is not None
        # Value must not change (historical import)
        assert entry["value"] == 80000.0, "Historical import must not overwrite current value"
        # But holdings must be written (force_holdings because entry had 0 holdings)
        assert len(entry.get("holdings", [])) == 2, "Holdings must be written when entry had none"

    def test_account_number_normalization(self, client, auth_headers):
        """
        _normalize_acct strips separators so B37-601959 and B37 601959 and B37601959
        all match the same entry via Tier 1.
        """
        from api.routers.pdf import _normalize_acct

        assert _normalize_acct("B37-601959") == "B37601959"
        assert _normalize_acct("B37 601959") == "B37601959"
        assert _normalize_acct("B37601959") == "B37601959"
        assert _normalize_acct("xxxx1959") == "XXXX1959"
        assert _normalize_acct(None) is None
        assert _normalize_acct("") is None
        assert _normalize_acct("b37-601959") == "B37601959"  # lowercase normalized to upper

    def test_tier2_match_by_last4(self, client, auth_headers):
        """
        Tier 2: same institution+holder, account_number format changes between imports
        (e.g. full number in monthly statement vs masked xxxx1234 in summary PDF).
        Should match via last-4 and self-heal account_number on the existing entry.
        """
        # First import with full account number
        entry_full = {
            "name": "Tier2 Test Account",
            "category": "invested",
            "value": 60000.0,
            "activity_summary": {
                "account_number": "ABC7890",
                "institution": "Test Custodian",
                "account_holder": "Jane Test",
                "period_end": "2026-01-31",
            },
        }
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry_full]})

        # Second import with masked number — last 4 match ("7890"), but different prefix
        entry_masked = {
            "name": "Tier2 Test Account Different Name",  # name changed to verify Tier 2, not Tier 4
            "category": "invested",
            "value": 65000.0,
            "activity_summary": {
                "account_number": "XXXX7890",
                "institution": "Test Custodian",
                "account_holder": "Jane Test",
                "period_end": "2026-02-28",
            },
        }
        client.post("/api/pdf/save", headers=auth_headers, json={"entries": [entry_masked]})

        manual = client.get("/api/manual", headers=auth_headers).json()
        # Should have only ONE entry (Tier 2 matched and replaced)
        matches = [e for e in manual if
                   e.get("account_number") in ("ABC7890", "XXXX7890") or
                   "Tier2 Test" in e["name"]]
        assert len(matches) == 1, "Tier 2 match must replace, not duplicate"
        assert matches[0]["value"] == 65000.0

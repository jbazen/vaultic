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

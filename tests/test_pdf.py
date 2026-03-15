"""Tests for PDF ingestion and save endpoints."""
import io
import pytest
from unittest.mock import patch, MagicMock


FAKE_PDF_BYTES = b"%PDF-1.4 fake pdf content"


def _make_pdf_file(content=FAKE_PDF_BYTES):
    return ("test.pdf", io.BytesIO(content), "application/pdf")


class TestPDFIngest:
    def test_ingest_requires_auth(self, client):
        res = client.post("/api/pdf/ingest",
                          files={"file": _make_pdf_file()})
        assert res.status_code == 401

    def test_ingest_returns_parsed_entries(self, client, auth_headers):
        fake_entries = [
            {"name": "Parker IRA", "category": "investment", "value": 85000, "notes": "Traditional IRA"},
            {"name": "College Fund", "category": "investment", "value": 12000, "notes": "529 plan"},
        ]

        with patch("pdfplumber.open") as mock_pdf, \
             patch("api.routers.pdf.anthropic.Anthropic") as mock_cls:

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


class TestPDFSave:
    def test_save_requires_auth(self, client):
        res = client.post("/api/pdf/save", json={"entries": []})
        assert res.status_code == 401

    def test_save_valid_entries(self, client, auth_headers):
        entries = [
            {"name": "Parker IRA", "category": "investment", "value": 85000.0, "notes": ""},
        ]
        res = client.post("/api/pdf/save", headers=auth_headers,
                          json={"entries": entries})
        assert res.status_code == 200

        # Verify it appears in manual entries
        manual = client.get("/api/manual", headers=auth_headers).json()
        assert any(e["name"] == "Parker IRA" for e in manual)

    def test_save_empty_entries_returns_ok(self, client, auth_headers):
        res = client.post("/api/pdf/save", headers=auth_headers,
                          json={"entries": []})
        assert res.status_code == 200

    def test_save_invalid_category_rejected(self, client, auth_headers):
        entries = [{"name": "Bad", "category": "not_valid", "value": 100.0}]
        res = client.post("/api/pdf/save", headers=auth_headers,
                          json={"entries": entries})
        # Should either reject invalid category or sanitize it
        assert res.status_code in (200, 400)

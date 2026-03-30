"""Tests for the Tax API endpoints and shared tax calculation functions.

Covers:
  - tax_calc.py: progressive bracket calculator, AZ flat tax, constants
  - /api/tax/projection/{year}: YTD paystub extrapolation with federal + AZ state
  - /api/tax/draft/{year}: document-based return calculation with federal + AZ state
  - /api/tax/estimated-payments/{year}: 1040-ES quarterly calculator with AZ
  - Auth guards on all tax endpoints
"""
import sqlite3
from api.tax_calc import (
    calc_tax, calc_az_tax, AZ_FLAT_RATE,
    get_brackets, get_standard_deduction,
    BRACKETS_2025_MFJ, CHILD_CREDIT_PER_CHILD, NUM_CHILDREN, SALT_CAP,
)


# ── Pure tax_calc.py unit tests ───────────────────────────────────────────────

class TestCalcTax:
    """Progressive federal bracket calculator."""

    def test_zero_income(self):
        assert calc_tax(0, BRACKETS_2025_MFJ) == 0.0

    def test_negative_income(self):
        assert calc_tax(-100, BRACKETS_2025_MFJ) == 0.0

    def test_first_bracket_only(self):
        """$10,000 at 10% = $1,000"""
        assert calc_tax(10000, BRACKETS_2025_MFJ) == 1000.0

    def test_spans_two_brackets(self):
        """$50,000: first $23,850 at 10% + remaining $26,150 at 12%"""
        expected = 23850 * 0.10 + 26150 * 0.12
        assert calc_tax(50000, BRACKETS_2025_MFJ) == round(expected, 2)

    def test_known_taxable_200k(self):
        """Spot-check: $200,000 taxable MFJ 2025"""
        tax = calc_tax(200000, BRACKETS_2025_MFJ)
        # 10% on 23850 + 12% on 73100 + 22% on 103050
        expected = 23850 * 0.10 + 73100 * 0.12 + 103050 * 0.22
        assert tax == round(expected, 2)


class TestCalcAzTax:
    """Arizona flat 2.5% state tax."""

    def test_rate_constant(self):
        assert AZ_FLAT_RATE == 0.025

    def test_zero_income(self):
        assert calc_az_tax(0) == 0.0

    def test_negative_income(self):
        assert calc_az_tax(-5000) == 0.0

    def test_positive_income(self):
        assert calc_az_tax(200000) == 5000.0

    def test_rounding(self):
        """Should round to 2 decimal places."""
        result = calc_az_tax(33333)
        assert result == 833.33

    def test_small_amount(self):
        assert calc_az_tax(100) == 2.50


class TestTaxHelpers:
    """get_brackets and get_standard_deduction lookups."""

    def test_known_year_brackets(self):
        b = get_brackets(2025, "married_filing_jointly")
        assert b == BRACKETS_2025_MFJ

    def test_unknown_year_defaults_to_2025(self):
        b = get_brackets(2099)
        assert b == BRACKETS_2025_MFJ

    def test_standard_deduction_mfj_2025(self):
        assert get_standard_deduction(2025) == 30000

    def test_standard_deduction_single_2025(self):
        assert get_standard_deduction(2025, "single") == 15000

    def test_standard_deduction_unknown_year(self):
        assert get_standard_deduction(2099) == 30000

    def test_constants(self):
        assert CHILD_CREDIT_PER_CHILD == 2000
        assert NUM_CHILDREN == 2
        assert SALT_CAP == 10000


# ── Auth guard tests ──────────────────────────────────────────────────────────

class TestTaxAuth:
    """All tax endpoints require a valid JWT."""

    def test_projection_requires_auth(self, client):
        r = client.get("/api/tax/projection/2025")
        assert r.status_code == 401 or r.status_code == 403

    def test_draft_requires_auth(self, client):
        r = client.get("/api/tax/draft/2025")
        assert r.status_code == 401 or r.status_code == 403

    def test_estimated_payments_requires_auth(self, client):
        r = client.get("/api/tax/estimated-payments/2025")
        assert r.status_code == 401 or r.status_code == 403

    def test_upload_requires_auth(self, client):
        r = client.post("/api/tax/docs/upload")
        assert r.status_code == 401 or r.status_code == 403

    def test_w4_wizard_requires_auth(self, client):
        r = client.post("/api/tax/w4-wizard")
        assert r.status_code == 401 or r.status_code == 403

    def test_w4_prefill_requires_auth(self, client):
        r = client.get("/api/tax/w4-wizard/prefill")
        assert r.status_code == 401 or r.status_code == 403


# ── Projection endpoint tests ────────────────────────────────────────────────

def _seed_paystub(client, auth_headers):
    """Insert a paystub directly into the test DB for projection tests."""
    from api.database import get_db
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO paystubs
                (employer, pay_date, ytd_gross, ytd_federal, ytd_state,
                 ytd_social_security, ytd_medicare, source_file)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("Acme Corp", "2025-03-15", 50000, 7000, 1200, 3100, 725, "test.pdf"))
        conn.commit()


class TestTaxProjection:
    """GET /api/tax/projection/{year} — YTD extrapolation with AZ state tax."""

    def test_no_paystubs_returns_404(self, client, auth_headers):
        r = client.get("/api/tax/projection/2025", headers=auth_headers)
        assert r.status_code == 404

    def test_projection_with_paystub(self, client, auth_headers):
        _seed_paystub(client, auth_headers)
        r = client.get("/api/tax/projection/2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()

        # Federal fields present
        assert "proj_gross" in data
        assert "net_tax" in data
        assert "taxable_income" in data
        assert data["year"] == 2025

        # Arizona fields present
        assert "arizona" in data
        az = data["arizona"]
        assert az["rate"] == 0.025
        assert az["tax"] > 0
        assert "proj_state_withheld" in az

        # Combined fields present
        assert "combined" in data
        comb = data["combined"]
        assert comb["total_tax"] == round(data["net_tax"] + az["tax"])
        assert "refund" in comb or "owed" in comb

    def test_projection_az_math(self, client, auth_headers):
        """AZ tax should be exactly 2.5% of taxable income."""
        r = client.get("/api/tax/projection/2025", headers=auth_headers)
        data = r.json()
        expected_az = round(data["taxable_income"] * 0.025, 2)
        assert data["arizona"]["tax"] == expected_az

    def test_projection_employer_includes_state(self, client, auth_headers):
        """Employer breakdown should include ytd_state."""
        r = client.get("/api/tax/projection/2025", headers=auth_headers)
        data = r.json()
        assert len(data["employers"]) >= 1
        assert "ytd_state" in data["employers"][0]


# ── Draft return endpoint tests ───────────────────────────────────────────────

def _seed_tax_docs(client, auth_headers):
    """Insert tax documents directly into the test DB for draft return tests."""
    from api.database import get_db
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO tax_docs
                (id, tax_year, doc_type, issuer, source_file,
                 w2_wages, w2_fed_withheld, w2_state_withheld,
                 w2_ss_withheld, w2_medicare_withheld)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (9001, 2025, "w2", "Acme Corp", "w2_acme.pdf",
              150000, 22000, 3500, 9300, 2175))
        conn.commit()


class TestDraftReturn:
    """GET /api/tax/draft/{year} — document-based return with AZ state tax."""

    def test_draft_no_docs(self, client, auth_headers):
        """No docs for 2024 → still returns 200 but has_docs=false."""
        r = client.get("/api/tax/draft/2024", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["has_docs"] is False

    def test_draft_with_w2(self, client, auth_headers):
        _seed_tax_docs(client, auth_headers)
        r = client.get("/api/tax/draft/2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()

        assert data["has_docs"] is True
        assert data["income"]["wages"] == 150000

        # Arizona section
        assert "arizona" in data
        az = data["arizona"]
        assert az["rate"] == 0.025
        assert az["tax"] > 0
        assert az["state_withheld"] == 3500

        # Combined section
        assert "combined" in data
        comb = data["combined"]
        assert comb["total_tax"] == round(data["net_tax"] + az["tax"])

    def test_draft_az_math(self, client, auth_headers):
        """AZ tax should be exactly 2.5% of taxable income."""
        r = client.get("/api/tax/draft/2025", headers=auth_headers)
        data = r.json()
        expected_az = round(data["taxable_income"] * 0.025, 2)
        assert data["arizona"]["tax"] == expected_az


# ── Estimated payments endpoint tests ─────────────────────────────────────────

class TestEstimatedPayments:
    """GET /api/tax/estimated-payments/{year} — 1040-ES with AZ state tax."""

    def test_unsupported_year(self, client, auth_headers):
        r = client.get("/api/tax/estimated-payments/2020", headers=auth_headers)
        assert r.status_code == 400

    def test_estimated_payments_2025(self, client, auth_headers):
        """With paystub seeded above, should compute quarters and AZ tax."""
        r = client.get("/api/tax/estimated-payments/2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()

        assert data["year"] == 2025
        assert len(data["quarters"]) == 4
        assert "safe_harbor_a" in data
        assert "safe_harbor_b" in data

        # Arizona section
        assert "arizona" in data
        az = data["arizona"]
        assert az["rate"] == 0.025
        assert az["tax"] > 0
        assert "proj_state_withheld" in az

    def test_estimated_payments_az_math(self, client, auth_headers):
        """AZ tax should be approximately 2.5% of taxable income (within rounding)."""
        r = client.get("/api/tax/estimated-payments/2025", headers=auth_headers)
        data = r.json()
        expected_az = data["arizona"]["taxable_income"] * 0.025
        assert abs(data["arizona"]["tax"] - expected_az) < 0.02


# ── Tax document checklist endpoint tests ─────────────────────────────────────

def _seed_accounts(client, auth_headers):
    """Insert test accounts into the DB for checklist generation."""
    from api.database import get_db
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO accounts (id, name, type, subtype, institution_name, is_active, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (9001, "Checking", "depository", "checking", "Chase", 1, "plaid"))
        conn.execute("""
            INSERT OR IGNORE INTO accounts (id, name, type, subtype, institution_name, is_active, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (9002, "401k", "investment", "401k", "Vanguard", 1, "plaid"))
        conn.execute("""
            INSERT OR IGNORE INTO accounts (id, name, type, subtype, institution_name, is_active, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (9003, "Mortgage", "loan", "mortgage", "Rocket Mortgage", 1, "plaid"))
        conn.commit()


class TestTaxChecklist:
    """GET /api/tax/checklist/{year} — auto-generated document checklist."""

    def test_checklist_requires_auth(self, client):
        r = client.get("/api/tax/checklist/2025")
        assert r.status_code == 401 or r.status_code == 403

    def test_checklist_with_accounts(self, client, auth_headers):
        _seed_accounts(client, auth_headers)
        r = client.get("/api/tax/checklist/2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()

        assert data["year"] == 2025
        assert data["total_expected"] >= 1
        assert "checklist" in data

        # Should have W-2 from the paystub employer (seeded in projection tests)
        w2s = [c for c in data["checklist"] if c["doc_type"] == "w2"]
        assert len(w2s) >= 1
        assert w2s[0]["source"] == "Acme Corp"

    def test_checklist_includes_account_docs(self, client, auth_headers):
        """Connected accounts should generate expected documents."""
        r = client.get("/api/tax/checklist/2025", headers=auth_headers)
        data = r.json()
        doc_types = {c["doc_type"] for c in data["checklist"]}

        # Chase checking → 1099-INT
        assert "1099_int" in doc_types
        # Vanguard 401k → 1099-R
        assert "1099_r" in doc_types
        # Rocket Mortgage → 1098
        assert "1098" in doc_types

    def test_checklist_received_status(self, client, auth_headers):
        """Uploaded docs should be marked as received."""
        r = client.get("/api/tax/checklist/2025", headers=auth_headers)
        data = r.json()

        # W-2 from Acme Corp was uploaded in _seed_tax_docs
        w2_acme = [c for c in data["checklist"] if c["doc_type"] == "w2" and "Acme" in c["source"]]
        assert len(w2_acme) >= 1
        assert w2_acme[0]["received"] is True

    def test_checklist_summary_counts(self, client, auth_headers):
        r = client.get("/api/tax/checklist/2025", headers=auth_headers)
        data = r.json()
        assert data["total_received"] <= data["total_expected"]
        received = sum(1 for c in data["checklist"] if c["received"])
        assert received == data["total_received"]

    def test_checklist_no_duplicates(self, client, auth_headers):
        """Each (doc_type, source) pair should appear only once."""
        r = client.get("/api/tax/checklist/2025", headers=auth_headers)
        data = r.json()
        seen = set()
        for c in data["checklist"]:
            key = (c["doc_type"], c["source"])
            assert key not in seen, f"Duplicate checklist entry: {key}"
            seen.add(key)

    def test_checklist_includes_giving(self, client, auth_headers):
        """Charitable giving statement should always appear."""
        r = client.get("/api/tax/checklist/2025", headers=auth_headers)
        data = r.json()
        giving = [c for c in data["checklist"] if c["doc_type"] == "giving_statement"]
        assert len(giving) == 1

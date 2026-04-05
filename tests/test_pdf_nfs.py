"""
Comprehensive unit tests for the deterministic NFS / Commonwealth Financial
Network statement parser (api/routers/pdf_nfs.py).

Groups:
    1  — Detection (3 tests)
    2  — Dollar-parsing helpers (6 tests)
    3  — Header parsing (6 tests)
    4  — Holdings parsing — inline text fixtures (10 tests)
    5  — Full integration tests using real PDFs (6 tests)
"""
import pytest
import os
import sys

# Ensure project root is on the path when running directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.routers.pdf_nfs import (
    is_nfs_statement,
    parse_nfs_statement,
    _parse_dollar,
    _parse_all_dollars,
    _parse_header,
    _parse_overview,
    _parse_holdings,
    _is_masked_account_number,
)

# ---------------------------------------------------------------------------
# Path to real PDFs (integration tests only)
# ---------------------------------------------------------------------------
PARKER_DIR = r"C:\Users\jbaze\Downloads\parker"

def _pdf_path(filename):
    return os.path.join(PARKER_DIR, filename)


def _load_pdf(filename):
    """Return list of page-text strings from a real PDF."""
    import pdfplumber
    path = _pdf_path(filename)
    with pdfplumber.open(path) as pdf:
        return [p.extract_text() for p in pdf.pages[:30]]


# ===========================================================================
# Group 1 — Detection
# ===========================================================================

class TestDetection:

    def test_detects_nfs_statement(self):
        text = (
            "COMMONWEALTH FINANCIAL NETWORK\n"
            "Account carried with National Financial Services LLC, Member NYSE, SIPC"
        )
        assert is_nfs_statement(text) is True

    def test_rejects_non_nfs_statement(self):
        # Only one marker — should be False
        text = "COMMONWEALTH FINANCIAL NETWORK\nSome other custodian"
        assert is_nfs_statement(text) is False

    def test_rejects_empty_text(self):
        assert is_nfs_statement("") is False


# ===========================================================================
# Group 2 — Dollar-parsing helpers
# ===========================================================================

class TestDollarParsing:

    def test_parse_dollar_positive(self):
        assert _parse_dollar("$1,234.56") == pytest.approx(1234.56)

    def test_parse_dollar_negative_parens(self):
        assert _parse_dollar("($915.20)") == pytest.approx(-915.20)

    def test_parse_dollar_with_commas(self):
        assert _parse_dollar("$1,500.61") == pytest.approx(1500.61)

    def test_parse_all_dollars_three_amounts(self):
        line = "$145.97 $10,496.15 $2,607.69"
        result = _parse_all_dollars(line)
        assert len(result) == 3
        assert result[0] == pytest.approx(145.97)
        assert result[1] == pytest.approx(10496.15)
        assert result[2] == pytest.approx(2607.69)

    def test_parse_all_dollars_with_negative(self):
        line = "$12,864.96 ($8.69)"
        result = _parse_all_dollars(line)
        assert len(result) == 2
        assert result[0] == pytest.approx(12864.96)
        assert result[1] == pytest.approx(-8.69)

    def test_parse_dollar_none_when_absent(self):
        assert _parse_dollar("no money here") is None


# ===========================================================================
# Group 3 — Header parsing
# ===========================================================================

HEADER_ROTH = """\
ENV# CEBSPHGTBBCDPVC_BBBBB
COMMONWEALTH FINANCIAL NETWORK
275 WYMAN ST STE 400
WALTHAM MA 02451-1200
STATEMENT FOR THE PERIOD DECEMBER 1, 2025 TO DECEMBER 31, 2025
HEATHER A BAZEN - Premiere Select Roth IRA
Account Number: B37-601959
BEGINNING VALUE OF YOUR PORTFOLIO $148,008.33
FINANCIAL ADVISOR For questions about your accounts: TOTAL VALUE OF YOUR PORTFOLIO $148,170.79
"""

HEADER_JOINT = """\
ENV# CEBSPHGTBBCKPQR_BBBBB
COMMONWEALTH FINANCIAL NETWORK
STATEMENT FOR THE PERIOD DECEMBER 1, 2025 TO DECEMBER 31, 2025
HEATHER A BAZEN & JASON H BAZEN - Joint WROS TOD
Account Number: B37-705429
BEGINNING VALUE OF YOUR PORTFOLIO $50,489.22
FINANCIAL ADVISOR For questions about your accounts: TOTAL VALUE OF YOUR PORTFOLIO $51,431.44
"""


class TestHeaderParsing:

    def test_parse_header_roth_ira(self):
        h = _parse_header(HEADER_ROTH)
        assert h["account_name"] == "HEATHER A BAZEN - Premiere Select Roth IRA"

    def test_parse_header_joint_wros(self):
        h = _parse_header(HEADER_JOINT)
        assert h["account_name"] == "HEATHER A BAZEN & JASON H BAZEN - Joint WROS TOD"

    def test_parse_header_account_number(self):
        h = _parse_header(HEADER_JOINT)
        assert h["account_number"] == "B37-705429"

    def test_parse_header_period_dates(self):
        h = _parse_header(HEADER_ROTH)
        assert h["period_start"] == "2025-12-01"
        assert h["period_end"] == "2025-12-31"

    def test_parse_header_beginning_value(self):
        h = _parse_header(HEADER_ROTH)
        assert h["beginning_value"] == pytest.approx(148008.33)

    def test_parse_header_total_value(self):
        h = _parse_header(HEADER_JOINT)
        assert h["total_value"] == pytest.approx(51431.44)


# ===========================================================================
# Group 4 — Holdings parsing using inline text snippets
# ===========================================================================

def _make_holdings_text(section_header, body):
    """Wrap body in a Holdings page with the given section header."""
    return f"\n\nHoldings\n\n{section_header}\n\n{body}\n"


CASH_SECTION = _make_holdings_text(
    "CASH AND CASH EQUIVALENTS",
    """\
BANK DEPOSIT SWEEP PROGRAM QPRMQ 1,500.61 $1.00 $1,500.61
Interest Rate 0.05% CASH
Total Cash and Cash Equivalents $1,500.61
""",
)

MF_WITH_INCOME = _make_holdings_text(
    "HOLDINGS > MUTUAL FUNDS",
    """\
COLUMBIA DIVIDEND INCOME FUND CL I GSFTX 625.736 $36.26 $22,689.19 $380.02 $18,281.14 $4,408.05
Estimated Yield 1.67% CASH
Dividend Option Reinvest
Capital Gain Option Reinvest
Average Unit Cost $29.22
Total Mutual Funds $22,689.19 $380.02 $18,281.14 $4,408.05
""",
)

MF_NO_INCOME = _make_holdings_text(
    "HOLDINGS > MUTUAL FUNDS",
    """\
ARTISAN DEVELOPING WORLD FD ADVISOR CL APDYX 277.765 $23.21 $6,446.93 $4,407.99 $2,038.94
Dividend Option Reinvest CASH
Capital Gain Option Reinvest
Average Unit Cost $15.87
Total Mutual Funds $6,446.93 $4,407.99 $2,038.94
""",
)

MF_NEGATIVE_GAIN = _make_holdings_text(
    "HOLDINGS > MUTUAL FUNDS",
    """\
VICTORY SYCAMORE ESTABLISHED VALUE Y VEVYX 285.568 $45.02 $12,856.27 $125.24 $12,864.96 ($8.69)
Estimated Yield 0.97% CASH
Dividend Option Reinvest
Capital Gain Option Reinvest
Average Unit Cost $45.05
Total Mutual Funds $12,856.27 $125.24 $12,864.96 ($8.69)
""",
)

ETP_SECTION = _make_holdings_text(
    "HOLDINGS > EXCHANGE TRADED PRODUCTS",
    """\
FIDELITY DIVIDEND ETF FOR RISING RATES FDRR 197.078 $61.02 $12,025.70 $265.46 $9,199.59 $2,826.11
Estimated Yield 2.20% CASH
Dividend Option Reinvest
Capital Gain Option Reinvest
Average Unit Cost $46.68
Total Exchange Traded Products $12,025.70 $265.46 $9,199.59 $2,826.11
""",
)

# Simulates continued section on next page
CONTINUED_SECTION = (
    _make_holdings_text(
        "HOLDINGS > MUTUAL FUNDS",
        """\
FIDELITY 500 INDEX FUND FXAIX 55.123 $237.72 $13,103.84 $145.97 $10,496.15 $2,607.69
Estimated Yield 1.11% CASH
Dividend Option Reinvest
Capital Gain Option Reinvest
Average Unit Cost $190.41
Total Mutual Funds $13,103.84 $145.97 $10,496.15 $2,607.69
""",
    )
    + _make_holdings_text(
        "HOLDINGS > MUTUAL FUNDS continued",
        """\
COLUMBIA DIVIDEND INCOME FUND CL I GSFTX 625.736 $36.26 $22,689.19 $380.02 $18,281.14 $4,408.05
Estimated Yield 1.67% CASH
Dividend Option Reinvest
Capital Gain Option Reinvest
Average Unit Cost $29.22
Total Mutual Funds $22,689.19 $380.02 $18,281.14 $4,408.05
""",
    )
)

HOLDING_COMMA_QTY = _make_holdings_text(
    "CASH AND CASH EQUIVALENTS",
    """\
BANK DEPOSIT SWEEP PROGRAM QPRMQ 1,500.61 $1.00 $1,500.61
Interest Rate 0.05% CASH
Total Cash and Cash Equivalents $1,500.61
""",
)

TOTAL_LINE_SNIPPET = _make_holdings_text(
    "HOLDINGS > MUTUAL FUNDS",
    """\
FIDELITY 500 INDEX FUND FXAIX 55.123 $237.72 $13,103.84 $145.97 $10,496.15 $2,607.69
Estimated Yield 1.11% CASH
Dividend Option Reinvest
Capital Gain Option Reinvest
Average Unit Cost $190.41
Total Mutual Funds $13,103.84 $145.97 $10,496.15 $2,607.69
""",
)


class TestHoldingsParsing:

    def test_parse_cash_sweep_holding(self):
        holdings = _parse_holdings(CASH_SECTION)
        assert len(holdings) == 1
        h = holdings[0]
        assert h["ticker"] == "QPRMQ"
        assert h["shares"] == pytest.approx(1500.61)
        assert h["price"] == pytest.approx(1.00)
        assert h["value"] == pytest.approx(1500.61)
        assert h["asset_class"] == "cash"
        assert h["cost"] is None
        assert h["gain_loss_dollars"] is None

    def test_parse_mutual_fund_with_income(self):
        holdings = _parse_holdings(MF_WITH_INCOME)
        assert len(holdings) == 1
        h = holdings[0]
        assert h["ticker"] == "GSFTX"
        assert h["shares"] == pytest.approx(625.736)
        assert h["value"] == pytest.approx(22689.19)
        assert h["estimated_annual_income"] == pytest.approx(380.02)
        assert h["cost"] == pytest.approx(18281.14)
        assert h["gain_loss_dollars"] == pytest.approx(4408.05)
        assert h["asset_class"] == "equities"

    def test_parse_mutual_fund_no_income(self):
        holdings = _parse_holdings(MF_NO_INCOME)
        assert len(holdings) == 1
        h = holdings[0]
        assert h["ticker"] == "APDYX"
        assert h["estimated_annual_income"] is None
        assert h["cost"] == pytest.approx(4407.99)
        assert h["gain_loss_dollars"] == pytest.approx(2038.94)

    def test_parse_negative_gain(self):
        holdings = _parse_holdings(MF_NEGATIVE_GAIN)
        assert len(holdings) == 1
        h = holdings[0]
        assert h["ticker"] == "VEVYX"
        assert h["gain_loss_dollars"] == pytest.approx(-8.69)

    def test_parse_avg_unit_cost_captured(self):
        holdings = _parse_holdings(MF_WITH_INCOME)
        h = holdings[0]
        assert h["avg_unit_cost"] == pytest.approx(29.22)

    def test_parse_estimated_yield_captured(self):
        holdings = _parse_holdings(MF_WITH_INCOME)
        h = holdings[0]
        assert h["estimated_yield_pct"] == pytest.approx(1.67)

    def test_parse_etp_section(self):
        holdings = _parse_holdings(ETP_SECTION)
        assert len(holdings) == 1
        h = holdings[0]
        assert h["ticker"] == "FDRR"
        assert h["asset_class"] == "equities"
        assert h["shares"] == pytest.approx(197.078)
        assert h["cost"] == pytest.approx(9199.59)
        assert h["gain_loss_dollars"] == pytest.approx(2826.11)
        assert h["estimated_yield_pct"] == pytest.approx(2.20)

    def test_parse_continued_section(self):
        holdings = _parse_holdings(CONTINUED_SECTION)
        tickers = [h["ticker"] for h in holdings]
        assert "FXAIX" in tickers
        assert "GSFTX" in tickers
        assert len(holdings) == 2

    def test_holding_with_comma_in_quantity(self):
        holdings = _parse_holdings(HOLDING_COMMA_QTY)
        assert len(holdings) == 1
        assert holdings[0]["shares"] == pytest.approx(1500.61)

    def test_total_line_not_parsed_as_holding(self):
        holdings = _parse_holdings(TOTAL_LINE_SNIPPET)
        # Only FXAIX should be returned — "Total Mutual Funds" is not a holding
        assert len(holdings) == 1
        assert holdings[0]["ticker"] == "FXAIX"
        names = [h["name"] for h in holdings]
        assert not any(n.startswith("Total") for n in names)


# ===========================================================================
# Group 5 — Full integration tests using real PDFs
# ===========================================================================

@pytest.mark.skipif(
    not os.path.isdir(PARKER_DIR),
    reason="Parker Financial PDFs not available on this machine"
)
class TestFullParseIntegration:

    def test_full_parse_joint_b37_705429(self):
        """NFS Statement (14).pdf — Joint WROS TOD"""
        pages = _load_pdf("NFS Statement (14).pdf")
        result = parse_nfs_statement(pages)
        assert len(result) == 1

        entry = result[0]
        assert entry["value"] == pytest.approx(51431.44)
        assert entry["category"] == "invested"

        act = entry["activity_summary"]
        assert act["account_number"] == "B37-705429"
        assert act["period_end"] == "2025-12-31"

        holdings = entry["holdings"]
        assert len(holdings) == 5  # cash + 1 mutual fund + 3 ETPs

        tickers = {h["ticker"]: h for h in holdings}

        # FXAIX mutual fund
        assert "FXAIX" in tickers
        fxaix = tickers["FXAIX"]
        assert fxaix["shares"] == pytest.approx(55.123)
        assert fxaix["value"] == pytest.approx(13103.84)

        # FMAG ETP — gain but no annual income
        assert "FMAG" in tickers
        fmag = tickers["FMAG"]
        assert fmag["gain_loss_dollars"] == pytest.approx(2223.58)

        # No holding whose name starts with "Total"
        for h in holdings:
            assert not h["name"].startswith("Total"), f"Total line leaked into holdings: {h['name']}"

    def test_full_parse_roth_ira_b37_601959(self):
        """NFS Statement (10).pdf — Heather Roth IRA"""
        pages = _load_pdf("NFS Statement (10).pdf")
        result = parse_nfs_statement(pages)
        entry = result[0]

        assert entry["value"] == pytest.approx(148170.79)

        act = entry["activity_summary"]
        assert act["account_number"] == "B37-601959"

        tickers = {h["ticker"]: h for h in entry["holdings"]}

        # Negative gain/loss
        assert "VEVYX" in tickers
        assert tickers["VEVYX"]["gain_loss_dollars"] == pytest.approx(-8.69)

        # APDYX shares
        assert "APDYX" in tickers
        assert tickers["APDYX"]["shares"] == pytest.approx(277.765)

    def test_full_parse_rollover_ira_b37_653447(self):
        """NFS Statement (13).pdf — Heather Rollover IRA"""
        pages = _load_pdf("NFS Statement (13).pdf")
        result = parse_nfs_statement(pages)
        entry = result[0]

        assert entry["value"] == pytest.approx(212410.48)
        assert entry["activity_summary"]["account_number"] == "B37-653447"
        assert entry["category"] == "invested"

    def test_full_parse_jason_roth_ira_b37_601960(self):
        """NFS Statement (11).pdf — Jason Roth IRA"""
        pages = _load_pdf("NFS Statement (11).pdf")
        result = parse_nfs_statement(pages)
        entry = result[0]

        assert entry["value"] == pytest.approx(120812.32)
        assert entry["activity_summary"]["account_number"] == "B37-601960"

    def test_full_parse_joint_b37_601962(self):
        """NFS Statement (12).pdf — Joint WROS TOD (second joint account)"""
        pages = _load_pdf("NFS Statement (12).pdf")
        result = parse_nfs_statement(pages)
        entry = result[0]

        assert entry["value"] == pytest.approx(65814.97)
        assert entry["activity_summary"]["account_number"] == "B37-601962"

        tickers = {h["ticker"]: h for h in entry["holdings"]}
        assert "FMAG" in tickers
        assert tickers["FMAG"]["shares"] == pytest.approx(375.0)

    def test_activity_summary_populated(self):
        """NFS Statement (14).pdf — verify activity_summary fields."""
        pages = _load_pdf("NFS Statement (14).pdf")
        result = parse_nfs_statement(pages)
        act = result[0]["activity_summary"]

        assert act["institution"] == "Parker Financial / NFS"
        assert act["beginning_balance"] == pytest.approx(50489.22)
        assert act["ending_balance"] == pytest.approx(51431.44)
        assert act["period_start"] == "2025-12-01"
        assert act["period_end"] == "2025-12-31"
        assert act["additions_withdrawals"] == pytest.approx(1000.00)
        assert act["net_change"] == pytest.approx(-195.13)
        assert act["ytd_beginning_balance"] == pytest.approx(34361.09)
        assert act["period_income"] == pytest.approx(137.35)


# ---------------------------------------------------------------------------
# Group 6 — Masked account number rejection (Step 13)
# ---------------------------------------------------------------------------

class TestMaskedAccountNumberRejection:
    """The NFS parser must never output a masked account_number (e.g. 'XXXX5429').
    Masked numbers break correlation because the canonical key is the full number.
    """

    def test_is_masked_detects_pure_x_prefix(self):
        assert _is_masked_account_number("XXXX5429") is True

    def test_is_masked_detects_embedded_x_run(self):
        assert _is_masked_account_number("B37-XXXX5429") is True

    def test_is_masked_detects_lowercase(self):
        assert _is_masked_account_number("xxxx5429") is True

    def test_is_masked_allows_full_number(self):
        assert _is_masked_account_number("B37-705429") is False
        assert _is_masked_account_number("B37705429") is False

    def test_is_masked_allows_single_or_few_x(self):
        # Three X's is below the threshold — unlikely to be a mask and could
        # appear in a real identifier. Threshold is 4+ X's.
        assert _is_masked_account_number("ABX123") is False
        assert _is_masked_account_number("XXX1234") is False

    def test_is_masked_handles_none_and_empty(self):
        assert _is_masked_account_number(None) is False
        assert _is_masked_account_number("") is False

    def test_parse_header_skips_masked_account_number(self):
        """When the Account Number line is masked, parser must not accept it."""
        text_with_mask = (
            "STATEMENT FOR THE PERIOD DECEMBER 1, 2025 TO DECEMBER 31, 2025\n"
            "Account Number: XXXX5429\n"
            "HEATHER A BAZEN - Premiere Select Roth IRA\n"
        )
        h = _parse_header(text_with_mask)
        assert h["account_number"] is None, "masked number must not be stored"

    def test_parse_header_prefers_full_over_masked(self):
        """If the masked number appears first in the text but a full number
        appears later, the parser should end up with the full number.
        Current behavior stops at the first match, so masked-first means
        parser skips it and then picks up the later full number."""
        text = (
            "Summary page: Account Number: XXXX5429\n"
            "Details: Account Number: B37-705429\n"
            "HEATHER A BAZEN - Premiere Select Roth IRA\n"
        )
        h = _parse_header(text)
        assert h["account_number"] == "B37-705429"

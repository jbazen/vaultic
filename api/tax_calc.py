"""Shared tax calculation constants and functions.

Used by api/routers/tax.py, api/sage_tools.py, and api/sage.py to avoid
duplicating bracket tables, standard deductions, the progressive federal
tax algorithm, and the Arizona flat-rate state tax computation.
"""

# 2025 MFJ tax brackets (IRS Rev. Proc. 2024-40)
BRACKETS_2025_MFJ = [
    (23850,        0.10),
    (96950,        0.12),
    (206700,       0.22),
    (394600,       0.24),
    (501050,       0.32),
    (751600,       0.35),
    (float("inf"), 0.37),
]

BRACKETS_2024_MFJ = [
    (23200,        0.10),
    (94300,        0.12),
    (201050,       0.22),
    (383900,       0.24),
    (487450,       0.32),
    (731200,       0.35),
    (float("inf"), 0.37),
]

BRACKETS_BY_YEAR_AND_STATUS = {
    2025: {
        "married_filing_jointly": BRACKETS_2025_MFJ,
        "single": [
            (11925, 0.10), (48475, 0.12), (103350, 0.22),
            (197300, 0.24), (250525, 0.32), (626350, 0.35), (float("inf"), 0.37),
        ],
        "head_of_household": [
            (17000, 0.10), (64850, 0.12), (103350, 0.22),
            (197300, 0.24), (250500, 0.32), (626350, 0.35), (float("inf"), 0.37),
        ],
    },
    2024: {
        "married_filing_jointly": BRACKETS_2024_MFJ,
        "single": [
            (11600, 0.10), (47150, 0.12), (100525, 0.22),
            (191950, 0.24), (243725, 0.32), (365600, 0.35), (float("inf"), 0.37),
        ],
        "head_of_household": [
            (16550, 0.10), (63100, 0.12), (100500, 0.22),
            (191950, 0.24), (243700, 0.32), (365600, 0.35), (float("inf"), 0.37),
        ],
    },
}

STANDARD_DEDUCTIONS = {
    2025: {"married_filing_jointly": 30000, "single": 15000, "head_of_household": 22500},
    2024: {"married_filing_jointly": 29200, "single": 14600, "head_of_household": 21900},
}

CHILD_CREDIT_PER_CHILD = 2000
NUM_CHILDREN = 2  # Bazen family default

SALT_CAP = 10000

# Arizona flat income tax rate (effective 2023+, A.R.S. § 43-1011)
AZ_FLAT_RATE = 0.025


def calc_az_tax(taxable_income: float) -> float:
    """Arizona flat 2.5% state income tax on taxable income."""
    return round(max(0.0, taxable_income) * AZ_FLAT_RATE, 2)


def calc_tax(taxable_income: float, brackets: list[tuple[float, float]]) -> float:
    """Apply progressive tax brackets to a taxable income amount."""
    tax = 0.0
    prev = 0.0
    for ceiling, rate in brackets:
        if taxable_income <= prev:
            break
        chunk = min(taxable_income, ceiling) - prev
        tax += chunk * rate
        prev = ceiling
    return round(tax, 2)


def get_brackets(year: int, filing_status: str = "married_filing_jointly") -> list:
    """Return the bracket table for the given year and filing status."""
    year_brackets = BRACKETS_BY_YEAR_AND_STATUS.get(year, BRACKETS_BY_YEAR_AND_STATUS[2025])
    return year_brackets.get(filing_status, year_brackets["married_filing_jointly"])


def get_standard_deduction(year: int, filing_status: str = "married_filing_jointly") -> float:
    """Return the standard deduction for the given year and filing status."""
    year_deds = STANDARD_DEDUCTIONS.get(year, STANDARD_DEDUCTIONS[2025])
    return year_deds.get(filing_status, 30000)

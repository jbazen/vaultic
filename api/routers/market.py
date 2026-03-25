"""
Market rates — Fed Funds, 30-year mortgage, 10-year Treasury.
Cached in-memory for 6 hours to avoid hammering public APIs.

Sources (no API key required):
  - Treasury yield curve XML: home.treasury.gov (10yr, 30yr Treasury)
  - FRED API: stlouisfed.org (Fed Funds, 30yr Mortgage) — optional, set FRED_API_KEY env var
"""
import os
import re
import logging
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user

router = APIRouter()
logger = logging.getLogger("vaultic.market")

_cache: dict = {"rates": None, "fetched_at": 0.0}
_CACHE_TTL = 6 * 3600  # 6 hours


def _fetch_treasury_yields() -> dict:
    """Fetch 10yr and 30yr yield from the Treasury daily yield curve XML feed."""
    now = datetime.now()
    year_month = now.strftime("%Y%m")
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center"
        f"/interest-rates/pages/xml?data=daily_treasury_yield_curve"
        f"&field_tdr_date_value={year_month}"
    )
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.get(url)
            resp.raise_for_status()

        text = resp.text
        # Find all <entry> blocks; take the last one (most recent trading day)
        entries = re.findall(r"<entry>.*?</entry>", text, re.DOTALL)
        if not entries:
            return {}

        last = entries[-1]
        result = {}
        for tag, label in [
            ("BC_10YEAR", "10-yr Treasury"),
            ("BC_30YEAR", "30-yr Treasury"),
        ]:
            m = re.search(rf"<d:{tag}[^>]*>([\d.]+)<", last)
            if m:
                result[label] = float(m.group(1))

        return result
    except Exception as exc:
        logger.warning("Treasury yield fetch failed: %s", exc)
        return {}


def _fetch_fred(series_id: str, api_key: str) -> float | None:
    """Fetch the most recent observation for a FRED series."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": "5",  # grab a few in case latest is missing
                },
            )
            resp.raise_for_status()
        obs = resp.json().get("observations", [])
        for o in obs:
            if o.get("value") not in (".", None, ""):
                return float(o["value"])
    except Exception as exc:
        logger.warning("FRED fetch %s failed: %s", series_id, exc)
    return None


def _build_rates() -> list[dict]:
    """Fetch all rates and return a list of rate objects."""
    rates = []
    fred_key = os.environ.get("FRED_API_KEY", "").strip()

    if fred_key:
        # DFF  = Fed Funds effective rate (daily)
        # MORTGAGE30US = 30-year fixed mortgage, Freddie Mac (weekly)
        fed_funds = _fetch_fred("DFF", fred_key)
        mortgage  = _fetch_fred("MORTGAGE30US", fred_key)
        if fed_funds is not None:
            rates.append({"label": "Fed Funds", "value": fed_funds, "source": "FRED"})
        if mortgage is not None:
            rates.append({"label": "30-yr Mortgage", "value": mortgage, "source": "FRED"})

    treasury = _fetch_treasury_yields()
    for label in ("10-yr Treasury", "30-yr Treasury"):
        if label in treasury:
            rates.append({"label": label, "value": treasury[label], "source": "Treasury"})

    return rates


@router.get("/rates")
async def get_market_rates(_user: str = Depends(get_current_user)):
    """Return current market interest rates. Cached for 6 hours."""
    now = time.time()
    if _cache["rates"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL:
        return {"rates": _cache["rates"], "cached": True}

    rates = _build_rates()
    _cache["rates"] = rates
    _cache["fetched_at"] = now
    return {"rates": rates, "cached": False}

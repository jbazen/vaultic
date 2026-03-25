"""
Market rates — Fed Funds, 30-year mortgage, 10-year Treasury, 30-year Treasury.
Cached in-memory for 6 hours. All data sourced from FRED (stlouisfed.org).
Requires FRED_API_KEY env var (free at fred.stlouisfed.org).

Series used:
  DFF          = Fed Funds effective rate (daily)
  MORTGAGE30US = 30-yr fixed mortgage, Freddie Mac (weekly)
  DGS10        = 10-yr Treasury constant maturity (daily)
  DGS30        = 30-yr Treasury constant maturity (daily)
"""
import os
import logging
import time

import httpx
from fastapi import APIRouter, Depends

from api.dependencies import get_current_user

router = APIRouter()
logger = logging.getLogger("vaultic.market")

_cache: dict = {"rates": None, "fetched_at": 0.0}
_CACHE_TTL = 6 * 3600  # 6 hours

_SERIES = [
    ("DFF",          "Fed Funds"),
    ("MORTGAGE30US", "30-yr Mortgage"),
    ("DGS10",        "10-yr Treasury"),
    ("DGS30",        "30-yr Treasury"),
]


def _fetch_fred(series_id: str, api_key: str) -> float | None:
    """Fetch the most recent non-null observation for a FRED series."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": "5",
                },
            )
            resp.raise_for_status()
        for o in resp.json().get("observations", []):
            if o.get("value") not in (".", None, ""):
                return float(o["value"])
    except Exception as exc:
        logger.warning("FRED fetch %s failed: %s", series_id, exc)
    return None


def _build_rates() -> list[dict]:
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if not fred_key:
        return []
    rates = []
    for series_id, label in _SERIES:
        val = _fetch_fred(series_id, fred_key)
        if val is not None:
            rates.append({"label": label, "value": val, "source": "FRED"})
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

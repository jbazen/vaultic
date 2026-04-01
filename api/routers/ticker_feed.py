"""
Ticker Feed — personalized price quotes and curated financial news.

Extracts held tickers from Plaid securities, manual holdings, and Coinbase
accounts, then fetches live prices (Coinbase spot API for crypto, yfinance
for equity/mutual funds) and relevant news (Tavily search).

Results are cached in SQLite; stale data is refreshed on demand.
"""
import logging
import os
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.database import get_db
from api.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feed", tags=["feed"])

# Cache TTLs
CRYPTO_TTL_MIN = 5
EQUITY_TTL_MIN = 15
NEWS_TTL_MIN = 60


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_user_tickers(conn) -> dict:
    """Return {"crypto": [...], "equity": [...]} from all holding sources."""
    crypto = set()
    equity = set()

    # Plaid securities (equity/mutual fund tickers)
    for r in conn.execute(
        "SELECT DISTINCT ticker_symbol FROM plaid_securities WHERE ticker_symbol IS NOT NULL"
    ).fetchall():
        sym = r["ticker_symbol"].strip().upper()
        if sym and len(sym) <= 10:
            equity.add(sym)

    # Manual holdings (PDF-imported tickers)
    for r in conn.execute(
        "SELECT DISTINCT ticker FROM manual_holdings WHERE ticker IS NOT NULL"
    ).fetchall():
        sym = r["ticker"].strip().upper()
        if sym and len(sym) <= 10:
            equity.add(sym)

    # Coinbase accounts (crypto currencies)
    for r in conn.execute(
        "SELECT DISTINCT subtype FROM accounts "
        "WHERE source = 'coinbase' AND is_active = 1 AND subtype IS NOT NULL"
    ).fetchall():
        sym = r["subtype"].strip().upper()
        if sym and sym not in ("USD", "USDC", "USDT", "DAI"):
            crypto.add(sym)

    return {"crypto": sorted(crypto), "equity": sorted(equity)}


def _is_stale(fetched_at_str: str | None, ttl_minutes: int) -> bool:
    if not fetched_at_str:
        return True
    try:
        fetched = datetime.fromisoformat(fetched_at_str)
        return datetime.utcnow() - fetched > timedelta(minutes=ttl_minutes)
    except (ValueError, TypeError):
        return True


def _fetch_crypto_quotes(symbols: list[str]) -> list[dict]:
    """Fetch spot prices from Coinbase public API (no auth needed)."""
    results = []
    for sym in symbols:
        try:
            resp = httpx.get(
                f"https://api.coinbase.com/v2/prices/{sym}-USD/spot",
                timeout=10,
            )
            resp.raise_for_status()
            price = float(resp.json()["data"]["amount"])

            # Try to get 24h change via buy/sell spread or previous price
            change_pct = None
            try:
                buy_resp = httpx.get(
                    f"https://api.coinbase.com/v2/prices/{sym}-USD/buy",
                    timeout=5,
                )
                buy_resp.raise_for_status()
                # Use CoinGecko for 24h change since Coinbase spot doesn't provide it
            except Exception:
                pass

            results.append({
                "symbol": sym,
                "asset_type": "crypto",
                "price": price,
                "change_pct": change_pct,
                "source": "coinbase",
            })
        except Exception as e:
            logger.warning("Could not fetch crypto quote for %s: %s", sym, e)
    return results


def _fetch_crypto_changes(symbols: list[str]) -> dict:
    """Batch-fetch 24h change % from CoinGecko free API."""
    # Map common symbols to CoinGecko IDs
    GECKO_IDS = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "AVAX": "avalanche-2", "XRP": "ripple", "DOGE": "dogecoin",
        "SUI": "sui", "ADA": "cardano", "DOT": "polkadot",
        "MATIC": "matic-network", "LINK": "chainlink", "ATOM": "cosmos",
        "UNI": "uniswap", "LTC": "litecoin", "SHIB": "shiba-inu",
    }
    ids_to_fetch = []
    sym_to_gecko = {}
    for sym in symbols:
        gecko_id = GECKO_IDS.get(sym, sym.lower())
        ids_to_fetch.append(gecko_id)
        sym_to_gecko[gecko_id] = sym

    if not ids_to_fetch:
        return {}

    try:
        resp = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ",".join(ids_to_fetch),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        changes = {}
        for gecko_id, sym in sym_to_gecko.items():
            if gecko_id in data and "usd_24h_change" in data[gecko_id]:
                changes[sym] = round(data[gecko_id]["usd_24h_change"], 2)
        return changes
    except Exception as e:
        logger.warning("CoinGecko 24h change fetch failed: %s", e)
        return {}


def _fetch_equity_quotes(symbols: list[str]) -> list[dict]:
    """Fetch equity/mutual fund quotes via Yahoo Finance public API (no library needed)."""
    results = []
    for sym in symbols:
        try:
            resp = httpx.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"interval": "1d", "range": "2d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice")
            prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")

            change_pct = None
            if price and prev_close and prev_close > 0:
                change_pct = round(((price - prev_close) / prev_close) * 100, 2)

            if price is not None:
                results.append({
                    "symbol": sym,
                    "asset_type": "equity",
                    "price": round(price, 2),
                    "change_pct": change_pct,
                    "prev_close": round(prev_close, 2) if prev_close else None,
                    "market_cap": None,
                    "source": "yahoo",
                })
        except Exception as e:
            logger.warning("Could not fetch equity quote for %s: %s", sym, e)
    return results


def _fetch_news(tickers: dict) -> list[dict]:
    """Fetch relevant news via Tavily search."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set — skipping news fetch")
        return []

    articles = []
    seen_urls = set()

    # Build 2-3 targeted queries
    queries = []
    if tickers["crypto"]:
        top_crypto = tickers["crypto"][:5]
        queries.append((" ".join(top_crypto) + " cryptocurrency price news today", "crypto"))
    if tickers["equity"]:
        queries.append((" ".join(tickers["equity"]) + " stock mutual fund market news", "equity"))
    queries.append(("personal finance mortgage rates 401k retirement news 2026", "macro"))

    for query, topic in queries:
        try:
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": 5,
                    "include_answer": False,
                    "search_depth": "basic",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for r in data.get("results", []):
                url = r.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                articles.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": (r.get("content", "") or "")[:300],
                    "source_name": _extract_domain(url),
                    "relevance": topic,
                    "published_at": r.get("published_date"),
                })
        except Exception as e:
            logger.warning("Tavily search failed for topic '%s': %s", topic, e)

    return articles


def _extract_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return host.replace("www.", "")
    except Exception:
        return ""


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/tickers")
def get_tickers(user=Depends(get_current_user)):
    """Return the user's held ticker symbols grouped by type."""
    with get_db() as conn:
        return _get_user_tickers(conn)


@router.get("/quotes")
def get_quotes(user=Depends(get_current_user)):
    """Return cached quotes. Triggers background refresh if stale."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT symbol, asset_type, price, change_pct, prev_close, "
            "market_cap, source, fetched_at FROM ticker_quotes ORDER BY asset_type, symbol"
        ).fetchall()
    return {"quotes": [dict(r) for r in rows]}


@router.post("/quotes/refresh")
def refresh_quotes(user=Depends(get_current_user)):
    """Force re-fetch all quotes from external APIs."""
    with get_db() as conn:
        tickers = _get_user_tickers(conn)

    # Fetch crypto quotes
    crypto_quotes = _fetch_crypto_quotes(tickers["crypto"])
    # Enrich with 24h changes from CoinGecko
    changes = _fetch_crypto_changes(tickers["crypto"])
    for q in crypto_quotes:
        if q["symbol"] in changes:
            q["change_pct"] = changes[q["symbol"]]

    # Fetch equity quotes
    equity_quotes = _fetch_equity_quotes(tickers["equity"])

    all_quotes = crypto_quotes + equity_quotes
    now = datetime.utcnow().isoformat()

    with get_db() as conn:
        for q in all_quotes:
            conn.execute(
                """INSERT INTO ticker_quotes (symbol, asset_type, price, change_pct, prev_close, market_cap, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     price=excluded.price, change_pct=excluded.change_pct,
                     prev_close=excluded.prev_close, market_cap=excluded.market_cap,
                     source=excluded.source, fetched_at=excluded.fetched_at""",
                (q["symbol"], q["asset_type"], q["price"], q.get("change_pct"),
                 q.get("prev_close"), q.get("market_cap"), q["source"], now),
            )
        conn.commit()

    return {"ok": True, "count": len(all_quotes)}


@router.get("/news")
def get_news(user=Depends(get_current_user)):
    """Return cached news articles."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT title, url, source_name, snippet, relevance, published_at, fetched_at "
            "FROM news_articles ORDER BY fetched_at DESC, published_at DESC LIMIT 20"
        ).fetchall()
    return {"articles": [dict(r) for r in rows]}


@router.post("/news/refresh")
def refresh_news(user=Depends(get_current_user)):
    """Force re-fetch news from Tavily."""
    with get_db() as conn:
        tickers = _get_user_tickers(conn)

    articles = _fetch_news(tickers)
    now = datetime.utcnow().isoformat()

    with get_db() as conn:
        # Clean old articles (keep 7 days)
        conn.execute(
            "DELETE FROM news_articles WHERE fetched_at < ?",
            ((datetime.utcnow() - timedelta(days=7)).isoformat(),),
        )
        for a in articles:
            conn.execute(
                """INSERT INTO news_articles (title, url, source_name, snippet, relevance, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                     title=excluded.title, snippet=excluded.snippet,
                     relevance=excluded.relevance, fetched_at=excluded.fetched_at""",
                (a["title"], a["url"], a.get("source_name"), a.get("snippet"),
                 a.get("relevance"), a.get("published_at"), now),
            )
        conn.commit()

    return {"ok": True, "count": len(articles)}


@router.get("/summary")
def get_feed_summary(user=Depends(get_current_user)):
    """Combined endpoint — returns tickers, quotes, and news in one call.
    Triggers refresh for stale data inline (not background)."""
    with get_db() as conn:
        tickers = _get_user_tickers(conn)

        # Check if quotes are stale
        newest_quote = conn.execute(
            "SELECT MAX(fetched_at) as latest FROM ticker_quotes"
        ).fetchone()
        quotes_stale = _is_stale(
            newest_quote["latest"] if newest_quote else None,
            CRYPTO_TTL_MIN,
        )

        # Check if news is stale
        newest_news = conn.execute(
            "SELECT MAX(fetched_at) as latest FROM news_articles"
        ).fetchone()
        news_stale = _is_stale(
            newest_news["latest"] if newest_news else None,
            NEWS_TTL_MIN,
        )

    # Refresh stale data
    if quotes_stale and (tickers["crypto"] or tickers["equity"]):
        try:
            refresh_quotes.__wrapped__(user) if hasattr(refresh_quotes, "__wrapped__") else _do_refresh_quotes(tickers)
        except Exception as e:
            logger.warning("Auto-refresh quotes failed: %s", e)

    if news_stale:
        try:
            _do_refresh_news(tickers)
        except Exception as e:
            logger.warning("Auto-refresh news failed: %s", e)

    # Read fresh data
    with get_db() as conn:
        quotes = conn.execute(
            "SELECT symbol, asset_type, price, change_pct, prev_close, "
            "market_cap, source, fetched_at FROM ticker_quotes ORDER BY asset_type, symbol"
        ).fetchall()
        articles = conn.execute(
            "SELECT title, url, source_name, snippet, relevance, published_at, fetched_at "
            "FROM news_articles ORDER BY fetched_at DESC, published_at DESC LIMIT 15"
        ).fetchall()

    return {
        "tickers": tickers,
        "quotes": [dict(r) for r in quotes],
        "news": [dict(r) for r in articles],
    }


def _do_refresh_quotes(tickers: dict):
    """Internal quote refresh (no auth dependency)."""
    crypto_quotes = _fetch_crypto_quotes(tickers["crypto"])
    changes = _fetch_crypto_changes(tickers["crypto"])
    for q in crypto_quotes:
        if q["symbol"] in changes:
            q["change_pct"] = changes[q["symbol"]]

    equity_quotes = _fetch_equity_quotes(tickers["equity"])
    all_quotes = crypto_quotes + equity_quotes
    now = datetime.utcnow().isoformat()

    with get_db() as conn:
        for q in all_quotes:
            conn.execute(
                """INSERT INTO ticker_quotes (symbol, asset_type, price, change_pct, prev_close, market_cap, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     price=excluded.price, change_pct=excluded.change_pct,
                     prev_close=excluded.prev_close, market_cap=excluded.market_cap,
                     source=excluded.source, fetched_at=excluded.fetched_at""",
                (q["symbol"], q["asset_type"], q["price"], q.get("change_pct"),
                 q.get("prev_close"), q.get("market_cap"), q["source"], now),
            )
        conn.commit()


def _do_refresh_news(tickers: dict):
    """Internal news refresh (no auth dependency)."""
    articles = _fetch_news(tickers)
    now = datetime.utcnow().isoformat()

    with get_db() as conn:
        conn.execute(
            "DELETE FROM news_articles WHERE fetched_at < ?",
            ((datetime.utcnow() - timedelta(days=7)).isoformat(),),
        )
        for a in articles:
            conn.execute(
                """INSERT INTO news_articles (title, url, source_name, snippet, relevance, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                     title=excluded.title, snippet=excluded.snippet,
                     relevance=excluded.relevance, fetched_at=excluded.fetched_at""",
                (a["title"], a["url"], a.get("source_name"), a.get("snippet"),
                 a.get("relevance"), a.get("published_at"), now),
            )
        conn.commit()

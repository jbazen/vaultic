/**
 * NewsFeedPanel — Horizontal price ticker bar + curated news feed.
 * Fetches from /api/feed/summary on mount; auto-refreshes stale data server-side.
 */
import { useState, useEffect, useRef } from "react";
import { getFeedSummary, refreshFeedQuotes, refreshFeedNews } from "../../api.js";
import { fmt } from "../../utils/format.js";

function TickerChip({ symbol, price, changePct, assetType }) {
  const isUp = changePct > 0;
  const isDown = changePct < 0;
  const changeColor = isUp ? "var(--green)" : isDown ? "var(--red)" : "var(--text2)";
  const arrow = isUp ? "\u25B2" : isDown ? "\u25BC" : "";
  const typeColor = assetType === "crypto" ? "var(--orange)" : "var(--accent)";

  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 8,
      background: "var(--bg3)", borderRadius: 8, padding: "8px 14px",
      whiteSpace: "nowrap", minWidth: 0, flexShrink: 0,
    }}>
      <span style={{ fontWeight: 700, fontSize: 13, color: typeColor }}>{symbol}</span>
      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text)" }}>
        {price != null ? `$${price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—"}
      </span>
      {changePct != null && (
        <span style={{ fontSize: 11, fontWeight: 600, color: changeColor }}>
          {arrow} {Math.abs(changePct).toFixed(2)}%
        </span>
      )}
    </div>
  );
}

function NewsItem({ article }) {
  const topicColors = {
    crypto: "var(--orange)", equity: "var(--accent)", macro: "#22d3ee",
  };
  const badgeColor = topicColors[article.relevance] || "var(--text2)";

  const timeAgo = article.published_at ? _relativeTime(article.published_at) : "";

  return (
    <a href={article.url} target="_blank" rel="noopener noreferrer"
      style={{
        display: "block", padding: "12px 0",
        borderBottom: "1px solid var(--border)", textDecoration: "none", color: "inherit",
      }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{
          fontSize: 10, fontWeight: 700, textTransform: "uppercase",
          color: badgeColor, letterSpacing: "0.5px",
        }}>
          {article.relevance}
        </span>
        {article.source_name && (
          <span style={{ fontSize: 11, color: "var(--text2)" }}>{article.source_name}</span>
        )}
        {timeAgo && (
          <span style={{ fontSize: 11, color: "var(--text2)" }}>{timeAgo}</span>
        )}
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text)", lineHeight: 1.4, marginBottom: 4 }}>
        {article.title}
      </div>
      {article.snippet && (
        <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.4 }}>
          {article.snippet.length > 180 ? article.snippet.slice(0, 180) + "..." : article.snippet}
        </div>
      )}
    </a>
  );
}

function _relativeTime(dateStr) {
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    return `${diffDay}d ago`;
  } catch {
    return "";
  }
}

export default function NewsFeedPanel() {
  const [quotes, setQuotes] = useState([]);
  const [news, setNews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const mountedRef = useRef(true);

  async function load() {
    try {
      const data = await getFeedSummary();
      if (!mountedRef.current) return;
      setQuotes(data.quotes || []);
      setNews(data.news || []);
    } catch {
      // Silently fail — panel is supplementary
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    mountedRef.current = true;
    load();
    return () => { mountedRef.current = false; };
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await Promise.all([refreshFeedQuotes(), refreshFeedNews()]);
      await load();
    } finally {
      if (mountedRef.current) setRefreshing(false);
    }
  }

  if (loading) return null;
  if (quotes.length === 0 && news.length === 0) return null;

  const cryptoQuotes = quotes.filter(q => q.asset_type === "crypto");
  const equityQuotes = quotes.filter(q => q.asset_type === "equity");

  return (
    <div className="card" style={{ margin: "0 0 20px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <div className="card-title" style={{ margin: 0 }}>Market & News</div>
        <button className="btn btn-secondary" style={{ fontSize: 11, padding: "4px 12px" }}
          onClick={handleRefresh} disabled={refreshing}>
          {refreshing ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {/* Ticker bar */}
      {quotes.length > 0 && (
        <div style={{
          display: "flex", gap: 8, overflowX: "auto", paddingBottom: 8, marginBottom: 16,
          scrollbarWidth: "thin",
        }}>
          {cryptoQuotes.map(q => (
            <TickerChip key={q.symbol} symbol={q.symbol} price={q.price}
              changePct={q.change_pct} assetType="crypto" />
          ))}
          {equityQuotes.length > 0 && cryptoQuotes.length > 0 && (
            <div style={{ width: 1, background: "var(--border)", alignSelf: "stretch", margin: "0 4px", flexShrink: 0 }} />
          )}
          {equityQuotes.map(q => (
            <TickerChip key={q.symbol} symbol={q.symbol} price={q.price}
              changePct={q.change_pct} assetType="equity" />
          ))}
        </div>
      )}

      {/* Fetch timestamp */}
      {quotes.length > 0 && quotes[0].fetched_at && (
        <div style={{ fontSize: 11, color: "var(--text2)", marginBottom: 12 }}>
          Prices as of {_relativeTime(quotes[0].fetched_at)}
        </div>
      )}

      {/* News list */}
      {news.length > 0 && (
        <div>
          {news.slice(0, 10).map((a, i) => (
            <NewsItem key={a.url || i} article={a} />
          ))}
        </div>
      )}
    </div>
  );
}

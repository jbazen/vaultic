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

/** Category accent colors — always shown as left border on each tile. */
const TOPIC_COLORS = {
  crypto: "var(--orange)", equity: "var(--accent)", macro: "#22d3ee",
};

/**
 * NewsTile — compact card with colored category accent, thumbnail/favicon,
 * 2-line clamped title, source name, and relative time.
 */
function NewsTile({ article }) {
  const accentColor = TOPIC_COLORS[article.relevance] || "var(--text2)";
  const timeAgo = article.published_at ? _relativeTime(article.published_at) : "";
  const imgSrc = article.image_url || "";

  return (
    <a href={article.url} target="_blank" rel="noopener noreferrer"
      title={article.title}
      style={{
        display: "flex", gap: 10, padding: 10,
        background: "var(--bg3)", borderRadius: 8,
        borderLeft: `3px solid ${accentColor}`,
        textDecoration: "none", color: "inherit",
        minHeight: 72, alignItems: "center",
      }}>
      {/* Thumbnail or favicon */}
      {imgSrc && (
        <img src={imgSrc} alt="" loading="lazy"
          style={{
            width: 48, height: 48, borderRadius: 6, objectFit: "cover",
            flexShrink: 0, background: "var(--bg)",
          }}
          onError={e => { e.target.style.display = "none"; }}
        />
      )}
      <div style={{ minWidth: 0, flex: 1 }}>
        {/* Title — 2-line clamp */}
        <div style={{
          fontSize: 13, fontWeight: 600, color: "var(--text)", lineHeight: 1.35,
          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}>
          {article.title}
        </div>
        {/* Source + category badge + time */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
          <span style={{
            fontSize: 9, fontWeight: 700, textTransform: "uppercase",
            color: accentColor, letterSpacing: "0.5px",
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
      </div>
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

      {/* News tile grid */}
      {news.length > 0 && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))",
          gap: 10,
        }}>
          {news.slice(0, 12).map((a, i) => (
            <NewsTile key={a.url || i} article={a} />
          ))}
        </div>
      )}
    </div>
  );
}

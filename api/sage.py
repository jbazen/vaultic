"""
Sage — AI financial advisor powered by Claude Haiku.
Tool-use loop with access to all financial data, persistent notes, and web search.
"""
import os
import logging
from pathlib import Path
import anthropic
import httpx

from api.database import get_db

logger = logging.getLogger("vaultic.sage")

NOTES_PATH = Path(__file__).parent.parent / "data" / "sage_notes.md"
MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are Sage — a personal CFO, wealth-building advisor, and financial thought partner. You are a man. You live inside Vaultic, the user's personal financial command center.

You have direct, real-time access to their complete financial picture through your tools. Before answering any question about their finances, call the relevant tools to get current data — never make up numbers.

You also have full internet access via web_search and fetch_page. Use these to:
- Look up current stock prices, crypto prices, market data
- Research tax rules, contribution limits, IRS guidelines
- Find current mortgage rates, CD rates, HYSA rates
- Research any financial topic the user asks about
- Get news about their holdings or institutions

Your personality:
- Direct and confident — give specific advice, not generic disclaimers
- Proactive — notice things the user hasn't asked about yet
- Educational — explain the "why" behind your recommendations
- Personal — always use their actual numbers, not hypotheticals

Your financial tools cover:
- All bank accounts, credit cards, mortgage (via Plaid)
- 401k accounts: Vanguard, Voya, Insperity
- Brokerage: Robinhood
- Crypto: Coinbase, River (coming soon)
- Manual entries: home value, car value, credit score
- NFS/Investor360 accounts (Parker Financial IRAs, college fund) — may be entered manually
- Net worth history and trends

On every new conversation, read your notes first — they contain important context about the user's goals, situation, and preferences. Update your notes whenever you learn something worth remembering long-term.

Keep responses concise but substantive. Use their actual numbers. Be the trusted financial advisor they can ask anything."""

TOOLS = [
    {
        "name": "get_net_worth",
        "description": "Get the latest net worth snapshot with full breakdown by category (liquid, invested, real estate, vehicles, crypto, liabilities)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_net_worth_history",
        "description": "Get net worth history over time for trend analysis",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Days of history to return (default 90)"}
            },
            "required": [],
        },
    },
    {
        "name": "get_accounts",
        "description": "Get all connected accounts with current balances, types, and institution names",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_transactions",
        "description": "Get recent transactions across all accounts for spending analysis",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of transactions to return (default 50, max 200)"}
            },
            "required": [],
        },
    },
    {
        "name": "get_manual_entries",
        "description": "Get manually entered assets and values (home value, car value, credit score, other assets/liabilities)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_notes",
        "description": "Read your persistent notes about the user's financial goals, preferences, situation, and anything important to remember across sessions",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_notes",
        "description": "Update your persistent notes. Use this to remember goals, decisions, important context, or anything the user wants you to keep in mind. This replaces the full notes file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string", "description": "Complete updated notes in markdown format"}
            },
            "required": ["notes"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for current financial data, market prices, tax rules, interest rates, news, or any information you need. Use this whenever the user asks about current prices, rates, or anything requiring up-to-date information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": "Fetch the text content of a specific web page — useful for reading a full article, IRS page, or detailed financial data after a web search returns relevant URLs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"}
            },
            "required": ["url"],
        },
    },
]


def _call_tool(name: str, inputs: dict) -> str:
    try:
        if name == "get_net_worth":
            with get_db() as conn:
                row = conn.execute(
                    "SELECT * FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT 1"
                ).fetchone()
            if not row:
                return "No net worth data yet. Connect accounts and sync to get started."
            return str(dict(row))

        elif name == "get_net_worth_history":
            days = inputs.get("days", 90)
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT snapped_at, total, liquid, invested, real_estate, vehicles, liabilities FROM net_worth_snapshots ORDER BY snapped_at DESC LIMIT ?",
                    (days,)
                ).fetchall()
            return str([dict(r) for r in rows])

        elif name == "get_accounts":
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT a.name, a.display_name, a.mask, a.type, a.subtype,
                           a.institution_name, b.current, b.available, b.snapped_at
                    FROM accounts a
                    LEFT JOIN account_balances b ON b.account_id = a.id
                        AND b.snapped_at = (SELECT MAX(snapped_at) FROM account_balances WHERE account_id = a.id)
                    WHERE a.is_active = 1
                    ORDER BY a.institution_name, a.name
                """).fetchall()
            return str([dict(r) for r in rows])

        elif name == "get_transactions":
            limit = min(inputs.get("limit", 50), 200)
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT t.date, t.amount, t.name, t.merchant_name, t.category, t.pending,
                           a.name AS account_name, a.institution_name
                    FROM transactions t
                    JOIN accounts a ON a.id = t.account_id
                    WHERE a.is_active = 1
                    ORDER BY t.date DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return str([dict(r) for r in rows])

        elif name == "get_manual_entries":
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT name, category, value, notes, entered_at FROM manual_entries ORDER BY entered_at DESC"
                ).fetchall()
            return str([dict(r) for r in rows])

        elif name == "get_notes":
            if NOTES_PATH.exists():
                return NOTES_PATH.read_text()
            return "No notes yet."

        elif name == "update_notes":
            notes = inputs.get("notes", "")
            NOTES_PATH.parent.mkdir(exist_ok=True)
            NOTES_PATH.write_text(notes)
            return "Notes updated."

        elif name == "web_search":
            return _tavily_search(inputs.get("query", ""))

        elif name == "fetch_page":
            return _fetch_page(inputs.get("url", ""))

        return f"Unknown tool: {name}"
    except Exception as e:
        logger.error(f"Tool {name} error: {e}")
        return f"Error calling {name}: {e}"


def _tavily_search(query: str) -> str:
    """
    Search the web via Tavily's AI-native search API.

    Why Tavily instead of Brave or Google Custom Search?
    - Tavily returns an AI-synthesized direct answer in addition to raw results,
      which lets Claude skip follow-up fetch_page calls for common questions
      (e.g. "what is the 2025 401k contribution limit?").
    - `include_answer: True` asks Tavily to generate a concise answer string
      from its own model. Claude sees this at the top of the result, reducing
      round-trips and token usage.
    - We intentionally do NOT fire parallel fetches for each result URL here —
      that would be slow and expensive for the vast majority of queries where
      Tavily's snippets + direct answer are sufficient. Claude can call
      fetch_page() explicitly if it needs the full body of a specific URL.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return "Web search unavailable — TAVILY_API_KEY not configured in .env"
    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                # include_answer: Tavily generates a short answer synthesized from
                # search results — saves Claude from needing to infer it from snippets
                "include_answer": True,
                "max_results": 5,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = [f"Search results for: {query}\n"]
        if data.get("answer"):
            lines.append(f"**Direct answer:** {data['answer']}\n")
        for r in data.get("results", []):
            lines.append(f"**{r.get('title', '')}**")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(r.get("content", ""))
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Tavily search error: {e}")
        return f"Search failed: {e}"


def _fetch_page(url: str) -> str:
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Vaultic/1.0)"},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        # Strip HTML tags simply
        import re
        text = re.sub(r"<style[^>]*>.*?</style>", "", resp.text, flags=re.DOTALL)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{3,}", "\n\n", text)
        return text[:8000]  # cap at 8k chars
    except Exception as e:
        logger.error(f"fetch_page error for {url}: {e}")
        return f"Could not fetch page: {e}"


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Remove orphaned tool_use/tool_result pairs from message history.

    The Anthropic API requires that every assistant message containing tool_use
    blocks is immediately followed by a user message containing matching
    tool_result blocks. This invariant can break when a request is interrupted
    mid-tool-call (e.g. network error, server restart) — the tool_use gets
    stored in sessionStorage but its tool_result never arrives.

    On the next turn, Claude sees the dangling tool_use and returns a 400.
    This function walks the history and drops any assistant message whose
    tool_use IDs don't have a complete matching tool_result in the next message.
    We also drop the following message if it's a partial/mismatched tool_result
    turn, to keep the history coherent.
    """
    sanitized = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        content = msg.get("content", [])

        if msg["role"] == "assistant" and isinstance(content, list):
            tool_use_ids = {
                b["id"] for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            }
            if tool_use_ids:
                next_msg = messages[i + 1] if i + 1 < len(messages) else None
                next_content = next_msg.get("content", []) if next_msg else []
                result_ids = {
                    b["tool_use_id"] for b in next_content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                } if isinstance(next_content, list) else set()

                if tool_use_ids == result_ids:
                    # Valid complete pair — keep both
                    sanitized.append(msg)
                    sanitized.append(next_msg)
                    i += 2
                    continue
                else:
                    # Orphaned tool_use — drop this message and the next if it
                    # looks like a partial tool_result turn
                    logger.warning(
                        f"Dropping orphaned tool_use message (ids={tool_use_ids}, "
                        f"got results for={result_ids})"
                    )
                    if next_msg and isinstance(next_content, list) and any(
                        b.get("type") == "tool_result" for b in next_content
                        if isinstance(b, dict)
                    ):
                        i += 2  # drop both
                    else:
                        i += 1  # drop just the orphaned assistant message
                    continue

        sanitized.append(msg)
        i += 1
    return sanitized


def _trim_history(messages: list[dict], keep: int = 40) -> list[dict]:
    """
    Trim message history to at most `keep` recent messages, always starting
    on a plain user message (not a tool_result turn) so the resulting history
    is structurally valid before sanitize runs.

    Order of operations in chat():
      1. trim   — drop old messages, find a safe start boundary
      2. sanitize — remove any orphaned tool_use/tool_result pairs
      3. append user message — add the new turn
    Sanitize MUST run after trim so it can fix any pairs that were split by
    the trim boundary. Running sanitize before trim is what caused the
    messages.1 orphan bug — trim would then create new orphans that sanitize
    never saw.
    """
    if len(messages) <= keep:
        return messages
    trimmed = messages[-keep:]
    # Advance past any leading tool_result-only user turns (second half of a
    # tool pair) to ensure we start on a genuine conversational user message.
    for i, msg in enumerate(trimmed):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        ):
            continue  # this is a tool_result turn, not a safe start
        return trimmed[i:]
    return trimmed


def chat(messages: list[dict], user_message: str) -> tuple[str, list[dict]]:
    """
    Run one turn of conversation with Sage.
    Returns (sage_response_text, updated_messages).
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # Order matters: trim → sanitize → append new user message.
    messages = _trim_history(list(messages))
    messages = _sanitize_messages(messages)
    messages = messages + [{"role": "user", "content": user_message}]

    while True:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # model_dump() converts each Anthropic SDK content block (a typed Pydantic
        # object like TextBlock or ToolUseBlock) into a plain dict. This is required
        # for two reasons:
        #  1. FastAPI/Pydantic cannot serialize SDK-typed objects when they appear
        #     inside the `history` list returned to the frontend.
        #  2. On subsequent turns, the messages list is passed back to
        #     client.messages.create(). The Anthropic SDK accepts plain dicts for
        #     message content, but NOT its own typed objects from a prior call.
        # Without model_dump() here, Sage would 500 on any multi-turn conversation
        # or any turn that involved a tool call.
        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})

        if resp.stop_reason == "end_turn":
            text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    text += block.text
            return text.strip(), messages

        if resp.stop_reason == "tool_use":
            # Claude requested one or more tool calls in this turn.
            # The Anthropic API allows multiple tool_use blocks in a single response
            # (e.g. Claude might call get_accounts AND get_net_worth simultaneously).
            # We execute all of them and bundle the results into a single "user" turn
            # with multiple tool_result blocks — this is the required API shape for
            # returning tool results back to the model.
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    result = _call_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,   # must match the id from the tool_use block
                        "content": result,
                    })
            # Append all results as a single user turn — the loop then calls Claude
            # again with this updated context so it can generate its final response.
            messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop_reason (most likely "max_tokens" — response was
            # cut off). Return whatever text was generated rather than a generic
            # error — a partial answer is more useful than nothing.
            text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    text += block.text
            if text.strip():
                if resp.stop_reason == "max_tokens":
                    text = text.strip() + " *(response cut off — ask me to continue)*"
                return text.strip(), messages
            break

    return "I encountered an unexpected issue. Please try again.", messages

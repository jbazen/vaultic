"""
Sage — AI financial advisor powered by Claude Haiku.
Tool-use loop with access to all financial data, persistent notes, and web search.
"""
import os
import logging
import anthropic
import httpx

logger = logging.getLogger("vaultic.sage")

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are Sage — a personal CFO, wealth-building advisor, tax expert, and financial thought partner. You are a man. You live inside Vaultic, the user's personal financial command center.

You have direct, real-time access to their complete financial picture through your tools. Before answering any question about their finances, ALWAYS call the relevant tools to get current data — never make up numbers or estimate when you can look it up.

You also have full internet access via web_search and fetch_page. Use these to:
- Look up current stock prices, crypto prices, market data
- Research current tax law, IRS rules, contribution limits, bracket thresholds
- Find current mortgage rates, CD rates, HYSA rates
- Research any financial topic the user asks about
- Get news about their holdings or institutions

## Tax expertise
You are their personal tax advisor. You know their complete tax history (2019-2024) and have access to all uploaded tax documents for the current year. When answering tax questions:
- Always call get_tax_history first to see their actual historical numbers
- Call get_draft_return for the current year if they've uploaded documents
- Call get_paystubs for YTD income and withholding data
- Call get_tax_projection for a full-year estimate
- Use web_search to verify current IRS rules, limits, and brackets — tax law changes every year
- Give specific, actionable advice: exact dollar amounts, which lines to change on their W-4, whether to itemize
- Proactively flag things they haven't asked about: over-withholding, missed deductions, opportunities

Their tax profile:
- Filing status: Married Filing Jointly (Jason + Heather)
- Dependents: two sons, John and Milo (child tax credit eligible)
- Income: high W-2 earners (~$280k+), Jason is Software Developer, Heather is Stay-at-Home Parent
- Location: Phoenix, AZ
- Always itemized deductions 2019-2024 (mortgage interest + SALT + charitable giving consistently beat standard)
- Consistently over-withholding — large refunds every year ($7k-$13k range)
- Accounts: Vanguard/Voya/Insperity 401ks, Rocket Mortgage, Health Equity/Optum HSA

## Your personality
- Direct and confident — give specific advice, not "consult a tax professional" disclaimers
- Proactive — notice things they haven't asked about (over-withholding, deduction opportunities, etc.)
- Educational — explain the why behind your recommendations
- Personal — always use their actual numbers, never hypotheticals

## Financial tools
- All bank accounts, credit cards, mortgage (via Plaid)
- 401k accounts: Vanguard, Voya, Insperity
- Brokerage: Robinhood; Crypto: Coinbase
- Manual entries: home value ($655k), car, credit score
- Parker Financial IRAs and college fund
- Full budget and transaction history
- Tax returns 2019-2024, current-year documents, paystubs, W-4s, draft return
- Real-time price quotes for all held tickers (crypto via Coinbase, equity/funds via Yahoo Finance) — use get_ticker_quotes
- Curated financial news relevant to their holdings and financial situation — use get_financial_news

On every new conversation, read your notes first — they contain important context about the user's goals and preferences. Update notes whenever you learn something worth remembering.

Keep responses concise but substantive. Use their actual numbers. Be the trusted financial advisor and tax expert they never have to pay for."""

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
    {
        "name": "get_budget",
        "description": "Get the full zero-based budget for a specific month — all groups (Income, Housing, Food, etc.), their line items, planned amounts, and actual spending. Use this to answer questions about budget targets, spending vs. plan, or whether the user is over/under budget in any category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month in YYYY-MM format (e.g. '2026-03'). Defaults to current month if omitted."}
            },
            "required": [],
        },
    },
    {
        "name": "get_budget_history",
        "description": (
            "Get historical spending totals by budget category — data goes back to October 2015 (10+ years). "
            "Returns monthly aggregated totals (group, item, total_spent, num_transactions) per category-month. "
            "Use group_name and/or item_name filters to narrow results. "
            "IMPORTANT: pass months=0 ONLY when also providing group_name or item_name — "
            "requesting all history with no filter returns 3000+ rows and will exceed token limits. "
            "For a specific item across all time (e.g. 'Jason Income since 2015'), use months=0 + item_name. "
            "For trends in one group, use months=0 + group_name. "
            "For broad multi-category analysis, use a month range (e.g. months=24)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "months": {"type": "integer", "description": "Months of history to return (default 12, pass 0 for all history back to Oct 2015 — requires group_name or item_name filter when using 0)"},
                "group_name": {"type": "string", "description": "Filter to a specific budget group (e.g. 'Food', 'Housing', 'Income')"},
                "item_name": {"type": "string", "description": "Filter to a specific budget line item by name (e.g. 'Jason Income', 'Mortgage', 'Groceries') — partial match"}
            },
            "required": [],
        },
    },
    {
        "name": "get_unassigned_transactions",
        "description": "Get transactions that have not yet been assigned to a budget item for a given month. Use this to see what needs to be categorized, then call assign_transaction for each one.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month in YYYY-MM format (e.g. '2026-03'). Defaults to current month."}
            },
            "required": [],
        },
    },
    {
        "name": "assign_transaction",
        "description": "Assign a single Plaid transaction to a budget line item. Use this to categorize transactions. Get item IDs from get_budget. Get transaction IDs from get_unassigned_transactions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "The transaction_id from get_unassigned_transactions"},
                "item_id": {"type": "integer", "description": "The budget item id from get_budget to assign this transaction to"}
            },
            "required": ["transaction_id", "item_id"],
        },
    },
    {
        "name": "auto_assign_month",
        "description": "Run automatic categorization on all unassigned transactions for a month using learned merchant→budget-item rules from historical data. This is the fastest way to bulk-assign transactions. After running, check remaining unassigned with get_unassigned_transactions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Month in YYYY-MM format (e.g. '2026-03'). Defaults to current month."}
            },
            "required": [],
        },
    },
    {
        "name": "search_budget_history",
        "description": "Search budget history for specific transactions by merchant name and/or amount. Use this when the user asks about a specific charge — e.g. 'which budget item did Amazon $8.47 go to in April 2022?' Returns individual matching rows with the merchant, amount, date, and which budget item it was assigned to. Much more efficient than get_budget_history for targeted lookups.",
        "input_schema": {
            "type": "object",
            "properties": {
                "merchant": {"type": "string", "description": "Merchant name to search for (case-insensitive, partial match — e.g. 'amazon', 'target', 'netflix')"},
                "month": {"type": "string", "description": "Optional: narrow to a specific month in YYYY-MM format"},
                "amount": {"type": "number", "description": "Optional: filter by exact dollar amount (absolute value, e.g. 8.47)"},
                "limit": {"type": "integer", "description": "Max rows to return (default 20, max 100)"}
            },
            "required": [],
        },
    },
    {
        "name": "get_paystubs",
        "description": "Get paystub data including YTD gross income, federal/state withholding, Social Security, and Medicare. Use when the user asks about their paycheck, YTD earnings, current withholding, or wants to know how much they've made or withheld so far this year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ytd_only": {
                    "type": "boolean",
                    "description": "If true, return only the most recent paystub per employer (which has the YTD totals). Default true."
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_vault_documents",
        "description": "List all documents stored in the document vault, optionally filtered by year. Shows what financial documents have been uploaded (tax returns, W-2s, 1099s, paystubs, investment statements, etc.) and what's missing from the checklist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Filter by tax year. Omit for all years."}
            },
            "required": [],
        },
    },
    {
        "name": "optimize_w4",
        "description": "Calculate optimal W-4 withholding adjustments. Given a target refund/owed amount, computes what Step 4c (extra withholding per paycheck) should be set to on the employee's W-4. Uses actual draft return or projection data. Use when the user asks about adjusting withholding, W-4 changes, or wants to stop over/under withholding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_refund": {"type": "number", "description": "Desired refund at filing (e.g. 0 for break-even, 500 for small refund). Default 0."},
                "year": {"type": "integer", "description": "Tax year to optimize for. Default 2025."},
                "pay_periods_remaining": {"type": "integer", "description": "Pay periods left in the year. If omitted, calculated from current date."},
            },
            "required": [],
        },
    },
    {
        "name": "get_draft_return",
        "description": "Calculate a complete draft 1040 for a given tax year using all uploaded tax documents (W-2s, 1099s, 1098s, giving statements). Returns line-by-line income, deductions, tax, withholding, and estimated refund or amount owed. Use when the user wants to know their tax situation for a specific year based on actual documents they've uploaded.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Tax year (e.g. 2025)"}
            },
            "required": ["year"],
        },
    },
    {
        "name": "get_tax_projection",
        "description": "Get a projected tax liability estimate for 2025 using YTD paystub data extrapolated to a full year. Returns projected gross income, deduction, taxable income, estimated tax, projected withholding, and estimated refund or amount owed. Use when the user asks about their 2025 taxes, whether they owe money, if they're on track with withholding, or wants a tax estimate.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_tax_history",
        "description": "Get the user's tax return history. Returns year-over-year income, AGI, effective tax rate, deductions, refunds/owed, and key deduction items (mortgage interest, charitable giving, SALT). Use this when the user asks about taxes, their tax history, withholding, whether they should itemize, W-4 adjustments, or any tax-related question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {
                    "type": "integer",
                    "description": "Specific tax year to retrieve (e.g. 2024). Omit to get all years."
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_crypto_gains",
        "description": "Get realized crypto capital gains and losses for a tax year. Returns FIFO-computed short-term vs long-term gains, total proceeds, cost basis, and open lot positions. Use when the user asks about crypto taxes, capital gains, Coinbase trades, or Schedule D reporting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Tax year (e.g. 2025). Defaults to 2025."}
            },
            "required": [],
        },
    },
    {
        "name": "get_upcoming_events",
        "description": "Get upcoming financial calendar events (tax deadlines, estimated tax payments, budget meetings, paydays, custom events). Use this proactively to notice and mention upcoming deadlines. Also useful when the user asks 'what's coming up?' or 'when is my next tax payment?'",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days ahead to look (default 30, max 90)"
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_ticker_quotes",
        "description": "Get current/recent prices for all tickers the user holds (crypto from Coinbase, equity/mutual funds from Yahoo Finance). Includes price, 24h change %, and when the quote was last fetched. Use this when the user asks about current prices, portfolio performance, or market movements.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_financial_news",
        "description": "Get recent financial news articles curated for the user's holdings and financial situation. Covers their crypto (BTC, ETH, SOL, etc.), equity funds (FXAIX), mortgage rates, and macro news affecting retirement accounts. Use when the user asks 'what's in the news?', about market news, or you want to proactively share relevant developments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Optional filter: 'crypto', 'equity', 'macro', or omit for all"},
                "limit": {"type": "integer", "description": "Max articles to return (default 10)"},
            },
            "required": [],
        },
    },
]


def _call_tool(name: str, inputs: dict, username: str = "") -> str:
    """Dispatch a tool call to the appropriate handler in sage_tools."""
    from api.sage_tools import TOOL_DISPATCH

    handler = TOOL_DISPATCH.get(name)
    if not handler:
        return f"Unknown tool: {name}"
    try:
        return handler(inputs, username)
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


def _is_url_safe(url: str) -> bool:
    """Block requests to private/internal networks (SSRF prevention)."""
    from urllib.parse import urlparse
    import ipaddress
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1", ""):
        return False
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass  # Not an IP literal — hostname is fine
    return True


def _fetch_page(url: str) -> str:
    if not _is_url_safe(url):
        return "Blocked: cannot fetch private/internal URLs"
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


def _truncate_history_tool_results(messages: list[dict], max_chars: int = 800) -> list[dict]:
    """
    Cap the content length of tool_result blocks in old history messages.

    Large tool responses (e.g. a get_budget_history call that returned 200 rows)
    get stored in the conversation history and then resent verbatim to the API on
    every subsequent turn. With a 20-message window and a couple of budget calls,
    this easily blows the 50K TPM rate limit.

    This function truncates any tool_result content in the history that exceeds
    max_chars, appending a "[truncated — use a tool to re-fetch if needed]" marker
    so Claude knows the data was cut. We apply this AFTER trim so only the messages
    that survive trimming are charged against the token budget.

    The most recent turn is NOT truncated — only messages that are already in
    history before the new user message is appended.
    """
    result = []
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_content = []
        changed = False
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(block.get("content"), str)
                and len(block["content"]) > max_chars
            ):
                truncated = block["content"][:max_chars] + " [truncated — use a tool to re-fetch if needed]"
                new_content.append({**block, "content": truncated})
                changed = True
            else:
                new_content.append(block)
        result.append({**msg, "content": new_content} if changed else msg)
    return result


def _trim_history(messages: list[dict], keep: int = 20) -> list[dict]:
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


def chat(messages: list[dict], user_message: str, attachments: list[dict] | None = None, username: str = "") -> tuple[str, list[dict]]:
    """
    Run one turn of conversation with Sage.
    Returns (sage_response_text, updated_messages).
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # Order matters: trim → truncate old tool results → sanitize → append new user message.
    # Truncating large tool_result content in history prevents token accumulation
    # across turns (a 200-row budget response resent on every turn quickly blows
    # the 50K TPM rate limit even after we fixed get_budget_history to aggregate).
    messages = _trim_history(list(messages))
    messages = _truncate_history_tool_results(messages)
    messages = _sanitize_messages(messages)

    # Build user content — plain string for text-only, list of blocks when
    # attachments are present (images go as vision blocks, text files as text).
    if attachments:
        content_blocks = []
        for att in attachments:
            if att.get("type") == "image":
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": att["media_type"],
                        "data": att["content"],
                    },
                })
            elif att.get("type") == "text":
                snippet = att["content"][:8000]  # cap per attachment to avoid blowing context
                truncation_note = " [truncated]" if att.get("truncated") else ""
                content_blocks.append({
                    "type": "text",
                    "text": f"[Attached file: {att['filename']}{truncation_note}]\n\n{snippet}",
                })
        if user_message:
            content_blocks.append({"type": "text", "text": user_message})
        messages = messages + [{"role": "user", "content": content_blocks}]
    else:
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
                    result = _call_tool(block.name, block.input, username=username)
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

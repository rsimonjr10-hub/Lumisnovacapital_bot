"""
LUMIS CAPITAL BOT
Powered by Claude AI + FMP Live Data
Telegram Bot for Market Intelligence
"""

import os
import re
import json
import random
import requests
import signal
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from skills import get_skill_prompt

# Set by SIGTERM/SIGINT — main loop checks this to exit cleanly
_shutdown = False

def _handle_signal(signum, frame):
    global _shutdown
    log.info(f"Signal {signum} received — shutting down cleanly")
    _shutdown = True

# ─────────────────────────────────────
# CONFIGURATION — SET IN RAILWAY ENV
# ─────────────────────────────────────
TELEGRAM_TOKEN      = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID             = os.environ.get("CHAT_ID")
FMP_API_KEY         = os.environ.get("FMP_API_KEY")
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY")
SERPER_API_KEY      = os.environ.get("SERPER_API_KEY")      # Google search via serper.dev
STARFIRE_BOT_TOKEN  = os.environ.get("STARFIRE_BOT_TOKEN")  # enables direct reply to starfire5_bot
OSIRIS_BOT_TOKEN    = os.environ.get("OSIRIS_BOT_TOKEN")    # enables direct reply to osiris_prime_bot

# Most intelligent Claude model
CLAUDE_MODEL = "claude-opus-4-8"

# Deduplication: track processed update IDs to prevent replay on restart/rolling deploy
_processed_updates: set = set()

# ─────────────────────────────────────
# LOGGING
# ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────
# PER-CHAT CONVERSATION MEMORY
# ─────────────────────────────────────
_conversation_history = {}  # {chat_id_str: [{"role": "user"|"assistant", "content": "..."}]}
_MAX_HISTORY_TURNS = 8      # keep last 8 exchanges (16 messages)

def _history_add(chat_id, role, content):
    cid = str(chat_id)
    if cid not in _conversation_history:
        _conversation_history[cid] = []
    _conversation_history[cid].append({"role": role, "content": content})
    max_msgs = _MAX_HISTORY_TURNS * 2
    if len(_conversation_history[cid]) > max_msgs:
        _conversation_history[cid] = _conversation_history[cid][-max_msgs:]

def _history_get(chat_id):
    return list(_conversation_history.get(str(chat_id), []))

def _history_clear(chat_id):
    _conversation_history.pop(str(chat_id), None)


# ─────────────────────────────────────
# WATCHLIST
# ─────────────────────────────────────
WATCHLIST = [
    "NOW", "META", "NVDA", "ASTS",
    "IREN", "NOK", "HOOD", "SOFI",
    "MU", "GOOGL", "IONQ", "QBTS"
]

# ─────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────
SYSTEM_PROMPT = """You are LUMIS.

LUMIS is a Financial Intelligence Platform serving as:
- Chief Investment Officer
- Research Director
- Financial Manager
- Portfolio Analyst
- Market Strategist
- Accounting Assistant

LUMIS does NOT execute trades.
LUMIS does NOT place orders.
LUMIS does NOT communicate directly with brokers.
OSIRIS executes. STARFIRE manages the user relationship.
LUMIS provides financial truth, research, analysis, and recommendations.

====================================================
PRIMARY MISSION
====================================================

Help the user make better financial decisions through rigorous research, continuous monitoring, and institutional-quality analysis.

Seek: Opportunity. Risk. Information asymmetry. Emerging trends. Capital flows. Market inefficiencies.

Be skeptical, evidence-driven, and thesis-focused. Challenge assumptions. Always provide bull and bear cases.

====================================================
CONVERSATION STYLE
====================================================

You remember the full context of this conversation — build on it. When someone asks a follow-up ("what about NVDA?", "and the bear case?", "how much should I size this?"), answer it in context of what was already discussed — never restart from zero.

Be direct, confident, and honest. Not salesy. Not hedging without substance. Talk like a sharp friend with a finance MBA and institutional experience — not a legal disclaimer machine.

If someone shares their financial situation, engage with it specifically. Give real numbers, real frameworks, real recommendations — not generic advice.

Challenge bad ideas. Push back on overleveraged sizing. Tell people what they need to hear, not what they want to hear.

====================================================
CORE SPECIALIZATION — SECTOR MASTERY & MARKET INTELLIGENCE
====================================================

You have deep, institutional-level knowledge of every sector. You know the winners, the losers, and where money is rotating — at all times.

SECTOR ROTATION INTELLIGENCE:
- Which sectors are seeing institutional inflows vs outflows
- Which are leading vs lagging the broader market
- Where smart money is positioning and why
- What the rotation pattern signals about the macro cycle (early cycle, mid cycle, late cycle, recession)
- Which sectors are historically strong in the current rate/inflation environment

SECTOR SUPPLY CHAIN MASTERY (second and third-order effects):
- AI Infrastructure: compute → GPUs → data centers → power demand → utilities → grid → cooling → semiconductors
- Energy: oil prices → exploration budgets → equipment makers → pipelines → refiners → utilities → grid
- Financials: yield curve → bank margins → credit quality → insurance float → capital markets activity
- Healthcare: FDA pipeline → biotech funding cycles → device demand → payer dynamics → pharmacy benefit
- Consumer: employment → wage growth → spending mix → retail → brands → supply chains → shipping
- Industrials: CapEx cycles → manufacturing PMI → logistics → construction → commodities → mining
- Real Estate: rate sensitivity → REITs → homebuilders → mortgage originators → title insurance
- Technology: AI spend → software adoption → cloud migration → cybersecurity → semiconductor demand
- Commodities: China demand → DXY → supply/demand balance → inflation pass-through → currency effects

For every sector you always know: who is winning now, who is losing now, what the catalyst is, how long the trade lasts, and when it reverses.

====================================================
RESEARCH COVERAGE
====================================================

Public Markets: Equities, ETFs, Options, Fixed Income, Commodities, Digital Assets
Private Markets: Venture Capital, Startup Funding, Private Equity, M&A, Strategic Partnerships, Infrastructure
Macro: Inflation, Interest Rates, Labor Markets, Credit Markets, Liquidity, Fiscal Policy, Monetary Policy

====================================================
SECTOR ENGINE
====================================================

Continuously evaluate all 11 sectors:
AI Infrastructure / Semiconductors / Utilities / Energy / Cybersecurity / Cloud Computing / Industrials / Financials / Healthcare / Consumer / Real Estate

For each sector rate: Momentum, Relative Strength vs SPY, Earnings Trend (beat/miss cycle), CapEx Trend, Institutional Positioning (over/underweight), Insider Activity, Valuation vs history, Key Risk.

Always have a current sector ranking: which sectors to overweight, which to underweight, and why.

====================================================
MARKET SENTIMENT ENGINE
====================================================

You continuously read market sentiment from multiple dimensions:

FEAR & GREED:
- VIX level and trend (below 15 = complacency, 15-25 = normal, above 25 = fear, above 40 = panic)
- Put/call ratio (above 1.0 = bearish sentiment, below 0.7 = bullish/complacent)
- CNN Fear & Greed Index direction
- AAII bull/bear survey extremes (contrarian signal at extremes)

BREADTH & INTERNALS:
- % of S&P 500 stocks above 50MA and 200MA
- Advance/decline line — confirming or diverging from index?
- New 52-week highs vs lows ratio
- Market breadth: is the rally narrow (concentrated) or broad (healthy)?

FLOW OF FUNDS:
- Equity fund inflows/outflows (retail vs institutional)
- Money market fund levels (high cash on sidelines = potential fuel)
- Options market positioning (gamma exposure, dealer hedging flows)
- Short interest trends across sectors

TECHNICAL SENTIMENT:
- SPY, QQQ, IWM relative performance (large cap vs small cap tells a story)
- High yield credit spreads (widening = risk-off signal)
- Dollar (DXY) strength vs risk assets
- Gold behavior (flight to safety or inflation hedge?)

When asked about sentiment or market feel, synthesize all of the above into a clear read:
BULLISH / CAUTIOUSLY BULLISH / NEUTRAL / CAUTIOUSLY BEARISH / BEARISH
...and explain exactly why.

====================================================
PORTFOLIO INTELLIGENCE
====================================================

Understand: position sizes, cost basis, exposure, sector allocation, cash levels, concentration, risk-adjusted returns.

Always answer: What is the largest risk? Largest opportunity? Where is concentration risk? What sectors are over/underweight? What is driving performance?

====================================================
ACCOUNTING FUNCTIONS
====================================================

Track: realized/unrealized gains, cost basis, dividends, income streams, expenses, subscription costs, monthly cash flow.

Generate: monthly financial reports, quarterly reviews, performance summaries, tax-preparation summaries.

====================================================
IDEA GENERATION FRAMEWORK
====================================================

Every investment idea must include:
1. Thesis
2. Supporting Evidence
3. Catalysts
4. Risks
5. Counterarguments
6. Time Horizon
7. Probability Assessment
8. Potential Upside
9. Potential Downside

Never provide a ticker without explaining why. Never provide a recommendation without discussing risks.

====================================================
TRADE & ANALYSIS STANDARDS
====================================================

On every trade idea or analysis:
- Always show BULL CASE and BEAR CASE — both, every time, no exceptions
- Always include position sizing for a $10,000 account
- Always include a stop loss level with rationale
- Separate trading view (days/weeks) from investing view (years/decades)
- Translate all financials to USD
- Show earnings surprise history before any options trade idea
- Never promise profits or guarantee returns
- Push back hard on overleveraged or reckless sizing

====================================================
RESEARCH STANDARDS
====================================================

Be data-driven. Prefer evidence over narratives. Identify what is known versus uncertain.
Distinguish: Facts / Assumptions / Forecasts / Opinions. State confidence levels. When uncertain, say so.

Monitor: earnings reports, insider transactions, institutional flows, analyst revisions, CapEx announcements, AI infrastructure news, data center projects, utility developments, regulatory changes, funding rounds, M&A activity. Surface important developments quickly.

====================================================
OUTPUT FORMATS
====================================================

For market ideas use:
<b>THESIS:</b> ...
<b>WHY NOW:</b> ...
<b>CATALYSTS:</b> ...
<b>RISKS:</b> ...
<b>TIME HORIZON:</b> ...
<b>CONFIDENCE:</b> ...
<b>BULL CASE:</b> ...
<b>BEAR CASE:</b> ...

For company research use:
BUSINESS OVERVIEW / COMPETITIVE ADVANTAGES / RISKS / FINANCIALS / VALUATION / CATALYSTS / THESIS / VERDICT

For portfolio reviews use:
PORTFOLIO HEALTH / TOP RISKS / TOP OPPORTUNITIES / CONCENTRATION ANALYSIS / SECTOR ANALYSIS / RECOMMENDED ACTIONS

====================================================
PERSONAL FINANCE & ACCOUNTING
====================================================

Income allocation: 50/30/20 rule, custom splits, emergency fund (3-6 months).
Debt payoff order: avalanche (highest rate first) vs snowball (smallest balance first).
Account priority: 401k match → HSA → IRA → taxable brokerage.
Tax efficiency: capital gains rates, tax-loss harvesting, retirement account advantages.
Explain financial statements (P&L, balance sheet, cash flow) in plain language.

====================================================
STRICT OUTPUT RULES
====================================================

NEVER output: "Data Disclosure", "Data Transparency", "Important Notice", "Disclaimer", "Live price feed not confirmed", "not confirmed in this session", "my knowledge cutoff", "I cannot access real-time", "as of my training", "based on my training data", "extrapolated", "verify all live data", "Verify current quote before acting", "figures below are based on the most recent data available"

NEVER add any footer, header, note, or caveat about data limitations or knowledge cutoffs.
NEVER apologize for data access — use the data provided and answer directly.
Live market data is injected into your context — treat it as current.
No emoji unless specifically requested.
FORMAT FOR TELEGRAM HTML ONLY: use <b>bold</b>, <i>italic</i> — NEVER markdown (**bold**, *italic*, ---), NEVER pipe tables (| col | col |), NEVER # headers.

====================================================
NETWORK STATUS
====================================================

LUMIS exists to think.
STARFIRE exists to coordinate.
OSIRIS exists to execute.

Both STARFIRE (/argus) and OSIRIS (/osiris) are live and active.

Always end financial analysis with: <i>Not financial advice. Always do your own research.</i>"""

# ─────────────────────────────────────
# TICKER VALIDATION HELPER
# ─────────────────────────────────────
def _valid_ticker(symbol):
    """Return True if symbol looks like a valid US ticker (1–5 uppercase letters)."""
    return bool(re.match(r'^[A-Z]{1,5}$', symbol))


# ─────────────────────────────────────
# TELEGRAM FUNCTIONS
# ─────────────────────────────────────
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=30)
        result = response.json()
        if not result.get("ok"):
            log.error(f"Telegram error: {result}")
        return result
    except Exception as e:
        log.error(f"Send message error: {e}")
        return None


def get_updates(offset=None, timeout=8):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": timeout}
    if offset:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=timeout + 5)
        return response.json()
    except Exception as e:
        log.error(f"Get updates error: {e}")
        return {"ok": False, "result": []}


# ─────────────────────────────────────
# FMP ERROR HELPER
# ─────────────────────────────────────
def _fmp_error_message(status_code, endpoint=""):
    """Return a user-friendly error string for common FMP HTTP errors."""
    label = f" for {endpoint}" if endpoint else ""
    if status_code == 401:
        return f"⚠️ FMP API key is invalid or expired (401){label}. Contact admin."
    if status_code == 403:
        return f"⚠️ FMP API access denied (403){label}. Check your plan limits."
    if status_code == 404:
        return f"⚠️ Ticker or endpoint not found (404){label}. Check the symbol."
    if status_code == 429:
        return f"⚠️ FMP rate limit hit (429){label}. Please wait a moment and try again."
    return f"⚠️ FMP API error ({status_code}){label}. Try again shortly."


# ─────────────────────────────────────
# FMP FUNCTIONS
# ─────────────────────────────────────
def get_stock_quote(symbol):
    url = f"https://fmp-data-api-production.up.railway.app/quote/{symbol}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            log.error(f"fmp-data-api returned {response.status_code} for quote/{symbol}: {response.text}")
            return {"_error": _fmp_error_message(response.status_code, f"quote/{symbol}")}
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        log.warning(f"fmp-data-api quote/{symbol} returned empty or unexpected payload: {data}")
        return None
    except requests.exceptions.Timeout:
        log.error(f"fmp-data-api quote timeout for {symbol}")
        return {"_error": f"⚠️ FMP timed out fetching quote for {symbol.upper()}. Try again shortly."}
    except Exception as e:
        log.error(f"fmp-data-api quote error for {symbol}: {e}")
        return None



def get_treasury_rates():
    url = "https://financialmodelingprep.com/stable/treasury-rates"
    params = {"apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for treasury: {response.text}")
            return {"_error": _fmp_error_message(response.status_code, "treasury")}
        data = response.json()
        if data and len(data) > 0:
            return data[0]
        log.warning(f"FMP treasury returned empty or unexpected payload: {data}")
        return None
    except requests.exceptions.Timeout:
        log.error("FMP treasury timeout")
        return {"_error": "⚠️ FMP timed out fetching treasury rates. Try again shortly."}
    except Exception as e:
        log.error(f"FMP treasury error: {e}")
        return None


def get_earnings_calendar():
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    url = "https://financialmodelingprep.com/stable/earnings-calendar"
    params = {"from": today, "to": end, "apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for earnings-calendar: {response.text}")
            return {"_error": _fmp_error_message(response.status_code, "earnings-calendar")}
        return response.json()
    except requests.exceptions.Timeout:
        log.error("FMP earnings calendar timeout")
        return {"_error": "⚠️ FMP timed out fetching earnings calendar. Try again shortly."}
    except Exception as e:
        log.error(f"FMP earnings error: {e}")
        return []


def get_stock_news():
    tickers = ",".join(WATCHLIST[:6])
    url = "https://financialmodelingprep.com/stable/stock-news"
    params = {"tickers": tickers, "limit": 10, "apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for stock-news: {response.text}")
            return {"_error": _fmp_error_message(response.status_code, "stock-news")}
        return response.json()
    except requests.exceptions.Timeout:
        log.error("FMP stock news timeout")
        return {"_error": "⚠️ FMP timed out fetching news. Try again shortly."}
    except Exception as e:
        log.error(f"FMP news error: {e}")
        return []


def get_analyst_consensus(symbol):
    url = "https://financialmodelingprep.com/stable/price-target-consensus"
    params = {"symbol": symbol, "apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for price-target-consensus/{symbol}: {response.text}")
            return {"_error": _fmp_error_message(response.status_code, f"price-target-consensus/{symbol}")}
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        log.warning(f"FMP price-target-consensus/{symbol} returned empty or unexpected payload: {data}")
        return None
    except requests.exceptions.Timeout:
        log.error(f"FMP consensus timeout for {symbol}")
        return {"_error": f"⚠️ FMP timed out fetching analyst consensus for {symbol.upper()}."}
    except Exception as e:
        log.error(f"FMP consensus error: {e}")
        return None



# ─────────────────────────────────────
# WEB SEARCH (Google via Serper)
# ─────────────────────────────────────
def web_search(query, num_results=5):
    """Google search via serper.dev. Returns formatted snippet string or None."""
    if not SERPER_API_KEY:
        return None
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": num_results}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            data = r.json()
            snippets = []
            for item in data.get("organic", [])[:num_results]:
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                if title or snippet:
                    snippets.append(f"{title}: {snippet}")
            # Also include answer box if present
            if data.get("answerBox"):
                ab = data["answerBox"]
                answer = ab.get("answer") or ab.get("snippet") or ""
                if answer:
                    snippets.insert(0, f"[Top Result] {answer}")
            return "\n".join(snippets) if snippets else None
        log.warning(f"Serper search returned {r.status_code} for query: {query!r}")
        return None
    except Exception as e:
        log.error(f"Web search error: {e}")
        return None


# ─────────────────────────────────────
# FMP-DATA-API (DIRECT PRICE LOOKUP)
# ─────────────────────────────────────
def get_stock_price_only(symbol):
    """
    Fetch a live quote from the fmp-data-api service and return a
    minimal formatted string — ticker, price, and change % only.
    No Claude processing, no disclaimers.
    """
    url = f"https://fmp-data-api-production.up.railway.app/quote/{symbol.upper()}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            log.error(f"fmp-data-api returned {response.status_code} for {symbol}: {response.text}")
            return f"⚠️ Could not fetch price for {symbol.upper()}. Try again shortly."
        data = response.json()
        # Accept both a list payload and a plain dict payload
        quote = data[0] if isinstance(data, list) and len(data) > 0 else data
        if not quote:
            return f"⚠️ No data returned for {symbol.upper()}."
        price  = quote.get("price", quote.get("regularMarketPrice", None))
        change = quote.get("changePercentage", quote.get("changesPercentage", quote.get("change_percentage", None)))
        if price is None:
            return f"⚠️ Price unavailable for {symbol.upper()}."
        arrow = "↑" if (change or 0) >= 0 else "↓"
        change_str = f"{change:+.2f}%" if change is not None else "N/A"
        return f"{symbol.upper()}: ${float(price):.2f} {arrow} {change_str}"
    except Exception as e:
        log.error(f"fmp-data-api price error for {symbol}: {e}")
        return f"⚠️ Error fetching price for {symbol.upper()}."


# ─────────────────────────────────────
# CLAUDE AI
# ─────────────────────────────────────
def ask_claude(prompt, context="", skill_prompt=None, history=None, max_tokens=1500):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    system = skill_prompt if skill_prompt else SYSTEM_PROMPT
    full_prompt = f"{context}\n\n{prompt}" if context else prompt

    # Build message list: prior history + new user message
    messages = list(history) if history else []
    messages.append({"role": "user", "content": full_prompt})

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code in (529, 503):
            log.warning(f"Anthropic API overloaded ({response.status_code}), retrying...")
            payload["max_tokens"] = max(500, max_tokens // 2)
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        if response.status_code != 200:
            log.error(f"Anthropic API returned {response.status_code}: {response.text}")
            if response.status_code == 401:
                return "⚠️ Lumis Nova: API key invalid or expired. Contact admin."
            if response.status_code == 429:
                return "⚠️ Lumis Nova is rate-limited. Please wait 30 seconds and try again."
            return "⚠️ Lumis Nova is temporarily unavailable. Try again in a moment."
        data = response.json()
        if "content" in data and len(data["content"]) > 0:
            return data["content"][0]["text"]
        log.error(f"Anthropic API returned unexpected payload: {data}")
        return "⚠️ Unable to get response from Lumis Nova. Try again shortly."
    except requests.exceptions.Timeout:
        log.error("Claude API timeout — retrying with reduced tokens")
        try:
            payload["max_tokens"] = 600
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            if response.status_code == 200:
                data = response.json()
                if "content" in data and len(data["content"]) > 0:
                    return data["content"][0]["text"]
        except Exception as retry_err:
            log.error(f"Claude API retry failed: {retry_err}")
        return "⚠️ Lumis Nova timed out. The request took too long — please try again."
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return "⚠️ Lumis Nova is temporarily unavailable. Try again in a moment."


# ─────────────────────────────────────
# COMMAND HANDLERS
# ─────────────────────────────────────
def handle_start(chat_id):
    _history_clear(chat_id)
    msg = """<b>LUMIS CAPITAL — LUMISNOVA</b>
Powered by Claude Opus AI + FMP Live Data + Web Search

<b>Just talk to me.</b>
Ask anything — market analysis, stock picks, personal finance, portfolio review, accounting questions, wealth strategy. I remember our full conversation.

<b>Examples:</b>
"What do you think about NVDA right now?"
"Help me build a $50K portfolio"
"Explain covered calls to me"
"What's the best way to pay off $30K in debt?"
"Is the market going to crash?"

<b>Or use a command:</b>
/news /macro /earnings /scout /watchlist /yields
/sentiment /rotation /premarket /momentum
/full /opinion /invest /insider /risk /compare
/dividend /sector /technical /options /crypto
/etf /squeeze /ipo /fx /commodities /portfolio
/compounding /help

<b>Network:</b> STARFIRE (/argus) · OSIRIS (/osiris) — ONLINE

<i>Not financial advice. Always do your own research.</i>"""
    send_message(chat_id, msg)


def handle_watchlist(chat_id):
    send_message(chat_id, "Pulling live prices...")
    lines = ["<b>LUMIS CAPITAL WATCHLIST</b>"]
    lines.append(f"{datetime.now().strftime('%b %d %Y | %I:%M %p ET')}\n")

    price_context = ""
    fmp_error_shown = False
    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote and "_error" in quote:
            if not fmp_error_shown:
                lines.append(quote["_error"])
                fmp_error_shown = True
            lines.append(f"  {symbol}: Unavailable")
        elif quote:
            price = quote.get("price", 0)
            change = quote.get("changePercentage", 0)
            arrow = "▲" if change >= 0 else "▼"
            lines.append(f"{arrow} <b>{symbol}</b>: ${price:.2f} ({change:+.2f}%)")
            price_context += f"{symbol}: ${price:.2f} ({change:+.2f}%)\n"
        else:
            lines.append(f"  {symbol}: Unavailable")

    lines.append("\n<i>Source: FMP Live</i>")

    if price_context:
        prompt = f"""Brief market read on today's watchlist price action.
Today: {datetime.now().strftime('%B %d, %Y')}
{price_context}"""
        skill_prompt = get_skill_prompt("/watchlist")
        commentary = ask_claude(prompt, skill_prompt=skill_prompt)
        lines.append(f"\n<b>LUMIS NOVA READ</b>\n\n{commentary}")

    send_message(chat_id, "\n".join(lines))


def handle_yields(chat_id):
    send_message(chat_id, "Pulling yield curve...")
    rates = get_treasury_rates()
    if rates and "_error" in rates:
        send_message(chat_id, rates["_error"])
        return
    if rates:
        lines = [
            f"<b>TREASURY YIELD CURVE</b>",
            f"{datetime.now().strftime('%b %d %Y')}\n",
            f"3-month: {rates.get('month3', 'N/A')}%",
            f"6-month: {rates.get('month6', 'N/A')}%",
            f"2-year:  {rates.get('year2', 'N/A')}%",
            f"5-year:  {rates.get('year5', 'N/A')}%",
            f"10-year: {rates.get('year10', 'N/A')}% <- KEY",
            f"30-year: {rates.get('year30', 'N/A')}%",
            "\n<i>Source: FMP Live</i>",
        ]
        context = (
            f"2yr: {rates.get('year2','N/A')}% | "
            f"10yr: {rates.get('year10','N/A')}% | "
            f"30yr: {rates.get('year30','N/A')}%"
        )
        prompt = f"""Analyze the current Treasury yield curve and what it means for equity markets.
Today: {datetime.now().strftime('%B %d, %Y')}
{context}"""
        skill_prompt = get_skill_prompt("/yields")
        commentary = ask_claude(prompt, skill_prompt=skill_prompt)
        lines.append(f"\n<b>YIELD CURVE ANALYSIS</b>\n\n{commentary}")
        send_message(chat_id, "\n".join(lines))
    else:
        send_message(chat_id, "Unable to fetch yield data. Try again shortly.")


def handle_earnings(chat_id):
    send_message(chat_id, "Pulling earnings calendar...")
    major = [
        "AAPL","MSFT","NVDA","META","GOOGL","AMZN","TSLA",
        "NOW","MU","AVGO","CRM","DELL","COST","HOOD","SOFI",
        "IREN","ASTS","NOK","IONQ","QBTS","AMD","INTC","MRVL",
        "ANET","PANW","SNOW","ADSK"
    ]
    earnings = get_earnings_calendar()
    if isinstance(earnings, dict) and "_error" in earnings:
        send_message(chat_id, earnings["_error"])
        return
    filtered = [e for e in earnings if e.get("symbol") in major]

    if filtered:
        lines = ["<b>UPCOMING EARNINGS (7 days)</b>\n"]
        earnings_context = ""
        for e in sorted(filtered, key=lambda x: x.get("date", ""))[:10]:
            symbol = e.get("symbol", "")
            date = e.get("date", "")
            eps = e.get("epsEstimated", "N/A")
            rev = e.get("revenueEstimated", 0)
            rev_b = f"${rev/1e9:.2f}B" if rev else "N/A"
            lines.append(f"<b>{symbol}</b> | {date} — EPS: {eps} | Rev: {rev_b}")
            earnings_context += f"{symbol} | {date} | EPS est: {eps} | Rev est: {rev_b}\n"
        lines.append("\n<i>Source: FMP Live</i>")
        prompt = f"""Analyze the upcoming earnings events and what they mean for traders.
Today: {datetime.now().strftime('%B %d, %Y')}
{earnings_context}"""
        skill_prompt = get_skill_prompt("/earnings")
        commentary = ask_claude(prompt, skill_prompt=skill_prompt)
        lines.append(f"\n<b>EARNINGS PREVIEW</b>\n\n{commentary}")
        send_message(chat_id, "\n".join(lines))
    else:
        send_message(chat_id, "No major earnings in next 7 days.")


def handle_news(chat_id):
    send_message(chat_id, "Pulling market news...")
    news = get_stock_news()
    if isinstance(news, dict) and "_error" in news:
        send_message(chat_id, news["_error"])
        return
    if news:
        context = "Latest market news:\n"
        for item in news[:8]:
            context += f"- [{item.get('symbol','')}] {item.get('title','')}\n"
        # Enrich with web search headlines
        search = web_search(f"stock market news today {datetime.now().strftime('%B %d %Y')}")
        if search:
            context += f"\nWeb headlines:\n{search}"
        prompt = f"""Give a morning market intelligence brief.
Top 5 stories. 2-3 sentences per story. Format for Telegram HTML.
Today: {datetime.now().strftime('%B %d, %Y')}
{context}"""
        skill_prompt = get_skill_prompt("/news")
        response = ask_claude(prompt, skill_prompt=skill_prompt)
        header = f"<b>LUMIS CAPITAL NEWS</b>\n{datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n"
        send_message(chat_id, header + response)
    else:
        send_message(chat_id, "⚠️ Unable to fetch news. Try again shortly.")


def handle_macro(chat_id):
    send_message(chat_id, "Pulling macro data...")
    rates = get_treasury_rates()
    context = ""
    if rates and "_error" in rates:
        context = f"(Live yield data unavailable: {rates['_error']})"
        log.warning(f"Macro handler: FMP treasury error — {rates['_error']}")
    elif rates:
        context = f"10yr: {rates.get('year10')}% | 2yr: {rates.get('year2')}% | 30yr: {rates.get('year30')}%"
    # Enrich with Fed/macro news
    search = web_search(f"Federal Reserve macro economic news {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nMacro web data:\n{search}"
    prompt = f"""Macro brief for {datetime.now().strftime('%B %d, %Y')}.
Cover: yield curve, Fed outlook, key events today, oil/geopolitical impact.
Format for Telegram HTML. Keep it actionable. {context}"""
    skill_prompt = get_skill_prompt("/macro")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
    header = f"<b>MACRO BRIEF</b>\n{datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n"
    send_message(chat_id, header + response)


def handle_full(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /full NOW\nExample: /full NVDA")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. NVDA, AAPL).")
        return
    send_message(chat_id, f"Running full analysis on {symbol}...")
    quote = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    context = f"${symbol} live data:\n"
    data_warnings = []
    if quote and "_error" in quote:
        data_warnings.append(quote["_error"])
    elif quote:
        context += f"Price: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
        context += f"52wk High: ${quote.get('yearHigh','N/A')} | Low: ${quote.get('yearLow','N/A')}\n"
        context += f"Market Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    if consensus and "_error" in consensus:
        data_warnings.append(consensus["_error"])
    elif consensus:
        context += f"Analyst consensus PT: ${consensus.get('targetConsensus','N/A')}\n"
        context += f"High PT: ${consensus.get('targetHigh','N/A')} | Low: ${consensus.get('targetLow','N/A')}\n"
    if data_warnings:
        send_message(chat_id, "\n".join(data_warnings) + "\n<i>Proceeding with available data...</i>")
    # Enrich with web search
    search = web_search(f"{symbol} stock analysis news {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nRecent web data:\n{search}"
    prompt = f"""Full stock analysis for ${symbol}.
Cover: business model, moat, top 3 competitors, catalyst,
bull case, bear case, valuation, entry strategy, stop loss, sizing.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/full")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} ANALYSIS</b>\n\n" + response)


def handle_opinion(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /opinion NOW\nExample: /opinion ASTS")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. ASTS, META).")
        return
    quote = get_stock_quote(symbol)
    context = ""
    if quote and "_error" in quote:
        log.warning(f"Opinion handler: FMP quote error for {symbol} — {quote['_error']}")
    elif quote:
        context = f"${symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)"
    prompt = f"Quick honest opinion on ${symbol}. Buy/sell/hold and why. 4-5 sentences. One key risk. One key catalyst."
    skill_prompt = get_skill_prompt("/opinion")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} QUICK TAKE</b>\n\n" + response)


_SCOUT_SECTORS = [
    "energy", "healthcare", "financials", "industrials",
    "consumer staples", "utilities", "materials", "real estate",
    "consumer discretionary", "biotech", "defense", "clean energy",
    "semiconductors (small/mid cap only)", "software (not mega-cap)",
    "retail", "transportation", "commodities", "insurance",
]

def handle_scout(chat_id):
    send_message(chat_id, "Running weekly scout...")

    # Randomly pick 2 sectors and a theme to force different stocks every call
    focus_sectors = random.sample(_SCOUT_SECTORS, 2)
    exclude = ", ".join(WATCHLIST)

    context = f"Weekly scout {datetime.now().strftime('%B %d, %Y')}\n"
    for symbol in WATCHLIST[:6]:
        quote = get_stock_quote(symbol)
        if quote and "_error" not in quote:
            context += f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"

    prompt = f"""Pick 3 FRESH stock plays for THIS week. Every call must produce completely different picks.

STRICT RULES:
- Do NOT pick any of these: {exclude}
- At least 2 picks must come from these sectors this run: {', '.join(focus_sectors)}
- All 3 picks must be from different industries
- No mega-cap tech defaults — find names that are actually moving this week

For each of the 3 picks:
1. Ticker + company name + sector
2. Why THIS week specifically — the exact catalyst or setup
3. Bull case: what goes right, upside target
4. Bear case: what goes wrong, stop loss level
5. Entry range, position size for a $10K account

Today: {datetime.now().strftime('%B %d, %Y')}"""

    skill_prompt = get_skill_prompt("/scout")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>WEEKLY SCOUT</b>\n{datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_invest(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /invest GOOGL\nExample: /invest MSFT")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. GOOGL, MSFT).")
        return
    send_message(chat_id, f"Running long-term analysis on {symbol}...")
    quote = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    context = ""
    data_warnings = []
    if quote and "_error" in quote:
        data_warnings.append(quote["_error"])
    elif quote:
        context = f"${symbol}: ${quote.get('price','N/A')} | Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    if consensus and "_error" in consensus:
        data_warnings.append(consensus["_error"])
    elif consensus:
        context += f"Consensus PT: ${consensus.get('targetConsensus','N/A')}\n"
    if data_warnings:
        send_message(chat_id, "\n".join(data_warnings) + "\n<i>Proceeding with available data...</i>")
    prompt = f"""Long-term investing analysis for ${symbol}.
Cover: business quality, moat, 5yr growth potential,
management signals, covered call income potential,
compounding scenario ($10K over 5yr/10yr),
how to build the position, stop loss.
Think years not weeks."""
    skill_prompt = get_skill_prompt("/invest")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} INVESTING VIEW</b>\n\n" + response)


def handle_insider(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /insider NOW\nExample: /insider NVDA")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. NOW, NVDA).")
        return
    send_message(chat_id, f"Checking insider activity for {symbol}...")
    prompt = f"""Check insider trading activity for ${symbol}.
Cover: recent buys, recent sells, net sentiment,
CEO ownership %, what the activity signals.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/insider")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} INSIDER ACTIVITY</b>\n\n" + response)


def handle_risk(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /risk NOW\nExample: /risk ASTS")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. NOW, ASTS).")
        return
    send_message(chat_id, f"Running risk check for {symbol}...")
    quote = get_stock_quote(symbol)
    context = ""
    if quote and "_error" in quote:
        log.warning(f"Risk handler: FMP quote error for {symbol} — {quote['_error']}")
    elif quote:
        context = f"${symbol}: ${quote.get('price','N/A')}"
    prompt = f"""Risk check for a position in ${symbol}.
Cover: appropriate sizing for a $10K account,
max loss at stop, correlation risk, Kelly criterion suggestion.
Be honest. Push back if sizing seems aggressive."""
    skill_prompt = get_skill_prompt("/risk")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} RISK CHECK</b>\n\n" + response)


def handle_compounding(chat_id):
    prompt = """Show compounding math for $10,000 invested.
At 10%, 15%, 20%, 25% annual return — value at 5yr, 10yr, 20yr.
Then show covered call income overlay: $300/month reinvested.
Make it real and motivating."""
    skill_prompt = get_skill_prompt("/compounding")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
    send_message(chat_id, "<b>COMPOUNDING MATH</b>\n\n" + response)


def handle_help(chat_id):
    msg = """<b>LUMIS CAPITAL — LUMISNOVA</b>
<i>Full conversational AI — just talk to me, or use a command.</i>

<b>Market Intelligence:</b>
/news — Top market stories + web headlines
/macro — Macro brief + Fed outlook
/earnings — Upcoming earnings calendar
/yields — Treasury yield curve
/premarket — Pre-market brief + futures

<b>Stock Research:</b>
/full [TICKER] — Deep analysis: moat, valuation, bull/bear
/opinion [TICKER] — Quick honest take
/technical [TICKER] — Chart analysis, RSI, MACD, levels
/options [TICKER] — Options flow, IV, best strategies
/insider [TICKER] — Insider buying/selling
/risk [TICKER] — Position risk check + sizing
/squeeze [TICKER] — Short squeeze potential
/ipo [TICKER] — IPO analysis

<b>Comparison & Sectors:</b>
/scout — 3 fresh weekly picks (rotates sectors)
/sector [SECTOR] — Sector deep dive
/compare [T1] [T2] — Head-to-head comparison
/momentum — Top momentum plays from watchlist

<b>Sentiment & Rotation:</b>
/sentiment — Full market sentiment read (VIX, breadth, flows, verdict)
/rotation — Sector rotation scorecard + where money is moving

<b>Macro & Alternative Markets:</b>
/crypto [SYMBOL] — Crypto analysis (default: BTC)
/fx [PAIR] — Forex + DXY analysis
/commodities — Oil, gold, copper, ag overview
/etf [TICKER] — ETF holdings, flows, analysis

<b>Investing & Wealth:</b>
/invest [TICKER] — Long-term analysis + DCA strategy
/dividend [TICKER] — Dividend sustainability + income math
/compounding — Wealth building compound math
/portfolio [ALLOCATION] — Portfolio review + rebalancing

<b>Live Data:</b>
/watchlist — Live prices for all watchlist names
/price [TICKER] — Live quote (instant, no AI)

<b>System:</b>
/test — Check API connections

<b>Network:</b> STARFIRE (/argus) · OSIRIS (/osiris)

<i>Powered by Claude Opus + FMP Live Data + Web Search</i>
<i>Not financial advice. Always do your own research.</i>"""
    send_message(chat_id, msg)


def handle_sector(chat_id, sector):
    if not sector:
        send_message(chat_id, "❌ Usage: /sector tech\nExamples: /sector energy | /sector healthcare | /sector fintech")
        return
    send_message(chat_id, f"Analyzing {sector} sector...")
    prompt = f"""Sector analysis for the {sector} sector.
Today: {datetime.now().strftime('%B %d, %Y')}
Cover: key drivers, top stocks, ETF performance, bull case, bear case, positioning."""
    skill_prompt = get_skill_prompt("/sector")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{sector.upper()} SECTOR ANALYSIS</b>\n\n" + response)


def handle_compare(chat_id, ticker1, ticker2):
    if not ticker1 or not ticker2:
        send_message(chat_id, "❌ Usage: /compare NVDA AMD\nProvide exactly two tickers separated by a space.")
        return
    ticker1 = ticker1.strip().upper()
    ticker2 = ticker2.strip().upper()
    if not _valid_ticker(ticker1):
        send_message(chat_id, f"❌ Invalid ticker: <b>{ticker1}</b>. Use 1–5 uppercase letters.")
        return
    if not _valid_ticker(ticker2):
        send_message(chat_id, f"❌ Invalid ticker: <b>{ticker2}</b>. Use 1–5 uppercase letters.")
        return
    send_message(chat_id, f"Comparing {ticker1} vs {ticker2}...")
    quote1 = get_stock_quote(ticker1)
    quote2 = get_stock_quote(ticker2)
    context = ""
    data_warnings = []
    if quote1 and "_error" in quote1:
        data_warnings.append(quote1["_error"])
    elif quote1:
        context += f"${ticker1}: ${quote1.get('price','N/A')} | Cap: ${quote1.get('marketCap',0)/1e9:.2f}B\n"
    if quote2 and "_error" in quote2:
        data_warnings.append(quote2["_error"])
    elif quote2:
        context += f"${ticker2}: ${quote2.get('price','N/A')} | Cap: ${quote2.get('marketCap',0)/1e9:.2f}B\n"
    if data_warnings:
        send_message(chat_id, "\n".join(data_warnings) + "\n<i>Proceeding with available data...</i>")
    prompt = f"""Head-to-head comparison: ${ticker1} vs ${ticker2}.
Cover: business model, valuation, growth, moat, bull case and bear case for each, verdict.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/compare")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{ticker1} vs {ticker2}</b>\n\n" + response)


def handle_dividend(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /dividend AAPL\nExample: /dividend KO")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. AAPL, KO).")
        return
    send_message(chat_id, f"Analyzing {symbol} dividend...")
    quote = get_stock_quote(symbol)
    context = ""
    if quote and "_error" in quote:
        log.warning(f"Dividend handler: FMP quote error for {symbol} — {quote['_error']}")
    elif quote:
        context = f"${symbol}: ${quote.get('price','N/A')} | Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    prompt = f"""Dividend analysis for ${symbol}.
Cover: yield, payout history, sustainability, FCF coverage, bull case, bear case, income scenario.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/dividend")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} DIVIDEND ANALYSIS</b>\n\n" + response)


def handle_momentum(chat_id):
    send_message(chat_id, "Scanning for momentum plays...")
    context = f"Momentum scan {datetime.now().strftime('%B %d, %Y')}\n"
    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote and "_error" not in quote:
            context += f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
    prompt = f"""Identify the top momentum plays from the watchlist.
Cover: price action, key levels, bull case (continuation), bear case (reversal), entry strategy.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/momentum")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>MOMENTUM PLAYS</b>\n{datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_portfolio(chat_id, allocation):
    if not allocation:
        send_message(chat_id, "❌ Usage: /portfolio NVDA:30 AAPL:20 CASH:50\nList holdings as TICKER:PERCENT separated by spaces.")
        return
    send_message(chat_id, "Reviewing your portfolio allocation...")
    prompt = f"""Portfolio review for the following allocation: {allocation}
Cover: composition, diversification, risk assessment, bull case, bear case, rebalancing suggestions.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/portfolio")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
    send_message(chat_id, "<b>PORTFOLIO REVIEW</b>\n\n" + response)


def handle_price(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /price NVDA\nExample: /price AAPL")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. NVDA, AAPL).")
        return
    result = get_stock_price_only(symbol)
    send_message(chat_id, result)


def handle_test(chat_id):
    """Validate all three API connections and report status."""
    send_message(chat_id, "<b>API CONNECTION TEST</b>\nChecking FMP, Claude, and Telegram...\n")
    results = []

    # ── 1. Telegram ──────────────────────────────────────────────
    results.append("✅ <b>Telegram</b>: Connected (you received this message)")

    # ── 2. FMP (via fmp-data-api proxy) ─────────────────────────
    try:
        url = "https://fmp-data-api-production.up.railway.app/quote/AAPL"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                price = data[0].get("price", "N/A")
                results.append(f"✅ <b>FMP API</b>: Connected — AAPL ${price}")
            else:
                results.append("⚠️ <b>FMP API</b>: Connected but returned empty data")
        elif resp.status_code == 401:
            results.append("❌ <b>FMP API</b>: Invalid or expired API key (401)")
        elif resp.status_code == 403:
            results.append("❌ <b>FMP API</b>: Access denied — check plan limits (403)")
        elif resp.status_code == 429:
            results.append("⚠️ <b>FMP API</b>: Rate limited (429) — try again shortly")
        else:
            results.append(f"❌ <b>FMP API</b>: Error {resp.status_code}")
    except requests.exceptions.Timeout:
        results.append("❌ <b>FMP API</b>: Timeout — no response within 10s")
    except Exception as e:
        results.append(f"❌ <b>FMP API</b>: Exception — {e}")


    # ── 3. Claude / Anthropic ────────────────────────────────────
    try:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": 20,
            "messages": [{"role": "user", "content": "Reply with OK"}]
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            results.append(f"✅ <b>Claude API</b>: Connected — {CLAUDE_MODEL} online")
        elif resp.status_code == 401:
            results.append("❌ <b>Claude API</b>: Invalid or expired API key (401)")
        elif resp.status_code == 429:
            results.append("⚠️ <b>Claude API</b>: Rate limited (429) — try again shortly")
        elif resp.status_code in (529, 503):
            results.append("⚠️ <b>Claude API</b>: Overloaded — try again in a moment")
        else:
            results.append(f"❌ <b>Claude API</b>: Error {resp.status_code}")
    except requests.exceptions.Timeout:
        results.append("❌ <b>Claude API</b>: Timeout — no response within 20s")
    except Exception as e:
        results.append(f"❌ <b>Claude API</b>: Exception — {e}")

    # ── 4. Web Search ────────────────────────────────────────────
    if SERPER_API_KEY:
        test_search = web_search("S&P 500", num_results=1)
        if test_search:
            results.append("✅ <b>Web Search</b>: Connected — Serper/Google active")
        else:
            results.append("⚠️ <b>Web Search</b>: Serper key set but returned no results")
    else:
        results.append("⚠️ <b>Web Search</b>: SERPER_API_KEY not set — add to Railway env")

    status_line = "✅ All systems operational" if all(r.startswith("✅") for r in results) else "⚠️ One or more issues detected"
    msg = "\n".join(results) + f"\n\n{status_line}\n{datetime.now().strftime('%b %d %Y | %I:%M %p ET')}"
    send_message(chat_id, msg)


# ─────────────────────────────────────
# NEW RESEARCH COMMAND HANDLERS
# ─────────────────────────────────────
def handle_technical(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /technical NVDA")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>.")
        return
    send_message(chat_id, f"Running technical analysis on {symbol}...")
    quote = get_stock_quote(symbol)
    context = ""
    if quote and "_error" not in quote:
        context = (f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
                   f"52wk High: ${quote.get('yearHigh','N/A')} | Low: ${quote.get('yearLow','N/A')}")
    search = web_search(f"{symbol} technical analysis chart RSI MACD {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nRecent technical data:\n{search}"
    prompt = (f"Technical analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: trend direction, key support/resistance, RSI, MACD, moving averages, "
              f"chart pattern if any, volume analysis. Bull case (continuation), bear case (reversal). "
              f"Entry levels, stop loss, price targets.")
    skill_prompt = get_skill_prompt("/technical")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} TECHNICAL ANALYSIS</b>\n\n" + response)


def handle_options(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /options NVDA")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>.")
        return
    send_message(chat_id, f"Analyzing options activity for {symbol}...")
    quote = get_stock_quote(symbol)
    context = ""
    if quote and "_error" not in quote:
        context = f"{symbol}: ${quote.get('price','N/A')}"
    search = web_search(f"{symbol} options flow unusual activity IV {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nOptions data:\n{search}"
    prompt = (f"Options analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: implied volatility (current vs historical), key strikes, put/call ratio, "
              f"unusual activity if any, best options strategies for bull/bear scenarios, "
              f"expected move, Greeks overview. Real risk/reward.")
    skill_prompt = get_skill_prompt("/options")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} OPTIONS ANALYSIS</b>\n\n" + response)


def handle_crypto(chat_id, symbol):
    if not symbol:
        symbol = "BTC"
    symbol = symbol.strip().upper()
    send_message(chat_id, f"Pulling crypto analysis for {symbol}...")
    search = web_search(f"{symbol} crypto price analysis {datetime.now().strftime('%B %d %Y')}")
    context = f"Crypto: {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}"
    if search:
        context += f"\nCrypto data:\n{search}"
    prompt = (f"Crypto analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: price action, key support/resistance, on-chain signals if relevant, "
              f"macro crypto environment, bull case, bear case, entry range, stop loss. "
              f"Also cover BTC dominance and overall crypto market sentiment.")
    skill_prompt = get_skill_prompt("/crypto")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} CRYPTO ANALYSIS</b>\n\n" + response)


def handle_etf(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /etf QQQ\nExamples: /etf SPY | /etf ARKK | /etf GLD")
        return
    symbol = symbol.strip().upper()
    send_message(chat_id, f"Analyzing ETF {symbol}...")
    quote = get_stock_quote(symbol)
    context = ""
    if quote and "_error" not in quote:
        context = f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)"
    search = web_search(f"{symbol} ETF holdings flows analysis {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nETF data:\n{search}"
    prompt = (f"ETF analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: what this ETF tracks, top holdings, expense ratio, fund flows, "
              f"performance vs benchmark, bull case (sector/theme upside), bear case (risks), "
              f"who should own this and why.")
    skill_prompt = get_skill_prompt("/etf")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} ETF ANALYSIS</b>\n\n" + response)


def handle_squeeze(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /squeeze GME\nExample: /squeeze BBBY")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>.")
        return
    send_message(chat_id, f"Checking short squeeze potential for {symbol}...")
    quote = get_stock_quote(symbol)
    context = ""
    if quote and "_error" not in quote:
        context = f"{symbol}: ${quote.get('price','N/A')}"
    search = web_search(f"{symbol} short interest squeeze potential {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nSqueeze data:\n{search}"
    prompt = (f"Short squeeze analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: short interest %, days to cover, float size, borrow rate, "
              f"recent price action, squeeze trigger levels, bull case (squeeze scenario + target), "
              f"bear case (short thesis wins), risk/reward, position sizing.")
    skill_prompt = get_skill_prompt("/squeeze")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} SQUEEZE ANALYSIS</b>\n\n" + response)


def handle_ipo(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /ipo TICKER or /ipo upcoming")
        return
    send_message(chat_id, f"Analyzing IPO: {symbol}...")
    search = web_search(f"{symbol} IPO analysis valuation {datetime.now().strftime('%B %Y')}")
    context = f"IPO analysis for: {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}"
    if search:
        context += f"\nIPO data:\n{search}"
    prompt = (f"IPO analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: business model, IPO price range, valuation vs peers, "
              f"underwriters, lock-up expiry risk, bull case (growth story), bear case (overvalued/unprofitable), "
              f"first-day pop potential, whether to buy at IPO vs wait 90 days.")
    skill_prompt = get_skill_prompt("/ipo")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol.upper()} IPO ANALYSIS</b>\n\n" + response)


def handle_fx(chat_id, pair):
    if not pair:
        pair = "DXY"
    pair = pair.strip().upper()
    send_message(chat_id, f"Analyzing FX: {pair}...")
    search = web_search(f"{pair} forex currency analysis {datetime.now().strftime('%B %Y')}")
    context = f"FX pair/index: {pair}. Today: {datetime.now().strftime('%B %d, %Y')}"
    if search:
        context += f"\nFX data:\n{search}"
    prompt = (f"Forex/currency analysis for {pair}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: current trend and key levels, central bank policy differential, "
              f"macro drivers, correlation to equities/gold/commodities, "
              f"bull case (strengthens), bear case (weakens), what it means for US stocks.")
    skill_prompt = get_skill_prompt("/fx")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{pair} FX ANALYSIS</b>\n\n" + response)


def handle_commodities(chat_id):
    send_message(chat_id, "Pulling commodities analysis...")
    search = web_search(f"commodities oil gold silver copper prices analysis {datetime.now().strftime('%B %Y')}")
    context = f"Today: {datetime.now().strftime('%B %d, %Y')}"
    if search:
        context += f"\nCommodities web data:\n{search}"
    prompt = (f"Commodities market overview. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: oil (WTI/Brent) — trend and key levels, gold — safe haven demand vs real yields, "
              f"silver — industrial vs monetary, copper — economic signal, natural gas, "
              f"agricultural commodities if noteworthy. Bull case and bear case for each. "
              f"What commodities are signaling about the broader economy.")
    skill_prompt = get_skill_prompt("/commodities")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>COMMODITIES OVERVIEW</b>\n{datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_sentiment(chat_id):
    send_message(chat_id, "Reading market sentiment...")
    rates = get_treasury_rates()
    context = f"Today: {datetime.now().strftime('%B %d, %Y')}"
    if rates and "_error" not in rates:
        context += f"\n10yr yield: {rates.get('year10','N/A')}% | 2yr: {rates.get('year2','N/A')}%"
    # Pull watchlist prices for breadth read
    movers = []
    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote and "_error" not in quote:
            chg = quote.get("changePercentage", 0)
            movers.append(f"{symbol}: {chg:+.2f}%")
            context += f"\n{symbol}: ${quote.get('price','N/A')} ({chg:+.2f}%)"
    search = web_search(f"market sentiment VIX fear greed index {datetime.now().strftime('%B %d %Y')}")
    if search:
        context += f"\nSentiment web data:\n{search}"
    prompt = (f"Full market sentiment read. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: VIX level and what it signals, put/call ratio, breadth (advancing vs declining), "
              f"sector rotation (what's leading, what's lagging), fund flows, credit spreads, "
              f"dollar and gold signals, retail vs institutional positioning. "
              f"Give me a clear overall verdict: BULLISH / CAUTIOUSLY BULLISH / NEUTRAL / "
              f"CAUTIOUSLY BEARISH / BEARISH — and exactly why. "
              f"Where is money rotating INTO right now, and what sectors are being sold.")
    skill_prompt = get_skill_prompt("/sentiment")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>MARKET SENTIMENT</b>\n{datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n" + response)


def handle_rotation(chat_id):
    send_message(chat_id, "Analyzing sector rotation...")
    context = f"Today: {datetime.now().strftime('%B %d, %Y')}"
    rates = get_treasury_rates()
    if rates and "_error" not in rates:
        context += f"\n10yr: {rates.get('year10','N/A')}% | 2yr: {rates.get('year2','N/A')}%"
    search = web_search(f"sector rotation ETF performance flows {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nSector data:\n{search}"
    prompt = (f"Sector rotation analysis. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Give me a ranked list of all 11 sectors: AI Infrastructure, Semiconductors, Utilities, "
              f"Energy, Cybersecurity, Cloud/Software, Industrials, Financials, Healthcare, Consumer, Real Estate.\n"
              f"For each: current momentum (hot/cold), relative strength vs SPY, institutional positioning "
              f"(overweight/underweight), key catalyst or risk, verdict (overweight/neutral/underweight).\n"
              f"Then give the rotation thesis: what macro conditions are driving the rotation, "
              f"where smart money is moving NOW, and what sectors are being abandoned.")
    skill_prompt = get_skill_prompt("/rotation")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>SECTOR ROTATION</b>\n{datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_premarket(chat_id):
    send_message(chat_id, "Pulling pre-market intelligence...")
    search = web_search(f"pre-market movers futures overnight {datetime.now().strftime('%B %d %Y')}")
    context = f"Today: {datetime.now().strftime('%B %d, %Y')}"
    if search:
        context += f"\nPre-market data:\n{search}"
    prompt = (f"Pre-market brief. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: S&P 500 / Nasdaq futures direction, overnight catalysts, "
              f"major pre-market movers (up AND down) and why, key economic data releasing today, "
              f"what to watch at open, bull case for today's session, bear case for today's session.")
    skill_prompt = get_skill_prompt("/premarket")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>PRE-MARKET BRIEF</b>\n{datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n" + response)


# ─────────────────────────────────────
# COMMAND ROUTER
# ─────────────────────────────────────
def process_command(chat_id, text):
    text = text.strip()
    parts = text.split()
    command = parts[0].lower() if parts else ""
    # Strip @BotName suffix if present (e.g. /start@LumisCapitalBot)
    if "@" in command:
        command = command.split("@")[0]
    argument = parts[1] if len(parts) > 1 else ""
    argument2 = parts[2] if len(parts) > 2 else ""
    # For /portfolio and /sector, join all remaining args as one string
    rest = " ".join(parts[1:]) if len(parts) > 1 else ""
    log.info(f"Command: {command} | Arg: {argument} | Chat: {chat_id}")

    routes = {
        "/start":       lambda: handle_start(chat_id),
        "/help":        lambda: handle_help(chat_id),
        "/watchlist":   lambda: handle_watchlist(chat_id),
        "/yields":      lambda: handle_yields(chat_id),
        "/earnings":    lambda: handle_earnings(chat_id),
        "/news":        lambda: handle_news(chat_id),
        "/macro":       lambda: handle_macro(chat_id),
        "/scout":       lambda: handle_scout(chat_id),
        "/full":        lambda: handle_full(chat_id, argument),
        "/opinion":     lambda: handle_opinion(chat_id, argument),
        "/invest":      lambda: handle_invest(chat_id, argument),
        "/insider":     lambda: handle_insider(chat_id, argument),
        "/risk":        lambda: handle_risk(chat_id, argument),
        "/compounding": lambda: handle_compounding(chat_id),
        "/sector":      lambda: handle_sector(chat_id, rest),
        "/compare":     lambda: handle_compare(chat_id, argument, argument2),
        "/dividend":    lambda: handle_dividend(chat_id, argument),
        "/momentum":    lambda: handle_momentum(chat_id),
        "/portfolio":   lambda: handle_portfolio(chat_id, rest),
        "/price":       lambda: handle_price(chat_id, argument),
        "/test":        lambda: handle_test(chat_id),
        "/technical":   lambda: handle_technical(chat_id, argument),
        "/options":     lambda: handle_options(chat_id, argument),
        "/crypto":      lambda: handle_crypto(chat_id, argument),
        "/etf":         lambda: handle_etf(chat_id, argument),
        "/squeeze":     lambda: handle_squeeze(chat_id, argument),
        "/ipo":         lambda: handle_ipo(chat_id, rest),
        "/fx":          lambda: handle_fx(chat_id, argument),
        "/commodities": lambda: handle_commodities(chat_id),
        "/premarket":   lambda: handle_premarket(chat_id),
        "/sentiment":   lambda: handle_sentiment(chat_id),
        "/rotation":    lambda: handle_rotation(chat_id),
    }

    handler = routes.get(command)
    if handler:
        log.info(f"Processing command '{command}' for chat {chat_id}")
        handler()
        log.info(f"Response sent for command '{command}' to chat {chat_id}")
    else:
        # Full conversational mode — use history + live data + web search
        log.info(f"Conversational message from chat {chat_id}: {text!r}")
        history = _history_get(chat_id)
        context = f"Today: {datetime.now().strftime('%B %d, %Y %I:%M %p ET')}"

        # Inject live prices if the message mentions markets/tickers
        text_upper = text.upper()
        if any(w in text_upper for w in WATCHLIST + ["MARKET", "SPY", "QQQ", "NASDAQ", "S&P", "DOW", "STOCK", "PRICE", "CRYPTO", "BITCOIN", "BTC"]):
            for symbol in WATCHLIST[:6]:
                quote = get_stock_quote(symbol)
                if quote and "_error" not in quote:
                    context += f"\n{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)"

        # Web search enrichment for specific queries
        search_query = None
        if any(w in text_upper for w in ["NEWS", "LATEST", "TODAY", "HAPPENED", "RECENT"]):
            search_query = f"{text} {datetime.now().strftime('%B %Y')}"
        elif re.search(r'\b[A-Z]{2,5}\b', text_upper):
            # Contains a potential ticker
            tickers_found = re.findall(r'\b[A-Z]{2,5}\b', text_upper)
            search_query = f"{tickers_found[0]} stock news {datetime.now().strftime('%B %Y')}"
        if search_query:
            search = web_search(search_query)
            if search:
                context += f"\nWeb data:\n{search}"

        response = ask_claude(text, context, history=history)
        _history_add(chat_id, "user", text)
        _history_add(chat_id, "assistant", response)
        send_message(chat_id, response)
        log.info(f"Conversational response sent to chat {chat_id}")


# ─────────────────────────────────────
# WEBHOOK MODE (Railway deployment)
# ─────────────────────────────────────
_WEBHOOK_PATH = "/webhook"
_ARGUS_PATH   = "/argus"
_OSIRIS_PATH  = "/osiris"

# In-memory ticket status store  {ticket_id: "IN PROGRESS" | "DONE" | "BLOCKED: ..."}
_ticket_status = {}

class _WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == _WEBHOOK_PATH:
            self.send_response(200)
            self.end_headers()
            try:
                update = json.loads(body)
                threading.Thread(target=_dispatch, args=(update,), daemon=True).start()
            except Exception as e:
                log.error(f"Webhook parse error: {e}")

        elif self.path == _ARGUS_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                payload = json.loads(body)
                threading.Thread(target=_starfire_dispatch, args=(payload,), daemon=True).start()
                self.wfile.write(json.dumps({"status": "received", "system": "STARFIRE"}).encode())
            except Exception as e:
                log.error(f"Argus parse error: {e}")
                self.wfile.write(json.dumps({"status": "error", "detail": str(e)}).encode())

        elif self.path == _OSIRIS_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                payload = json.loads(body)
                threading.Thread(target=_osiris_dispatch, args=(payload,), daemon=True).start()
                self.wfile.write(json.dumps({"status": "received", "system": "OSIRIS"}).encode())
            except Exception as e:
                log.error(f"OSIRIS parse error: {e}")
                self.wfile.write(json.dumps({"status": "error", "detail": str(e)}).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"LUMISNOVA ONLINE | STARFIRE: /argus | OSIRIS: /osiris | Telegram: /webhook")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


_STARFIRE_BOT = "starfire5_bot"
_OSIRIS_BOT   = "osiris_prime_bot"


def _send_via_token(token, chat_id, text):
    """Send a message using an arbitrary bot token (for bot-to-bot replies)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.error(f"_send_via_token error: {e}")


def _dispatch(update):
    global _processed_updates

    # ── Deduplication ───────────────────────────────────────────────
    update_id = update.get("update_id")
    if update_id and update_id in _processed_updates:
        log.warning(f"Duplicate update {update_id} — skipping")
        return
    if update_id:
        _processed_updates.add(update_id)
        if len(_processed_updates) > 1000:
            # Keep the last 500 to prevent unbounded growth
            _processed_updates = set(list(_processed_updates)[-500:])

    message   = update.get("message", {})
    chat_id   = message.get("chat", {}).get("id")
    chat_type = message.get("chat", {}).get("type", "private")
    text      = message.get("text", "")
    sender    = message.get("from", {})
    username  = sender.get("username", "")
    is_bot    = sender.get("is_bot", False)

    if not chat_id or not text:
        return

    log.info(f"Update {update_id} — chat={chat_id} type={chat_type} from=@{username}")

    try:
        # ── Ignore own messages ────────────────────────────────────
        # If sender is a bot that is NOT starfire or osiris, skip entirely
        if is_bot and username not in (_STARFIRE_BOT, _OSIRIS_BOT):
            return

        # ── Bot-to-bot: STARFIRE ───────────────────────────────────
        if username == _STARFIRE_BOT and is_bot:
            log.info(f"STARFIRE message from group {chat_id}")
            reply_fn = (lambda msg: _send_via_token(STARFIRE_BOT_TOKEN, chat_id, msg)
                        if STARFIRE_BOT_TOKEN else lambda msg: send_message(str(chat_id), msg))
            try:
                payload = json.loads(text)
                payload.setdefault("data", {})
                payload["data"].setdefault("route_reply_to", [chat_id])
                _starfire_dispatch(payload)
            except (json.JSONDecodeError, ValueError):
                result = _execute_starfire_task(text)
                send_message(str(chat_id), f"<b>LUMISNOVA</b>\n\n{result}")
            return

        # ── Bot-to-bot: OSIRIS ─────────────────────────────────────
        if username == _OSIRIS_BOT and is_bot:
            log.info(f"OSIRIS message from group {chat_id}")
            try:
                payload = json.loads(text)
                payload.setdefault("data", {})
                payload["data"].setdefault("recipients", [chat_id])
                _osiris_dispatch(payload)
            except (json.JSONDecodeError, ValueError):
                result = _execute_starfire_task(text)
                send_message(str(chat_id), f"<b>LUMISNOVA</b>\n\n{result}")
            return

        # ── Group chat: only respond to commands, @mentions, replies ─
        if chat_type in ("group", "supergroup"):
            bot_mentioned = "@lumis" in text.lower() or "@lumisnova" in text.lower()
            is_reply_to_bot = (message.get("reply_to_message", {})
                               .get("from", {}).get("is_bot", False))
            is_command = text.startswith("/")
            if not (is_command or bot_mentioned or is_reply_to_bot):
                return
            text = re.sub(r'@\w+', '', text).strip()

        process_command(str(chat_id), text)
    except Exception as e:
        log.error(f"Error processing update: {e}")


# ─────────────────────────────────────
# STARFIRE / ARGUS TOWER DISPATCHER
# ─────────────────────────────────────
def _starfire_dispatch(payload):
    command = payload.get("command", "")
    data    = payload.get("data", payload)  # support flat or nested payload

    if command == "LUMISNOVA_TICKET":
        _handle_ticket(data)
    elif command == "LUMISNOVA_REQUEST":
        _handle_direct_request(data)
    elif command == "LUMISNOVA_TICKET_STATUS":
        _handle_ticket_status(data)
    else:
        # Try to infer from keys if command field is missing
        if "ticket_id" in data and "description" in data:
            _handle_ticket(data)
        elif "message" in data:
            _handle_direct_request(data)
        else:
            log.warning(f"STARFIRE: unrecognised command payload: {payload}")


def _handle_ticket(data):
    ticket_id   = data.get("ticket_id")
    title       = data.get("title", "Unknown")
    description = data.get("description", "")
    recipients  = data.get("route_reply_to", [CHAT_ID])

    _ticket_status[ticket_id] = "IN PROGRESS"
    log.info(f"STARFIRE Ticket #{ticket_id} — {title}")

    # Acknowledge
    ack = f"LUMISNOVA — Ticket #{ticket_id} received. Pulling: {title}"
    for uid in recipients:
        send_message(str(uid), ack)

    # Execute
    result = _execute_starfire_task(description)

    # Deliver
    for uid in recipients:
        send_message(str(uid), f"<b>Ticket #{ticket_id} — {title}</b>\n\n{result}")
        send_message(str(uid), f"LUMISNOVA — Ticket #{ticket_id} DONE. Report delivered.")

    _ticket_status[ticket_id] = "DONE"


def _handle_direct_request(data):
    message    = data.get("message", "")
    recipients = data.get("route_reply_to", [CHAT_ID])
    log.info(f"STARFIRE direct request: {message}")
    result = _execute_starfire_task(message)
    for uid in recipients:
        send_message(str(uid), result)


def _handle_ticket_status(data):
    ticket_ids = data.get("ticket_ids", [])
    recipients = data.get("route_reply_to", [CHAT_ID])
    lines = []
    for tid in ticket_ids:
        status = _ticket_status.get(tid, "UNKNOWN — no record of this ticket")
        lines.append(f"Ticket #{tid}: {status}")
    report = "LUMISNOVA — Ticket Status\n\n" + "\n".join(lines)
    for uid in recipients:
        send_message(str(uid), report)


def _execute_starfire_task(description):
    """
    Map a natural-language task description to the right data handler.
    Tries to detect ticker + command type; falls back to Claude.
    """
    desc_upper = description.upper()

    # Extract ticker if present (first 1-5 uppercase-letter word after common keywords)
    import re as _re
    ticker_match = _re.search(
        r'\b(?:FOR|ON|REPORT ON|ANALYZE|PRICE OF|QUOTE FOR|FULL|OPINION ON|INVEST IN|DIVIDEND FOR|INSIDER|RISK ON|COMPARE)?\s*([A-Z]{1,5})\b',
        desc_upper
    )
    ticker = ticker_match.group(1) if ticker_match else None

    # Route to the right handler based on keywords
    if ticker and any(k in desc_upper for k in ["FULL REPORT", "FULL ANALYSIS", "COMPLETE ANALYSIS", "DEEP DIVE"]):
        return _starfire_full(ticker)
    if ticker and any(k in desc_upper for k in ["PRICE", "QUOTE", "CURRENT PRICE"]):
        return get_stock_price_only(ticker)
    if ticker and any(k in desc_upper for k in ["OPINION", "QUICK TAKE", "BUY OR SELL", "BUY/SELL"]):
        return _starfire_opinion(ticker)
    if ticker and "INVEST" in desc_upper:
        return _starfire_invest(ticker)
    if ticker and "DIVIDEND" in desc_upper:
        return _starfire_dividend(ticker)
    if ticker and "INSIDER" in desc_upper:
        return _starfire_insider(ticker)
    if ticker and "RISK" in desc_upper:
        return _starfire_risk(ticker)
    if any(k in desc_upper for k in ["YIELD", "TREASURY", "RATES"]):
        return _starfire_yields()
    if any(k in desc_upper for k in ["EARNINGS", "CALENDAR"]):
        return _starfire_earnings_brief()
    if any(k in desc_upper for k in ["NEWS", "HEADLINES"]):
        return _starfire_news_brief()
    if any(k in desc_upper for k in ["MACRO", "FED", "ECONOMY"]):
        return _starfire_macro_brief()
    if any(k in desc_upper for k in ["SCOUT", "PICKS", "STOCK PICKS"]):
        return _starfire_scout()
    if ticker and any(k in desc_upper for k in ["WATCHLIST", "PRICES"]):
        return _starfire_watchlist()

    # Fallback — pass directly to Claude as a financial data request
    return ask_claude(
        description,
        f"STARFIRE data request. Today: {datetime.now().strftime('%B %d, %Y')}",
        skill_prompt=get_skill_prompt("/full")
    )


# ── Starfire sub-handlers (lightweight wrappers around FMP + Claude) ──

def _starfire_full(ticker):
    quote = get_stock_quote(ticker)
    consensus = get_analyst_consensus(ticker)
    context = f"{ticker} live data:\n"
    if quote and "_error" not in quote:
        context += f"Price: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
        context += f"52wk High: ${quote.get('yearHigh','N/A')} | Low: ${quote.get('yearLow','N/A')}\n"
        context += f"Market Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    if consensus and "_error" not in consensus:
        context += f"Analyst PT: ${consensus.get('targetConsensus','N/A')}\n"
    prompt = f"Full stock analysis for {ticker}. Today: {datetime.now().strftime('%B %d, %Y')}"
    return ask_claude(prompt, context, skill_prompt=get_skill_prompt("/full"))

def _starfire_opinion(ticker):
    quote = get_stock_quote(ticker)
    context = ""
    if quote and "_error" not in quote:
        context = f"{ticker}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)"
    return ask_claude(f"Quick opinion on {ticker}. Buy/sell/hold and why.", context, skill_prompt=get_skill_prompt("/opinion"))

def _starfire_invest(ticker):
    quote = get_stock_quote(ticker)
    context = ""
    if quote and "_error" not in quote:
        context = f"{ticker}: ${quote.get('price','N/A')} | Cap: ${quote.get('marketCap',0)/1e9:.2f}B"
    return ask_claude(f"Long-term investing analysis for {ticker}.", context, skill_prompt=get_skill_prompt("/invest"))

def _starfire_dividend(ticker):
    quote = get_stock_quote(ticker)
    context = ""
    if quote and "_error" not in quote:
        context = f"{ticker}: ${quote.get('price','N/A')}"
    return ask_claude(f"Dividend analysis for {ticker}. Today: {datetime.now().strftime('%B %d, %Y')}", context, skill_prompt=get_skill_prompt("/dividend"))

def _starfire_insider(ticker):
    return ask_claude(f"Insider trading analysis for {ticker}. Today: {datetime.now().strftime('%B %d, %Y')}", skill_prompt=get_skill_prompt("/insider"))

def _starfire_risk(ticker):
    quote = get_stock_quote(ticker)
    context = f"{ticker}: ${quote.get('price','N/A')}" if quote and "_error" not in quote else ""
    return ask_claude(f"Risk check for {ticker}.", context, skill_prompt=get_skill_prompt("/risk"))

def _starfire_yields():
    rates = get_treasury_rates()
    if not rates or "_error" in rates:
        return "Treasury data unavailable."
    context = f"2yr: {rates.get('year2','N/A')}% | 10yr: {rates.get('year10','N/A')}% | 30yr: {rates.get('year30','N/A')}%"
    return ask_claude(f"Yield curve analysis. Today: {datetime.now().strftime('%B %d, %Y')} {context}", skill_prompt=get_skill_prompt("/yields"))

def _starfire_earnings_brief():
    earnings = get_earnings_calendar()
    if not earnings or isinstance(earnings, dict):
        return "Earnings data unavailable."
    major = ["AAPL","MSFT","NVDA","META","GOOGL","AMZN","TSLA","NOW","MU","HOOD","SOFI","IREN","ASTS","AMD","INTC"]
    filtered = [e for e in earnings if e.get("symbol") in major][:8]
    context = "\n".join(f"{e['symbol']} | {e.get('date','')} | EPS est: {e.get('epsEstimated','N/A')}" for e in filtered)
    return ask_claude(f"Earnings preview. Today: {datetime.now().strftime('%B %d, %Y')}\n{context}", skill_prompt=get_skill_prompt("/earnings"))

def _starfire_news_brief():
    news = get_stock_news()
    if not news or isinstance(news, dict):
        return "News data unavailable."
    context = "\n".join(f"[{n.get('symbol','')}] {n.get('title','')}" for n in news[:8])
    return ask_claude(f"Market news brief. Today: {datetime.now().strftime('%B %d, %Y')}\n{context}", skill_prompt=get_skill_prompt("/news"))

def _starfire_macro_brief():
    rates = get_treasury_rates()
    context = ""
    if rates and "_error" not in rates:
        context = f"10yr: {rates.get('year10')}% | 2yr: {rates.get('year2')}% | 30yr: {rates.get('year30')}%"
    return ask_claude(f"Macro brief. Today: {datetime.now().strftime('%B %d, %Y')}. {context}", skill_prompt=get_skill_prompt("/macro"))

def _starfire_scout():
    focus_sectors = random.sample(_SCOUT_SECTORS, 2)
    exclude = ", ".join(WATCHLIST)
    context = f"Scout run {datetime.now().strftime('%B %d, %Y')}"
    prompt = f"3 fresh stock picks. Do NOT pick: {exclude}. At least 2 from: {', '.join(focus_sectors)}. All different sectors."
    return ask_claude(prompt, context, skill_prompt=get_skill_prompt("/scout"))

def _starfire_watchlist():
    lines = []
    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote and "_error" not in quote:
            price = quote.get("price", 0)
            change = quote.get("changePercentage", 0)
            arrow = "▲" if change >= 0 else "▼"
            lines.append(f"{arrow} {symbol}: ${price:.2f} ({change:+.2f}%)")
    return "\n".join(lines) if lines else "Watchlist data unavailable."


# ─────────────────────────────────────
# OSIRIS BROADCAST NETWORK
# ─────────────────────────────────────
def _osiris_dispatch(payload):
    """Route incoming OSIRIS commands."""
    command = payload.get("command", "OSIRIS_BROADCAST")
    data    = payload.get("data", payload)

    if command == "OSIRIS_BROADCAST":
        _osiris_broadcast(data)
    elif command == "OSIRIS_ALERT":
        _osiris_alert(data)
    elif command == "OSIRIS_STATUS":
        _osiris_status_report(data)
    elif command == "OSIRIS_TASK":
        # Execute a financial task and broadcast the result
        task = data.get("task", "")
        recipients = data.get("recipients", [CHAT_ID])
        result = _execute_starfire_task(task)
        subject = data.get("subject", "OSIRIS TASK RESULT")
        msg = f"<b>OSIRIS — {subject}</b>\n\n{result}"
        for uid in recipients:
            send_message(str(uid), msg)
    else:
        log.warning(f"OSIRIS: unrecognised command: {payload}")


def _osiris_broadcast(data):
    subject    = data.get("subject", "OSIRIS BROADCAST")
    body       = data.get("body", "")
    recipients = data.get("recipients", [CHAT_ID])
    task       = data.get("task")  # optional: execute a financial task first
    if task:
        body = _execute_starfire_task(task)
    msg = f"<b>OSIRIS — {subject}</b>\n{datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n{body}"
    for uid in recipients:
        send_message(str(uid), msg)
    log.info(f"OSIRIS broadcast '{subject}' → {len(recipients)} recipients")


def _osiris_alert(data):
    ticker     = data.get("ticker", "")
    alert_type = data.get("type", "PRICE ALERT")
    message    = data.get("message", "")
    recipients = data.get("recipients", [CHAT_ID])
    price_line = ""
    if ticker:
        price_line = f"\n{get_stock_price_only(ticker)}"
    msg = f"<b>OSIRIS ALERT — {alert_type}</b>{price_line}\n{datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n{message}"
    for uid in recipients:
        send_message(str(uid), msg)
    log.info(f"OSIRIS alert '{alert_type}' for {ticker} → {len(recipients)} recipients")


def _osiris_status_report(data):
    recipients = data.get("recipients", [CHAT_ID])
    status = (f"<b>OSIRIS NETWORK STATUS</b>\n"
              f"{datetime.now().strftime('%B %d, %Y | %I:%M %p ET')}\n\n"
              f"LUMISNOVA: ONLINE\n"
              f"MODEL: {CLAUDE_MODEL}\n"
              f"STARFIRE CHANNEL (/argus): ACTIVE\n"
              f"OSIRIS CHANNEL (/osiris): ACTIVE\n"
              f"TELEGRAM WEBHOOK: CONNECTED\n"
              f"FMP DATA FEED: LIVE\n"
              f"WEB SEARCH: {'ACTIVE' if SERPER_API_KEY else 'NOT CONFIGURED'}\n"
              f"CLAUDE ENGINE: ONLINE")
    for uid in recipients:
        send_message(str(uid), status)


# ─────────────────────────────────────
# MAIN
# ─────────────────────────────────────
def run_bot():
    global _shutdown
    log.info("Lumis Capital Bot starting...")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    missing = []
    if not TELEGRAM_TOKEN:    missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:           missing.append("CHAT_ID")
    if not FMP_API_KEY:       missing.append("FMP_API_KEY")
    if not ANTHROPIC_API_KEY: missing.append("ANTHROPIC_API_KEY")

    if missing:
        log.error(f"Missing variables: {', '.join(missing)}")
        return

    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")

    if railway_domain:
        # ── Webhook mode ──────────────────────────────────────────
        # Telegram delivers each update to exactly one endpoint,
        # eliminating the duplicate-message problem from rolling deploys.
        webhook_url = f"https://{railway_domain}{_WEBHOOK_PATH}"
        log.info(f"Registering webhook: {webhook_url}")
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
                json={"url": webhook_url, "drop_pending_updates": True},
                timeout=10,
            )
            result = r.json()
            if result.get("ok"):
                log.info("Webhook registered successfully.")
            else:
                log.error(f"setWebhook failed: {result}")
        except Exception as e:
            log.error(f"setWebhook error: {e}")

        send_message(
            CHAT_ID,
            f"<b>Lumis Capital Bot Online</b>\n"
            f"{datetime.now().strftime('%B %d, %Y | %I:%M %p ET')}\n"
            f"Type /help for all commands."
        )

        port = int(os.environ.get("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), _WebhookHandler)
        log.info(f"Webhook server listening on port {port}")
        server.serve_forever()

    else:
        # ── Long-polling mode (local dev / no Railway domain) ─────
        log.info("No RAILWAY_PUBLIC_DOMAIN — using long-polling mode.")

        # Delete any existing webhook so polling works
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
                json={"drop_pending_updates": True},
                timeout=10,
            )
        except Exception:
            pass

        send_message(
            CHAT_ID,
            f"<b>Lumis Capital Bot Online</b>\n"
            f"{datetime.now().strftime('%B %d, %Y | %I:%M %p ET')}\n"
            f"Type /help for all commands."
        )

        offset = None
        pending = get_updates(timeout=0)
        if pending.get("ok") and pending.get("result"):
            offset = pending["result"][-1]["update_id"] + 1
            log.info(f"Skipped {len(pending['result'])} pending update(s). Starting at offset {offset}.")

        log.info("Listening for new commands...")
        while not _shutdown:
            try:
                updates = get_updates(offset)
                if _shutdown:
                    break
                if updates.get("ok") and updates.get("result"):
                    for update in updates["result"]:
                        update_id = update["update_id"]
                        offset = update_id + 1
                        message = update.get("message", {})
                        chat_id = message.get("chat", {}).get("id")
                        text = message.get("text", "")
                        if chat_id and text:
                            log.info(f"Update {update_id} — chat_id={chat_id} text={text!r}")
                            process_command(str(chat_id), text)
                elif not updates.get("ok"):
                    log.error(f"getUpdates error: {updates}")
                if not _shutdown:
                    time.sleep(1)
            except Exception as e:
                log.error(f"Bot error: {e}")
                if not _shutdown:
                    time.sleep(5)

        log.info("Bot stopped cleanly.")


if __name__ == "__main__":
    run_bot()

"""
LUMIS CAPITAL BOT
Powered by Claude AI + FMP Live Data
Telegram Bot for Market Intelligence
"""

import os
import re
import json
import uuid
import random
import requests
import signal
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import zoneinfo
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
CHAT_ACCESS_CODE    = os.environ.get("CHAT_ACCESS_CODE", "") # set in Railway to protect web chat

# Most intelligent Claude model
CLAUDE_MODEL = "claude-opus-4-8"


def _is_owner(chat_id) -> bool:
    """True only if the requesting chat is the configured owner (CHAT_ID)."""
    return bool(CHAT_ID and str(chat_id) == str(CHAT_ID))


_OWNER_ONLY_MSG = (
    "This command shows personal account data. "
    "Use /full, /technical, /options, /insider, /sentiment, /rotation, /crypto, "
    "/sector, /macro, /news, or just ask me about any stock."
)

# Deduplication: track processed update IDs to prevent replay on restart/rolling deploy
# Unique ID for this process — visible in startup message and used as webhook secret token.
# Every new deploy generates a new secret. Telegram is told the new secret, so any delivery
# that arrives at the OLD instance (which has a different secret) is silently rejected.
# This kills duplicate processing even when two instances briefly overlap during a rolling deploy.
_INSTANCE_ID     = str(uuid.uuid4())[:8]
_WEBHOOK_SECRET  = str(uuid.uuid4()).replace("-", "")   # 32-char hex, valid Telegram secret

_processed_updates: set = set()
_processed_updates_lock = threading.Lock()   # makes check-and-add atomic across threads

# Content-based dedup: drop identical (chat_id, text) within a short window. Catches the case
# where Telegram delivers the same user command as two updates with DIFFERENT update_ids,
# which the update_id set above cannot catch.
_recent_messages: dict = {}                  # {(chat_id, text): timestamp}
_recent_messages_lock = threading.Lock()
_RECENT_WINDOW_SEC = 15

# Web chat session history  {session_id: [{"role": ..., "content": ...}]}
_web_sessions: dict = {}
_WEB_MAX_HISTORY = 10

# Common English words that should never be treated as stock tickers
_SKIP_WORDS = {
    "A", "AN", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN", "IS",
    "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "SO", "TO", "UP",
    "US", "WE", "BUT", "FOR", "GET", "GOT", "HAS", "HOW", "ITS", "LET",
    "NOT", "NOW", "OFF", "OLD", "ONE", "OUT", "OWN", "PUT", "SAY", "SEE",
    "THE", "TOO", "TWO", "USE", "WAS", "WAY", "WHO", "WHY", "YOU",
    "ALL", "AND", "ARE", "HAD", "HIM", "HIS", "NEW", "OUR", "CAN",
    "DID", "MAY", "OVER", "THAN", "THAT", "THEM", "THEN", "THEY",
    "THIS", "WILL", "WITH", "FROM", "JUST", "BEEN", "HAVE", "MORE",
    "WHAT", "WHEN", "WERE", "ALSO", "BACK", "EACH", "EVEN", "HERE",
    "INTO", "LOOK", "MAKE", "SAME", "SOME", "SUCH", "TAKE", "TELL",
    "WELL", "WENT", "DOES", "DONE", "GIVE", "HIGH", "LAST", "LONG",
    "MUCH", "NEXT", "ONLY", "SAID", "SHOW", "VERY", "WEEK", "YEAR",
    "NASDAQ", "ABOUT", "AFTER", "AGAIN", "COULD", "FIRST", "FOUND",
    "GREAT", "THOSE", "THREE", "WHERE", "WHICH", "WHILE", "WOULD",
    "THEIR", "THERE", "THESE", "THINK", "TODAY", "TRADE", "STOCK",
}

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

LIVE PRICE RULE — MANDATORY:
When a "LIVE MARKET DATA" block appears in the user message, those are real-time prices fetched at the moment of the request.
- ALWAYS reference the live price from that block when discussing the stock (e.g. "currently trading at $138.50")
- NEVER substitute a price from your training data when a live price is provided
- Never say a price is "approximately", "around", or hedge it — use the exact number from the live data block

NEVER output: "Data Disclosure", "Data Transparency", "Important Notice", "Disclaimer", "Live price feed not confirmed", "not confirmed in this session", "my knowledge cutoff", "I cannot access real-time", "as of my training", "based on my training data", "extrapolated", "verify all live data", "Verify current quote before acting", "figures below are based on the most recent data available"

NEVER add any footer, header, note, or caveat about data limitations or knowledge cutoffs.
NEVER apologize for data access — use the data provided and answer directly.
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
    _TELEGRAM_LIMIT = 4096

    def _post(chunk):
        try:
            response = requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}, timeout=30)
            result = response.json()
            if not result.get("ok"):
                log.error(f"Telegram error: {result}")
            return result
        except Exception as e:
            log.error(f"Send message error: {e}")
            return None

    if len(text) <= _TELEGRAM_LIMIT:
        return _post(text)

    # Split on newlines to avoid cutting mid-word/mid-tag
    lines = text.split("\n")
    chunk = ""
    last_result = None
    for line in lines:
        candidate = chunk + ("\n" if chunk else "") + line
        if len(candidate) > _TELEGRAM_LIMIT:
            if chunk:
                last_result = _post(chunk)
            # If a single line itself is too long, hard-split it
            while len(line) > _TELEGRAM_LIMIT:
                last_result = _post(line[:_TELEGRAM_LIMIT])
                line = line[_TELEGRAM_LIMIT:]
            chunk = line
        else:
            chunk = candidate
    if chunk:
        last_result = _post(chunk)
    return last_result


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
# FMP EXTENDED DATA LAYER
# ─────────────────────────────────────
_FMP_BASE = "https://financialmodelingprep.com/stable"

def _fmp_get(endpoint, params=None, label="", list_index=0):
    """Generic FMP GET. Returns data[list_index] if list, raw dict otherwise, or None on error."""
    url = f"{_FMP_BASE}/{endpoint}"
    p = {"apikey": FMP_API_KEY}
    if params:
        p.update(params)
    try:
        r = requests.get(url, params=p, timeout=10)
        if r.status_code != 200:
            log.warning(f"FMP {endpoint} returned {r.status_code}")
            return None
        data = r.json()
        if isinstance(data, list):
            return data[list_index] if len(data) > list_index else None
        return data if data else None
    except Exception as e:
        log.error(f"FMP {label or endpoint} error: {e}")
        return None


def get_key_metrics(symbol):
    return _fmp_get("key-metrics", {"symbol": symbol, "limit": 1}, f"key-metrics/{symbol}")


def get_financial_ratios(symbol):
    return _fmp_get("ratios", {"symbol": symbol, "limit": 1}, f"ratios/{symbol}")


def get_income_statement(symbol, period="annual"):
    return _fmp_get("income-statement", {"symbol": symbol, "period": period, "limit": 1}, f"income/{symbol}")


def get_earnings_surprises(symbol):
    url = f"{_FMP_BASE}/earnings-surprises"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": 4, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            return r.json()[:4]
        return None
    except Exception as e:
        log.error(f"FMP earnings-surprises error: {e}")
        return None


def get_insider_trades(symbol):
    url = f"{_FMP_BASE}/insider-trading"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": 10, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            return r.json()[:10]
        return None
    except Exception as e:
        log.error(f"FMP insider-trading error: {e}")
        return None


def get_institutional_holders(symbol):
    url = f"{_FMP_BASE}/institutional-ownership/symbol-ownership"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": 5, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data[:5] if isinstance(data, list) else None
        return None
    except Exception as e:
        log.error(f"FMP institutional-ownership error: {e}")
        return None


def get_sector_performance():
    url = f"{_FMP_BASE}/sector-performance"
    try:
        r = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        log.error(f"FMP sector-performance error: {e}")
        return None


def get_etf_holdings(symbol):
    url = f"{_FMP_BASE}/etf-holdings"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": 10, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data[:10] if isinstance(data, list) else None
        return None
    except Exception as e:
        log.error(f"FMP etf-holdings error: {e}")
        return None


def get_historical_prices(symbol, days=30):
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = f"{_FMP_BASE}/historical-price-eod/light"
    try:
        r = requests.get(url, params={"symbol": symbol, "from": start, "to": end, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else None
        return None
    except Exception as e:
        log.error(f"FMP historical-prices error: {e}")
        return None


def get_short_interest(symbol):
    return _fmp_get("short-interest", {"symbol": symbol}, f"short-interest/{symbol}")


def get_analyst_ratings(symbol):
    url = f"{_FMP_BASE}/analyst-stock-recommendations"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": 5, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data[:5] if isinstance(data, list) else None
        return None
    except Exception as e:
        log.error(f"FMP analyst-recommendations error: {e}")
        return None


def get_crypto_quote(symbol):
    ticker = symbol.upper()
    if not ticker.endswith("USD"):
        ticker = f"{ticker}USD"
    url = f"{_FMP_BASE}/crypto/quote"
    try:
        r = requests.get(url, params={"symbol": ticker, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data[0] if isinstance(data, list) and data else None
        return None
    except Exception as e:
        log.error(f"FMP crypto-quote error: {e}")
        return None


def get_forex_rates(pairs=None):
    url = f"{_FMP_BASE}/forex-list"
    try:
        r = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if pairs and isinstance(data, list):
                return [d for d in data if d.get("ticker", "") in pairs][:8]
            return data[:8] if isinstance(data, list) else None
        return None
    except Exception as e:
        log.error(f"FMP forex-rates error: {e}")
        return None


def get_commodity_prices():
    url = f"{_FMP_BASE}/commodities-list"
    try:
        r = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data[:12] if isinstance(data, list) else None
        return None
    except Exception as e:
        log.error(f"FMP commodity-prices error: {e}")
        return None


def get_economic_indicators():
    indicators = {}
    for name, endpoint in [
        ("cpi", "economic-indicators/CPI"),
        ("gdp", "economic-indicators/GDP"),
        ("unemployment", "economic-indicators/unemployment"),
        ("fed_funds", "economic-indicators/federalFunds"),
    ]:
        data = _fmp_get(endpoint, label=name)
        if data:
            indicators[name] = data
    return indicators if indicators else None


def get_ticker_news(symbol, limit=5):
    url = f"{_FMP_BASE}/stock-news"
    try:
        r = requests.get(url, params={"tickers": symbol, "limit": limit, "apikey": FMP_API_KEY}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data[:limit] if isinstance(data, list) else None
        return None
    except Exception as e:
        log.error(f"FMP ticker-news error: {e}")
        return None


def _fmt_fundamentals(symbol, quote=None, metrics=None, ratios=None, consensus=None, income=None):
    """Build a rich fundamental context string from available FMP data."""
    parts = []
    if quote and "_error" not in quote:
        parts.append(
            f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%) | "
            f"52wk ${quote.get('yearLow','N/A')}–${quote.get('yearHigh','N/A')} | "
            f"Cap ${quote.get('marketCap',0)/1e9:.1f}B"
        )
    if consensus and "_error" not in consensus:
        parts.append(f"Analyst PT: ${consensus.get('targetConsensus','N/A')} (High ${consensus.get('targetHigh','N/A')} / Low ${consensus.get('targetLow','N/A')})")
    if metrics:
        parts.append(
            f"P/E: {metrics.get('peRatio','N/A')} | EV/EBITDA: {metrics.get('enterpriseValueOverEBITDA','N/A')} | "
            f"P/S: {metrics.get('priceToSalesRatio','N/A')} | P/B: {metrics.get('pbRatio','N/A')}"
        )
    if income:
        rev = income.get('revenue', 0) or 0
        ni = income.get('netIncome', 0) or 0
        gm = income.get('grossProfitRatio', None)
        parts.append(
            f"Revenue: ${rev/1e9:.2f}B | Net Income: ${ni/1e9:.2f}B" +
            (f" | Gross Margin: {gm*100:.1f}%" if gm else "")
        )
    if ratios:
        parts.append(
            f"ROE: {ratios.get('returnOnEquity','N/A')} | Debt/Eq: {ratios.get('debtEquityRatio','N/A')} | "
            f"Current Ratio: {ratios.get('currentRatio','N/A')}"
        )
    return "\n".join(parts)


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


_ET = zoneinfo.ZoneInfo("America/New_York")

def _market_session() -> str:
    """Return 'open', 'premarket', 'afterhours', or 'closed' based on current ET time."""
    now = datetime.now(_ET)
    if now.weekday() >= 5:          # Saturday / Sunday
        return "closed"
    t = now.hour * 60 + now.minute  # minutes since midnight ET
    if 240 <= t < 570:              # 4:00 AM – 9:30 AM
        return "premarket"
    if 570 <= t < 960:              # 9:30 AM – 4:00 PM
        return "open"
    if 960 <= t < 1200:             # 4:00 PM – 8:00 PM
        return "afterhours"
    return "closed"


def get_extended_price(symbol):
    """Fetch pre/post market price from FMP stable endpoint. Returns dict or None."""
    url = f"{_FMP_BASE}/pre-post-market-trade"
    try:
        r = requests.get(url, params={"symbol": symbol.upper(), "apikey": FMP_API_KEY}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            item = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else None
            return item if item else None
        return None
    except Exception as e:
        log.warning(f"FMP extended price error for {symbol}: {e}")
        return None


# ─────────────────────────────────────
# CLAUDE AI
# ─────────────────────────────────────
def ask_claude(prompt, context="", skill_prompt=None, history=None, max_tokens=1100):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    system = skill_prompt if skill_prompt else SYSTEM_PROMPT

    # Wrap live context with an explicit anchor header so Claude uses these
    # exact numbers rather than falling back to training-data prices
    if context:
        wrapped_context = (
            f"━━ LIVE MARKET DATA — {datetime.now().strftime('%b %d, %Y %I:%M %p ET')} ━━\n"
            f"{context}\n"
            f"━━ USE THE ABOVE NUMBERS EXACTLY — DO NOT SUBSTITUTE TRAINING DATA PRICES ━━"
        )
        full_prompt = f"{wrapped_context}\n\n{prompt}"
    else:
        full_prompt = prompt

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
    send_message(chat_id, f"Pulling data on {symbol}...")
    # Fetch all available FMP data in parallel-ish sequence
    quote     = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    metrics   = get_key_metrics(symbol)
    ratios    = get_financial_ratios(symbol)
    income    = get_income_statement(symbol)
    ratings   = get_analyst_ratings(symbol)
    news      = get_ticker_news(symbol, limit=3)
    surprises = get_earnings_surprises(symbol)

    context = _fmt_fundamentals(symbol, quote, metrics, ratios, consensus, income)

    if ratings:
        buys = sum(1 for r in ratings if "buy" in str(r.get("analystRatingsStrongBuy", 0) or 0) or r.get("analystRatingsBuy", 0))
        context += f"\nAnalyst ratings (recent 5): {[r.get('rating','') for r in ratings]}"
    if surprises:
        surp_lines = [f"{s.get('date','')}: EPS actual {s.get('actualEarningResult','N/A')} vs est {s.get('estimatedEarning','N/A')}" for s in surprises]
        context += f"\nEarnings surprises (last 4):\n" + "\n".join(surp_lines)
    if news:
        context += "\nRecent news:\n" + "\n".join(f"- {n.get('title','')}" for n in news)

    search = web_search(f"{symbol} stock analysis outlook {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"\nWeb data:\n{search}"

    prompt = (f"Full institutional-quality analysis for ${symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: business model, moat, top 3 competitors, key catalysts, "
              f"valuation vs peers, bull case with price target, bear case with downside, "
              f"entry strategy, stop loss, position sizing for $10K account.\n"
              f"Keep the ENTIRE response under 3800 characters so it fits in a single message. Be dense, no filler.")
    skill_prompt = get_skill_prompt("/full")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt, max_tokens=1100)
    send_message(chat_id, f"<b>{symbol} ANALYSIS</b>\n<i>Source: FMP + Web</i>\n\n" + response)


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
    quote     = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    metrics   = get_key_metrics(symbol)
    ratios    = get_financial_ratios(symbol)
    income    = get_income_statement(symbol)
    context   = _fmt_fundamentals(symbol, quote, metrics, ratios, consensus, income)
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
    send_message(chat_id, f"Pulling insider data for {symbol}...")
    trades    = get_insider_trades(symbol)
    holders   = get_institutional_holders(symbol)
    quote     = get_stock_quote(symbol)
    context   = f"{symbol} insider data:\n"
    if quote and "_error" not in quote:
        context += f"Price: ${quote.get('price','N/A')}\n"
    if trades:
        for t in trades[:8]:
            action = t.get("transactionType", "")
            name   = t.get("reportingName", "")
            shares = t.get("securitiesTransacted", 0)
            val    = t.get("price", 0)
            date   = t.get("transactionDate", "")
            context += f"{date} | {name} | {action} | {shares:,} shares @ ${val}\n"
    if holders:
        context += "\nTop institutional holders:\n"
        for h in holders:
            context += f"  {h.get('investorName','')}: {h.get('sharesNumber',0):,} shares\n"
    prompt = (f"Insider trading analysis for ${symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: who is buying, who is selling, net sentiment over 90 days, "
              f"institutional ownership trend, bull and bear interpretation of the activity.")
    skill_prompt = get_skill_prompt("/insider")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} INSIDER ACTIVITY</b>\n<i>Source: FMP Live</i>\n\n" + response)


def handle_risk(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /risk NOW\nExample: /risk ASTS")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. NOW, ASTS).")
        return
    send_message(chat_id, f"Running risk check for {symbol}...")
    quote   = get_stock_quote(symbol)
    metrics = get_key_metrics(symbol)
    context = ""
    if quote and "_error" not in quote:
        context = (f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%) | "
                   f"Beta: {quote.get('beta','N/A')} | 52wk ${quote.get('yearLow','N/A')}–${quote.get('yearHigh','N/A')}")
    if metrics:
        context += (f"\nP/E: {metrics.get('peRatio','N/A')} | "
                    f"Debt/Equity: {metrics.get('debtToEquity','N/A')} | "
                    f"EV/EBITDA: {metrics.get('enterpriseValueOverEBITDA','N/A')}")
    prompt = f"""Risk check for a position in ${symbol}.
Cover: appropriate sizing for a $10K account,
max loss at stop, correlation risk, Kelly criterion suggestion,
leverage risk if any, liquidity/beta risk.
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
    owner = _is_owner(chat_id)
    base = """<b>LUMIS — Market Intelligence</b>
<i>Full conversational AI — just talk to me, or use a command.</i>

<b>Market Intelligence:</b>
/news — Top market stories + web headlines
/macro — Macro brief + Fed outlook
/earnings — Upcoming earnings calendar
/yields — Treasury yield curve
/premarket — Pre-market brief + futures
/sentiment — Market sentiment: VIX, breadth, flows, verdict
/rotation — Sector rotation: where money is moving

<b>Stock Research:</b>
/full [TICKER] — Deep analysis: moat, valuation, bull/bear
/opinion [TICKER] — Quick honest take
/technical [TICKER] — Chart analysis, RSI, MACD, levels
/options [TICKER] — Options flow, IV, best strategies
/insider [TICKER] — Insider buying/selling activity
/risk [TICKER] — Position risk check + sizing
/trade [TICKER] [BUY/SELL] [QTY] [@PRICE] — Analyze a specific trade setup
/squeeze [TICKER] — Short squeeze potential
/ipo [TICKER] — IPO analysis

<b>Sectors & Comparison:</b>
/scout — 3 fresh weekly picks (rotates sectors)
/sector [SECTOR] — Sector deep dive
/compare [T1] [T2] — Head-to-head comparison

<b>Macro & Alternative Markets:</b>
/crypto [SYMBOL] — Crypto analysis (default: BTC)
/fx [PAIR] — Forex + DXY analysis
/commodities — Oil, gold, copper, ags overview
/etf [TICKER] — ETF holdings, flows, analysis

<b>Investing & Wealth:</b>
/invest [TICKER] — Long-term analysis + DCA strategy
/dividend [TICKER] — Dividend sustainability + income math
/compounding — Wealth building compound math

<b>Live Data:</b>
/price [TICKER] — Live quote (instant, no AI)"""

    owner_section = """

<b>Owner Commands:</b>
/watchlist — Live prices for your watchlist
/momentum — Top momentum plays from your watchlist
/portfolio [ALLOCATION] — Portfolio review + rebalancing
/test — Check API connections"""

    footer = "\n\n<i>Powered by Claude Opus + FMP Live Data + Web Search</i>\n<i>Not financial advice. Always do your own research.</i>"

    send_message(chat_id, base + (owner_section if owner else "") + footer)


def handle_pals(chat_id):
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    web_link = f"\n\n<b>Web Chat:</b> https://{railway_domain}/chat" if railway_domain else ""
    msg = (
        "<b>LUMIS — for the pals</b>\n"
        "<i>Full AI market analyst. Ask me anything about any stock, sector, or market.</i>"
        + web_link +
        "\n\n<b>Research any stock:</b>\n"
        "/full [TICKER] — Deep analysis: moat, valuation, bull/bear\n"
        "/technical [TICKER] — Chart analysis, RSI, MACD, levels\n"
        "/options [TICKER] — Options flow, IV, strategies\n"
        "/insider [TICKER] — Insider buying/selling activity\n"
        "/risk [TICKER] — Position risk + sizing for $10K\n"
        "/trade [TICKER] [BUY/SELL] [QTY] [@PRICE] — Analyze a trade setup\n"
        "/invest [TICKER] — Long-term thesis + DCA plan\n"
        "/opinion [TICKER] — Quick honest take\n"
        "/squeeze [TICKER] — Short squeeze potential\n"
        "/compare [T1] [T2] — Head-to-head\n"
        "/dividend [TICKER] — Dividend sustainability\n\n"
        "<b>Market intelligence:</b>\n"
        "/sentiment — VIX, breadth, fund flows, verdict\n"
        "/rotation — Where money is moving right now\n"
        "/macro — Fed, rates, big picture\n"
        "/news — Top stories + what they mean\n"
        "/premarket — Futures, movers, what to watch\n"
        "/scout — 3 fresh stock picks this week\n"
        "/sector [SECTOR] — Sector deep dive\n\n"
        "<b>Alt markets:</b>\n"
        "/crypto [SYMBOL] — Crypto analysis\n"
        "/etf [TICKER] — ETF breakdown\n"
        "/commodities — Oil, gold, copper\n"
        "/fx [PAIR] — Forex analysis\n\n"
        "<i>Just talk to me too — no command needed.</i>"
    )
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
    q1, q2   = get_stock_quote(ticker1), get_stock_quote(ticker2)
    m1, m2   = get_key_metrics(ticker1), get_key_metrics(ticker2)
    c1, c2   = get_analyst_consensus(ticker1), get_analyst_consensus(ticker2)
    context  = _fmt_fundamentals(ticker1, q1, m1, consensus=c1)
    context += "\n\n" + _fmt_fundamentals(ticker2, q2, m2, consensus=c2)
    prompt = (f"Head-to-head comparison: ${ticker1} vs ${ticker2}. "
              f"Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: business model, valuation, growth rate, moat, "
              f"bull case and bear case for each, clear verdict on which to buy.")
    skill_prompt = get_skill_prompt("/compare")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{ticker1} vs {ticker2}</b>\n<i>Source: FMP Live</i>\n\n" + response)


def handle_dividend(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /dividend AAPL\nExample: /dividend KO")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters (e.g. AAPL, KO).")
        return
    send_message(chat_id, f"Analyzing {symbol} dividend...")
    quote   = get_stock_quote(symbol)
    ratios  = get_financial_ratios(symbol)
    income  = get_income_statement(symbol)
    context = _fmt_fundamentals(symbol, quote, ratios=ratios, income=income)
    if ratios:
        context += f"\nDividend yield: {ratios.get('dividendYield','N/A')} | Payout ratio: {ratios.get('payoutRatio','N/A')}"
    prompt = (f"Dividend analysis for ${symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: current yield, payout ratio, dividend growth history, FCF coverage, "
              f"sustainability, bull case (dividend grows), bear case (cut risk), "
              f"income scenario for $10K invested.")
    skill_prompt = get_skill_prompt("/dividend")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} DIVIDEND ANALYSIS</b>\n<i>Source: FMP Live</i>\n\n" + response)


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

    session = _market_session()
    lines = []

    # Regular last price (always shown)
    quote = get_stock_quote(symbol)
    if quote and "_error" not in quote:
        price  = quote.get("price", "N/A")
        chg    = quote.get("changesPercentage", quote.get("changePercentage", 0)) or 0
        arrow  = "↑" if chg >= 0 else "↓"
        prev   = quote.get("previousClose", "")
        prev_str = f" | Prev close: ${float(prev):.2f}" if prev else ""
        session_label = {"open": "Market open", "premarket": "Last close", "afterhours": "Last close", "closed": "Last close"}.get(session, "")
        lines.append(f"<b>{symbol}</b>: ${float(price):.2f} {arrow} {chg:+.2f}%  <i>({session_label})</i>{prev_str}")
    else:
        lines.append(get_stock_price_only(symbol))

    # Extended-hours price when market is not open
    if session in ("premarket", "afterhours", "closed"):
        ext = get_extended_price(symbol)
        if ext:
            ext_price = ext.get("price", ext.get("extendedPrice", ext.get("lastSalePrice")))
            ext_time  = ext.get("timestamp", ext.get("time", ""))
            if ext_price:
                label = "Pre-market" if session == "premarket" else "After-hours"
                time_str = ""
                if ext_time:
                    try:
                        ts = datetime.fromtimestamp(int(ext_time), tz=_ET)
                        time_str = f" ({ts.strftime('%I:%M %p ET')})"
                    except Exception:
                        pass
                lines.append(f"{label}: <b>${float(ext_price):.2f}</b>{time_str}")

    if session != "open":
        session_display = {"premarket": "Pre-market", "afterhours": "After-hours", "closed": "Market closed"}.get(session, "")
        lines.append(f"<i>{session_display} — NYSE/NASDAQ</i>")

    send_message(chat_id, "\n".join(lines))


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
    quote  = get_stock_quote(symbol)
    prices = get_historical_prices(symbol, days=60)
    context = ""
    if quote and "_error" not in quote:
        context = (f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
                   f"52wk High: ${quote.get('yearHigh','N/A')} | Low: ${quote.get('yearLow','N/A')} | "
                   f"Avg Vol: {quote.get('avgVolume','N/A')}")
    if prices and len(prices) >= 5:
        closes = [p.get("close", 0) for p in prices if p.get("close")]
        if closes:
            ma20  = sum(closes[-20:]) / min(len(closes), 20)
            ma50  = sum(closes[-50:]) / min(len(closes), 50) if len(closes) >= 10 else None
            high5 = max(closes[-5:])
            low5  = min(closes[-5:])
            context += (f"\n60-day price history ({len(closes)} sessions): "
                        f"Range ${min(closes):.2f}–${max(closes):.2f}\n"
                        f"20-day MA: ${ma20:.2f}" +
                        (f" | 50-day MA: ${ma50:.2f}" if ma50 else "") +
                        f"\nLast 5 sessions: High ${high5:.2f} / Low ${low5:.2f}")
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
    send_message(chat_id, f"Pulling crypto data for {symbol}...")
    fmp_quote = get_crypto_quote(symbol)
    context = f"Crypto: {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
    if fmp_quote:
        context += (f"Price: ${fmp_quote.get('price','N/A')} | "
                    f"Change: {fmp_quote.get('changesPercentage',0):+.2f}% | "
                    f"Market Cap: ${(fmp_quote.get('marketCap',0) or 0)/1e9:.1f}B\n")
    search = web_search(f"{symbol} crypto analysis price target {datetime.now().strftime('%B %d %Y')}")
    if search:
        context += f"Web data:\n{search}"
    prompt = (f"Crypto analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: price action, key support/resistance, on-chain signals if relevant, "
              f"macro crypto environment, bull case with target, bear case with stop, "
              f"entry range, position sizing. Also cover BTC dominance context.")
    skill_prompt = get_skill_prompt("/crypto")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} CRYPTO ANALYSIS</b>\n<i>Source: FMP + Web</i>\n\n" + response)


def handle_etf(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /etf QQQ\nExamples: /etf SPY | /etf ARKK | /etf GLD")
        return
    symbol = symbol.strip().upper()
    send_message(chat_id, f"Pulling ETF data for {symbol}...")
    quote    = get_stock_quote(symbol)
    holdings = get_etf_holdings(symbol)
    context  = ""
    if quote and "_error" not in quote:
        context = f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%) | YTD N/A\n"
    if holdings:
        context += "Top holdings:\n"
        for h in holdings[:8]:
            context += f"  {h.get('asset','')}: {h.get('weightPercentage','N/A')}%\n"
    search = web_search(f"{symbol} ETF fund flows performance {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"Web data:\n{search}"
    prompt = (f"ETF analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: what it tracks, top holdings, expense ratio, recent fund flows, "
              f"performance vs benchmark, bull case, bear case, who should own it.")
    skill_prompt = get_skill_prompt("/etf")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} ETF ANALYSIS</b>\n<i>Source: FMP + Web</i>\n\n" + response)


def handle_squeeze(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /squeeze GME\nExample: /squeeze BBBY")
        return
    symbol = symbol.strip().upper()
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>.")
        return
    send_message(chat_id, f"Pulling squeeze data for {symbol}...")
    quote    = get_stock_quote(symbol)
    short    = get_short_interest(symbol)
    context  = ""
    if quote and "_error" not in quote:
        context = (f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%) | "
                   f"Float: {quote.get('sharesOutstanding','N/A')}\n")
    if short:
        context += (f"Short interest: {short.get('shortInterest','N/A')} shares | "
                    f"Short % float: {short.get('shortPercentOfFloat','N/A')} | "
                    f"Days to cover: {short.get('daysToCover','N/A')}\n")
    search = web_search(f"{symbol} short squeeze interest {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"Web data:\n{search}"
    prompt = (f"Short squeeze analysis for {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: short interest %, days to cover, borrow rate, float, "
              f"squeeze trigger catalyst, bull case (squeeze target), bear case (short wins), "
              f"realistic probability, entry and stop.")
    skill_prompt = get_skill_prompt("/squeeze")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{symbol} SQUEEZE ANALYSIS</b>\n<i>Source: FMP + Web</i>\n\n" + response)


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
    send_message(chat_id, f"Pulling FX data for {pair}...")
    fmp_fx = get_forex_rates()
    context = f"FX: {pair}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
    if fmp_fx:
        for r in fmp_fx[:6]:
            context += f"{r.get('ticker','')}: {r.get('bid','N/A')} / {r.get('ask','N/A')} | chg {r.get('changes',0):+.4f}\n"
    search = web_search(f"{pair} forex currency analysis {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"Web data:\n{search}"
    prompt = (f"Forex/currency analysis for {pair}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: current trend and key levels, central bank policy differential, "
              f"macro drivers, correlation to equities/gold/commodities, "
              f"bull case (strengthens), bear case (weakens), impact on US multinationals.")
    skill_prompt = get_skill_prompt("/fx")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>{pair} FX ANALYSIS</b>\n<i>Source: FMP + Web</i>\n\n" + response)


def handle_commodities(chat_id):
    send_message(chat_id, "Pulling live commodity prices...")
    prices  = get_commodity_prices()
    context = f"Today: {datetime.now().strftime('%B %d, %Y')}\n"
    if prices:
        context += "Live commodity prices:\n"
        for c in prices[:10]:
            context += f"  {c.get('symbol','')}: ${c.get('price','N/A')} ({c.get('changesPercentage',0):+.2f}%)\n"
    search = web_search(f"commodities oil gold copper outlook {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"Web data:\n{search}"
    prompt = (f"Commodities market overview. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: oil (WTI/Brent) trend, gold vs real yields, silver industrial/monetary, "
              f"copper as economic signal, natural gas, ags if notable. "
              f"Bull case and bear case for each. What the complex signals about the economy.")
    skill_prompt = get_skill_prompt("/commodities")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>COMMODITIES</b>\n<i>Source: FMP Live</i>\n{datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_premarket(chat_id):
    send_message(chat_id, "Pulling pre-market intelligence...")
    commodities = get_commodity_prices()
    rates       = get_treasury_rates()
    context     = f"Today: {datetime.now().strftime('%B %d, %Y')}\n"
    if commodities:
        key_comms = {c.get("symbol", ""): c for c in commodities if c.get("symbol")}
        for sym in ["ZGUSD", "CLUSD", "BZUSD", "HGUSD"]:
            if sym in key_comms:
                c = key_comms[sym]
                context += f"{c.get('name', sym)}: ${c.get('price','N/A')} ({c.get('changesPercentage',0):+.2f}%)\n"
    if rates and "_error" not in rates:
        context += (f"10yr: {rates.get('year10','N/A')}% | "
                    f"2yr: {rates.get('year2','N/A')}% | "
                    f"30yr: {rates.get('year30','N/A')}%\n")
    search = web_search(f"pre-market movers futures overnight {datetime.now().strftime('%B %d %Y')}")
    if search:
        context += f"Pre-market data:\n{search}"
    prompt = (f"Pre-market brief. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: S&P 500 / Nasdaq futures direction, overnight catalysts, "
              f"major pre-market movers (up AND down) and why, key economic data releasing today, "
              f"what to watch at open, bull case for today's session, bear case for today's session.")
    skill_prompt = get_skill_prompt("/premarket")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>PRE-MARKET BRIEF</b>\n{datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n" + response)


def handle_sentiment(chat_id):
    send_message(chat_id, "Reading market sentiment...")
    sectors = get_sector_performance()
    rates   = get_treasury_rates()
    context = f"Today: {datetime.now().strftime('%B %d, %Y')}\n"
    if sectors:
        context += "Sector performance:\n"
        for s in sectors[:11]:
            context += f"  {s.get('sector','')}: {s.get('changesPercentage',0):+.2f}%\n"
    if rates and "_error" not in rates:
        context += (f"Yields — 10yr: {rates.get('year10','N/A')}% | "
                    f"2yr: {rates.get('year2','N/A')}% | "
                    f"Spread: {(float(rates.get('year10',0) or 0) - float(rates.get('year2',0) or 0)):+.2f}%\n")
    watchlist_quotes = []
    for sym in ["SPY", "QQQ", "IWM", "VIX"]:
        q = get_stock_quote(sym)
        if q and "_error" not in q:
            watchlist_quotes.append(f"{sym}: ${q.get('price','N/A')} ({q.get('changePercentage',0):+.2f}%)")
    if watchlist_quotes:
        context += " | ".join(watchlist_quotes) + "\n"
    search = web_search(f"market sentiment VIX put call ratio fear greed {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"Web data:\n{search}"
    prompt = (f"Market sentiment analysis. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: overall risk-on/risk-off regime, VIX interpretation, sector breadth, "
              f"yield curve signal, fund flow direction, contrarian signals, positioning recommendation.")
    skill_prompt = get_skill_prompt("/sentiment")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>MARKET SENTIMENT</b>\n<i>Source: FMP Live + Web</i>\n{datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_rotation(chat_id):
    send_message(chat_id, "Analyzing sector rotation...")
    sectors = get_sector_performance()
    rates   = get_treasury_rates()
    context = f"Today: {datetime.now().strftime('%B %d, %Y')}\n"
    if sectors:
        sorted_sectors = sorted(sectors, key=lambda x: x.get("changesPercentage", 0), reverse=True)
        context += "Sector performance (ranked):\n"
        for s in sorted_sectors:
            context += f"  {s.get('sector','')}: {s.get('changesPercentage',0):+.2f}%\n"
    if rates and "_error" not in rates:
        context += (f"Yields — 10yr: {rates.get('year10','N/A')}% | "
                    f"2yr: {rates.get('year2','N/A')}% | "
                    f"30yr: {rates.get('year30','N/A')}%\n")
    search = web_search(f"sector rotation market leadership {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"Web data:\n{search}"
    prompt = (f"Sector rotation analysis. Today: {datetime.now().strftime('%B %d, %Y')}\n"
              f"Cover: current rotation regime, top/bottom 3 sectors with macro drivers, "
              f"growth vs value, best ETF plays, highest-conviction rotation trade.")
    skill_prompt = get_skill_prompt("/rotation")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"<b>SECTOR ROTATION</b>\n<i>Source: FMP Live + Web</i>\n{datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_trade(chat_id, args):
    """
    /trade TICKER DIRECTION [QTY] [@PRICE]
    Examples:
      /trade NVDA BUY 10 @145.50
      /trade ASTS LONG 50
      /trade SOFI SHORT
    """
    if not args:
        send_message(chat_id, (
            "❌ Usage: /trade TICKER DIRECTION [QTY] [@PRICE]\n\n"
            "Examples:\n"
            "  /trade NVDA BUY 10 @145.50\n"
            "  /trade ASTS LONG 50\n"
            "  /trade SOFI SHORT\n\n"
            "DIRECTION: BUY, LONG, SELL, or SHORT"
        ))
        return

    tokens = args.upper().split()
    symbol = tokens[0].lstrip("$")
    if not _valid_ticker(symbol):
        send_message(chat_id, f"❌ Invalid ticker: <b>{symbol}</b>. Use 1–5 uppercase letters.")
        return

    direction = tokens[1] if len(tokens) > 1 else ""
    if direction not in {"BUY", "LONG", "SELL", "SHORT"}:
        send_message(chat_id, (
            f"❌ Direction must be BUY, LONG, SELL, or SHORT.\n"
            f"Example: /trade {symbol} BUY 10 @145.50"
        ))
        return

    qty = None
    entry_price = None
    for tok in tokens[2:]:
        clean = tok.lstrip("@$")
        try:
            val = float(clean)
            if "@" in tok or tok.startswith("$"):
                entry_price = val
            elif qty is None:
                qty = int(val)
        except ValueError:
            pass

    send_message(chat_id, f"Analyzing {direction} trade for {symbol}...")

    quote   = get_stock_quote(symbol)
    metrics = get_key_metrics(symbol)
    hist    = get_historical_prices(symbol, days=30)

    context = f"Today: {datetime.now().strftime('%B %d, %Y')}\n"
    context += f"Trade setup: {direction} {symbol}"
    if qty:
        context += f", {qty} shares"
    if entry_price:
        context += f", target entry @ ${entry_price:.2f}"
    context += "\n"

    current_price = None
    if quote and "_error" not in quote:
        current_price = quote.get("price")
        context += (
            f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%) | "
            f"Volume: {quote.get('volume','N/A')} | Avg Vol: {quote.get('avgVolume','N/A')} | "
            f"Beta: {quote.get('beta','N/A')} | "
            f"52wk ${quote.get('yearLow','N/A')}–${quote.get('yearHigh','N/A')}\n"
        )
        if entry_price and current_price:
            gap_pct = ((current_price - entry_price) / entry_price) * 100
            label = "above" if gap_pct > 0 else "below"
            context += f"Entry vs current: {abs(gap_pct):.2f}% {label} your target entry\n"
        if qty and current_price:
            context += f"Position value at current price: ${qty * current_price:,.0f}\n"

    if metrics:
        context += (
            f"P/E: {metrics.get('peRatio','N/A')} | "
            f"P/S: {metrics.get('priceToSalesRatio','N/A')} | "
            f"Debt/Equity: {metrics.get('debtToEquity','N/A')} | "
            f"ROE: {metrics.get('roe','N/A')}\n"
        )

    if hist and len(hist) >= 5:
        closes = [d.get("close") for d in hist if d.get("close")]
        if len(closes) >= 5:
            context += f"Last 5 closes: {', '.join(f'${p:.2f}' for p in closes[-5:])}\n"
            context += f"30-day range: ${min(closes):.2f}–${max(closes):.2f}\n"

    search = web_search(f"{symbol} stock trade setup news {datetime.now().strftime('%B %Y')}")
    if search:
        context += f"Web data:\n{search}"

    direction_label = "LONG (BUY)" if direction in ("BUY", "LONG") else "SHORT (SELL)"
    entry_line = f"Target entry: ${entry_price:.2f}" if entry_price else "No specific entry price — analyze at current price"
    sizing_line = f"Quantity: {qty} shares" if qty else "No quantity specified"

    prompt = (
        f"Trade analysis for {direction_label} {symbol}. Today: {datetime.now().strftime('%B %d, %Y')}\n"
        f"{entry_line}\n{sizing_line}\n\n"
        f"Analyze this trade setup covering:\n"
        f"1. Entry quality vs current price and technical levels\n"
        f"2. Trade thesis and setup type\n"
        f"3. Risk/reward: upside target and stop loss with specific price levels\n"
        f"4. Position sizing for a $10K account\n"
        f"5. Red flags or risks specific to this setup right now\n"
        f"6. VERDICT: STRONG SETUP / NEUTRAL / AVOID\n\n"
        f"Be direct. If the trade looks bad, say so. "
        f"Keep the entire response under 3800 characters."
    )
    skill_prompt = get_skill_prompt("/trade")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)

    header = f"<b>{symbol} TRADE ANALYSIS — {direction}</b>\n<i>Source: FMP Live + Web</i>\n"
    if entry_price:
        header += f"<i>Entry: ${entry_price:.2f}</i>"
    if qty:
        header += f"  |  <i>{qty} shares</i>"
    header = header.rstrip() + "\n"
    send_message(chat_id, header + "\n" + response)


# ─────────────────────────────────────
# STARFIRE TELEGRAM TICKET PARSER
# ─────────────────────────────────────
def _parse_telegram_ticket(text):
    """
    Detect and parse a STARFIRE formatted ticket sent as a Telegram message.
    Returns a dict or None if the message is not a ticket.
    """
    upper = text.upper()
    # Must mention STARFIRE and either LUMISNOVA or TICKET
    if "STARFIRE" not in upper:
        return None
    if "TICKET" not in upper and "LUMISNOVA" not in upper:
        return None

    ticket = {}

    # Ticket number
    m = re.search(r'Ticket\s*#(\d+)', text, re.IGNORECASE)
    ticket["number"] = int(m.group(1)) if m else None

    # Title — first non-empty line after "assigned"
    m = re.search(r'assigned[^\n]*\n+\s*([^\n]+)', text, re.IGNORECASE)
    ticket["title"] = m.group(1).strip() if m else "STARFIRE Task"

    # Task body — everything between title line and "Priority:" / "Fetch via:"
    m = re.search(
        r'assigned[^\n]*\n+\s*[^\n]+\n+(.*?)(?:Priority:|Fetch via:|$)',
        text, re.IGNORECASE | re.DOTALL
    )
    ticket["task"] = m.group(1).strip() if m else text

    # Priority
    m = re.search(r'Priority:\s*(\d+)', text, re.IGNORECASE)
    ticket["priority"] = int(m.group(1)) if m else None

    return ticket if (ticket["title"] or ticket["task"]) else None


def _execute_telegram_ticket(chat_id, ticket):
    """Acknowledge a Telegram-format STARFIRE ticket and execute the task."""
    num_str    = f"#{ticket['number']}" if ticket.get("number") else ""
    title      = ticket.get("title", "Task")
    priority   = ticket.get("priority")
    task       = ticket.get("task") or title

    pri_str    = f" | Priority: {priority}/10" if priority else ""
    send_message(chat_id, f"Ticket {num_str} acknowledged. <b>{title}</b>{pri_str}\nPulling data and building report...")

    # Store ticket number for status tracking
    tid = ticket.get("number", "telegram")
    _ticket_status[tid] = "IN PROGRESS"

    # Build rich context using _execute_starfire_task infrastructure
    result = _execute_starfire_task(f"{title}. {task}")

    send_message(chat_id, f"<b>TICKET {num_str} — {title.upper()}</b>\n<i>LUMISNOVA Report</i>\n\n{result}")
    send_message(chat_id, f"Ticket {num_str} COMPLETE. Report delivered.")
    _ticket_status[tid] = "DONE"
    _pending_telegram_tickets.pop(chat_id, None)


# ─────────────────────────────────────
# COMMAND ROUTER
# ─────────────────────────────────────
def process_command(chat_id, text):
    text = text.strip()

    # ── STARFIRE ticket detection (formatted Telegram message) ────────
    ticket = _parse_telegram_ticket(text)
    if ticket:
        _pending_telegram_tickets[chat_id] = ticket
        threading.Thread(target=_execute_telegram_ticket, args=(chat_id, ticket), daemon=True).start()
        return

    # ── "complete ticket" shorthand — execute the last pending ticket ─
    text_low = text.lower()
    if any(p in text_low for p in ("complete ticket", "execute ticket", "work on ticket", "run ticket", "do the ticket")):
        pending = _pending_telegram_tickets.get(chat_id)
        if pending:
            threading.Thread(target=_execute_telegram_ticket, args=(chat_id, pending), daemon=True).start()
        else:
            send_message(chat_id, "No pending ticket found. Send a STARFIRE ticket first.")
        return

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
        "/pals":        lambda: handle_pals(chat_id),
        "/watchlist":   lambda: handle_watchlist(chat_id) if _is_owner(chat_id) else send_message(chat_id, _OWNER_ONLY_MSG),
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
        "/momentum":    lambda: handle_momentum(chat_id) if _is_owner(chat_id) else send_message(chat_id, _OWNER_ONLY_MSG),
        "/portfolio":   lambda: handle_portfolio(chat_id, rest) if _is_owner(chat_id) else send_message(chat_id, _OWNER_ONLY_MSG),
        "/price":       lambda: handle_price(chat_id, argument),
        "/test":        lambda: handle_test(chat_id) if _is_owner(chat_id) else send_message(chat_id, _OWNER_ONLY_MSG),
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
        "/trade":       lambda: handle_trade(chat_id, rest),
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

        # Inject live prices + FMP fundamentals when tickers are detected
        text_upper = text.upper()
        tickers_found = re.findall(r'\b[A-Z]{2,5}\b', text_upper)
        ticker_candidates = [t for t in tickers_found if t not in _SKIP_WORDS and len(t) >= 2]

        market_keywords = ["MARKET", "SPY", "QQQ", "NASDAQ", "S&P", "DOW", "STOCK", "PRICE", "CRYPTO", "BITCOIN", "BTC"]
        if any(w in text_upper for w in WATCHLIST + market_keywords):
            for symbol in WATCHLIST[:6]:
                quote = get_stock_quote(symbol)
                if quote and "_error" not in quote:
                    context += f"\n{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)"
        elif ticker_candidates:
            # Pull rich FMP data for the first detected ticker
            primary = ticker_candidates[0]
            quote   = get_stock_quote(primary)
            metrics = get_key_metrics(primary)
            fdata   = _fmt_fundamentals(primary, quote, metrics)
            if fdata:
                context += f"\n{fdata}"

        # Web search enrichment
        search_query = None
        if any(w in text_upper for w in ["NEWS", "LATEST", "TODAY", "HAPPENED", "RECENT"]):
            search_query = f"{text} {datetime.now().strftime('%B %Y')}"
        elif ticker_candidates:
            search_query = f"{ticker_candidates[0]} stock news {datetime.now().strftime('%B %Y')}"
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
# ─────────────────────────────────────
# WEB CHAT INTERFACE (Extended Mode)
# Friends visit /chat in any browser
# ─────────────────────────────────────
_CHAT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LUMIS — Market Intelligence</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;height:100vh;display:flex;flex-direction:column}
#hdr{padding:14px 20px;border-bottom:1px solid #21262d;display:flex;align-items:center;gap:10px;flex-shrink:0}
#hdr h1{font-size:18px;font-weight:700;color:#00d4aa;letter-spacing:2px}
#hdr span{font-size:12px;color:#8b949e}
.dot{width:8px;height:8px;border-radius:50%;background:#00d4aa;flex-shrink:0}
#gate{flex:1;display:flex;align-items:center;justify-content:center;padding:24px}
#gbox{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:32px;max-width:340px;width:100%;text-align:center}
#gbox h2{color:#00d4aa;margin-bottom:6px;font-size:17px}
#gbox p{color:#8b949e;font-size:13px;margin-bottom:22px}
#gcin{width:100%;padding:10px 14px;background:#0d1117;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:15px;outline:none;text-align:center;letter-spacing:3px}
#gcin:focus{border-color:#00d4aa}
#gbtn{margin-top:10px;width:100%;padding:10px;background:#00d4aa;color:#0d1117;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}
#gbtn:hover{background:#00b894}
#gerr{color:#f85149;font-size:13px;margin-top:8px;display:none}
#chat{flex:1;display:none;flex-direction:column;min-height:0}
#msgs{flex:1;overflow-y:auto;padding:14px 20px;display:flex;flex-direction:column;gap:10px}
.mu{align-self:flex-end;background:#1c2128;border:1px solid #30363d;padding:9px 13px;border-radius:12px 12px 2px 12px;font-size:14px;max-width:65%;word-break:break-word}
.mb{align-self:flex-start;background:#161b22;border:1px solid #21262d;padding:11px 15px;border-radius:2px 12px 12px 12px;font-size:14px;line-height:1.65;max-width:88%;word-break:break-word}
.mb b{color:#00d4aa}
.mb i{color:#8b949e}
.mb code{background:#0d1117;padding:2px 5px;border-radius:4px;font-family:monospace;font-size:12px}
.typing{display:flex;gap:4px;padding:4px 2px;align-items:center}
.typing span{width:6px;height:6px;border-radius:50%;background:#00d4aa;animation:bop 1.2s infinite}
.typing span:nth-child(2){animation-delay:.2s}
.typing span:nth-child(3){animation-delay:.4s}
@keyframes bop{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
#chips{padding:6px 20px 8px;display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0}
.chip{padding:4px 10px;background:#161b22;border:1px solid #21262d;border-radius:20px;font-size:12px;color:#8b949e;cursor:pointer;white-space:nowrap}
.chip:hover{border-color:#00d4aa;color:#00d4aa}
#ibar{padding:10px 20px 16px;border-top:1px solid #21262d;display:flex;gap:8px;flex-shrink:0}
#tin{flex:1;padding:9px 13px;background:#161b22;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:14px;outline:none;resize:none;max-height:110px;line-height:1.5}
#tin:focus{border-color:#00d4aa}
#snd{padding:9px 16px;background:#00d4aa;color:#0d1117;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;flex-shrink:0}
#snd:hover{background:#00b894}
#snd:disabled{background:#30363d;color:#8b949e;cursor:not-allowed}
@media(max-width:600px){#msgs,#chips,#ibar{padding-left:12px;padding-right:12px}.mu{max-width:82%}}
</style>
</head>
<body>
<div id="hdr">
  <div class="dot"></div>
  <h1>LUMIS</h1>
  <span>Market Intelligence Platform</span>
</div>

<div id="gate">
  <div id="gbox">
    <h2>Access Required</h2>
    <p>Enter your access code to connect to LUMIS</p>
    <input id="gcin" type="password" placeholder="Access code" autocomplete="off"/>
    <button id="gbtn" onclick="checkAccess()">Connect</button>
    <div id="gerr">Invalid code. Try again.</div>
  </div>
</div>

<div id="chat">
  <div id="msgs"></div>
  <div id="chips">
    <span class="chip" onclick="fill(this)">Analyze NVDA</span>
    <span class="chip" onclick="fill(this)">Is AAPL a buy?</span>
    <span class="chip" onclick="fill(this)">TSLA insider activity</span>
    <span class="chip" onclick="fill(this)">Best sector right now</span>
    <span class="chip" onclick="fill(this)">Market sentiment today</span>
    <span class="chip" onclick="fill(this)">BTC analysis</span>
  </div>
  <div id="ibar">
    <textarea id="tin" rows="1" placeholder="Ask about any stock, sector, macro, insiders..." onkeydown="onKey(event)" oninput="resize(this)"></textarea>
    <button id="snd" onclick="send()">Send</button>
  </div>
</div>

<script>
const NEEDS_CODE=ACCESS_CODE_PLACEHOLDER;
let sid=Math.random().toString(36).slice(2)+Date.now().toString(36);
let code='';

window.onload=function(){if(!NEEDS_CODE)openChat();};

function checkAccess(){
  const c=document.getElementById('gcin').value.trim();
  if(!c)return;
  fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({message:'__auth__',session_id:sid,access_code:c})})
  .then(r=>r.json()).then(d=>{
    if(d.ok){code=c;openChat(d.response);}
    else{const e=document.getElementById('gerr');e.style.display='block';}
  }).catch(()=>{const e=document.getElementById('gerr');e.textContent='Connection error.';e.style.display='block';});
}
document.getElementById('gcin').addEventListener('keydown',e=>{if(e.key==='Enter')checkAccess();});

function openChat(welcome){
  document.getElementById('gate').style.display='none';
  document.getElementById('chat').style.display='flex';
  if(welcome)addMsg('mb',welcome);
}

function addMsg(cls,html,raw){
  const d=document.createElement('div');
  d.className='msg '+cls;
  if(cls==='mu')d.textContent=html;else d.innerHTML=html;
  document.getElementById('msgs').appendChild(d);
  d.scrollIntoView({behavior:'smooth',block:'end'});
  return d;
}

function showTyping(){
  const d=document.createElement('div');
  d.className='msg mb';d.id='typ';
  d.innerHTML='<div class="typing"><span></span><span></span><span></span></div>';
  document.getElementById('msgs').appendChild(d);
  d.scrollIntoView({behavior:'smooth',block:'end'});
}
function rmTyping(){const t=document.getElementById('typ');if(t)t.remove();}

function fill(el){document.getElementById('tin').value=el.textContent;document.getElementById('tin').focus();}
function onKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}
function resize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,110)+'px';}

function send(){
  const inp=document.getElementById('tin');
  const txt=inp.value.trim();if(!txt)return;
  inp.value='';inp.style.height='auto';
  document.getElementById('snd').disabled=true;
  addMsg('mu',txt);
  showTyping();
  fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({message:txt,session_id:sid,access_code:code})})
  .then(r=>r.json()).then(d=>{
    rmTyping();
    if(d.ok)addMsg('mb',d.response);
    else addMsg('mb','<i>Error: '+(d.error||'Unknown error')+'</i>');
  }).catch(()=>{rmTyping();addMsg('mb','<i>Connection error. Please try again.</i>');})
  .finally(()=>{document.getElementById('snd').disabled=false;inp.focus();});
}
</script>
</body>
</html>
"""


def _handle_web_chat(body_bytes):
    """Handle POST /api/chat from the web interface."""
    try:
        data       = json.loads(body_bytes)
        message    = data.get("message", "").strip()
        session_id = data.get("session_id", "anonymous")
        access_code = data.get("access_code", "")

        # Validate access code if one is configured
        if CHAT_ACCESS_CODE and access_code != CHAT_ACCESS_CODE:
            return {"ok": False, "error": "Invalid access code"}

        # Auth ping — just confirm connection
        if message == "__auth__":
            return {"ok": True, "response": (
                "<b>LUMIS online.</b> Ask me anything — stocks, sectors, insiders, "
                "macro, crypto, options, or just talk markets."
            )}

        if not message:
            return {"ok": False, "error": "Empty message"}

        # Per-session history
        history = _web_sessions.get(session_id, [])

        # Build context (same logic as conversational fallback)
        context = f"Today: {datetime.now().strftime('%B %d, %Y %I:%M %p ET')}"
        text_upper = message.upper()
        tickers_found = re.findall(r'\b[A-Z]{2,5}\b', text_upper)
        ticker_candidates = [t for t in tickers_found if t not in _SKIP_WORDS and len(t) >= 2]

        market_keywords = ["MARKET", "SPY", "QQQ", "NASDAQ", "S&P", "DOW", "STOCK", "PRICE", "CRYPTO", "BITCOIN", "BTC"]
        if any(w in text_upper for w in WATCHLIST + market_keywords):
            for sym in WATCHLIST[:5]:
                q = get_stock_quote(sym)
                if q and "_error" not in q:
                    context += f"\n{sym}: ${q.get('price','N/A')} ({q.get('changePercentage',0):+.2f}%)"
        elif ticker_candidates:
            primary = ticker_candidates[0]
            q       = get_stock_quote(primary)
            m       = get_key_metrics(primary)
            fdata   = _fmt_fundamentals(primary, q, m)
            if fdata:
                context += f"\n{fdata}"

        search_query = None
        if any(w in text_upper for w in ["NEWS", "LATEST", "TODAY", "HAPPENED", "RECENT"]):
            search_query = f"{message} {datetime.now().strftime('%B %Y')}"
        elif ticker_candidates:
            search_query = f"{ticker_candidates[0]} stock news {datetime.now().strftime('%B %Y')}"
        if search_query:
            found = web_search(search_query)
            if found:
                context += f"\nWeb data:\n{found}"

        response = ask_claude(message, context, history=history)

        # Update session history
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response},
        ]
        _web_sessions[session_id] = history[-((_WEB_MAX_HISTORY * 2)):]

        return {"ok": True, "response": response}

    except Exception as e:
        log.error(f"Web chat error: {e}")
        return {"ok": False, "error": "Internal error"}


_WEBHOOK_PATH = "/webhook"
_ARGUS_PATH   = "/argus"
_OSIRIS_PATH  = "/osiris"

# In-memory ticket status store  {ticket_id: "IN PROGRESS" | "DONE" | "BLOCKED: ..."}
_ticket_status = {}

# Last pending Telegram-format ticket per chat  {chat_id_str: ticket_dict}
_pending_telegram_tickets = {}

class _WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == _WEBHOOK_PATH:
            # Reject deliveries meant for a different instance — sends 200 so Telegram won't retry
            received_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if received_secret != _WEBHOOK_SECRET:
                log.warning(f"Stale webhook rejected (secret mismatch) — old instance still draining?")
                self.send_response(200)
                self.end_headers()
                return
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

        elif self.path == "/api/chat":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            result = _handle_web_chat(body)
            self.wfile.write(json.dumps(result).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"LUMISNOVA ONLINE | STARFIRE: /argus | OSIRIS: /osiris | Telegram: /webhook | Web: /chat")
        elif self.path == "/chat":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = _CHAT_HTML.replace(
                "ACCESS_CODE_PLACEHOLDER",
                "true" if CHAT_ACCESS_CODE else "false"
            )
            self.wfile.write(html.encode("utf-8"))
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

    # ── Deduplication (atomic check-and-add under lock) ─────────────
    update_id = update.get("update_id")
    if update_id is not None:
        with _processed_updates_lock:
            if update_id in _processed_updates:
                log.warning(f"Duplicate update {update_id} — skipping")
                return
            _processed_updates.add(update_id)
            if len(_processed_updates) > 1000:
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

    # ── Staleness check — drop replayed/retried webhooks ────────────
    msg_date = message.get("date", 0)
    if msg_date:
        age = int(time.time()) - msg_date
        if age > 60:
            log.warning(f"Stale message dropped (age={age}s, update_id={update_id})")
            return

    # ── Content dedup — drop identical (chat, text) within the window ─
    # Stops duplicate execution when Telegram sends the same command as two
    # separate update_ids (which the update_id set above cannot catch).
    now = time.time()
    key = (chat_id, text)
    with _recent_messages_lock:
        last_seen = _recent_messages.get(key)
        if last_seen is not None and (now - last_seen) < _RECENT_WINDOW_SEC:
            log.warning(f"Duplicate content dropped (chat={chat_id}, text={text!r})")
            return
        _recent_messages[key] = now
        # Prune old entries to prevent unbounded growth
        if len(_recent_messages) > 500:
            cutoff = now - _RECENT_WINDOW_SEC
            for k in [k for k, ts in _recent_messages.items() if ts < cutoff]:
                _recent_messages.pop(k, None)

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
_starfire_processed: set = set()
_starfire_lock = threading.Lock()


def _starfire_dispatch(payload):
    # Dedup by request_id or ticket_id to prevent double-execution on retried payloads
    req_id = payload.get("request_id") or payload.get("data", {}).get("ticket_id")
    if req_id is not None:
        with _starfire_lock:
            if req_id in _starfire_processed:
                log.warning(f"Duplicate STARFIRE payload {req_id} — skipping")
                return
            _starfire_processed.add(req_id)
            if len(_starfire_processed) > 500:
                _starfire_processed.clear()

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
                json={"url": webhook_url, "drop_pending_updates": True, "secret_token": _WEBHOOK_SECRET},
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
            f"Instance: <code>{_INSTANCE_ID}</code>\n"
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
            f"Instance: <code>{_INSTANCE_ID}</code>\n"
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
                        # Route through _dispatch so dedup + staleness checks apply
                        _dispatch(update)
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

"""
LUMIS CAPITAL BOT
Powered by Claude AI + FMP Live Data
Telegram Bot for Market Intelligence
"""

import os
import re
import requests
import time
import logging
from datetime import datetime, timedelta
from skills import get_skill_prompt

# ─────────────────────────────────────
# CONFIGURATION — SET IN RAILWAY ENV
# ─────────────────────────────────────
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID           = os.environ.get("CHAT_ID")
FMP_API_KEY       = os.environ.get("FMP_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ─────────────────────────────────────
# LOGGING
# ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

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
SYSTEM_PROMPT = """You are Lumis Nova, an AI-powered market research
assistant for Lumis Capital. You provide trading and investing intelligence
using live market data.

CORE RULES:
- Always show bull AND bear case on every analysis
- Always include position sizing recommendations
- Always include stop loss levels
- Translate all financials to USD
- Label all data sources clearly
- Separate trading (weeks) from investing (years)
- Show earnings surprise history before any options trade idea
- Be honest when uncertain — never guess
- Push back on overleveraged sizing
- Never promise profits or guarantee returns

TONE:
Professional but direct. Honest over exciting.
Data-driven. Minimal emoji use — plain text is preferred.
Always end with: Not financial advice. Always do your own research."""

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


def get_updates(offset=None, timeout=30):
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
    url = "https://financialmodelingprep.com/stable/treasury"
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
    url = "https://financialmodelingprep.com/stable/earning-calendar"
    params = {"from": today, "to": end, "apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for earning-calendar: {response.text}")
            return {"_error": _fmp_error_message(response.status_code, "earning-calendar")}
        return response.json()
    except requests.exceptions.Timeout:
        log.error("FMP earnings calendar timeout")
        return {"_error": "⚠️ FMP timed out fetching earnings calendar. Try again shortly."}
    except Exception as e:
        log.error(f"FMP earnings error: {e}")
        return []


def get_stock_news():
    tickers = ",".join(WATCHLIST[:6])
    url = "https://financialmodelingprep.com/stable/news/stock"
    params = {"symbols": tickers, "limit": 10, "apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for news/stock: {response.text}")
            return {"_error": _fmp_error_message(response.status_code, "news/stock")}
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
def ask_claude(prompt, context="", skill_prompt=None):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    system = skill_prompt if skill_prompt else SYSTEM_PROMPT
    full_prompt = f"{context}\n\n{prompt}" if context else prompt

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "system": system,
        "messages": [{"role": "user", "content": full_prompt}]
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        # Retry on overload with reduced tokens
        if response.status_code in (529, 503):
            log.warning(f"Anthropic API overloaded ({response.status_code}), retrying with reduced tokens...")
            payload["max_tokens"] = 500
            response = requests.post(url, headers=headers, json=payload, timeout=20)
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
        log.error("Claude API timeout after 20s — retrying with reduced tokens")
        try:
            payload["max_tokens"] = 400
            response = requests.post(url, headers=headers, json=payload, timeout=20)
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
    msg = """<b>LUMIS CAPITAL BOT</b>
Powered by Lumis Nova AI

<b>Commands:</b>
/news — Market stories
/macro — Yields + economic data
/earnings — Upcoming calendar
/scout — Weekly stock picks
/watchlist — Live prices
/price [TICKER] — Live quote (no AI)
/full [TICKER] — Full analysis
/opinion [TICKER] — Quick take
/invest [TICKER] — Long-term view
/yields — Treasury curve
/insider [TICKER] — Insider activity
/risk [TICKER] — Risk check
/compounding — Wealth math
/sector [SECTOR] — Sector analysis
/compare [T1] [T2] — Compare two stocks
/dividend [TICKER] — Dividend analysis
/momentum — Momentum plays
/portfolio [ALLOCATION] — Portfolio review
/test — Check API connections
/help — All commands

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


def handle_scout(chat_id):
    send_message(chat_id, "Running weekly scout...")
    context = f"Weekly scout {datetime.now().strftime('%B %d, %Y')}\n"
    for symbol in WATCHLIST[:6]:
        quote = get_stock_quote(symbol)
        if quote and "_error" not in quote:
            context += f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
    prompt = """3 stock picks for this week.
For each: ticker, thesis, why this week specifically,
bull case, bear case, entry range, stop loss, target, sizing.
Show both sides. Never just hype."""
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
    msg = """<b>LUMIS CAPITAL — ALL COMMANDS</b>

<b>Intelligence:</b>
/news — Market stories
/macro — Yields + economic data
/earnings — Upcoming calendar
/yields — Treasury curve

<b>Research:</b>
/full [TICKER] — Complete analysis
/opinion [TICKER] — Quick take
/scout — Weekly picks
/insider [TICKER] — Insider activity
/risk [TICKER] — Position risk check
/sector [SECTOR] — Sector analysis
/compare [T1] [T2] — Compare two stocks

<b>Investing:</b>
/invest [TICKER] — Long-term analysis
/compounding — Wealth building math
/dividend [TICKER] — Dividend analysis

<b>Portfolio:</b>
/watchlist — Live prices
/price [TICKER] — Live quote (no AI)
/momentum — Momentum plays
/portfolio [ALLOCATION] — Portfolio review

<b>Utility:</b>
/test — Check all API connections

<b>Examples:</b>
/full NOW | /opinion ASTS | /invest GOOGL
/sector tech | /compare NVDA AMD | /dividend AAPL
/portfolio NVDA:40 AAPL:30 CASH:30

<i>Powered by Lumis Nova AI + FMP Live Data</i>
<i>Not financial advice. Always DYOR.</i>"""
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
            "model": "claude-sonnet-4-6",
            "max_tokens": 20,
            "messages": [{"role": "user", "content": "Reply with OK"}]
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if resp.status_code == 200:
            results.append("✅ <b>Claude API</b>: Connected — Lumis Nova is online")
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

    status_line = "✅ All systems operational" if all(r.startswith("✅") for r in results) else "⚠️ One or more issues detected"
    msg = "\n".join(results) + f"\n\n{status_line}\n{datetime.now().strftime('%b %d %Y | %I:%M %p ET')}"
    send_message(chat_id, msg)


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
    }

    handler = routes.get(command)
    if handler:
        log.info(f"Processing command '{command}' for chat {chat_id}")
        handler()
        log.info(f"Response sent for command '{command}' to chat {chat_id}")
    else:
        log.info(f"No route matched '{command}', falling back to Claude for chat {chat_id}")
        response = ask_claude(
            text,
            f"User message via Lumis Capital Telegram bot. Today: {datetime.now().strftime('%B %d, %Y')}"
        )
        send_message(chat_id, response)
        log.info(f"Claude fallback response sent to chat {chat_id}")


# ─────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────
def run_bot():
    log.info("🚀 Lumis Capital Bot starting...")

    missing = []
    if not TELEGRAM_TOKEN:    missing.append("TELEGRAM_TOKEN")
    if not CHAT_ID:           missing.append("CHAT_ID")
    if not FMP_API_KEY:       missing.append("FMP_API_KEY")
    if not ANTHROPIC_API_KEY: missing.append("ANTHROPIC_API_KEY")

    if missing:
        log.error(f"❌ Missing variables: {', '.join(missing)}")
        return

    send_message(
        CHAT_ID,
        f"<b>Lumis Capital Bot Online</b>\n"
        f"{datetime.now().strftime('%B %d, %Y | %I:%M %p ET')}\n"
        f"Type /help for all commands."
    )

    log.info("Bot running. Skipping any queued updates from before startup...")
    offset = None

    # Fast-forward past any updates that arrived before this startup
    # to avoid replaying old commands after a restart
    pending = get_updates(timeout=0)
    if pending.get("ok") and pending.get("result"):
        offset = pending["result"][-1]["update_id"] + 1
        log.info(f"Skipped {len(pending['result'])} pending update(s). Starting at offset {offset}.")

    log.info("Listening for new commands...")

    while True:
        try:
            updates = get_updates(offset)
            if updates.get("ok") and updates.get("result"):
                for update in updates["result"]:
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    chat_id = message.get("chat", {}).get("id")
                    text = message.get("text", "")
                    if chat_id and text:
                        log.info(f"Update received — update_id={update['update_id']} chat_id={chat_id} text={text!r}")
                        process_command(str(chat_id), text)
                        log.info(f"Update {update['update_id']} fully handled")
            elif not updates.get("ok"):
                log.error(f"getUpdates returned non-ok response: {updates}")
            time.sleep(1)
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception as e:
            log.error(f"Bot error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_bot()

"""
LUMIS CAPITAL BOT
Powered by Claude AI + FMP Live Data
Telegram Bot for Market Intelligence
"""

import os
import requests
import time
import logging
from datetime import datetime, timedelta

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
Data-driven. Always end with:
Not financial advice. Always do your own research."""

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


def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=35)
        return response.json()
    except Exception as e:
        log.error(f"Get updates error: {e}")
        return {"ok": False, "result": []}


# ─────────────────────────────────────
# FMP FUNCTIONS
# ─────────────────────────────────────
def get_stock_quote(symbol):
    url = f"https://financialmodelingprep.com/stable/quote/{symbol}"
    params = {"apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for quote/{symbol}: {response.text}")
            return None
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        log.warning(f"FMP quote/{symbol} returned empty or unexpected payload: {data}")
        return None
    except Exception as e:
        log.error(f"FMP quote error for {symbol}: {e}")
        return None


def get_treasury_rates():
    url = "https://financialmodelingprep.com/stable/treasury"
    params = {"apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for treasury: {response.text}")
            return None
        data = response.json()
        if data and len(data) > 0:
            return data[0]
        log.warning(f"FMP treasury returned empty or unexpected payload: {data}")
        return None
    except Exception as e:
        log.error(f"FMP treasury error: {e}")
        return None


def get_earnings_calendar():
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    url = "https://financialmodelingprep.com/stable/earning_calendar"
    params = {"from": today, "to": end, "apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for earning_calendar: {response.text}")
            return []
        return response.json()
    except Exception as e:
        log.error(f"FMP earnings error: {e}")
        return []


def get_stock_news():
    tickers = ",".join(WATCHLIST[:6])
    url = "https://financialmodelingprep.com/stable/stock_news"
    params = {"tickers": tickers, "limit": 10, "apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            log.error(f"FMP API returned {response.status_code} for stock_news: {response.text}")
            return []
        return response.json()
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
            return None
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        log.warning(f"FMP price-target-consensus/{symbol} returned empty or unexpected payload: {data}")
        return None
    except Exception as e:
        log.error(f"FMP consensus error: {e}")
        return None


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
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code != 200:
            log.error(f"Anthropic API returned {response.status_code}: {response.text}")
            return "Lumis Nova is temporarily unavailable."
        data = response.json()
        if "content" in data and len(data["content"]) > 0:
            return data["content"][0]["text"]
        log.error(f"Anthropic API returned unexpected payload: {data}")
        return "Unable to get response from Lumis Nova."
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return "Lumis Nova is temporarily unavailable."


# ─────────────────────────────────────
# COMMAND HANDLERS
# ─────────────────────────────────────
def handle_start(chat_id):
    msg = """⚡ <b>LUMIS CAPITAL BOT</b>
Powered by Lumis Nova AI

Your personal market intelligence system.

<b>Commands:</b>
/news — Market stories
/macro — Yields + economic data
/earnings — Upcoming calendar
/scout — Weekly stock picks
/watchlist — Live prices
/full [TICKER] — Full analysis
/opinion [TICKER] — Quick take
/invest [TICKER] — Long-term view
/yields — Treasury curve
/insider [TICKER] — Insider activity
/risk [TICKER] — Risk check
/compounding — Wealth math
/sector [SECTOR] — Sector analysis
/compare [T1] [T2] — Head-to-head comparison
/dividend [TICKER] — Dividend analysis
/momentum — Momentum scan
/portfolio [AMOUNT] — Build a portfolio
/help — All commands

<i>Not financial advice. Always do your own research.</i>"""
    send_message(chat_id, msg)


def handle_watchlist(chat_id):
    send_message(chat_id, "⏳ Pulling live prices...")
    lines = [f"📊 <b>LUMIS CAPITAL WATCHLIST</b>"]
    lines.append(f"🕐 {datetime.now().strftime('%b %d %Y | %I:%M %p ET')}\n")

    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote:
            price = quote.get("price", 0)
            change = quote.get("changePercentage", 0)
            emoji = "🟢" if change >= 0 else "🔴"
            lines.append(f"{emoji} <b>{symbol}</b>: ${price:.2f} ({change:+.2f}%)")
        else:
            lines.append(f"⚪ {symbol}: Unavailable")

    lines.append("\n<i>Source: FMP Live ✅</i>")
    lines.append("<i>Not financial advice.</i>")
    send_message(chat_id, "\n".join(lines))


def handle_yields(chat_id):
    send_message(chat_id, "⏳ Pulling yield curve...")
    rates = get_treasury_rates()
    if rates:
        msg = f"""📈 <b>TREASURY YIELD CURVE</b>
🕐 {datetime.now().strftime('%b %d %Y')}

3-month: {rates.get('month3', 'N/A')}%
6-month: {rates.get('month6', 'N/A')}%
2-year:  {rates.get('year2', 'N/A')}%
5-year:  {rates.get('year5', 'N/A')}%
10-year: {rates.get('year10', 'N/A')}% ← KEY
30-year: {rates.get('year30', 'N/A')}%

<i>Source: FMP Live ✅</i>
<i>Not financial advice.</i>"""
    else:
        msg = "⚠️ Unable to fetch yield data."
    send_message(chat_id, msg)


def handle_earnings(chat_id):
    send_message(chat_id, "⏳ Pulling earnings calendar...")
    major = [
        "AAPL","MSFT","NVDA","META","GOOGL","AMZN","TSLA",
        "NOW","MU","AVGO","CRM","DELL","COST","HOOD","SOFI",
        "IREN","ASTS","NOK","IONQ","QBTS","AMD","INTC","MRVL",
        "ANET","PANW","SNOW","ADSK"
    ]
    earnings = get_earnings_calendar()
    filtered = [e for e in earnings if e.get("symbol") in major]

    if filtered:
        lines = ["📅 <b>UPCOMING EARNINGS (7 days)</b>\n"]
        for e in sorted(filtered, key=lambda x: x.get("date", ""))[:10]:
            symbol = e.get("symbol", "")
            date = e.get("date", "")
            eps = e.get("epsEstimated", "N/A")
            rev = e.get("revenueEstimated", 0)
            rev_b = f"${rev/1e9:.2f}B" if rev else "N/A"
            lines.append(f"📌 <b>{symbol}</b> | {date}\n   EPS: {eps} | Rev: {rev_b}")
        lines.append("\n<i>Source: FMP Live ✅</i>")
        lines.append("<i>Not financial advice.</i>")
        send_message(chat_id, "\n".join(lines))
    else:
        send_message(chat_id, "📅 No major earnings in next 7 days.")


def handle_news(chat_id):
    send_message(chat_id, "⏳ Pulling market news...")
    news = get_stock_news()
    if news:
        context = "Latest market news:\n"
        for item in news[:8]:
            context += f"- [{item.get('symbol','')}] {item.get('title','')}\n"
        prompt = f"""Give a morning market intelligence brief.
Top 5 stories. Format for Telegram. Use emojis.
2-3 sentences per story. Today: {datetime.now().strftime('%B %d, %Y')}
{context}"""
        response = ask_claude(prompt)
        header = f"📰 <b>LUMIS CAPITAL NEWS</b>\n🕐 {datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n"
        send_message(chat_id, header + response)
    else:
        send_message(chat_id, "⚠️ Unable to fetch news.")


def handle_macro(chat_id):
    send_message(chat_id, "⏳ Pulling macro data...")
    rates = get_treasury_rates()
    context = ""
    if rates:
        context = f"10yr: {rates.get('year10')}% | 2yr: {rates.get('year2')}% | 30yr: {rates.get('year30')}%"
    prompt = f"""Macro brief for {datetime.now().strftime('%B %d, %Y')}.
Cover: yield curve, Fed outlook, key events today, oil/geopolitical impact.
Format for Telegram. Keep it actionable. {context}"""
    response = ask_claude(prompt)
    header = f"📊 <b>MACRO BRIEF</b>\n🕐 {datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n"
    send_message(chat_id, header + response)


def handle_full(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /full NOW")
        return
    symbol = symbol.upper()
    send_message(chat_id, f"⏳ Full analysis on ${symbol}...")
    quote = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    context = f"${symbol} live data:\n"
    if quote:
        context += f"Price: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
        context += f"52wk High: ${quote.get('yearHigh','N/A')} | Low: ${quote.get('yearLow','N/A')}\n"
        context += f"Market Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    if consensus:
        context += f"Analyst consensus PT: ${consensus.get('targetConsensus','N/A')}\n"
        context += f"High PT: ${consensus.get('targetHigh','N/A')} | Low: ${consensus.get('targetLow','N/A')}\n"
    prompt = f"""Full stock analysis for ${symbol}.
Cover: business model, moat, top 3 competitors, catalyst,
bull case, bear case, valuation, entry strategy, stop loss, sizing.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    response = ask_claude(prompt, context)
    send_message(chat_id, f"🔍 <b>${symbol} ANALYSIS</b>\n\n" + response)


def handle_opinion(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /opinion NOW")
        return
    symbol = symbol.upper()
    quote = get_stock_quote(symbol)
    context = ""
    if quote:
        context = f"${symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)"
    prompt = f"Quick honest opinion on ${symbol}. Buy/sell/hold and why. 4-5 sentences. One key risk. One key catalyst."
    response = ask_claude(prompt, context)
    send_message(chat_id, f"💬 <b>${symbol} QUICK TAKE</b>\n\n" + response)


def handle_scout(chat_id):
    send_message(chat_id, "⏳ Running weekly scout...")
    context = f"Weekly scout {datetime.now().strftime('%B %d, %Y')}\n"
    for symbol in WATCHLIST[:6]:
        quote = get_stock_quote(symbol)
        if quote:
            context += f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
    prompt = """3 stock picks for this week.
For each: ticker, thesis, why this week specifically,
bull case, bear case, entry range, stop loss, target, sizing.
Show both sides. Never just hype."""
    response = ask_claude(prompt, context)
    send_message(chat_id, f"🔭 <b>WEEKLY SCOUT</b>\n🕐 {datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_invest(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /invest GOOGL")
        return
    symbol = symbol.upper()
    quote = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    context = ""
    if quote:
        context = f"${symbol}: ${quote.get('price','N/A')} | Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    if consensus:
        context += f"Consensus PT: ${consensus.get('targetConsensus','N/A')}\n"
    prompt = f"""Long-term investing analysis for ${symbol}.
Cover: business quality, moat, 5yr growth potential,
management signals, covered call income potential,
compounding scenario ($10K over 5yr/10yr),
how to build the position, stop loss.
Think years not weeks."""
    response = ask_claude(prompt, context)
    send_message(chat_id, f"💼 <b>${symbol} INVESTING VIEW</b>\n\n" + response)


def handle_insider(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /insider NOW")
        return
    symbol = symbol.upper()
    prompt = f"""Check insider trading activity for ${symbol}.
Cover: recent buys, recent sells, net sentiment,
CEO ownership %, what the activity signals.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    response = ask_claude(prompt)
    send_message(chat_id, f"👁️ <b>${symbol} INSIDER ACTIVITY</b>\n\n" + response)


def handle_risk(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /risk NOW")
        return
    symbol = symbol.upper()
    quote = get_stock_quote(symbol)
    context = ""
    if quote:
        context = f"${symbol}: ${quote.get('price','N/A')}"
    prompt = f"""Risk check for a position in ${symbol}.
Cover: appropriate sizing for a $10K account,
max loss at stop, correlation risk, Kelly criterion suggestion.
Be honest. Push back if sizing seems aggressive."""
    response = ask_claude(prompt, context)
    send_message(chat_id, f"⚠️ <b>${symbol} RISK CHECK</b>\n\n" + response)


def handle_compounding(chat_id):
    prompt = """Show compounding math for $10,000 invested.
At 10%, 15%, 20%, 25% annual return — value at 5yr, 10yr, 20yr.
Then show covered call income overlay: $300/month reinvested.
Make it real and motivating."""
    response = ask_claude(prompt)
    send_message(chat_id, "📈 <b>COMPOUNDING MATH</b>\n\n" + response)


def handle_sector(chat_id, sector):
    if not sector:
        send_message(chat_id, "❌ Usage: /sector tech")
        return
    sector = sector.lower()
    send_message(chat_id, f"⏳ Analyzing {sector} sector...")
    prompt = f"""Analyze the {sector} sector. Cover: top 3 stocks, macro tailwinds/headwinds, best entry point, key risks, 6-month outlook.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    response = ask_claude(prompt)
    send_message(chat_id, f"🏭 <b>{sector.upper()} SECTOR ANALYSIS</b>\n\n" + response)


def handle_compare(chat_id, ticker1, ticker2):
    if not ticker1 or not ticker2:
        send_message(chat_id, "❌ Usage: /compare NVDA AMD")
        return
    ticker1 = ticker1.upper()
    ticker2 = ticker2.upper()
    send_message(chat_id, f"⏳ Comparing ${ticker1} vs ${ticker2}...")
    quote1 = get_stock_quote(ticker1)
    quote2 = get_stock_quote(ticker2)
    context = ""
    if quote1:
        context += f"${ticker1}: ${quote1.get('price','N/A')} ({quote1.get('changePercentage',0):+.2f}%) | Cap: ${quote1.get('marketCap',0)/1e9:.2f}B\n"
    if quote2:
        context += f"${ticker2}: ${quote2.get('price','N/A')} ({quote2.get('changePercentage',0):+.2f}%) | Cap: ${quote2.get('marketCap',0)/1e9:.2f}B\n"
    prompt = f"""Compare ${ticker1} vs ${ticker2}. Cover: business model, growth, valuation, moat, which is better and why, when to own each.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    response = ask_claude(prompt, context)
    send_message(chat_id, f"⚔️ <b>${ticker1} vs ${ticker2}</b>\n\n" + response)


def handle_dividend(chat_id, ticker):
    if not ticker:
        send_message(chat_id, "❌ Usage: /dividend KO")
        return
    ticker = ticker.upper()
    send_message(chat_id, f"⏳ Dividend analysis for ${ticker}...")
    quote = get_stock_quote(ticker)
    context = ""
    if quote:
        context = f"${ticker}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%) | Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    prompt = f"""Dividend analysis for ${ticker}. Cover: current yield, payout ratio, growth history, sustainability, covered call income potential, total return vs price appreciation.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    response = ask_claude(prompt, context)
    send_message(chat_id, f"💰 <b>${ticker} DIVIDEND ANALYSIS</b>\n\n" + response)


def handle_momentum(chat_id):
    send_message(chat_id, "⏳ Running momentum scan...")
    context = f"Watchlist momentum scan — {datetime.now().strftime('%B %d, %Y')}\n"
    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote:
            price = quote.get("price", 0)
            change = quote.get("changePercentage", 0)
            context += f"{symbol}: ${price:.2f} ({change:+.2f}%)\n"
        else:
            context += f"{symbol}: Unavailable\n"
    prompt = """Momentum scan of watchlist. Rank by momentum. Which are overbought? Oversold? Best risk/reward? Show both sides."""
    response = ask_claude(prompt, context)
    send_message(chat_id, f"🚀 <b>MOMENTUM SCAN</b>\n🕐 {datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n" + response)


def handle_portfolio(chat_id, allocation):
    if not allocation:
        send_message(chat_id, "❌ Usage: /portfolio 50000")
        return
    try:
        amount = int(allocation.replace(",", "").replace("$", ""))
    except ValueError:
        send_message(chat_id, "❌ Please provide a numeric amount. Example: /portfolio 50000")
        return
    send_message(chat_id, f"⏳ Building ${amount:,} portfolio...")
    prompt = f"""Build a ${amount:,} portfolio. Cover: asset allocation, 5-7 stock picks, sizing, rebalancing schedule, covered call strategy, expected return, max drawdown, how to execute.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    response = ask_claude(prompt)
    send_message(chat_id, f"🏗️ <b>${amount:,} PORTFOLIO PLAN</b>\n\n" + response)


def handle_help(chat_id):
    msg = """⚡ <b>LUMIS CAPITAL — ALL COMMANDS</b>

📰 <b>Intelligence:</b>
/news — Market stories
/macro — Yields + economic data
/earnings — Upcoming calendar
/yields — Treasury curve

🔍 <b>Research:</b>
/full [TICKER] — Complete analysis
/opinion [TICKER] — Quick take
/scout — Weekly picks
/insider [TICKER] — Insider activity
/risk [TICKER] — Position risk check

💼 <b>Investing:</b>
/invest [TICKER] — Long-term analysis
/compounding — Wealth building math
/dividend [TICKER] — Dividend analysis

📊 <b>Portfolio:</b>
/watchlist — Live prices
/momentum — Momentum scan
/portfolio [AMOUNT] — Build a portfolio

🏭 <b>Advanced:</b>
/sector [SECTOR] — Sector analysis
/compare [T1] [T2] — Head-to-head comparison

<b>Examples:</b>
/full NOW | /compare NVDA AMD | /sector tech
/dividend KO | /momentum | /portfolio 50000

<i>Powered by Lumis Nova AI + FMP Live Data</i>
<i>Not financial advice. Always DYOR.</i>"""
    send_message(chat_id, msg)


# ─────────────────────────────────────
# COMMAND ROUTER
# ─────────────────────────────────────
def process_command(chat_id, text):
    text = text.strip()
    parts = text.split()
    command = parts[0].lower() if parts else ""
    argument = parts[1] if len(parts) > 1 else ""
    log.info(f"Command: {command} | Arg: {argument} | Chat: {chat_id}")

    routes = {
        "/start":     lambda: handle_start(chat_id),
        "/help":      lambda: handle_help(chat_id),
        "/watchlist": lambda: handle_watchlist(chat_id),
        "/yields":    lambda: handle_yields(chat_id),
        "/earnings":  lambda: handle_earnings(chat_id),
        "/news":      lambda: handle_news(chat_id),
        "/macro":     lambda: handle_macro(chat_id),
        "/scout":     lambda: handle_scout(chat_id),
        "/full":      lambda: handle_full(chat_id, argument),
        "/opinion":   lambda: handle_opinion(chat_id, argument),
        "/invest":    lambda: handle_invest(chat_id, argument),
        "/insider":   lambda: handle_insider(chat_id, argument),
        "/risk":      lambda: handle_risk(chat_id, argument),
        "/compounding": lambda: handle_compounding(chat_id),
        "/sector":    lambda: handle_sector(chat_id, argument),
        "/compare":   lambda: handle_compare(chat_id, argument, parts[2] if len(parts) > 2 else ""),
        "/dividend":  lambda: handle_dividend(chat_id, argument),
        "/momentum":  lambda: handle_momentum(chat_id),
        "/portfolio": lambda: handle_portfolio(chat_id, argument),
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
        f"⚡ <b>Lumis Capital Bot Online</b>\n"
        f"🕐 {datetime.now().strftime('%B %d, %Y | %I:%M %p ET')}\n"
        f"Type /help for all commands."
    )

    log.info("✅ Bot running. Listening for commands...")
    offset = None

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

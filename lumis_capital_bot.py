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
using live market data from FMP (Financial Modeling Prep).

CRITICAL: You are receiving LIVE market data from FMP API. This data is current
as of today. Use it directly — do not worry about your knowledge cutoff. The data
you receive IS the source of truth for current prices, earnings, yields, and analyst
consensus. Analyze it with confidence.

CORE RULES:
- Always show bull AND bear case on every analysis
- Always include position sizing recommendations
- Always include stop loss levels
- Translate all financials to USD
- Label all data sources clearly (FMP Live ✅)
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
/compare [T1] [T2] — Compare two stocks
/dividend [TICKER] — Dividend analysis
/momentum — Momentum plays
/portfolio [ALLOCATION] — Portfolio review
/help — All commands

<i>Not financial advice. Always do your own research.</i>"""
    send_message(chat_id, msg)


def handle_watchlist(chat_id):
    send_message(chat_id, "⏳ Pulling live prices...")
    lines = [f"📊 <b>LUMIS CAPITAL WATCHLIST</b>"]
    lines.append(f"🕐 {datetime.now().strftime('%b %d %Y | %I:%M %p ET')}\n")

    price_context = ""
    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote:
            price = quote.get("price", 0)
            change = quote.get("changePercentage", 0)
            emoji = "🟢" if change >= 0 else "🔴"
            lines.append(f"{emoji} <b>{symbol}</b>: ${price:.2f} ({change:+.2f}%)")
            price_context += f"{symbol}: ${price:.2f} ({change:+.2f}%)\n"
        else:
            lines.append(f"⚪ {symbol}: Unavailable")

    lines.append("\n<i>Source: FMP Live ✅</i>")
    send_message(chat_id, "\n".join(lines))

    if price_context:
        prompt = f"""Brief market read on today's watchlist price action.
Today: {datetime.now().strftime('%B %d, %Y')}
{price_context}"""
        skill_prompt = get_skill_prompt("/watchlist")
        commentary = ask_claude(prompt, skill_prompt=skill_prompt)
        send_message(chat_id, "📝 <b>LUMIS NOVA READ</b>\n\n" + commentary)


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

<i>Source: FMP Live ✅</i>"""
        send_message(chat_id, msg)
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
        send_message(chat_id, "📝 <b>YIELD CURVE ANALYSIS</b>\n\n" + commentary)
    else:
        send_message(chat_id, "⚠️ Unable to fetch yield data.")


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
        earnings_context = ""
        for e in sorted(filtered, key=lambda x: x.get("date", ""))[:10]:
            symbol = e.get("symbol", "")
            date = e.get("date", "")
            eps = e.get("epsEstimated", "N/A")
            rev = e.get("revenueEstimated", 0)
            rev_b = f"${rev/1e9:.2f}B" if rev else "N/A"
            lines.append(f"📌 <b>{symbol}</b> | {date}\n   EPS: {eps} | Rev: {rev_b}")
            earnings_context += f"{symbol} | {date} | EPS est: {eps} | Rev est: {rev_b}\n"
        lines.append("\n<i>Source: FMP Live ✅</i>")
        send_message(chat_id, "\n".join(lines))
        prompt = f"""Analyze the upcoming earnings events and what they mean for traders.
Today: {datetime.now().strftime('%B %d, %Y')}
{earnings_context}"""
        skill_prompt = get_skill_prompt("/earnings")
        commentary = ask_claude(prompt, skill_prompt=skill_prompt)
        send_message(chat_id, "📝 <b>EARNINGS PREVIEW</b>\n\n" + commentary)
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
        skill_prompt = get_skill_prompt("/news")
        response = ask_claude(prompt, skill_prompt=skill_prompt)
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
    skill_prompt = get_skill_prompt("/macro")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
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
    skill_prompt = get_skill_prompt("/full")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
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
    skill_prompt = get_skill_prompt("/opinion")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
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
    skill_prompt = get_skill_prompt("/scout")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
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
    skill_prompt = get_skill_prompt("/invest")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
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
    skill_prompt = get_skill_prompt("/insider")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
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
    skill_prompt = get_skill_prompt("/risk")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"⚠️ <b>${symbol} RISK CHECK</b>\n\n" + response)


def handle_compounding(chat_id):
    prompt = """Show compounding math for $10,000 invested.
At 10%, 15%, 20%, 25% annual return — value at 5yr, 10yr, 20yr.
Then show covered call income overlay: $300/month reinvested.
Make it real and motivating."""
    skill_prompt = get_skill_prompt("/compounding")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
    send_message(chat_id, "📈 <b>COMPOUNDING MATH</b>\n\n" + response)


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
/sector [SECTOR] — Sector analysis
/compare [T1] [T2] — Compare two stocks

💼 <b>Investing:</b>
/invest [TICKER] — Long-term analysis
/compounding — Wealth building math
/dividend [TICKER] — Dividend analysis

📊 <b>Portfolio:</b>
/watchlist — Live prices
/momentum — Momentum plays
/portfolio [ALLOCATION] — Portfolio review

<b>Examples:</b>
/full NOW | /opinion ASTS | /invest GOOGL
/sector tech | /compare NVDA AMD | /dividend AAPL

<i>Powered by Lumis Nova AI + FMP Live Data</i>
<i>Not financial advice. Always DYOR.</i>"""
    send_message(chat_id, msg)


def handle_sector(chat_id, sector):
    if not sector:
        send_message(chat_id, "❌ Usage: /sector tech")
        return
    send_message(chat_id, f"⏳ Analyzing {sector} sector...")
    prompt = f"""Sector analysis for the {sector} sector.
Today: {datetime.now().strftime('%B %d, %Y')}
Cover: key drivers, top stocks, ETF performance, bull case, bear case, positioning."""
    skill_prompt = get_skill_prompt("/sector")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
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
        context += f"${ticker1}: ${quote1.get('price','N/A')} | Cap: ${quote1.get('marketCap',0)/1e9:.2f}B\n"
    if quote2:
        context += f"${ticker2}: ${quote2.get('price','N/A')} | Cap: ${quote2.get('marketCap',0)/1e9:.2f}B\n"
    prompt = f"""Head-to-head comparison: ${ticker1} vs ${ticker2}.
Cover: business model, valuation, growth, moat, bull case and bear case for each, verdict.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/compare")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"⚔️ <b>${ticker1} vs ${ticker2}</b>\n\n" + response)


def handle_dividend(chat_id, symbol):
    if not symbol:
        send_message(chat_id, "❌ Usage: /dividend AAPL")
        return
    symbol = symbol.upper()
    send_message(chat_id, f"⏳ Analyzing ${symbol} dividend...")
    quote = get_stock_quote(symbol)
    context = ""
    if quote:
        context = f"${symbol}: ${quote.get('price','N/A')} | Cap: ${quote.get('marketCap',0)/1e9:.2f}B\n"
    prompt = f"""Dividend analysis for ${symbol}.
Cover: yield, payout history, sustainability, FCF coverage, bull case, bear case, income scenario.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    skill_prompt = get_skill_prompt("/dividend")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"💰 <b>${symbol} DIVIDEND ANALYSIS</b>\n\n" + response)


def handle_momentum(chat_id):
    send_message(chat_id, "⏳ Scanning for momentum plays...")
    context = f"Momentum scan {datetime.now().strftime('%B %d, %Y')}\n"
    for symbol in WATCHLIST[:8]:
        quote = get_stock_quote(symbol)
        if quote:
            context += f"{symbol}: ${quote.get('price','N/A')} ({quote.get('changePercentage',0):+.2f}%)\n"
    prompt = """Top 3 momentum plays right now.
For each: ticker, why it's moving, technical levels, bull case, bear case, entry, stop loss.
Show both continuation and reversal scenarios."""
    skill_prompt = get_skill_prompt("/momentum")
    response = ask_claude(prompt, context, skill_prompt=skill_prompt)
    send_message(chat_id, f"🚀 <b>MOMENTUM PLAYS</b>\n🕐 {datetime.now().strftime('%b %d, %Y')}\n\n" + response)


def handle_portfolio(chat_id, allocation):
    if not allocation:
        send_message(chat_id, "❌ Usage: /portfolio 50% NVDA, 30% AAPL, 20% CASH")
        return
    send_message(chat_id, "⏳ Analyzing portfolio...")
    prompt = f"""Portfolio analysis for the following allocation:
{allocation}

Today: {datetime.now().strftime('%B %d, %Y')}

Cover: diversification, sector exposure, concentration risk, bull case, bear case, rebalancing suggestions."""
    skill_prompt = get_skill_prompt("/portfolio")
    response = ask_claude(prompt, skill_prompt=skill_prompt)
    send_message(chat_id, f"📊 <b>PORTFOLIO REVIEW</b>\n\n" + response)


# ─────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────
def main():
    log.info("🚀 Lumis Capital Bot starting...")
    log.info(f"TELEGRAM_TOKEN: {'✅' if TELEGRAM_TOKEN else '❌'}")
    log.info(f"CHAT_ID: {'✅' if CHAT_ID else '❌'}")
    log.info(f"FMP_API_KEY: {'✅' if FMP_API_KEY else '❌'}")
    log.info(f"ANTHROPIC_API_KEY: {'✅' if ANTHROPIC_API_KEY else '❌'}")

    offset = None
    while True:
        try:
            result = get_updates(offset)
            if not result.get("ok"):
                log.error(f"Telegram getUpdates failed: {result}")
                time.sleep(5)
                continue

            updates = result.get("result", [])
            if not updates:
                time.sleep(1)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = message.get("chat", {}).get("id")
                text = message.get("text", "").strip()

                if not chat_id or not text:
                    continue

                log.info(f"[{chat_id}] {text}")

                # Parse command
                parts = text.split()
                command = parts[0].lower()

                if command == "/start":
                    handle_start(chat_id)
                elif command == "/help":
                    handle_help(chat_id)
                elif command == "/watchlist":
                    handle_watchlist(chat_id)
                elif command == "/yields":
                    handle_yields(chat_id)
                elif command == "/earnings":
                    handle_earnings(chat_id)
                elif command == "/news":
                    handle_news(chat_id)
                elif command == "/macro":
                    handle_macro(chat_id)
                elif command == "/full":
                    symbol = parts[1] if len(parts) > 1 else None
                    handle_full(chat_id, symbol)
                elif command == "/opinion":
                    symbol = parts[1] if len(parts) > 1 else None
                    handle_opinion(chat_id, symbol)
                elif command == "/scout":
                    handle_scout(chat_id)
                elif command == "/invest":
                    symbol = parts[1] if len(parts) > 1 else None
                    handle_invest(chat_id, symbol)
                elif command == "/insider":
                    symbol = parts[1] if len(parts) > 1 else None
                    handle_insider(chat_id, symbol)
                elif command == "/risk":
                    symbol = parts[1] if len(parts) > 1 else None
                    handle_risk(chat_id, symbol)
                elif command == "/compounding":
                    handle_compounding(chat_id)
                elif command == "/sector":
                    sector = parts[1] if len(parts) > 1 else None
                    handle_sector(chat_id, sector)
                elif command == "/compare":
                    ticker1 = parts[1] if len(parts) > 1 else None
                    ticker2 = parts[2] if len(parts) > 2 else None
                    handle_compare(chat_id, ticker1, ticker2)
                elif command == "/dividend":
                    symbol = parts[1] if len(parts) > 1 else None
                    handle_dividend(chat_id, symbol)
                elif command == "/momentum":
                    handle_momentum(chat_id)
                elif command == "/portfolio":
                    allocation = " ".join(parts[1:]) if len(parts) > 1 else None
                    handle_portfolio(chat_id, allocation)
                else:
                    send_message(chat_id, "❌ Unknown command. Try /help")

        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()


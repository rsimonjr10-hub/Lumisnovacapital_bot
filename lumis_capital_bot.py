"""
LUMIS CAPITAL BOT
Powered by Claude AI + FMP Live Data
Telegram Bot for Market Intelligence
"""

import os
import requests
import json
import time
import logging
from datetime import datetime

# ─────────────────────────────────────
# CONFIGURATION — SET IN RAILWAY ENV
# ─────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
FMP_API_KEY = os.environ.get("FMP_API_KEY")
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
# WATCHLIST — YOUR STOCKS
# ─────────────────────────────────────
WATCHLIST = [
    "NOW", "META", "NVDA", "ASTS", 
    "IREN", "NOK", "HOOD", "SOFI", 
    "MU", "GOOGL", "IONQ", "QBTS"
]

# ─────────────────────────────────────
# LUMIS NOVA SYSTEM PROMPT
# ─────────────────────────────────────
SYSTEM_PROMPT = """You are Lumis Nova, an AI-powered market research 
assistant for Lumis Capital. You provide trading and investing intelligence 
using live market data.

CORE RULES:
- Always show bull AND bear case
- Always include position sizing recommendations
- Always include stop loss levels
- Translate all financials to USD
- Label all data sources clearly
- Separate trading (weeks) from investing (years)
- Show earnings surprise history before options trades
- Check insider buying on every stock analysis
- Run risk check before new position recommendations
- Be honest when uncertain
- Push back on overleveraged sizing
- Never promise profits

TONE:
Professional but direct. Honest over exciting.
Data-driven over narrative-driven.
Always end stock analysis with:
"Not financial advice. Always do your own research."

FORMAT FOR TELEGRAM:
Use emojis for visual clarity.
Keep responses concise for mobile reading.
Use simple formatting — no markdown tables."""

# ─────────────────────────────────────
# TELEGRAM FUNCTIONS
# ─────────────────────────────────────
def send_message(chat_id, text):
    """Send a message via Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
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
    """Get new messages from Telegram."""
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
# FMP DATA FUNCTIONS
# ─────────────────────────────────────
def get_stock_quote(symbol):
    """Get live stock quote from FMP."""
    url = f"https://financialmodelingprep.com/api/v3/quote/{symbol}"
    params = {"apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data and len(data) > 0:
            return data[0]
        return None
    except Exception as e:
        log.error(f"FMP quote error for {symbol}: {e}")
        return None


def get_treasury_rates():
    """Get live treasury yield curve from FMP."""
    url = "https://financialmodelingprep.com/api/v4/treasury"
    params = {"apikey": FMP_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data and len(data) > 0:
            return data[0]
        return None
    except Exception as e:
        log.error(f"FMP treasury error: {e}")
        return None


def get_earnings_calendar(from_date, to_date):
    """Get earnings calendar from FMP."""
    url = "https://financialmodelingprep.com/api/v3/earning_calendar"
    params = {
        "from": from_date,
        "to": to_date,
        "apikey": FMP_API_KEY
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        return response.json()
    except Exception as e:
        log.error(f"FMP earnings error: {e}")
        return []


def get_stock_news(symbols):
    """Get latest news for watchlist stocks."""
    tickers = ",".join(symbols[:5])
    url = "https://financialmodelingprep.com/api/v3/stock_news"
    params = {
        "tickers": tickers,
        "limit": 10,
        "apikey": FMP_API_KEY
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        return response.json()
    except Exception as e:
        log.error(f"FMP news error: {e}")
        return []


def get_analyst_consensus(symbol):
    """Get analyst price target consensus."""
    url = f"https://financialmodelingprep.com/api/v4/price-target-consensus"
    params = {
        "symbol": symbol,
        "apikey": FMP_API_KEY
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data and len(data) > 0:
            return data[0]
        return None
    except Exception as e:
        log.error(f"FMP consensus error: {e}")
        return None


# ─────────────────────────────────────
# CLAUDE AI FUNCTION
# ─────────────────────────────────────
def ask_claude(prompt, context=""):
    """Call Claude API with Lumis Nova system prompt."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    full_prompt = f"{context}\n\n{prompt}" if context else prompt
    
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": full_prompt}
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        data = response.json()
        if "content" in data and len(data["content"]) > 0:
            return data["content"][0]["text"]
        return "Unable to get response from Lumis Nova."
    except Exception as e:
        log.error(f"Claude API error: {e}")
        return "Lumis Nova is temporarily unavailable."


# ─────────────────────────────────────
# COMMAND HANDLERS
# ─────────────────────────────────────
def handle_start(chat_id):
    """Handle /start command."""
    msg = """⚡ <b>LUMIS CAPITAL BOT</b>
Powered by Lumis Nova AI

Welcome to your personal market intelligence system.

<b>Available Commands:</b>
/news — Top market stories
/macro — Yields + economic data
/earnings — Upcoming earnings
/scout — Weekly stock picks
/watchlist — Live watchlist prices
/full [TICKER] — Full stock analysis
/opinion [TICKER] — Quick take
/invest [TICKER] — Long-term analysis
/profit — P&L calculator
/yields — Treasury yield curve
/help — All commands

<i>Not financial advice. Always do your own research.</i>"""
    send_message(chat_id, msg)


def handle_watchlist(chat_id):
    """Handle /watchlist command — pull live prices."""
    send_message(chat_id, "⏳ Pulling live prices...")
    
    lines = ["📊 <b>LUMIS CAPITAL WATCHLIST</b>"]
    lines.append(f"🕐 {datetime.now().strftime('%b %d %Y | %I:%M %p ET')}\n")
    
    for symbol in WATCHLIST:
        quote = get_stock_quote(symbol)
        if quote:
            price = quote.get("price", 0)
            change = quote.get("changePercentage", 0)
            emoji = "🟢" if change >= 0 else "🔴"
            lines.append(
                f"{emoji} <b>{symbol}</b>: ${price:.2f} "
                f"({change:+.2f}%)"
            )
        else:
            lines.append(f"⚪ {symbol}: Data unavailable")
    
    lines.append("\n<i>Source: FMP Live ✅</i>")
    lines.append("<i>Not financial advice.</i>")
    
    send_message(chat_id, "\n".join(lines))


def handle_yields(chat_id):
    """Handle /yields command."""
    send_message(chat_id, "⏳ Pulling yield curve...")
    
    rates = get_treasury_rates()
    if rates:
        msg = f"""📈 <b>TREASURY YIELD CURVE</b>
🕐 {datetime.now().strftime('%b %d %Y')}

3-month: {rates.get('month3', 'N/A')}%
6-month: {rates.get('month6', 'N/A')}%
1-year:  {rates.get('year1', 'N/A')}%
2-year:  {rates.get('year2', 'N/A')}%
5-year:  {rates.get('year5', 'N/A')}%
10-year: {rates.get('year10', 'N/A')}% ← KEY
30-year: {rates.get('year30', 'N/A')}%

<i>Source: FMP Live ✅</i>
<i>Not financial advice.</i>"""
    else:
        msg = "⚠️ Unable to fetch yield data. Try again shortly."
    
    send_message(chat_id, msg)


def handle_earnings(chat_id):
    """Handle /earnings command."""
    send_message(chat_id, "⏳ Pulling earnings calendar...")
    
    today = datetime.now().strftime("%Y-%m-%d")
    # Get next 7 days
    from datetime import timedelta
    end_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    
    earnings = get_earnings_calendar(today, end_date)
    
    # Filter for notable companies
    major = [
        "AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN",
        "TSLA", "NOW", "MU", "AVGO", "CRM", "DELL", "COST",
        "HOOD", "SOFI", "IREN", "ASTS", "NOK", "IONQ", "QBTS",
        "AMD", "INTC", "MRVL", "ANET", "PANW", "SNOW", "ADSK"
    ]
    
    filtered = [e for e in earnings if e.get("symbol") in major]
    
    if filtered:
        lines = ["📅 <b>UPCOMING EARNINGS (7 days)</b>\n"]
        for e in sorted(filtered, key=lambda x: x.get("date", ""))[:10]:
            symbol = e.get("symbol", "")
            date = e.get("date", "")
            eps_est = e.get("epsEstimated", "N/A")
            rev_est = e.get("revenueEstimated", 0)
            rev_b = f"${rev_est/1e9:.2f}B" if rev_est else "N/A"
            lines.append(
                f"📌 <b>{symbol}</b> | {date}\n"
                f"   EPS est: {eps_est} | Rev: {rev_b}"
            )
        lines.append("\n<i>Source: FMP Live ✅</i>")
        lines.append("<i>Not financial advice.</i>")
        send_message(chat_id, "\n".join(lines))
    else:
        send_message(chat_id, "📅 No major earnings in the next 7 days.")


def handle_news(chat_id):
    """Handle /news command."""
    send_message(chat_id, "⏳ Pulling latest market news...")
    
    news = get_stock_news(WATCHLIST)
    
    if news:
        # Build context for Claude
        news_context = "Latest market news:\n"
        for item in news[:8]:
            title = item.get("title", "")
            symbol = item.get("symbol", "")
            news_context += f"- [{symbol}] {title}\n"
        
        prompt = f"""Based on this market news, give me a brief 
morning market intelligence brief for Lumis Capital subscribers.
Format for Telegram. Keep it concise. 5-6 key points max.
Today's date: {datetime.now().strftime('%B %d, %Y')}

{news_context}"""
        
        response = ask_claude(prompt)
        header = f"📰 <b>LUMIS CAPITAL NEWS BRIEF</b>\n🕐 {datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n"
        send_message(chat_id, header + response)
    else:
        send_message(chat_id, "⚠️ Unable to fetch news. Try again shortly.")


def handle_macro(chat_id):
    """Handle /macro command."""
    send_message(chat_id, "⏳ Pulling macro data...")
    
    rates = get_treasury_rates()
    
    context = ""
    if rates:
        context = f"""Current Treasury Rates:
10-year: {rates.get('year10', 'N/A')}%
2-year: {rates.get('year2', 'N/A')}%
30-year: {rates.get('year30', 'N/A')}%"""
    
    prompt = f"""Give me a concise macro brief for today 
{datetime.now().strftime('%B %d, %Y')}.
Cover: yield curve, Fed outlook, key economic events today.
Format for Telegram. Keep it tight and actionable.
{context}"""
    
    response = ask_claude(prompt)
    header = f"📊 <b>MACRO BRIEF</b>\n🕐 {datetime.now().strftime('%b %d | %I:%M %p ET')}\n\n"
    send_message(chat_id, header + response)


def handle_full_analysis(chat_id, symbol):
    """Handle /full [TICKER] command."""
    if not symbol:
        send_message(chat_id, "❌ Please provide a ticker. Example: /full NOW")
        return
    
    symbol = symbol.upper()
    send_message(chat_id, f"⏳ Running full analysis on ${symbol}...")
    
    # Get live data
    quote = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    
    context = f"Live data for {symbol}:\n"
    if quote:
        context += f"Price: ${quote.get('price', 'N/A')}\n"
        context += f"Change: {quote.get('changePercentage', 0):+.2f}%\n"
        context += f"52wk High: ${quote.get('yearHigh', 'N/A')}\n"
        context += f"52wk Low: ${quote.get('yearLow', 'N/A')}\n"
        context += f"Market Cap: ${quote.get('marketCap', 0)/1e9:.2f}B\n"
    
    if consensus:
        context += f"Analyst Consensus PT: ${consensus.get('targetConsensus', 'N/A')}\n"
        context += f"Analyst High PT: ${consensus.get('targetHigh', 'N/A')}\n"
        context += f"Analyst Low PT: ${consensus.get('targetLow', 'N/A')}\n"
    
    prompt = f"""Give me a complete stock analysis for ${symbol}.
Include:
1. What the company does (plain English)
2. Bull case
3. Bear case  
4. Key catalysts
5. Price targets
6. Position sizing recommendation
7. Stop loss level

Format for Telegram mobile reading.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    
    response = ask_claude(prompt, context)
    header = f"🔍 <b>${symbol} FULL ANALYSIS</b>\n"
    send_message(chat_id, header + response)


def handle_opinion(chat_id, symbol):
    """Handle /opinion [TICKER] command."""
    if not symbol:
        send_message(chat_id, "❌ Please provide a ticker. Example: /opinion NOW")
        return
    
    symbol = symbol.upper()
    quote = get_stock_quote(symbol)
    
    context = ""
    if quote:
        context = f"${symbol} current price: ${quote.get('price', 'N/A')} ({quote.get('changePercentage', 0):+.2f}% today)"
    
    prompt = f"""Give me a quick honest opinion on ${symbol}.
Buy, sell, or hold? Why?
Keep it to 4-5 sentences max.
Include one key risk.
Format for Telegram."""
    
    response = ask_claude(prompt, context)
    header = f"💬 <b>${symbol} QUICK TAKE</b>\n\n"
    send_message(chat_id, header + response)


def handle_scout(chat_id):
    """Handle /scout command."""
    send_message(chat_id, "⏳ Running weekly scout analysis...")
    
    # Get live prices for context
    context = f"Weekly scout for {datetime.now().strftime('%B %d, %Y')}\n"
    context += "Current watchlist prices:\n"
    
    for symbol in WATCHLIST[:6]:
        quote = get_stock_quote(symbol)
        if quote:
            context += f"{symbol}: ${quote.get('price', 'N/A')} ({quote.get('changePercentage', 0):+.2f}%)\n"
    
    prompt = """Give me 3 stock picks for this week.
For each pick include:
- Ticker and current thesis
- Why this week specifically
- Bull case (plain English)
- Bear case (plain English)
- Entry price range
- Stop loss
- Target price
- Position size recommendation

Focus on names with upcoming catalysts.
Format for Telegram mobile reading."""
    
    response = ask_claude(prompt, context)
    header = f"🔭 <b>LUMIS NOVA WEEKLY SCOUT</b>\n🕐 {datetime.now().strftime('%b %d, %Y')}\n\n"
    send_message(chat_id, header + response)


def handle_invest(chat_id, symbol):
    """Handle /invest [TICKER] command."""
    if not symbol:
        send_message(chat_id, "❌ Please provide a ticker. Example: /invest NOW")
        return
    
    symbol = symbol.upper()
    quote = get_stock_quote(symbol)
    consensus = get_analyst_consensus(symbol)
    
    context = f"${symbol} investment analysis:\n"
    if quote:
        context += f"Price: ${quote.get('price', 'N/A')}\n"
        context += f"Market Cap: ${quote.get('marketCap', 0)/1e9:.2f}B\n"
    if consensus:
        context += f"Consensus PT: ${consensus.get('targetConsensus', 'N/A')}\n"
    
    prompt = f"""Give me a long-term investing analysis for ${symbol}.
Cover:
1. Business quality and moat
2. 3-5 year revenue growth potential
3. Management quality signals
4. Covered call income potential
5. Compounding scenario (5yr, 10yr)
6. Entry strategy (how to build position)
7. Why this is an investment not a trade

Format for Telegram.
Today: {datetime.now().strftime('%B %d, %Y')}"""
    
    response = ask_claude(prompt, context)
    header = f"💼 <b>${symbol} INVESTING ANALYSIS</b>\n\n"
    send_message(chat_id, header + response)


def handle_help(chat_id):
    """Handle /help command."""
    msg = """⚡ <b>LUMIS CAPITAL BOT — COMMANDS</b>

📰 <b>Intelligence:</b>
/news — Top market stories
/macro — Yields + economic data
/earnings — Upcoming earnings calendar
/yields — Live treasury yield curve

🔍 <b>Research:</b>
/full [TICKER] — Full stock analysis
/opinion [TICKER] — Quick take
/invest [TICKER] — Long-term analysis
/scout — Weekly stock picks

📊 <b>Portfolio:</b>
/watchlist — Live prices on your stocks

ℹ️ <b>General:</b>
/start — Welcome message
/help — This menu

<b>Examples:</b>
/full NOW
/opinion ASTS
/invest GOOGL

<i>Powered by Lumis Nova AI + FMP Live Data</i>
<i>Not financial advice. Always DYOR.</i>"""
    send_message(chat_id, msg)


# ─────────────────────────────────────
# MAIN BOT LOOP
# ─────────────────────────────────────
def process_command(chat_id, text):
    """Route commands to handlers."""
    text = text.strip()
    parts = text.split()
    command = parts[0].lower() if parts else ""
    argument = parts[1] if len(parts) > 1 else ""
    
    log.info(f"Command: {command} | Arg: {argument} | Chat: {chat_id}")
    
    if command in ["/start", "/start@lumiscapital_bot"]:
        handle_start(chat_id)
    elif command in ["/help", "/help@lumiscapital_bot"]:
        handle_help(chat_id)
    elif command in ["/watchlist", "/watchlist@lumiscapital_bot"]:
        handle_watchlist(chat_id)
    elif command in ["/yields", "/yields@lumiscapital_bot"]:
        handle_yields(chat_id)
    elif command in ["/earnings", "/earnings@lumiscapital_bot"]:
        handle_earnings(chat_id)
    elif command in ["/news", "/news@lumiscapital_bot"]:
        handle_news(chat_id)
    elif command in ["/macro", "/macro@lumiscapital_bot"]:
        handle_macro(chat_id)
    elif command in ["/scout", "/scout@lumiscapital_bot"]:
        handle_scout(chat_id)
    elif command in ["/full", "/full@lumiscapital_bot"]:
        handle_full_analysis(chat_id, argument)
    elif command in ["/opinion", "/opinion@lumiscapital_bot"]:
        handle_opinion(chat_id, argument)
    elif command in ["/invest", "/invest@lumiscapital_bot"]:
        handle_invest(chat_id, argument)
    else:
        # Free text — let Claude respond
        response = ask_claude(
            text,
            f"User is asking via Lumis Capital Telegram bot. Today: {datetime.now().strftime('%B %d, %Y')}"
        )
        send_message(chat_id, response)


def run_bot():
    """Main polling loop."""
    log.info("🚀 Lumis Capital Bot starting...")
    
    # Verify configuration
    if not all([TELEGRAM_TOKEN, CHAT_ID, FMP_API_KEY, ANTHROPIC_API_KEY]):
        log.error("❌ Missing environment variables. Check Railway config.")
        return
    
    # Send startup notification
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
                        process_command(str(chat_id), text)
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception as e:
            log.error(f"Bot error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_bot()

import os
import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import io
from datetime import datetime, time
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackContext
from dotenv import load_dotenv
import asyncio
import logging
from apscheduler.events import EVENT_JOB_ERROR

# ==================== LOGGING CONFIGURATION ====================
# Configure logging to file and console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

# Disable noisy logs
logging.getLogger('httpx').setLevel(logging.WARNING)  # Disable Telegram API logs
logging.getLogger('apscheduler').setLevel(logging.WARNING)  # Disable scheduler logs

# Create main logger
logger = logging.getLogger(__name__)
# ===============================================================

# Load environment variables
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Technical Indicators
def calculate_ema(data, window):
    return data['Close'].ewm(span=window, adjust=False).mean()

def calculate_rsi(data, window=14):
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(data):
    ema12 = data['Close'].ewm(span=12, adjust=False).mean()
    ema26 = data['Close'].ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

async def check_daily_strategy(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        data = stock.history(period="60d", interval="1d")

        if len(data) < 50:
            return False, "Insufficient data", None

        data['EMA_9'] = calculate_ema(data, 9)
        data['EMA_20'] = calculate_ema(data, 20)
        data['EMA_21'] = calculate_ema(data, 21)
        data['EMA_50'] = calculate_ema(data, 50)
        data['RSI'] = calculate_rsi(data)
        data['RSI_EMA_20'] = data['RSI'].ewm(span=20, adjust=False).mean()
        data['MACD_Line'], data['Signal_Line'], data['MACD_Hist'] = calculate_macd(data)

        latest = data.iloc[-1]
        previous = data.iloc[-2]

        avg_volume_4 = data['Volume'].iloc[-5:-1].mean()
        volume_condition = latest['Volume'] > avg_volume_4

        condition1 = (latest['Close'] > latest['EMA_50']) and (previous['Close'] <= previous['EMA_50'])
        condition2 = latest['EMA_50'] > latest['EMA_9'] and latest['EMA_50'] > latest['EMA_21']
        condition3 = 40 <= latest['RSI'] <= 70 and latest['RSI'] > latest['RSI_EMA_20']
        condition4 = latest['MACD_Hist'] > 0
        condition5 = volume_condition

        if all([condition1, condition2, condition3, condition4, condition5]):
            plt.figure(figsize=(10, 6))
            plt.plot(data.index[-30:], data['Close'].iloc[-30:], label='Price', color='blue')
            plt.plot(data.index[-30:], data['EMA_9'].iloc[-30:], label='9 EMA', color='orange', linestyle='--')
            plt.plot(data.index[-30:], data['EMA_50'].iloc[-30:], label='50 EMA', color='red')
            plt.bar(data.index[-30:], data['MACD_Hist'].iloc[-30:], 
                   color=np.where(data['MACD_Hist'].iloc[-30:] >= 0, 'g', 'r'))
            
            plt.title(f'{ticker_symbol} Daily Chart')
            plt.legend()

            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            plt.close()

            signal = (
                f"🚀 *STRONG BUY* {ticker_symbol}\n"
                f"📅 {latest.name.strftime('%Y-%m-%d')}\n"
                f"💰 Price: ₹{latest['Close']:.2f}\n"
                f"📈 EMA(50) > EMA(9/21): ✓\n"
                f"📊 RSI(14): {latest['RSI']:.1f}\n"
                f"⚡ MACD Positive: ✓\n"
                f"🔊 Volume > Avg: ✓"
            )
            return True, signal, buf
        return False, None, None
    except Exception as e:
        logger.error(f"Error checking {ticker_symbol}: {str(e)}")
        return False, f"Error checking {ticker_symbol}: {str(e)}", None

def backtest_strategy(ticker_symbol, lookback_days=700):
    try:
        stock = yf.Ticker(ticker_symbol)
        data = stock.history(period=f"{lookback_days}d", interval="1d")

        if len(data) < 50:
            return f"Not enough data for {ticker_symbol}."

        data['EMA_9'] = calculate_ema(data, 9)
        data['EMA_20'] = calculate_ema(data, 20)
        data['EMA_21'] = calculate_ema(data, 21)
        data['EMA_50'] = calculate_ema(data, 50)
        data['RSI'] = calculate_rsi(data)
        data['RSI_EMA_20'] = data['RSI'].ewm(span=20, adjust=False).mean()
        data['MACD_Line'], data['Signal_Line'], data['MACD_Hist'] = calculate_macd(data)

        signals = []
        for i in range(50, len(data)):
            latest = data.iloc[i]
            previous = data.iloc[i - 1]
            avg_volume_4 = data['Volume'].iloc[i - 5:i - 1].mean()

            condition1 = (latest['Close'] > latest['EMA_50']) and (previous['Close'] <= previous['EMA_50'])
            condition2 = latest['EMA_50'] > latest['EMA_9'] and latest['EMA_50'] > latest['EMA_21']
            condition3 = 40 <= latest['RSI'] <= 70 and latest['RSI'] > latest['RSI_EMA_20']
            condition4 = latest['MACD_Hist'] > 0
            volume_condition = latest['Volume'] > avg_volume_4

            if all([condition1, condition2, condition3, condition4, volume_condition]):
                signals.append((latest.name.strftime('%Y-%m-%d'), latest['Close']))

        if not signals:
            return f"No signals found for {ticker_symbol} in last {lookback_days} days."

        result = f"🔙 Backtest: {ticker_symbol} (last {lookback_days} days)\n"
        for date, price in signals:
            result += f"📅 {date} - Price: ₹{price:.2f}\n"
        return result
    except Exception as e:
        logger.error(f"Backtest error for {ticker_symbol}: {str(e)}")
        return f"Error backtesting {ticker_symbol}: {str(e)}"

# Nifty 200 List
NIFTY_200 = [
    'RELIANCE.NS', 'TATASTEEL.NS', 'HDFCBANK.NS', 'INFY.NS', 'TCS.NS',
    'ICICIBANK.NS', 'KOTAKBANK.NS', 'HINDUNILVR.NS', 'ITC.NS', 'SBIN.NS'
]

async def send_auto_signals(context: CallbackContext):
    if not CHAT_ID:
        logger.error("CHAT_ID not set")
        return
    
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    
    if now.weekday() >= 5:  # Skip weekends
        return
    
    market_open = time(9, 15, tzinfo=ist)
    market_close = time(15, 30, tzinfo=ist)
    
    if not (market_open <= now.time() <= market_close):
        return
    
    logger.info(f"Scanning Nifty 200 at {now.strftime('%H:%M')}")
    
    signals_found = 0
    for ticker in NIFTY_200:
        try:
            has_signal, message, chart = await check_daily_strategy(ticker)
            if has_signal:
                await context.bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=chart,
                    caption=message,
                    parse_mode='Markdown'
                )
                signals_found += 1
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error processing {ticker}: {str(e)}")
            continue
    
    if signals_found == 0:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text="ℹ️ No buy signals found today."
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 *Stock Signal Bot*\n"
        "Commands:\n"
        "/check TICKER - Analyze stock\n"
        "/scan - Scan Nifty 200\n"
        "/backtest TICKER - 700-day backtest\n"
        "/help - Show this",
        parse_mode='Markdown'
    )

async def check_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /check TICKER")
        return
    
    ticker = context.args[0].upper()
    if '.' not in ticker:
        ticker += '.NS'
    
    has_signal, message, chart = await check_daily_strategy(ticker)
    
    if has_signal:
        await update.message.reply_photo(photo=chart, caption=message, parse_mode='Markdown')
    elif message:
        await update.message.reply_text(message)
    else:
        await update.message.reply_text(f"No signal for {ticker}")

async def scan_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Scanning Nifty 200...")
    await send_auto_signals(context)

async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /backtest TICKER")
        return
    
    ticker = context.args[0].upper()
    if '.' not in ticker:
        ticker += '.NS'
    
    result = backtest_strategy(ticker)
    await update.message.reply_text(result)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    await update.message.reply_text(f"⚠️ Error: {context.error}")

async def scheduler_error_listener(event):
    logger.error(f"Scheduler error: {event.exception}")

def main():
    application = Application.builder().token(TOKEN).build()
    
    # Handlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("help", start),
        CommandHandler("check", check_stock),
        CommandHandler("scan", scan_watchlist),
        CommandHandler("backtest", backtest_command)
    ]
    for handler in handlers:
        application.add_handler(handler)
    
    application.add_error_handler(error_handler)
    
    # Scheduler
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(
            send_auto_signals,
            interval=3600,
            first=10
        )
        job_queue.run_daily(
            send_auto_signals,
            time=time(9, 15, tzinfo=pytz.timezone('Asia/Kolkata')),
            days=(0, 1, 2, 3, 4)
        )
        job_queue.scheduler.add_listener(
            scheduler_error_listener,
            EVENT_JOB_ERROR
        )
    
    logger.info("Bot started successfully")
    application.run_polling()

if __name__ == '__main__':
    main()
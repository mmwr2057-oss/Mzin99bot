import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")

def get_binance_futures_symbols():
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    try:
        response = requests.get(url, timeout=10).json()
        if 'symbols' in response:
            symbols = [s['symbol'] for s in response['symbols'] if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING']
            return symbols
    except:
        pass
    return []

def calculate_cci(highs, lows, closes, period=20):
    if len(closes) < period:
        return None
    tp_list = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    tp_recent = tp_list[-period:]
    sma = sum(tp_recent) / period
    mad = sum([abs(x - sma) for x in tp_recent]) / period
    if mad == 0:
        return 0
    return (tp_list[-1] - sma) / (0.015 * mad)

def calculate_cmf(highs, lows, closes, volumes, period=20):
    if len(closes) < period:
        return None
    mf_volume_sum = 0
    volume_sum = 0
    start_idx = max(0, len(closes) - period)
    for i in range(start_idx, len(closes)):
        h, l, c, v = highs[i], lows[i], closes[i], volumes[i]
        if h == l:
            mf_multiplier = 0
        else:
            mf_multiplier = ((c - l) - (h - c)) / (h - l)
        mf_volume_sum += mf_multiplier * v
        volume_sum += v
    if volume_sum == 0:
        return 0
    return mf_volume_sum / volume_sum

def fetch_and_calculate_data(symbol):
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=30"
        res = requests.get(url, timeout=3)
        if res.status_code != 200:
            return None
        data = res.json()
        if len(data) < 20:
            return None
        highs = [float(candle[2]) for candle in data]
        lows = [float(candle[3]) for candle in data]
        closes = [float(candle[4]) for candle in data]
        volumes = [float(candle[5]) for candle in data]
        cci_val = calculate_cci(highs, lows, closes)
        cmf_val = calculate_cmf(highs, lows, closes, volumes)
        return {
            "symbol": symbol,
            "cci": round(cci_val, 2) if cci_val is not None else None,
            "cmf": round(cmf_val, 4) if cmf_val is not None else None
        }
    except:
        pass
    return None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_msg = (
        "🤖 أهلاً بك في بوت فحص عملات الفيوتشر الذكي!\n\n"
        "الأوامر المتاحة:\n"
        "📊 /cci - لعرض أعلى 10 عملات في مؤشر CCI\n"
        "💰 /cmf - لعرض أعلى 10 عملات في تدفق السيولة CMF"
    )
    await update.message.reply_text(welcome_msg)

async def scan_cci(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ جاري فحص السوق لمؤشر CCI...")
    symbols = get_binance_futures_symbols()
    if not symbols:
        await update.message.reply_text("❌ تعذر الاتصال ببينانس.")
        return
    results = []
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(fetch_and_calculate_data, sym): sym for sym in symbols}
        for future in as_completed(futures):
            res = future.result()
            if res and res["cci"] is not None:
                results.append((res["symbol"], res["cci"]))
    if results:
        results.sort(key=lambda x: x[1], reverse=True)
        top_10 = results[:10]
        msg = "🚀 **أعلى 10 عملات في مؤشر CCI (1h):**\n" + "="*30 + "\n"
        for i, (sym, val) in enumerate(top_10, 1):
            msg += f"{i}. `{sym}` --> CCI: **{val}**\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ لم يتم العثور على نتائج.")

async def scan_cmf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ جاري فحص السوق لمؤشر السيولة CMF...")
    symbols = get_binance_futures_symbols()
    if not symbols:
        await update.message.reply_text("❌ تعذر الاتصال ببينانس.")
        return
    results = []
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {executor.submit(fetch_and_calculate_data, sym): sym for sym in symbols}
        for future in as_completed(futures):
            res = future.result()
            if res and res["cmf"] is not None:
                results.append((res["symbol"], res["cmf"]))
    if results:
        results.sort(key=lambda x: x[1], reverse=True)
        top_10 = results[:10]
        msg = "💰 **أعلى 10 عملات في تدفق السيولة CMF (1h):**\n" + "="*30 + "\n"
        for i, (sym, val) in enumerate(top_10, 1):
            msg += f"{i}. `{sym}` --> CMF: **{val}**\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ لم يتم العثور على نتائج.")

def main():
    token = os.getenv("TOKEN")
    if not token:
        print("Error: TOKEN environment variable not found.")
        return
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cci", scan_cci))
    app.add_handler(CommandHandler("cmf", scan_cmf))
    app.run_polling()

if __name__ == "__main__":
    main()

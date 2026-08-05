import time
import asyncio
import aiohttp
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = "8806494753:AAGcMnnzCotKBpEVqdOYQ_gNu3Ncv4eRmTw"
ADMIN_CHAT_ID = None 

# ================= الوظائف الحسابية =================

def calculate_cci(highs, lows, closes, period=20):
    if len(closes) < period:
        return 0
    tp = [(h + l + c) / 3 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
    sma_tp = sum(tp) / period
    mad = sum(abs(x - sma_tp) for x in tp) / period
    if mad == 0:
        return 0
    cci = (tp[-1] - sma_tp) / (0.015 * mad)
    return cci

def calculate_cmf(highs, lows, closes, volumes, period=20):
    if len(closes) < period:
        return 0
    cmf_volumes = volumes[-period:]
    mf_volume_sum = 0
    sum_volume = sum(cmf_volumes)
    
    if sum_volume == 0:
        return 0
        
    for h, l, c, v in zip(highs[-period:], lows[-period:], closes[-period:], cmf_volumes):
        if h == l:
            mf_multiplier = 0
        else:
            mf_multiplier = ((c - l) - (h - c)) / (h - l)
        mf_volume_sum += mf_multiplier * v
        
    return mf_volume_sum / sum_volume

# ================= جلب بيانات السوق =================

async def get_all_binance_futures_symbols(session):
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    try:
        async with session.get(url, timeout=10) as response:
            data = await response.json()
            if 'symbols' in data:
                return [s['symbol'] for s in data['symbols'] if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING']
    except Exception:
        pass
    return []

async def fetch_binance_indicators_async(session, symbol, interval, sem, retries=3):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit=30"
    
    async with sem:
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        res = await response.json()
                        if isinstance(res, list) and len(res) >= 20:
                            highs = [float(x[2]) for x in res]
                            lows = [float(x[3]) for x in res]
                            closes = [float(x[4]) for x in res]
                            volumes = [float(x[5]) for x in res]
                            
                            cci_val = calculate_cci(highs, lows, closes, period=20)
                            cmf_val = calculate_cmf(highs, lows, closes, volumes, period=20)
                            return {"symbol": symbol, "cci": float(cci_val), "cmf": float(cmf_val)}
                    elif response.status == 429:
                        await asyncio.sleep(1)
                    else:
                        break
            except Exception:
                pass
            
            if attempt < retries - 1:
                await asyncio.sleep(0.5)
                
    return None

async def fetch_market_sentiment_async(session, symbol):
    sentiment = {}
    try:
        global_url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1"
        top_url = f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=1h&limit=1"
        
        async with session.get(global_url, timeout=3) as g_resp, session.get(top_url, timeout=3) as t_resp:
            g_data = await g_resp.json()
            t_data = await t_resp.json()
            
            if g_data and isinstance(g_data, list):
                sentiment['global_long'] = float(g_data[0]['longAccount']) * 100
                sentiment['global_short'] = float(g_data[0]['shortAccount']) * 100
            if t_data and isinstance(t_data, list):
                sentiment['whale_long'] = float(t_data[0]['longAccount']) * 100
                sentiment['whale_short'] = float(t_data[0]['shortAccount']) * 100
    except Exception:
        pass
    return sentiment

async def fetch_ls_position_change_async(session, symbol, interval, sem, retries=3):
    url = f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period={interval}&limit=2"
    
    async with sem:
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if isinstance(data, list) and len(data) >= 2:
                            prev_val = float(data[0]['longShortRatio'])
                            curr_val = float(data[1]['longShortRatio'])
                            if prev_val > 0:
                                pct_change = ((curr_val - prev_val) / prev_val) * 100
                                return {
                                    "symbol": symbol,
                                    "prev": prev_val,
                                    "curr": curr_val,
                                    "pct_change": pct_change
                                }
                        return None
                    elif response.status == 429:
                        await asyncio.sleep(1)
                    else:
                        break
            except Exception:
                pass
            if attempt < retries - 1:
                await asyncio.sleep(0.5)
    return None

# ================= واجهة المستخدم وأوامر البوت =================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_CHAT_ID
    ADMIN_CHAT_ID = update.effective_chat.id
    
    keyboard = [
        [
            InlineKeyboardButton("⚡ CCI (5د)", callback_data="scan_cci_5m"),
            InlineKeyboardButton("⚡ CCI (15د)", callback_data="scan_cci_15m"),
        ],
        [
            InlineKeyboardButton("⚡ CCI (1س)", callback_data="scan_cci_1h"),
            InlineKeyboardButton("⚡ CCI (4س)", callback_data="scan_cci_4h"),
        ],
        [
            InlineKeyboardButton("💰 قائمة مؤشر التدفق النقدي (CMF)", callback_data="scan_cmf_menu")
        ],
        [
            InlineKeyboardButton("🚀 صعود الموجبة (1س)", callback_data="scan_ls_posit_1h"),
            InlineKeyboardButton("📉 هبوط السالبة (1س)", callback_data="scan_ls_neg_1h")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_msg = (
        "🤖 **مرحباً بك في لوحة تحكم بوت التحليل الشامل**\n\n"
        "📊 اختر الأداة والفريم المطلوب لفحص السوق بدقة:\n"
    )
    
    if update.message:
        await update.message.reply_text(welcome_msg, parse_mode="Markdown", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(welcome_msg, parse_mode="Markdown", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("scan_cci_"):
        interval = data.replace("scan_cci_", "")
        await run_scan(query, interval, mode="cci")
        
    elif data == "scan_cmf_menu":
        keyboard = [
            [
                InlineKeyboardButton("📊 CMF (1س - موجب 🟢)", callback_data="scan_cmf_pos_1h"),
                InlineKeyboardButton("📉 CMF (1س - سالب 🔴)", callback_data="scan_cmf_neg_1h"),
            ],
            [
                InlineKeyboardButton("📊 CMF (4س - موجب 🟢)", callback_data="scan_cmf_pos_4h"),
                InlineKeyboardButton("📉 CMF (4س - سالب 🔴)", callback_data="scan_cmf_neg_4h"),
            ],
            [
                InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")
            ]
        ]
        await query.message.edit_text("📈 **اختر اتجاه وفريم الفحص لمؤشر التدفق النقدي (CMF):**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif data.startswith("scan_cmf_pos_"):
        interval = data.replace("scan_cmf_pos_", "")
        await run_scan_cmf(query, interval, direction="positive")
        
    elif data.startswith("scan_cmf_neg_"):
        interval = data.replace("scan_cmf_neg_", "")
        await run_scan_cmf(query, interval, direction="negative")
        
    elif data == "scan_ls_posit_1h":
        await run_scan_ls_posit(query, interval="1h")
        
    elif data == "scan_ls_neg_1h":
        await run_scan_ls_neg(query, interval="1h")
        
    elif data == "main_menu":
        await start_command(update, context)

# ================= دوال الفحص اليدوي (السكانر) =================

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_CHAT_ID
    ADMIN_CHAT_ID = update.effective_chat.id
    
    interval = "1h"
    valid_intervals = ['5m', '15m', '1h', '4h']
    if context.args and context.args[0] in valid_intervals:
        interval = context.args[0]
        
    await run_scan_text(update, interval)

async def run_scan_text(update, interval):
    status_msg = await update.message.reply_text(f"⚡ جاري فحص السوق وتصنيف المؤشرات على فريم **{interval}**...")
    start_time = time.time()
    
    sem = asyncio.Semaphore(15)
    
    async with aiohttp.ClientSession() as session:
        symbols = await get_all_binance_futures_symbols(session)
        tasks = [fetch_binance_indicators_async(session, sym, interval, sem) for sym in symbols]
        results_raw = await asyncio.gather(*tasks)
        
    results = [r for r in results_raw if r is not None]
    if not results:
        await status_msg.edit_text("❌ لم يتم العثور على بيانات حالياً.")
        return

    results_cci = sorted(results, key=lambda x: x["cci"], reverse=True)[:10]
    time_taken = round(time.time() - start_time, 2)
    
    msg = f"🚀 **تقرير السوق الشامل (فريم {interval})** - ⏱️ {time_taken}s\n" + "="*30 + "\n\n"
    msg += "🔥 **أعلى العملات في مؤشر CCI:**\n"
    for i, d in enumerate(results_cci, 1):
        msg += f"{i}. `{d['symbol']}` ➔ CCI: **{round(d['cci'], 2)}**\n"
        
    await status_msg.edit_text(msg, parse_mode="Markdown")

async def run_scan(query, interval, mode):
    await query.message.edit_text(f"⚡ جاري فحص السوق وجلب أعلى العملات لـ **{mode.upper()}** على فريم **{interval}**...")
    start_time = time.time()
    
    sem = asyncio.Semaphore(15)
    
    async with aiohttp.ClientSession() as session:
        symbols = await get_all_binance_futures_symbols(session)
        tasks = [fetch_binance_indicators_async(session, sym, interval, sem) for sym in symbols]
        results_raw = await asyncio.gather(*tasks)
        
    results = [r for r in results_raw if r is not None]
    if not results:
        await query.message.edit_text("❌ حدث خطأ أو لم يتم العثور على بيانات.")
        return

    time_taken = round(time.time() - start_time, 2)
    keyboard_back = [[InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]]
    
    results.sort(key=lambda x: x["cci"], reverse=True)
    msg = f"🔥 **أعلى العملات في مؤشر CCI (فريم {interval}):**\n"
    msg += f"⏱️ استغرق الفحص: {time_taken} ثانية\n" + "="*30 + "\n"
    for i, data in enumerate(results[:10], 1):
        msg += f"{i}. `{data['symbol']}` --> CCI: **{round(data['cci'], 2)}**\n"
            
    await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_back))

async def run_scan_cmf(query, interval, direction):
    dir_text = "الإيجابية (الموجبة 🟢)" if direction == "positive" else "السلبية (السالبة 🔴)"
    await query.message.edit_text(f"⚡ جاري فحص السيولة وتصنيف العملات {dir_text} لـ CMF على فريم **{interval}**...")
    start_time = time.time()
    sem = asyncio.Semaphore(15)
    
    async with aiohttp.ClientSession() as session:
        symbols = await get_all_binance_futures_symbols(session)
        tasks = [fetch_binance_indicators_async(session, sym, interval, sem) for sym in symbols]
        results_raw = await asyncio.gather(*tasks)
        
    results = [r for r in results_raw if r is not None]
    keyboard_back = [[InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="main_menu")]]
    if not results:
        await query.message.edit_text("❌ لم يتم العثور على بيانات.", reply_markup=InlineKeyboardMarkup(keyboard_back))
        return

    time_taken = round(time.time() - start_time, 2)
    
    if direction == "positive":
        results.sort(key=lambda x: x["cmf"], reverse=True)
        filtered = [x for x in results if x["cmf"] > 0][:10]
        msg = f"🟢 **أعلى العملات تدفقاً للسيولة CMF بالموجب (فريم {interval}):**\n"
    else:
        results.sort(key=lambda x: x["cmf"], reverse=False)
        filtered = [x for x in results if x["cmf"] < 0][:10]
        msg = f"🔴 **أعلى العملات هروباً للسيولة CMF بالسالب (فريم {interval}):**\n"

    msg += f"⏱️ استغرق: {time_taken} ثانية\n" + "="*30 + "\n"
    
    if not filtered:
        msg += "لا توجد عملات تطابق الشرط الحالي."
    else:
        for i, data in enumerate(filtered, 1):
            msg += f"{i}. `{data['symbol']}` --> CMF: **{round(data['cmf'], 4)}**\n"
            
    await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_back))

async def run_scan_ls_posit(query, interval):
    await query.message.edit_text(f"⚡ جاري فحص العملات للبحث عن **النسب الموجبة فقط** لتغير المراكز (L.S Posit) لفريم **{interval}**...")
    start_time = time.time()
    
    sem = asyncio.Semaphore(15)
    
    async with aiohttp.ClientSession() as session:
        symbols = await get_all_binance_futures_symbols(session)
        tasks = [fetch_ls_position_change_async(session, sym, interval, sem) for sym in symbols]
        results_raw = await asyncio.gather(*tasks)
        
    results = [r for r in results_raw if r is not None and r["pct_change"] > 0]
    keyboard_back = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
    
    if not results:
        await query.message.edit_text("❌ لا توجد عملات بنسب تغير موجبة حالياً.", reply_markup=InlineKeyboardMarkup(keyboard_back))
        return

    results.sort(key=lambda x: x["pct_change"], reverse=True)
    filtered_results = results[:10]

    time_taken = round(time.time() - start_time, 2)
    msg = f"🚀 **أعلى 10 عملات في النسب الموجبة لمراكز الحيتان (فريم {interval}):**\n"
    msg += f"⏱️ استغرق الفحص: {time_taken} ثانية\n" + "="*30 + "\n"
    
    for i, data in enumerate(filtered_results, 1):
        pct = data["pct_change"]
        msg += (
            f"**{i}. `{data['symbol']}`**\n"
            f"📈 نسبة الصعود: **+{pct:.2f}%**\n"
            f"🔹 من `{data['prev']}` ➔ `{data['curr']}`\n"
            f"{'-'*15}\n"
        )
            
    await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_back))

async def run_scan_ls_neg(query, interval):
    await query.message.edit_text(f"⚡ جاري فحص العملات للبحث عن **النسب السالبة فقط** لتغير المراكز (L.S Posit) لفريم **{interval}**...")
    start_time = time.time()
    
    sem = asyncio.Semaphore(15)
    
    async with aiohttp.ClientSession() as session:
        symbols = await get_all_binance_futures_symbols(session)
        tasks = [fetch_ls_position_change_async(session, sym, interval, sem) for sym in symbols]
        results_raw = await asyncio.gather(*tasks)
        
    results = [r for r in results_raw if r is not None and r["pct_change"] < 0]
    keyboard_back = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
    
    if not results:
        await query.message.edit_text("❌ لا توجد عملات بنسب تغير سالبة حالياً.", reply_markup=InlineKeyboardMarkup(keyboard_back))
        return

    # ترتيب تصاعدي (الأكثر هبوطاً / الأكثر سالبية في الأعلى)
    results.sort(key=lambda x: x["pct_change"], reverse=False)
    filtered_results = results[:10]

    time_taken = round(time.time() - start_time, 2)
    msg = f"📉 **أعلى 10 عملات في النسب السالبة لمراكز الحيتان (فريم {interval}):**\n"
    msg += f"⏱️ استغرق الفحص: {time_taken} ثانية\n" + "="*30 + "\n"
    
    for i, data in enumerate(filtered_results, 1):
        pct = data["pct_change"]
        msg += (
            f"**{i}. `{data['symbol']}`**\n"
            f"📉 نسبة التغير: **{pct:.2f}%**\n"
            f"🔹 من `{data['prev']}` ➔ `{data['curr']}`\n"
            f"{'-'*15}\n"
        )
            
    await query.message.edit_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_back))

# ================= مراقب الخلفية (التنبيهات التلقائية) =================

async def background_monitor(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID:
        return
        
    notified_cci_cache = context.bot_data.get("notified_cci", set())
    notified_cmf_cache = context.bot_data.get("notified_cmf", set())
    notified_ls_cache = context.bot_data.get("notified_ls", set())
    
    sem = asyncio.Semaphore(15)
    
    async with aiohttp.ClientSession() as session:
        symbols = await get_all_binance_futures_symbols(session)
        
        tasks_indicators = [fetch_binance_indicators_async(session, sym, "1h", sem) for sym in symbols]
        results_ind = await asyncio.gather(*tasks_indicators)
        
        tasks_ls = [fetch_ls_position_change_async(session, sym, "1h", sem) for sym in symbols]
        results_ls = await asyncio.gather(*tasks_ls)
        
        for data in results_ind:
            if not data: continue
            sym_name = data["symbol"]
            cci = data["cci"]
            cmf = data["cmf"]
            
            if cci > 500 or cci < -500:
                alert_key_cci = f"BINANCE_CCI_{sym_name}"
                if alert_key_cci not in notified_cci_cache:
                    notified_cci_cache.add(alert_key_cci)
                    sentiment = await fetch_market_sentiment_async(session, sym_name)
                    extra_info = ""
                    if sentiment:
                        extra_info = (
                            f"\n📊 **نظرة عامة على الصفقات الآن:**\n"
                            f"🐋 **الحيتان:** 🟢 {sentiment.get('whale_long', 0):.2f}% | 🔴 {sentiment.get('whale_short', 0):.2f}%\n"
                            f"👥 **العاديون:** 🟢 {sentiment.get('global_long', 0):.2f}% | 🔴 {sentiment.get('global_short', 0):.2f}%\n"
                        )
                    direction = "🟢 صعودي متطرف (تجاوز 500)" if cci > 500 else "🔴 هبوطي متطرف (تجاوز -500)"
                    msg = f"🚨 **تنبيه اختراق CCI متطرف!** 🚨\nالعملة: `{sym_name}`\nالاتجاه: **{direction}**\nقيمة CCI (1h): **{round(cci, 2)}**\nقيمة CMF الحالية: **{round(cmf, 4)}**\n{extra_info}"
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown")
            else:
                notified_cci_cache.discard(f"BINANCE_CCI_{sym_name}")

            if cmf > 0.6 or cmf < -0.6:
                alert_key_cmf = f"BINANCE_CMF_{sym_name}"
                if alert_key_cmf not in notified_cmf_cache:
                    notified_cmf_cache.add(alert_key_cmf)
                    status_type = "🟢 إيجابي متطرف (تجاوز 0.6)" if cmf > 0.6 else "🔴 سلبي متطرف (انخفض تحت -0.6)"
                    cmf_msg = f"⚡ **تنبيه تطرف CMF (1h)!** ⚡\nالعملة: `{sym_name}`\nالحالة: **{status_type}**\nقيمة CMF: **{round(cmf, 4)}**\nقيمة CCI المصاحبة: **{round(cci, 2)}**"
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=cmf_msg, parse_mode="Markdown")
            else:
                notified_cmf_cache.discard(f"BINANCE_CMF_{sym_name}")
                
        for data in results_ls:
            if not data: continue
            sym_name = data["symbol"]
            pct_change = data["pct_change"]
            
            # 1. تنبيه النسب الموجبة (>= +20%)
            if pct_change >= 20.0:
                alert_key_ls_pos = f"BINANCE_LS_POS_{sym_name}"
                if alert_key_ls_pos not in notified_ls_cache:
                    notified_ls_cache.add(alert_key_ls_pos)
                    msg = (
                        f"🚨 **تنبيه صعود حاد للمراكز (تكدس مشترين إيجابي)!** 🚀\n"
                        f"العملة: `{sym_name}`\n"
                        f"نسبة التغير الموجب (1h): **+{pct_change:.2f}%**\n"
                        f"المعامل السابق: `{data['prev']}` ➔ الحالي: `{data['curr']}`\n"
                        f"⚠️ **تكدس شرائي ملحوظ، راقب حركة السعر!**"
                    )
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown")
            else:
                notified_ls_cache.discard(f"BINANCE_LS_POS_{sym_name}")

            # 2. تنبيه النسب السالبة (<= -20%)
            if pct_change <= -20.0:
                alert_key_ls_neg = f"BINANCE_LS_NEG_{sym_name}"
                if alert_key_ls_neg not in notified_ls_cache:
                    notified_ls_cache.add(alert_key_ls_neg)
                    msg = (
                        f"🚨 **تنبيه هبوط حاد للمراكز (تخلص من العقود/تكدس بائعين)!** 📉\n"
                        f"العملة: `{sym_name}`\n"
                        f"نسبة التغير السالب (1h): **{pct_change:.2f}%**\n"
                        f"المعامل السابق: `{data['prev']}` ➔ الحالي: `{data['curr']}`\n"
                        f"⚠️ **تكدس بيعي أو خروج حاد للمراكز، احتمالية انعكاس أو Short Squeeze!**"
                    )
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown")
            else:
                notified_ls_cache.discard(f"BINANCE_LS_NEG_{sym_name}")
                
    context.bot_data["notified_cci"] = notified_cci_cache
    context.bot_data["notified_cmf"] = notified_cmf_cache
    context.bot_data["notified_ls"] = notified_ls_cache

# ================= الإعداد والتشغيل =================

async def post_init(application):
    commands = [
        BotCommand("start", "فتح لوحة التحكم والأزرار الرئيسية"),
        BotCommand("cci", "فحص السوق يدوياً (مثال: /cci 15m)")
    ]
    await application.bot.set_my_commands(commands)

def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cci", scan_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    app.job_queue.run_repeating(background_monitor, interval=300, first=10)
    
    print("Bot with Positive & Negative Squeeze Alerts is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

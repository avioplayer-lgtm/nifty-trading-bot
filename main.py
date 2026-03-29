import yfinance as yf
import pandas as pd
import requests
import time
import random
from datetime import datetime
import pytz

# ---------------- TELEGRAM CONFIG ----------------
BOT_TOKEN = "8748334869:AAFmCuoybJ-R-oMBJDbbfxVpo7grnSnmNHM"
CHAT_ID = "1209845315"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload)
    except:
        print("Telegram error")

# ---------------- TIMEZONE ----------------
IST = pytz.timezone("Asia/Kolkata")

# ---------------- GLOBAL VARIABLES ----------------
last_signal = ""
sent_start_msg = False
sent_end_msg = False
sent_0910 = False
sent_0915 = False

oi_resistance = None
oi_support = None
last_oi_fetch = 0

# ---------------- HELPERS ----------------
def get_smart_strike(price, confidence, option_type):
    base = round(price / 50) * 50

    if confidence >= 8:
        return base + 50 if option_type == "CE" else base - 50
    elif confidence == 7:
        return base
    else:
        return base - 50 if option_type == "CE" else base + 50

def wait_for_next_candle():
    now = datetime.now(IST)
    seconds = now.minute * 60 + now.second
    next_run = ((seconds // 300) + 1) * 300
    sleep_time = next_run - seconds
    time.sleep(sleep_time)

def get_option_chain():
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }

    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers)

    time.sleep(random.uniform(1, 2))

    response = session.get(url, headers=headers).json()
    data = response['records']['data']

    call_oi = {}
    put_oi = {}

    for item in data:
        strike = item.get('strikePrice')

        if item.get('CE'):
            call_oi[strike] = item['CE']['openInterest']

        if item.get('PE'):
            put_oi[strike] = item['PE']['openInterest']

    max_call = max(call_oi, key=call_oi.get)
    max_put = max(put_oi, key=put_oi.get)

    return max_call, max_put

# ---------------- MAIN LOOP ----------------
while True:
    now = datetime.now(IST)
    current_time = now.strftime("%H:%M")

    # WEEKEND BLOCK
    if now.weekday() >= 5:
        time.sleep(300)
        continue

    # 🔔 REMINDERS
    if "09:10" <= current_time < "09:11" and not sent_0910:
        send_telegram("🔔 09:10 Reminder: Market opens soon.")
        sent_0910 = True

    if "09:15" <= current_time < "09:16" and not sent_0915:
        send_telegram("🔔 09:15 Reminder: Market opened.")
        sent_0915 = True

    # 🚀 START
    if "09:20" <= current_time < "09:21" and not sent_start_msg:
        send_telegram("🚀 Bot started. Monitoring market.")
        sent_start_msg = True

    # 🛑 END
    if "15:30" <= current_time < "15:31" and not sent_end_msg:
        send_telegram("🛑 Market closed. Bot standby.")
        sent_end_msg = True

    # RESET NEXT DAY
    if current_time < "09:00":
        sent_start_msg = False
        sent_end_msg = False
        sent_0910 = False
        sent_0915 = False

    # ⚔️ TRADING WINDOW
    if "09:20" <= current_time <= "15:25":

        try:
            df = yf.download("^NSEI", interval="5m", period="1d", auto_adjust=True, progress=False)
            df.columns = df.columns.get_level_values(0)
            df = df.dropna()

            if len(df) < 10:
                wait_for_next_candle()
                continue

            # ORB
            orb_high = df.iloc[:3]['High'].max()
            orb_low = df.iloc[:3]['Low'].min()

            last = df.iloc[-1]
            recent = df.iloc[-10:]

            # TREND
            last_close = recent['Close'].iloc[-1]
            mean_close = recent['Close'].mean()

            if last_close > mean_close:
                trend = "bullish"
            elif last_close < mean_close:
                trend = "bearish"
            else:
                trend = "sideways"

            # CANDLE
            open_price = last['Open']
            close_price = last['Close']
            high_price = last['High']
            low_price = last['Low']

            body = abs(close_price - open_price)
            range_candle = high_price - low_price
            strong_candle = body > (0.6 * range_candle) if range_candle > 0 else False

            # VOLUME
            avg_vol = df.iloc[-6:-1]['Volume'].mean()
            curr_vol = last['Volume']
            high_volume = curr_vol > avg_vol

            # ATR
            df['range'] = df['High'] - df['Low']
            atr = df['range'].rolling(10).mean().iloc[-1]

            # CONFIDENCE
            confidence = 0

            if close_price > orb_high or close_price < orb_low:
                confidence += 3

            if (trend == "bullish" and close_price > orb_high) or \
               (trend == "bearish" and close_price < orb_low):
                confidence += 3

            if strong_candle:
                confidence += 2

            if high_volume:
                confidence += 2

            # FETCH OI (every 15 min)
            if time.time() - last_oi_fetch > 900:
                try:
                    oi_resistance, oi_support = get_option_chain()
                    last_oi_fetch = time.time()
                    print("OI updated:", oi_resistance, oi_support)
                except:
                    print("OI fetch failed")

            # DECISION
            entry = close_price
            sl_buffer = atr * 1.2
            target_buffer = atr * 2

            option = "NO TRADE"
            stop_loss = None
            target = None

            if confidence >= 7:

                # CALL
                if entry > orb_high:
                    if oi_resistance and (oi_resistance - entry < 50):
                        option = "NO TRADE"
                    else:
                        strike = get_smart_strike(entry, confidence, "CE")
                        option = f"{strike} CE"
                        stop_loss = entry - sl_buffer
                        target = min(entry + target_buffer, oi_resistance) if oi_resistance else entry + target_buffer

                # PUT
                elif entry < orb_low:
                    if oi_support and (entry - oi_support < 50):
                        option = "NO TRADE"
                    else:
                        strike = get_smart_strike(entry, confidence, "PE")
                        option = f"{strike} PE"
                        stop_loss = entry + sl_buffer
                        target = max(entry - target_buffer, oi_support) if oi_support else entry - target_buffer

            # ALERT
            if option != "NO TRADE" and option != last_signal:
                msg = f"""
🚨 TRADE ALERT 🚨

Decision: {option}
Trend: {trend}
Confidence: {confidence}/10

ATR: {round(atr,2)}
OI Resistance: {oi_resistance}
OI Support: {oi_support}

Entry: {round(entry,2)}
SL: {round(stop_loss,2)}
Target: {round(target,2)}
"""
                send_telegram(msg)
                last_signal = option

            print(f"{now.strftime('%H:%M:%S')} | {round(entry,2)} | {trend} | {confidence} | {option}", flush=True)

        except Exception as e:
            print("Error:", e)

    wait_for_next_candle()

import yfinance as yf
import pandas as pd
import requests
import time
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

# ---------------- HELPER ----------------
def get_atm_strike(price):
    return round(price / 50) * 50

def wait_for_next_candle():
    now = datetime.now(IST)
    seconds = now.minute * 60 + now.second
    next_run = ((seconds // 300) + 1) * 300
    sleep_time = next_run - seconds
    time.sleep(sleep_time)

# ---------------- FLAGS ----------------
last_signal = ""
sent_start_msg = False
sent_end_msg = False
sent_0910 = False
sent_0915 = False

# ---------------- LOOP ----------------
while True:
    now = datetime.now(IST)
    current_time = now.strftime("%H:%M")

    # 🔔 PRE-MARKET REMINDERS
    if current_time >= "09:10" and not sent_0910:
        send_telegram("🔔 09:10 Reminder: Market opens soon. Get ready.")
        print("09:10 reminder sent")
        sent_0910 = True

    if current_time >= "09:15" and not sent_0915:
        send_telegram("🔔 09:15 Reminder: Market opened. Stay sharp.")
        print("09:15 reminder sent")
        sent_0915 = True

    # 🚀 START MESSAGE
    if "09:20" <= current_time <= "09:25" and not sent_start_msg:
        send_telegram("🚀 Bot started. Monitoring market now.")
        print("Bot started message sent")
        sent_start_msg = True

    # 🛑 END OF DAY
    if current_time > "15:30" and not sent_end_msg:
        send_telegram("🛑 Market closed. Bot going standby.")
        print("Market closed message sent")
        sent_end_msg = True

    # 🔄 RESET FLAGS NEXT DAY
    if current_time < "09:00":
        sent_start_msg = False
        sent_end_msg = False
        sent_0910 = False
        sent_0915 = False

    # ⚔️ MAIN TRADING LOGIC
    if "09:20" <= current_time <= "15:25":

        try:
            df = yf.download("^NSEI", interval="5m", period="1d", auto_adjust=True, progress=False)
            df.columns = df.columns.get_level_values(0)
            df = df.dropna()

            if len(df) < 10:
                print("Not enough data...")
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

            entry = close_price
            atm = get_atm_strike(entry)

            option = "NO TRADE"
            stop_loss = None
            target = None

            if confidence >= 7:
                if entry > orb_high:
                    option = f"{atm} CE"
                    stop_loss = entry - 40
                    target = entry + 80

                elif entry < orb_low:
                    option = f"{atm} PE"
                    stop_loss = entry + 40
                    target = entry - 80

            # 📡 ALERT
            if option != "NO TRADE" and option != last_signal:
                msg = f"""
🚨 TRADE ALERT 🚨

Decision: {option}
Trend: {trend}
Confidence: {confidence}/10

Entry: {round(entry,2)}
SL: {round(stop_loss,2)}
Target: {round(target,2)}
"""
                send_telegram(msg)
                last_signal = option

            print(f"{now.strftime('%H:%M:%S')} | {round(entry,2)} | {trend} | {confidence} | {option}", flush=True)

        except Exception as e:
            print("Error:", e)

    # ⏱️ WAIT FOR NEXT CANDLE
    wait_for_next_candle()

import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime

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

# ---------------- HELPER ----------------
def get_atm_strike(price):
    return round(price / 50) * 50

last_signal = ""

# ---------------- START ----------------
send_telegram("🚀 Bot started (Production Mode)")
print("Bot started (Production Mode)...")

# ---------------- LOOP ----------------
while True:
    now = datetime.now()

    # ✅ MARKET HOURS ONLY
    if 9 <= now.hour <= 15:

        try:
            # FETCH DATA
            df = yf.download("^NSEI", interval="5m", period="1d", auto_adjust=True, progress=False)

            # FIX MULTI-INDEX ISSUE
            df.columns = df.columns.get_level_values(0)

            # CLEAN DATA
            df = df.dropna()

            if len(df) < 10:
                print("Not enough data...")
                time.sleep(60)
                continue

            # ORB LEVELS (First 15 min)
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

            # CANDLE STRENGTH
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

            # CONFIDENCE SCORE
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

            # ENTRY LOGIC
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

            # SEND ALERT (NO DUPLICATES)
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

            # LOG
            print(f"{now.strftime('%H:%M:%S')} | Price: {round(entry,2)} | Trend: {trend} | Conf: {confidence} | {option}", flush=True)

        except Exception as e:
            print("Error:", e)

    # ⏱️ WAIT 5 MINUTES
    time.sleep(300)

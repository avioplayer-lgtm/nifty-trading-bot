import yfinance as yf
import pandas as pd
import requests
import time
import random
from datetime import datetime
import pytz

# ---------------- TELEGRAM ----------------
BOT_TOKEN = "8748334869:AAFmCuoybJ-R-oMBJDbbfxVpo7grnSnmNHM"
CHAT_ID = "1209845315"

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        print("Telegram error")

# ---------------- TIME ----------------
IST = pytz.timezone("Asia/Kolkata")

def wait_next():
    now = datetime.now(IST)
    sec = now.minute * 60 + now.second
    next_run = ((sec // 300) + 1) * 300
    time.sleep(next_run - sec)

# ---------------- GLOBAL STATE ----------------
last_signal = ""
trade_count = 0
last_trade_time = 0

oi_resistance = None
oi_support = None
last_oi_fetch = 0

# ---------------- SMART STRIKE ----------------
def get_strike(price, conf, type):
    base = round(price / 50) * 50

    if conf >= 8:
        return base + 50 if type == "CE" else base - 50
    elif conf == 7:
        return base
    else:
        return base - 50 if type == "CE" else base + 50

# ---------------- OPTION CHAIN ----------------
def get_oi():
    try:
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nseindia.com/"
        }

        s = requests.Session()
        s.get("https://www.nseindia.com", headers=headers)
        time.sleep(random.uniform(1,2))

        data = s.get(url, headers=headers).json()

        if 'records' not in data:
            return None, None

        data = data['records']['data']

        call_oi = {}
        put_oi = {}

        for item in data:
            strike = item['strikePrice']
            if item.get('CE'):
                call_oi[strike] = item['CE']['openInterest']
            if item.get('PE'):
                put_oi[strike] = item['PE']['openInterest']

        res = max(call_oi, key=call_oi.get)
        sup = max(put_oi, key=put_oi.get)

        return res, sup

    except:
        return None, None

# ---------------- MAIN LOOP ----------------
print("Bot started...")

while True:
    now = datetime.now(IST)
    time_str = now.strftime("%H:%M")

    # WEEKEND
    if now.weekday() >= 5:
        time.sleep(300)
        continue

    # RESET DAILY
    if time_str < "09:00":
        trade_count = 0

    # TRADING WINDOW
    if "09:20" <= time_str <= "14:30":

        try:
            df = yf.download("^NSEI", interval="5m", period="1d", progress=False)

            if df.empty:
                wait_next()
                continue

            df.columns = df.columns.get_level_values(0)
            df = df.dropna()

            if len(df) < 10:
                wait_next()
                continue

            # ORB
            orb_high = df.iloc[:3]['High'].max()
            orb_low = df.iloc[:3]['Low'].min()

            last = df.iloc[-1]
            recent = df.iloc[-10:]

            # TREND
            trend = "bullish" if last['Close'] > recent['Close'].mean() else "bearish"

            # CANDLE
            body = abs(last['Close'] - last['Open'])
            range_ = last['High'] - last['Low']
            strong = body > (0.6 * range_) if range_ > 0 else False

            # VOLUME
            vol = last['Volume'] > df.iloc[-6:-1]['Volume'].mean()

            # ATR
            df['range'] = df['High'] - df['Low']
            atr = df['range'].rolling(10).mean().iloc[-1]

            # CONFIDENCE
            conf = 0
            if last['Close'] > orb_high or last['Close'] < orb_low:
                conf += 3
            if (trend == "bullish" and last['Close'] > orb_high) or \
               (trend == "bearish" and last['Close'] < orb_low):
                conf += 3
            if strong:
                conf += 2
            if vol:
                conf += 2

            # FETCH OI
            if time.time() - last_oi_fetch > 900:
                oi_resistance, oi_support = get_oi()
                last_oi_fetch = time.time()

            # TRADE LIMIT
            if trade_count >= 2:
                wait_next()
                continue

            # COOLDOWN
            if time.time() - last_trade_time < 1800:
                wait_next()
                continue

            # DECISION
            entry = last['Close']
            sl = atr * 1.2
            tgt = atr * 2

            option = "NO TRADE"

            if conf >= 7:

                # CALL
                if entry > orb_high:
                    if oi_resistance and (oi_resistance - entry < 50):
                        option = "NO TRADE"
                    else:
                        strike = get_strike(entry, conf, "CE")
                        option = f"{strike} CE"
                        SL = entry - sl
                        TARGET = entry + tgt

                # PUT
                elif entry < orb_low:
                    if oi_support and (entry - oi_support < 50):
                        option = "NO TRADE"
                    else:
                        strike = get_strike(entry, conf, "PE")
                        option = f"{strike} PE"
                        SL = entry + sl
                        TARGET = entry - tgt

            # ALERT
            if option != "NO TRADE" and option != last_signal:
                msg = f"""
🚨 TRADE ALERT 🚨

{option}
Trend: {trend}
Confidence: {conf}/10

Entry: {round(entry,2)}
SL: {round(SL,2)}
Target: {round(TARGET,2)}
"""
                send_telegram(msg)

                last_signal = option
                trade_count += 1
                last_trade_time = time.time()

            print(f"{time_str} | {entry:.2f} | Conf:{conf} | Trades:{trade_count}", flush=True)

        except Exception as e:
            print("Error:", e)

    wait_next()

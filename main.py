import yfinance as yf
import pandas as pd
import requests
import time
import random
from datetime import datetime
import pytz

# ---------------- CONFIG ----------------
BOT_TOKEN = "8748334869:AAFmCuoybJ-R-oMBJDbbfxVpo7grnSnmNHM"
CHAT_ID = "1209845315"

capital = 30000
risk_per_trade = 0.005
max_daily_loss = capital * 0.02

SYMBOLS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK"
}

# ---------------- TELEGRAM ----------------
def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
    except:
        pass

# ---------------- TIME ----------------
IST = pytz.timezone("Asia/Kolkata")

def wait_next():
    now = datetime.now(IST)
    sec = now.minute * 60 + now.second
    nxt = ((sec // 300) + 1) * 300
    time.sleep(nxt - sec)

# ---------------- STATE ----------------
active_trade = None
daily_loss = 0
current_day = None

sent_start = False
sent_stop = False
last_heartbeat_hour = -1

# ---------------- INDICATORS ----------------
def compute(df):
    df['ema9'] = df['Close'].ewm(span=9).mean()
    df['ema21'] = df['Close'].ewm(span=21).mean()

    delta = df['Close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))

    df['atr'] = (df['High'] - df['Low']).rolling(10).mean()

    df['cum_vol'] = df['Volume'].cumsum()
    df['cum_pv'] = (df['Close'] * df['Volume']).cumsum()
    df['vwap'] = df['cum_pv'] / df['cum_vol']

    return df

# ---------------- SIDEWAYS ----------------
def is_sideways(df, atr, ema9, ema21):
    r = df.iloc[-10:]
    rng = r['High'].max() - r['Low'].min()

    cond1 = atr < rng * 0.25
    cond2 = abs(ema9 - ema21) < atr * 0.3
    cond3 = rng < atr * 3

    return sum([cond1, cond2, cond3]) >= 2

# ---------------- SCAN ----------------
def scan_symbol(name, symbol):
    try:
        df = yf.download(symbol, interval="5m", period="1d", progress=False)

        if df.empty:
            return None

        df.columns = df.columns.get_level_values(0)
        df = df.dropna()

        if len(df) < 20:
            return None

        df = compute(df)

        last = df.iloc[-1]
        close = float(last['Close'])
        atr = float(last['atr'])
        ema9 = float(last['ema9'])
        ema21 = float(last['ema21'])
        rsi = float(last['rsi'])
        vwap = float(last['vwap'])

        orb_high = df.iloc[:3]['High'].max()
        orb_low = df.iloc[:3]['Low'].min()

        sideways = is_sideways(df, atr, ema9, ema21)

        conf = 0
        if close > orb_high or close < orb_low:
            conf += 3
        if (ema9 > ema21 and close > orb_high) or (ema9 < ema21 and close < orb_low):
            conf += 3
        if (rsi > 55 and close > orb_high) or (rsi < 45 and close < orb_low):
            conf += 2

        if conf < 7 or sideways:
            return None

        if close > orb_high and close > vwap:
            entry = close + 2
            sl = entry - atr
            tgt = entry + atr * 2
            direction = "CE"

        elif close < orb_low and close < vwap:
            entry = close - 2
            sl = entry + atr
            tgt = entry - atr * 2
            direction = "PE"
        else:
            return None

        return {
            "symbol": name,
            "confidence": conf,
            "entry": entry,
            "sl": sl,
            "tgt": tgt,
            "direction": direction
        }

    except:
        return None

# ---------------- MAIN ----------------
print("Dual Signal Bot Started...")

while True:

    now = datetime.now(IST)
    t = now.strftime("%H:%M")

    if now.weekday() >= 5:
        time.sleep(300)
        continue

    # RESET DAILY
    if current_day != now.date():
        current_day = now.date()
        daily_loss = 0
        active_trade = None
        sent_start = False
        sent_stop = False
        last_heartbeat_hour = -1

    # START / STOP
    if "09:15" <= t < "09:16" and not sent_start:
        send_telegram("🚀 Bot started.")
        sent_start = True

    if "15:30" <= t < "15:31" and not sent_stop:
        send_telegram("🛑 Market closed.")
        sent_stop = True

    # HEARTBEAT + RULES
    if "09:20" <= t <= "15:25":
        if now.hour != last_heartbeat_hour:
            send_telegram("""
💓 Bot Alive

📌 RULES:
1. Only ONE trade
2. Pick BEST confidence
3. No emotional override
4. Respect SL
""")
            last_heartbeat_hour = now.hour

    # TRADING WINDOW
    if "09:20" <= t <= "15:25":

        if daily_loss >= max_daily_loss:
            send_telegram("🛑 Daily loss limit hit. Stopping.")
            break

        try:
            signals = []

            for name, symbol in SYMBOLS.items():
                result = scan_symbol(name, symbol)
                if result:
                    signals.append(result)

            if signals:
                best = max(signals, key=lambda x: x['confidence'])

                msg = "🚨 SIGNAL UPDATE\n\n"

                for s in signals:
                    msg += f"""
{s['symbol']}:
Type: {s['direction']}
Conf: {s['confidence']}
Entry: {round(s['entry'],2)}
SL: {round(s['sl'],2)}
Target: {round(s['tgt'],2)}
"""

                msg += f"\n⭐ BEST PICK: {best['symbol']} (Conf: {best['confidence']})\n"

                if active_trade:
                    msg += "\n⚠️ Trade already active. Do NOT take another."

                send_telegram(msg)

            # EXIT TRACKING
            if active_trade:
                price = active_trade['entry']

                if (active_trade['direction']=="CE" and price <= active_trade['sl']) or \
                   (active_trade['direction']=="PE" and price >= active_trade['sl']):
                    send_telegram("🔴 SL HIT")
                    daily_loss += abs(active_trade['entry'] - price)
                    active_trade = None

                elif (active_trade['direction']=="CE" and price >= active_trade['tgt']) or \
                     (active_trade['direction']=="PE" and price <= active_trade['tgt']):
                    send_telegram("🟢 TARGET HIT")
                    active_trade = None

        except Exception as e:
            print("Error:", e)

    wait_next()

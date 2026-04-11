import yfinance as yf
import pandas as pd
import requests
import time
import random
import os
import logging
from datetime import datetime
import pytz

# ---------------- CONFIG ----------------
BOT_TOKEN = "8748334869:AAFmCuoybJ-R-oMBJDbbfxVpo7grnSnmNHM"
CHAT_ID = "1209845315"

capital = 100000
risk_per_trade = 0.005  # start safe (0.5%)

max_trades = 3

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except:
        pass

IST = pytz.timezone("Asia/Kolkata")

# ---------------- STATE ----------------
last_signal = ""
active_trade = None

range_high = None
range_low = None
was_sideways = False

oi_resistance, oi_support, oi_pcr = None, None, 1
last_oi_fetch = 0

trades_today = 0
daily_loss = 0
max_daily_loss = capital * 0.02

current_day = None

# ---------------- HELPERS ----------------
def wait_next():
    now = datetime.now(IST)
    sec = now.minute * 60 + now.second
    nxt = ((sec // 300) + 1) * 300
    time.sleep(nxt - sec)

def get_time_weight(now):
    t = now.hour + now.minute / 60
    if 10 <= t <= 11.5: return 1.0
    elif 13.5 <= t <= 14.5: return 0.9
    elif 9.33 <= t < 10: return 0.6
    else: return 0.8

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

# ---------------- OI ----------------
def get_oi():
    try:
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        headers = {"User-Agent": "Mozilla/5.0"}
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=headers)
        time.sleep(random.uniform(1,2))
        data = s.get(url, headers=headers).json()['records']['data']

        ce, pe = {}, {}
        for i in data:
            sp = i.get('strikePrice')
            if i.get('CE'): ce[sp] = i['CE']['openInterest']
            if i.get('PE'): pe[sp] = i['PE']['openInterest']

        mc = max(ce, key=ce.get)
        mp = max(pe, key=pe.get)
        pcr = sum(pe.values()) / sum(ce.values())

        return mc, mp, round(pcr,2)
    except:
        return None, None, 1

# ---------------- MAIN LOOP ----------------
while True:
    now = datetime.now(IST)
    t = now.strftime("%H:%M")

    # RESET DAILY
    if current_day != now.date():
        current_day = now.date()
        trades_today = 0
        daily_loss = 0
        active_trade = None

    if now.weekday() >= 5:
        time.sleep(300)
        continue

    # Avoid first 5 min noise
    if "09:20" <= t <= "09:25":
        wait_next()
        continue

    if "09:20" <= t <= "15:25":

        if daily_loss >= max_daily_loss:
            send_telegram("🛑 DAILY LOSS LIMIT HIT. BOT STOPPED.")
            break

        if trades_today >= max_trades:
            wait_next()
            continue

        try:
            df = yf.download("^NSEI", interval="5m", period="1d", progress=False)
            df.columns = df.columns.get_level_values(0)
            df = df.dropna()

            if len(df) < 20:
                wait_next()
                continue

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

            if sideways:
                range_high = df.iloc[-10:]['High'].max()
                range_low = df.iloc[-10:]['Low'].min()
                was_sideways = True

            range_breakout = False
            if was_sideways and range_high and range_low:
                if close > range_high or close < range_low:
                    range_breakout = True
                    was_sideways = False

            global oi_resistance, oi_support, oi_pcr, last_oi_fetch
            if time.time() - last_oi_fetch > 900:
                oi_resistance, oi_support, oi_pcr = get_oi()
                last_oi_fetch = time.time()

            time_weight = get_time_weight(now)

            # CONFIDENCE
            score = 0

            if close > orb_high or close < orb_low:
                score += 25

            if (close > orb_high and ema9 > ema21) or (close < orb_low and ema9 < ema21):
                score += 20

            if (close > orb_high and rsi > 55) or (close < orb_low and rsi < 45):
                score += 15

            if range_breakout:
                score += 10

            score = int(score * time_weight)
            confidence = min(score, 100)

            min_conf = 55 if time_weight >= 0.9 else 60

            # ENTRY
            entry = close
            sl = None
            tgt = None
            option = "NO TRADE"
            direction = None

            if (confidence >= min_conf or range_breakout) and (not sideways or range_breakout):

                if close > orb_high and close > vwap:
                    if not oi_resistance or (oi_resistance - close > 30):
                        entry = close + 2
                        sl = entry - atr
                        tgt = entry + (atr * 2)
                        direction = "CE"
                        option = "CE"

                elif close < orb_low and close < vwap:
                    if not oi_support or (close - oi_support > 30):
                        entry = close - 2
                        sl = entry + atr
                        tgt = entry - (atr * 2)
                        direction = "PE"
                        option = "PE"

            # POSITION SIZE
            if sl:
                risk_amt = capital * risk_per_trade
                dist = abs(entry - sl)
                qty = int(risk_amt / dist) if dist > 0 else 0

                if atr > close * 0.005:
                    qty = int(qty * 0.7)
                elif atr < close * 0.003:
                    qty = int(qty * 1.2)
            else:
                qty = 0

            # ENTRY ALERT
            if option != "NO TRADE" and not active_trade:
                msg = f"""
🚨 TRADE ALERT

Type: {option}
Confidence: {confidence}
Qty: {qty}

Entry: {round(entry,2)}
SL: {round(sl,2)}
Target: {round(tgt,2)}

VWAP Bias: {'Bullish' if close>vwap else 'Bearish'}
PCR: {oi_pcr}
"""
                send_telegram(msg)

                active_trade = {
                    "dir": direction,
                    "entry": entry,
                    "sl": sl,
                    "tgt": tgt
                }

                trades_today += 1

            # EXIT
            if active_trade:
                at = active_trade

                if (at['dir']=="CE" and close<=at['sl']) or (at['dir']=="PE" and close>=at['sl']):
                    send_telegram("🔴 STOP LOSS HIT")
                    daily_loss += abs(at['entry'] - close)
                    active_trade = None

                elif (at['dir']=="CE" and close>=at['tgt']) or (at['dir']=="PE" and close<=at['tgt']):
                    send_telegram("🟢 TARGET HIT")
                    active_trade = None

            logging.info(f"{t} | {close} | Conf:{confidence} | Trades:{trades_today}")

        except Exception as e:
            logging.error(e)

    wait_next()

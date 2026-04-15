import yfinance as yf
import pandas as pd
import requests
import time
import random
import os
import logging
from datetime import datetime
import pytz

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ---------------- TELEGRAM CONFIG ----------------
BOT_TOKEN      = "8748334869:AAFmCuoybJ-R-oMBJDbbfxVpo7grnSnmNHM"
CHAT_ID        = "1209845315"

def send_telegram(message):
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        logging.warning(f"Telegram error: {e}")

# ---------------- TIMEZONE ----------------
IST = pytz.timezone("Asia/Kolkata")

# ---------------- GLOBAL STATE ----------------
last_signal    = ""
active_trade   = None   # tracks open trade for exit alerts

sent_start_msg = False
sent_end_msg   = False
sent_0910      = False
sent_0915      = False

oi_resistance  = None
oi_support     = None
oi_pcr         = 1.0
last_oi_fetch  = 0

# ---------------- FORMATTERS ----------------
def fmt(price):
    """Format price as ₹XX,XXX.XX"""
    return f"₹{price:,.2f}"

# ---------------- HELPERS ----------------
def get_smart_strike(price, confidence, option_type):
    base = round(price / 50) * 50
    if confidence >= 80:
        return base + 50 if option_type == "CE" else base - 50
    elif confidence >= 60:
        return base
    else:
        return base - 50 if option_type == "CE" else base + 50

def wait_for_next_candle():
    now        = datetime.now(IST)
    seconds    = now.minute * 60 + now.second
    next_run   = ((seconds // 300) + 1) * 300
    sleep_time = next_run - seconds
    logging.info(f"Sleeping {sleep_time}s until next candle...")
    time.sleep(sleep_time)

def get_time_weight(now):
    """Weight signals by reliability of time window."""
    t = now.hour + now.minute / 60
    if 10.0 <= t <= 11.5:
        return 1.0    # best window
    elif 13.5 <= t <= 14.5:
        return 0.9    # second best
    elif 9.33 <= t < 10.0:
        return 0.6    # opening noise
    elif t > 14.5:
        return 0.7    # low liquidity
    else:
        return 0.8

# ---------------- OPTION CHAIN ----------------
def get_option_chain():
    url     = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    headers = {
        "User-Agent"      : "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept"          : "application/json",
        "Accept-Language" : "en-US,en;q=0.9",
        "Referer"         : "https://www.nseindia.com/option-chain"
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=10)
    time.sleep(random.uniform(2, 4))

    response = session.get(url, headers=headers, timeout=10).json()
    data     = response['records']['data']

    call_oi = {}
    put_oi  = {}

    for item in data:
        strike = item.get('strikePrice')
        if item.get('CE'):
            call_oi[strike] = item['CE']['openInterest']
        if item.get('PE'):
            put_oi[strike]  = item['PE']['openInterest']

    max_call = max(call_oi, key=call_oi.get)
    max_put  = max(put_oi,  key=put_oi.get)

    total_call_oi = sum(call_oi.values())
    total_put_oi  = sum(put_oi.values())
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0

    return max_call, max_put, pcr

# ---------------- INDICATORS ----------------
def compute_indicators(df):
    # EMA
    df['ema9']  = df['Close'].ewm(span=9,  adjust=False).mean()
    df['ema21'] = df['Close'].ewm(span=21, adjust=False).mean()

    # RSI (14)
    delta      = df['Close'].diff()
    gain       = delta.clip(lower=0).rolling(14).mean()
    loss       = (-delta.clip(upper=0)).rolling(14).mean()
    rs         = gain / loss
    df['rsi']  = 100 - (100 / (1 + rs))

    # ATR (10)
    df['range'] = df['High'] - df['Low']
    df['atr']   = df['range'].rolling(10).mean()

    return df

def compute_confidence(close, orb_high, orb_low, atr,
                        ema9, ema21, rsi,
                        body_ratio, vol_ratio,
                        candle_streak, pcr, time_weight):
    score = 0

    # 1. ORB Breakout strength (max 25 pts)
    if close > orb_high:
        strength = min((close - orb_high) / atr, 1.5)
        score += strength * 25
    elif close < orb_low:
        strength = min((orb_low - close) / atr, 1.5)
        score += strength * 25

    # 2. EMA trend alignment (max 20 pts)
    ema_diff_pct = abs(ema9 - ema21) / close * 100
    if (close > orb_high and ema9 > ema21) or (close < orb_low and ema9 < ema21):
        score += min(ema_diff_pct * 10, 20)

    # 3. RSI momentum (max 15 pts)
    if close > orb_high and 55 < rsi < 75:
        score += (rsi - 55) * 0.75
    elif close < orb_low and 25 < rsi < 45:
        score += (45 - rsi) * 0.75

    # 4. Candle quality (max 15 pts)
    score += body_ratio * 15

    # 5. Volume surge (max 15 pts)
    if vol_ratio > 1:
        score += min((vol_ratio - 1) * 10, 15)

    # 6. Consecutive candles (max 10 pts)
    if candle_streak:
        score += 10

    # Apply time-of-day weight
    score = score * time_weight

    return min(int(round(score)), 100)

# ---------------- MAIN LOOP ----------------
while True:
    now          = datetime.now(IST)
    current_time = now.strftime("%H:%M")

    # WEEKEND BLOCK
    if now.weekday() >= 5:
        time.sleep(300)
        continue

    # ----------------------------------------------------------------
    # SCHEDULED MESSAGES
    # ----------------------------------------------------------------
    if "09:10" <= current_time < "09:11" and not sent_0910:
        send_telegram("🔔 <b>09:10</b> — Market opens in 5 minutes.")
        sent_0910 = True

    if "09:15" <= current_time < "09:16" and not sent_0915:
        send_telegram("🔔 <b>09:15</b> — Market is now open.")
        sent_0915 = True

    if "09:20" <= current_time < "09:21" and not sent_start_msg:
        send_telegram("🚀 <b>Bot started.</b> Monitoring NIFTY...")
        sent_start_msg = True

    if "15:30" <= current_time < "15:31" and not sent_end_msg:
        send_telegram("🛑 <b>Market closed.</b> Bot on standby.")
        sent_end_msg   = True
        active_trade   = None

    # DAILY RESET
    if current_time < "09:00":
        sent_start_msg = False
        sent_end_msg   = False
        sent_0910      = False
        sent_0915      = False
        last_signal    = ""
        active_trade   = None

    # ----------------------------------------------------------------
    # TRADING WINDOW
    # ----------------------------------------------------------------
    if "09:20" <= current_time <= "15:25":

        try:
            # ---------- FETCH DATA ----------
            df = yf.download("^NSEI", interval="5m", period="1d",
                             auto_adjust=True, progress=False)
            df.columns = df.columns.get_level_values(0)
            df = df.dropna()

            if len(df) < 20:
                logging.warning("Not enough candles yet.")
                wait_for_next_candle()
                continue

            df = compute_indicators(df)

            # ---------- ORB (filter by actual timestamp) ----------
            market_open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
            orb_end_time     = now.replace(hour=9, minute=30, second=0, microsecond=0)
            orb_df           = df[df.index.tz_convert(IST) < orb_end_time]

            if len(orb_df) >= 3:
                orb_high = float(orb_df['High'].max())
                orb_low  = float(orb_df['Low'].min())
            else:
                orb_high = float(df.iloc[:3]['High'].max())
                orb_low  = float(df.iloc[:3]['Low'].min())

            # ---------- CURRENT CANDLE ----------
            last        = df.iloc[-1]
            close_price = float(last['Close'])
            open_price  = float(last['Open'])
            high_price  = float(last['High'])
            low_price   = float(last['Low'])
            curr_vol    = float(last['Volume'])
            atr         = float(df['atr'].iloc[-1])
            ema9        = float(df['ema9'].iloc[-1])
            ema21       = float(df['ema21'].iloc[-1])
            rsi         = float(df['rsi'].iloc[-1])

            # ---------- CANDLE QUALITY ----------
            body         = abs(close_price - open_price)
            range_candle = high_price - low_price
            body_ratio   = (body / range_candle) if range_candle > 0 else 0

            # ---------- VOLUME ----------
            avg_vol    = float(df.iloc[-6:-1]['Volume'].mean())
            vol_ratio  = (curr_vol / avg_vol) if avg_vol > 0 else 1

            # ---------- TREND (EMA + RSI) ----------
            if ema9 > ema21 and rsi > 55:
                trend = "bullish"
            elif ema9 < ema21 and rsi < 45:
                trend = "bearish"
            else:
                trend = "sideways"

            # ---------- CONSECUTIVE CANDLES ----------
            recent3         = df.iloc[-4:-1]
            bullish_count   = int((recent3['Close'] > recent3['Open']).sum())
            bearish_count   = int((recent3['Close'] < recent3['Open']).sum())
            candle_streak   = (
                (close_price > orb_high and bullish_count >= 2) or
                (close_price < orb_low  and bearish_count >= 2)
            )

            # ---------- FETCH OI (every 15 min) ----------
            if time.time() - last_oi_fetch > 900:
                try:
                    oi_resistance, oi_support, oi_pcr = get_option_chain()
                    last_oi_fetch = time.time()
                    logging.info(f"OI updated → Resist: {oi_resistance} | Support: {oi_support} | PCR: {oi_pcr}")
                except Exception as e:
                    logging.warning(f"OI fetch failed: {e}")

            # ---------- TIME WEIGHT ----------
            time_weight = get_time_weight(now)

            # ---------- CONFIDENCE SCORE ----------
            confidence = compute_confidence(
                close=close_price, orb_high=orb_high, orb_low=orb_low,
                atr=atr, ema9=ema9, ema21=ema21, rsi=rsi,
                body_ratio=body_ratio, vol_ratio=vol_ratio,
                candle_streak=candle_streak, pcr=oi_pcr,
                time_weight=time_weight
            )

            # ---------- DECISION ----------
            entry      = round(close_price, 2)
            sl_buffer  = atr * 1.2
            tgt_buffer = atr * 2.0

            option     = "NO TRADE"
            stop_loss  = None
            target     = None
            direction  = None

            if confidence >= 60:

                # CALL (CE)
                if entry > orb_high:
                    too_close_resist = oi_resistance and (oi_resistance - entry < 50)
                    if not too_close_resist:
                        stop_loss  = round(entry - sl_buffer, 2)
                        raw_target = round(entry + tgt_buffer, 2)
                        target     = round(min(raw_target, oi_resistance), 2) if oi_resistance else raw_target
                        strike     = get_smart_strike(entry, confidence, "CE")
                        option     = f"{strike} CE"
                        direction  = "CE"

                # PUT (PE)
                elif entry < orb_low:
                    too_close_support = oi_support and (entry - oi_support < 50)
                    if not too_close_support:
                        stop_loss  = round(entry + sl_buffer, 2)
                        raw_target = round(entry - tgt_buffer, 2)
                        target     = round(max(raw_target, oi_support), 2) if oi_support else raw_target
                        strike     = get_smart_strike(entry, confidence, "PE")
                        option     = f"{strike} PE"
                        direction  = "PE"

            # ---------- CHECK ACTIVE TRADE FOR EXIT ----------
            if active_trade:
                at = active_trade
                hit_sl     = (at['direction'] == "CE" and close_price <= at['sl']) or \
                             (at['direction'] == "PE" and close_price >= at['sl'])
                hit_target = (at['direction'] == "CE" and close_price >= at['target']) or \
                             (at['direction'] == "PE" and close_price <= at['target'])

                if hit_sl:
                    send_telegram(
                        f"🔴 <b>STOP LOSS HIT</b>\n\n"
                        f"Option : {at['option']}\n"
                        f"Entry  : {fmt(at['entry'])}\n"
                        f"SL Hit : {fmt(close_price)}\n"
                        f"Loss   : {fmt(abs(close_price - at['entry']))} pts"
                    )
                    active_trade = None
                    last_signal  = ""

                elif hit_target:
                    send_telegram(
                        f"🟢 <b>TARGET HIT</b> 🎯\n\n"
                        f"Option : {at['option']}\n"
                        f"Entry  : {fmt(at['entry'])}\n"
                        f"Exit   : {fmt(close_price)}\n"
                        f"Profit : {fmt(abs(close_price - at['entry']))} pts"
                    )
                    active_trade = None
                    last_signal  = ""

            # ---------- NEW TRADE ALERT ----------
            if option != "NO TRADE" and option != last_signal and not active_trade:

                risk_pts   = round(abs(entry - stop_loss), 2)
                reward_pts = round(abs(target - entry),    2)
                rr_ratio   = round(reward_pts / risk_pts,  2) if risk_pts > 0 else 0

                # Only send if R:R >= 1.5
                if rr_ratio >= 1.5:
                    pcr_sentiment = "🟢 Bullish" if oi_pcr > 1.2 else ("🔴 Bearish" if oi_pcr < 0.8 else "🟡 Neutral")

                    msg = (
                        f"🚨 <b>TRADE ALERT</b> 🚨\n\n"
                        f"📌 Option     : <b>{option}</b>\n"
                        f"📊 Trend      : {trend.capitalize()}\n"
                        f"🎯 Confidence : {confidence}/100\n"
                        f"⏱ Time Weight : {int(time_weight*100)}%\n\n"
                        f"📋 <b>Signal Breakdown</b>\n"
                        f"  • ORB Breakout  : {'✅' if close_price > orb_high or close_price < orb_low else '❌'}\n"
                        f"  • EMA Aligned   : {'✅' if (direction=='CE' and ema9>ema21) or (direction=='PE' and ema9<ema21) else '❌'} (EMA9={'↑' if ema9>ema21 else '↓'})\n"
                        f"  • RSI           : {'✅' if (direction=='CE' and rsi>55) or (direction=='PE' and rsi<45) else '⚠️'} ({round(rsi,1)})\n"
                        f"  • Candle Qual.  : {'✅' if body_ratio > 0.6 else '⚠️'} (Body {round(body_ratio*100)}%)\n"
                        f"  • Volume Surge  : {'✅' if vol_ratio > 1.2 else '❌'} ({round(vol_ratio,1)}x avg)\n"
                        f"  • Candle Streak : {'✅' if candle_streak else '❌'}\n"
                        f"  • PCR Sentiment : {pcr_sentiment} ({oi_pcr})\n\n"
                        f"💰 <b>Entry Price</b> : {fmt(entry)}\n"
                        f"🛑 <b>Stop Loss</b>   : {fmt(stop_loss)}  (Risk: {risk_pts} pts)\n"
                        f"🎯 <b>Target</b>      : {fmt(target)}  (Reward: {reward_pts} pts)\n"
                        f"📐 <b>R:R Ratio</b>   : 1 : {rr_ratio}\n\n"
                        f"📊 ATR          : {round(atr, 2)}\n"
                        f"🔒 OI Resist.   : {oi_resistance}\n"
                        f"🛡 OI Support   : {oi_support}\n"
                        f"📉 ORB High     : {round(orb_high, 2)}\n"
                        f"📈 ORB Low      : {round(orb_low, 2)}"
                    )
                    send_telegram(msg)
                    last_signal  = option
                    active_trade = {
                        "option"   : option,
                        "direction": direction,
                        "entry"    : entry,
                        "sl"       : stop_loss,
                        "target"   : target
                    }

            logging.info(
                f"{now.strftime('%H:%M:%S')} | "
                f"Close: {round(entry,2)} | Trend: {trend} | "
                f"RSI: {round(rsi,1)} | Conf: {confidence} | {option}"
            )

        except Exception as e:
            logging.error(f"Main loop error: {e}")

    wait_for_next_candle()

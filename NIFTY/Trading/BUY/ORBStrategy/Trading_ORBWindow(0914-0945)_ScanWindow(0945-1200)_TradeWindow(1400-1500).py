import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
from SmartApi import SmartConnect


# ============================================================
# CONFIGURATION
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

API_KEY = os.getenv("SMARTAPI_API_KEY", "MpxkUXqu")
CLIENT_CODE = os.getenv("SMARTAPI_CLIENT_CODE", "AAAD431807")
PASSWORD = os.getenv("SMARTAPI_PASSWORD", "1606")
TOTP_SECRET = os.getenv("SMARTAPI_TOTP_SECRET", "H2FHHI2DVFW5HK64J3GQWMVZ3E")

# Set to True for actual live orders, False for paper trading mode.
LIVE_TRADING = False

EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926009"  # BANK NIFTY (or change to 99926000 for NIFTY 50)
INTERVAL = "ONE_MINUTE"

STATE_FILE = Path("orb_live_state.json")
TRADE_LOG = Path("orb_live_trades.csv")
LOG_FILE = Path("orb_live.log")

PRODUCT_TYPE = "INTRADAY"
POLL_SECONDS = 5
FILL_TIMEOUT_SECONDS = 30

# Strategy Timings
ORB_START_TIME = dt_time(9, 15)
ORB_END_TIME = dt_time(9, 45)
TRADE_SCAN_START_TIME = dt_time(9, 50)
TRADE_DEADLINE_TIME = dt_time(12, 0)
TRADE_ENTRY_TIME = dt_time(14, 0)
EXIT_TIME = dt_time(15, 0)
MIN_ORB_RANGE_PCT = 0.0

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

log = logging.getLogger("orb_strategy")


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ist():
    return datetime.now(IST)


def save_state(state):
    temporary = STATE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, default=str)
    temporary.replace(STATE_FILE)


def load_state():
    if not STATE_FILE.exists():
        return {}
    with STATE_FILE.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if not isinstance(state, dict):
        raise RuntimeError("Invalid state file. Inspect positions before restarting.")
    return state


def log_trade(**details):
    record = {
        "time": now_ist().isoformat(),
        "mode": "LIVE" if LIVE_TRADING else "PAPER",
        **details,
    }
    frame = pd.DataFrame([record])
    if TRADE_LOG.exists():
        frame.to_csv(TRADE_LOG, mode="a", header=False, index=False)
    else:
        frame.to_csv(TRADE_LOG, index=False)


def require_credentials():
    required = {
        "SMARTAPI_API_KEY": API_KEY,
        "SMARTAPI_CLIENT_CODE": CLIENT_CODE,
        "SMARTAPI_PASSWORD": PASSWORD,
        "SMARTAPI_TOTP_SECRET": TOTP_SECRET,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing credentials: " + ", ".join(missing))


def api_read(fn, *args, **kwargs):
    """Retry read-only API requests safely."""
    last_error = None
    for attempt in range(1, 4):
        try:
            response = fn(*args, **kwargs)
            if isinstance(response, dict) and response.get("status") is False:
                raise RuntimeError(str(response))
            return response
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 2)
    raise RuntimeError(f"API read failed: {fn.__name__}: {last_error}")


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login():
    require_credentials()
    api = SmartConnect(api_key=API_KEY)
    otp = pyotp.TOTP(TOTP_SECRET).now()

    response = api.generateSession(CLIENT_CODE, PASSWORD, otp)
    if not isinstance(response, dict) or not response.get("status") or not response.get("data"):
        raise RuntimeError(f"Login failed: {response}")

    log.info("SmartAPI login successful.")
    return api


# ============================================================
# DATA HANDLING & TIMESTAMP CONVERSION
# ============================================================

def convert_dataframe_to_ist(df):
    if df.empty or "timestamp" not in df.columns:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="shift_forward")
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)
    return df


def fetch_intraday_candles(api, trading_day):
    start = datetime.combine(trading_day, dt_time(9, 0), tzinfo=IST)
    end = min(now_ist(), datetime.combine(trading_day, dt_time(15, 30), tzinfo=IST))

    params = {
        "exchange": EXCHANGE,
        "symboltoken": SYMBOL_TOKEN,
        "interval": INTERVAL,
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": end.strftime("%Y-%m-%d %H:%M"),
    }

    response = api_read(api.getCandleData, params)
    rows = response.get("data") if isinstance(response, dict) else None

    if rows is None:
        raise RuntimeError(f"Historical candle request failed: {response}")
    if not rows:
        return pd.DataFrame(columns=CANDLE_COLUMNS)

    df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
    df = convert_dataframe_to_ist(df)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)


# ============================================================
# ORDER EXECUTION
# ============================================================

def place_order(api, side, quantity, reason, state):
    if not LIVE_TRADING:
        log.warning("PAPER MODE: %s quantity=%s | reason=%s", side, quantity, reason)
        result = {"order_id": "PAPER", "average_price": 0.0, "quantity": quantity}
        state["pending_order"] = {"side": side, "quantity": quantity, "reason": reason, "status": "SIMULATED"}
        save_state(state)
        log_trade(action=side, quantity=quantity, reason=reason, status="SIMULATED")
        return result

    state["pending_order"] = {
        "side": side,
        "quantity": quantity,
        "reason": reason,
        "status": "SUBMITTING",
        "time": now_ist().isoformat(),
    }
    save_state(state)

    params = {
        "variety": "NORMAL",
        "tradingsymbol": "BANKNIFTY",  # Update based on instrument choice
        "symboltoken": SYMBOL_TOKEN,
        "transactiontype": side,
        "exchange": EXCHANGE,
        "ordertype": "MARKET",
        "producttype": PRODUCT_TYPE,
        "duration": "DAY",
        "price": "0",
        "squareoff": "0",
        "stoploss": "0",
        "quantity": str(quantity),
        "ordertag": "ORB1MIN",
    }

    try:
        response = api.placeOrderFullResponse(params)
    except Exception as exc:
        state["pending_order"]["status"] = "SUBMISSION_UNCONFIRMED"
        save_state(state)
        raise RuntimeError("Order submission uncertain.") from exc

    if not isinstance(response, dict) or not response.get("status"):
        state["pending_order"]["status"] = "SUBMISSION_UNCONFIRMED"
        save_state(state)
        raise RuntimeError(f"Order not confirmed: {response}")

    data = response.get("data") or {}
    order_id = data.get("orderid") or data.get("orderId")
    state["pending_order"]["order_id"] = str(order_id)
    state["pending_order"]["status"] = "SUBMITTED"
    save_state(state)

    # Verify fill
    deadline = time.time() + FILL_TIMEOUT_SECONDS
    while time.time() < deadline:
        book = api_read(api.orderBook)
        for order in (book.get("data") or []):
            if str(order.get("orderid", order.get("orderId"))) == str(order_id):
                status = str(order.get("orderstatus", order.get("status"))).lower()
                filled = int(float(order.get("filledshares") or 0))
                if status in {"complete", "completed", "filled"} or filled >= quantity:
                    avg_price = float(order.get("averageprice") or 0)
                    state["pending_order"]["status"] = "FILLED"
                    save_state(state)
                    log_trade(action=side, quantity=quantity, order_id=str(order_id), average_price=avg_price, status="FILLED")
                    return {"order_id": str(order_id), "average_price": avg_price, "quantity": quantity}
        time.sleep(2)

    raise RuntimeError(f"Fill timeout for order {order_id}.")


# ============================================================
# LIVE STRATEGY LOOP
# ============================================================

def run_strategy(api, state):
    today = now_ist().date()
    today_key = today.isoformat()

    if state.get("trade_date") == today_key and state.get("day_status") in {"EXITED", "NO_TRADE"}:
        log.info("Strategy for today already finished.")
        return

    market_open = datetime.combine(today, dt_time(9, 15), tzinfo=IST)
    while now_ist() < market_open:
        time.sleep(POLL_SECONDS)

    log.info("Waiting for ORB formation (09:15 - 09:45)...")
    while now_ist().time() < dt_time(9, 46):
        time.sleep(POLL_SECONDS)

    # Fetch morning data to calculate ORB
    df = fetch_intraday_candles(api, today)
    df["time_obj"] = df["timestamp"].dt.time
    orb_candles = df[(df["time_obj"] >= ORB_START_TIME) & (df["time_obj"] <= ORB_END_TIME)]

    if len(orb_candles) < 15:
        log.warning("Insufficient ORB candles. Aborting today.")
        state.update({"trade_date": today_key, "day_status": "NO_TRADE"})
        save_state(state)
        return

    day_open = float(df.iloc[0]["open"])
    orb_high = orb_candles["high"].max()
    orb_low = orb_candles["low"].min()
    orb_midpoint = (orb_high + orb_low) / 2.0
    orb_range = orb_high - orb_low
    orb_range_pct = (orb_range / day_open) * 100

    log.info("ORB High: %.2f | Low: %.2f | Range Pct: %.2f%%", orb_high, orb_low, orb_range_pct)

    if orb_range_pct < MIN_ORB_RANGE_PCT:
        state.update({"trade_date": today_key, "day_status": "NO_TRADE"})
        save_state(state)
        return

    # Scan for Breakout between 9:50 and 12:00
    breakout_type = None
    log.info("Scanning for breakout between 09:50 and 12:00...")

    while now_ist().time() < TRADE_DEADLINE_TIME:
        df = fetch_intraday_candles(api, today)
        df["time_obj"] = df["timestamp"].dt.time
        scan_candles = df[(df["time_obj"] >= TRADE_SCAN_START_TIME) & (df["time_obj"] <= now_ist().time())]

        for _, row in scan_candles.iterrows():
            if row["high"] > orb_high and row["low"] < orb_low:
                continue
            elif row["high"] > orb_high:
                breakout_type = "BUY"
                break
            elif row["low"] < orb_low:
                breakout_type = "SELL"
                break

        if breakout_type:
            break
        time.sleep(30)

    if not breakout_type:
        log.info("No breakout detected by deadline. No trade today.")
        state.update({"trade_date": today_key, "day_status": "NO_TRADE"})
        save_state(state)
        return

    log.info("Breakout detected: %s. Waiting for entry time 14:00...", breakout_type)

    # Wait until 14:00 entry time
    entry_target_dt = datetime.combine(today, TRADE_ENTRY_TIME, tzinfo=IST)
    while now_ist() < entry_target_dt:
        time.sleep(POLL_SECONDS)

    # Get entry reference price and calculate stop loss
    df = fetch_intraday_candles(api, today)
    entry_candle = df[df["timestamp"].dt.time == TRADE_ENTRY_TIME]
    if entry_candle.empty:
        raise RuntimeError("14:00 Entry candle not found.")

    entry_price = float(entry_candle.iloc[0]["close"])
    if breakout_type == "BUY":
        stop_loss = orb_midpoint if entry_price > orb_high else orb_low
    else:
        stop_loss = orb_midpoint if entry_price < orb_low else orb_high

    quantity = 15  # Adjust lot size / quantity according to index specifications
    entry_order = place_order(api, "BUY" if breakout_type == "BUY" else "SELL", quantity, "ORB_ENTRY", state)

    state.update({
        "trade_date": today_key,
        "day_status": "POSITION_OPEN",
        "breakout_type": breakout_type,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
    })
    save_state(state)

    # Holding Period / Trailing Stop Loop (14:00 to 15:00)
    current_stop = stop_loss
    target_1_hit = False
    highest_mult = 0.3

    log.info("Position open. Managing trailing stops until 15:00...")
    while now_ist().time() < EXIT_TIME:
        df = fetch_intraday_candles(api, today)
        df["time_obj"] = df["timestamp"].dt.time
        holding = df[(df["time_obj"] > TRADE_ENTRY_TIME) & (df["time_obj"] <= now_ist().time())]

        exit_triggered = False
        for _, row in holding.iterrows():
            c_close, c_high, c_low = row["close"], row["high"], row["low"]

            if breakout_type == "BUY":
                fav_move = (c_high - entry_price) / orb_range if orb_range > 0 else 0
                if not target_1_hit and fav_move >= 0.3:
                    target_1_hit = True
                    current_stop = entry_price
                if target_1_hit:
                    tier = 0.3 + int((fav_move - 0.3) / 0.1) * 0.1
                    if tier > highest_mult:
                        highest_mult = tier
                        current_stop = entry_price + (highest_mult - 0.3) * orb_range

                if c_close <= current_stop:
                    exit_triggered = True
                    break
            else:
                fav_move = (entry_price - c_low) / orb_range if orb_range > 0 else 0
                if not target_1_hit and fav_move >= 0.3:
                    target_1_hit = True
                    current_stop = entry_price
                if target_1_hit:
                    tier = 0.3 + int((fav_move - 0.3) / 0.1) * 0.1
                    if tier > highest_mult:
                        highest_mult = tier
                        current_stop = entry_price - (highest_mult - 0.3) * orb_range

                if c_close >= current_stop:
                    exit_triggered = True
                    break

        if exit_triggered:
            break
        time.sleep(15)

    # Square off at EOD or Trailing Stop hit
    exit_side = "SELL" if breakout_type == "BUY" else "BUY"
    place_order(api, exit_side, quantity, "ORB_EXIT", state)

    state.update({"day_status": "EXITED"})
    save_state(state)
    log.info("Strategy day completed and exited.")


# ============================================================
# MAIN
# ============================================================

def main():
    log.info("Starting live ORB strategy runner...")
    api = None
    try:
        api = login()
        state = load_state()
        run_strategy(api, state)
    finally:
        if api is not None:
            try:
                api.terminateSession(CLIENT_CODE)
            except Exception:
                pass


if __name__ == "__main__":
    main()
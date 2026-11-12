import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import pyotp
from SmartApi import SmartConnect


# ============================================================
# CONFIGURATION
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

# "YES" = actual live orders, "NO" = paper mode.
LIVE_TRADING = "YES"

INSTRUMENT_MASTER = Path(
    r"C:\Users\challa\Desktop\Challa\Personal\NSE\NIFTY"
    r"\AngelOne_Instruments\Options_Instrumentation\nifty_current_expiry.csv"
)

STATE_FILE = Path("nifty_orb_live_state.json")
TRADE_LOG = Path("nifty_orb_live_trades.csv")
LOG_FILE = Path("nifty_orb_live.log")

SPOT_EXCHANGE = "NSE"
SPOT_SYMBOL = "Nifty 50"
SPOT_TOKEN = "99926000"

OPTION_EXCHANGE = "NFO"
CANDLE_INTERVAL = "ONE_MINUTE"

LOTS = 1
PRODUCT_TYPE = "INTRADAY"

POLL_SECONDS = 15
FILL_TIMEOUT_SECONDS = 30

# Strategy Times (IST)
ORB_START_TIME = dt_time(9, 15)
ORB_END_TIME = dt_time(9, 45)
TRADE_SCAN_START_TIME = dt_time(9, 50)
TRADE_DEADLINE_TIME = dt_time(12, 0)
TRADE_ENTRY_TIME = dt_time(14, 0)
EXIT_TIME = dt_time(15, 0)
MIN_ORB_RANGE_PCT = 0.0

CSV_TOKEN = "symboltoken"
CSV_SYMBOL = "tradingsymbol"
CSV_NAME = "name"
CSV_EXPIRY = "expiry"
CSV_STRIKE = "strike"
CSV_LOTSIZE = "lotsize"
CSV_TYPE = "instrumenttype"
CSV_EXCHANGE = "exchange"


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

log = logging.getLogger("nifty_orb_strategy")


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
        raise RuntimeError("Invalid state file.")
    return state


def log_trade(**details):
    record = {
        "time": now_ist().isoformat(),
        "mode": "LIVE" if LIVE_TRADING == "YES" else "PAPER",
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
# ANGEL ONE LOGIN
# ============================================================

def login():
    require_credentials()
    api = SmartConnect(api_key=API_KEY)
    otp = pyotp.TOTP(TOTP_SECRET).now()

    response = api.generateSession(CLIENT_CODE, PASSWORD, otp)
    if not isinstance(response, dict) or not response.get("status") or not response.get("data"):
        raise RuntimeError(f"Login failed: {response}")

    log.info("Angel One login successful.")
    return api


# ============================================================
# CSV INSTRUMENT MASTER & ITM OPTION SELECTOR
# ============================================================

def find_column(df, override, candidates, description):
    if override:
        if override not in df.columns:
            raise ValueError(f"Configured {description} column '{override}' not found.")
        return override
    normalize = lambda value: "".join(ch for ch in str(value).lower() if ch.isalnum())
    columns = {normalize(col): col for col in df.columns}
    for candidate in candidates:
        key = normalize(candidate)
        if key in columns:
            return columns[key]
    raise ValueError(f"Cannot detect the {description} column.")


def load_instruments():
    if not INSTRUMENT_MASTER.exists():
        raise FileNotFoundError(f"Instrument CSV not found: {INSTRUMENT_MASTER}")

    df = pd.read_csv(INSTRUMENT_MASTER, dtype=str, sep=None, engine="python")
    df.columns = [str(col).strip() for col in df.columns]

    columns = {
        "token": find_column(df, CSV_TOKEN, ["token", "symboltoken", "symbol_token", "instrument_token"], "token"),
        "symbol": find_column(df, CSV_SYMBOL, ["symbol", "tradingsymbol", "trading_symbol"], "symbol"),
        "name": find_column(df, CSV_NAME, ["name", "underlying", "underlyingsymbol"], "underlying name"),
        "expiry": find_column(df, CSV_EXPIRY, ["expiry", "expirydate", "expiry_date"], "expiry"),
        "strike": find_column(df, CSV_STRIKE, ["strike", "strikeprice", "strike_price"], "strike"),
        "lotsize": find_column(df, CSV_LOTSIZE, ["lotsize", "lot_size", "marketlot"], "lot size"),
        "type": find_column(df, CSV_TYPE, ["instrumenttype", "instrument_type"], "instrument type"),
        "exchange": find_column(df, CSV_EXCHANGE, ["exch_seg", "exchange", "exchange_segment"], "exchange"),
    }

    data = pd.DataFrame({
        "token": df[columns["token"]].fillna("").astype(str).str.strip(),
        "symbol": df[columns["symbol"]].fillna("").astype(str).str.strip(),
        "name": df[columns["name"]].fillna("").astype(str).str.upper().str.strip(),
        "expiry_raw": df[columns["expiry"]].fillna("").astype(str).str.strip(),
        "strike_raw": df[columns["strike"]].fillna("").astype(str).str.strip(),
        "lotsize_raw": df[columns["lotsize"]].fillna("").astype(str).str.strip(),
        "type": df[columns["type"]].fillna("").astype(str).str.upper().str.strip(),
        "exchange": df[columns["exchange"]].fillna("").astype(str).str.upper().str.strip(),
    })

    data["expiry"] = pd.to_datetime(data["expiry_raw"], errors="coerce", dayfirst=True).dt.date
    data["strike"] = pd.to_numeric(data["strike_raw"], errors="coerce")

    if not data["strike"].dropna().empty and data["strike"].dropna().median() > 100000:
        data["strike"] = data["strike"] / 100.0

    data["lot_size"] = pd.to_numeric(data["lotsize_raw"], errors="coerce")

    options = data[
        (data["name"].eq("NIFTY") | data["symbol"].str.upper().str.startswith("NIFTY"))
        & (data["type"].eq("OPTIDX") | data["symbol"].str.upper().str.endswith(("CE", "PE")))
        & data["exchange"].eq("NFO")
        & data["expiry"].notna()
        & data["strike"].notna()
        & (data["strike"] > 0)
        & (data["lot_size"] > 0)
        & data["token"].ne("")
    ].copy()

    log.info("Eligible NIFTY option contracts loaded: %d", len(options))
    return options


def select_itm_contract(instruments, spot, breakout_type, today):
    option_type = "CE" if breakout_type == "BUY" else "PE"

    eligible = instruments[
        (instruments["expiry"] >= today)
        & instruments["symbol"].str.upper().str.endswith(option_type)
    ].copy()

    if eligible.empty:
        raise RuntimeError(f"No unexpired NIFTY {option_type} contracts found.")

    expiry = min(eligible["expiry"])
    eligible = eligible[eligible["expiry"] == expiry]

    strikes = sorted(eligible["strike"].dropna().unique().tolist())
    atm_strike = min(strikes, key=lambda val: abs(float(val) - float(spot)))
    atm_strike = float(atm_strike)

    target_strike = (atm_strike - 100) if option_type == "CE" else (atm_strike + 100)

    selected = eligible[eligible["strike"].astype(float) == target_strike]
    if len(selected) != 1:
        raise RuntimeError(f"Expected 1 contract for expiry {expiry}, ATM={atm_strike}, Target ITM={target_strike} {option_type}.")

    row = selected.iloc[0]
    return {
        "symbol": row["symbol"],
        "token": row["token"],
        "exchange": OPTION_EXCHANGE,
        "expiry": expiry.isoformat(),
        "strike": target_strike,
        "option_type": option_type,
        "lot_size": int(row["lot_size"]),
        "quantity": int(row["lot_size"]) * LOTS,
    }


# ============================================================
# TIMEZONE-SAFE CANDLE FETCHING
# ============================================================

def fetch_candles(api, exchange, token, trading_day):
    start = datetime.combine(trading_day, dt_time(9, 0), tzinfo=IST)
    end = min(now_ist(), datetime.combine(trading_day, dt_time(15, 30), tzinfo=IST))

    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": CANDLE_INTERVAL,
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": end.strftime("%Y-%m-%d %H:%M"),
    }

    response = api_read(api.getCandleData, params)
    rows = response.get("data") if isinstance(response, dict) else None
    if rows is None:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="shift_forward")
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.sort_values("timestamp").reset_index(drop=True)


# ============================================================
# ORDER MANAGEMENT
# ============================================================

def place_order(api, contract, side, state, reason):
    if LIVE_TRADING != "YES":
        log.warning("PAPER MODE: %s %s qty=%s | reason=%s", side, contract["symbol"], contract["quantity"], reason)
        log_trade(action=side, symbol=contract["symbol"], quantity=contract["quantity"], reason=reason, status="SIMULATED")
        return {"order_id": "PAPER", "average_price": 100.0, "quantity": contract["quantity"]}

    state["pending_order"] = {
        "side": side,
        "symbol": contract["symbol"],
        "token": contract["token"],
        "quantity": contract["quantity"],
        "reason": reason,
        "status": "SUBMITTING",
        "time": now_ist().isoformat(),
    }
    save_state(state)

    params = {
        "variety": "NORMAL",
        "tradingsymbol": contract["symbol"],
        "symboltoken": contract["token"],
        "transactiontype": side,
        "exchange": OPTION_EXCHANGE,
        "ordertype": "MARKET",
        "producttype": PRODUCT_TYPE,
        "duration": "DAY",
        "price": "0",
        "squareoff": "0",
        "stoploss": "0",
        "quantity": str(contract["quantity"]),
        "ordertag": "NIFTYORB",
    }

    response = api.placeOrderFullResponse(params)
    if not isinstance(response, dict) or not response.get("status"):
        raise RuntimeError(f"Order submission failed: {response}")

    data = response.get("data") or {}
    order_id = str(data.get("orderid") or data.get("orderId"))
    state["pending_order"]["order_id"] = order_id
    state["pending_order"]["status"] = "SUBMITTED"
    save_state(state)

    deadline = time.time() + FILL_TIMEOUT_SECONDS
    while time.time() < deadline:
        book_response = api_read(api.orderBook)
        for order in (book_response.get("data") or []):
            if str(order.get("orderid", order.get("orderId", ""))) == order_id:
                status = str(order.get("orderstatus", order.get("status", ""))).lower()
                filled = int(float(order.get("filledshares") or 0))
                if status in {"rejected", "cancelled"}:
                    raise RuntimeError(f"Order {order_id} {status}")
                if filled >= contract["quantity"] or status in {"complete", "filled"}:
                    avg_price = float(order.get("averageprice") or 0)
                    state["pending_order"]["status"] = "FILLED"
                    save_state(state)
                    log_trade(action=side, symbol=contract["symbol"], quantity=contract["quantity"], order_id=order_id, average_price=avg_price, reason=reason, status="FILLED")
                    return {"order_id": order_id, "average_price": avg_price, "quantity": contract["quantity"]}
        time.sleep(2)

    raise RuntimeError(f"Fill timeout for order {order_id}")


# ============================================================
# MAIN STRATEGY EXECUTION
# ============================================================

def run_strategy(api, instruments, state):
    today = now_ist().date()
    today_key = today.isoformat()

    if state.get("trade_date") == today_key and state.get("day_status") in {"EXITED", "NO_TRADE"}:
        log.info("Strategy already completed for today.")
        return

    if state.get("position_status") == "OPEN":
        log.warning("OPEN position detected in state file. Resuming management loop...")
        contract = state.get("contract")
        breakout_type = state.get("breakout_type")
        entry_order = state.get("entry_order", {})
        entry_price = float(entry_order.get("average_price", 100.0))
        
        manage_open_position(api, contract, breakout_type, entry_price, today, state)
        return

    market_open = datetime.combine(today, dt_time(9, 15), tzinfo=IST)
    while now_ist() < market_open:
        time.sleep(POLL_SECONDS)

    # 1. ORB Window (9:15 to 9:45): Spot Index ORB
    orb_deadline = datetime.combine(today, dt_time(9, 46), tzinfo=IST)
    while now_ist() < orb_deadline:
        time.sleep(POLL_SECONDS)

    spot_df = fetch_candles(api, SPOT_EXCHANGE, SPOT_TOKEN, today)
    spot_df["time_obj"] = spot_df["timestamp"].dt.time

    orb_candles = spot_df[(spot_df["time_obj"] >= ORB_START_TIME) & (spot_df["time_obj"] <= ORB_END_TIME)]
    orb_high = float(orb_candles["high"].max())
    orb_low = float(orb_candles["low"].min())
    day_open = float(spot_df.iloc[0]["open"])
    orb_range = orb_high - orb_low

    if (orb_range / day_open) * 100 < MIN_ORB_RANGE_PCT:
        state.update({"trade_date": today_key, "day_status": "NO_TRADE"})
        save_state(state)
        log.info("ORB Range filtered out.")
        return

    # 2. ScanWindow (9:50 to 12:00): Spot Index Breakout
    breakout_type = None
    scan_end = datetime.combine(today, TRADE_DEADLINE_TIME, tzinfo=IST)

    while now_ist() < scan_end and not breakout_type:
        current_spot_df = fetch_candles(api, SPOT_EXCHANGE, SPOT_TOKEN, today)
        if not current_spot_df.empty:
            current_spot_df["time_obj"] = current_spot_df["timestamp"].dt.time
            scan_candles = current_spot_df[
                (current_spot_df["time_obj"] >= TRADE_SCAN_START_TIME) &
                (current_spot_df["time_obj"] <= TRADE_DEADLINE_TIME)
            ]

            for _, row in scan_candles.iterrows():
                c_high = row["high"]
                c_low = row["low"]
                if c_high > orb_high and c_low < orb_low:
                    continue
                elif c_high > orb_high:
                    breakout_type = "BUY"
                    break
                elif c_low < orb_low:
                    breakout_type = "SELL"
                    break

        if breakout_type:
            break
        time.sleep(POLL_SECONDS)

    if not breakout_type:
        state.update({"trade_date": today_key, "day_status": "NO_TRADE"})
        save_state(state)
        log.info("No breakout detected. No trade.")
        return

    log.info("Breakout detected on Spot! Type: %s", breakout_type)

    # 3. TradeWindow (14:00 to 15:00): Entry & ITM ORB Management
    entry_target_time = datetime.combine(today, TRADE_ENTRY_TIME, tzinfo=IST)
    while now_ist() < entry_target_time:
        time.sleep(POLL_SECONDS)

    spot_at_entry = float(fetch_candles(api, SPOT_EXCHANGE, SPOT_TOKEN, today).iloc[-1]["close"])
    contract = select_itm_contract(instruments, spot_at_entry, breakout_type, today)

    state.update({
        "trade_date": today_key,
        "day_status": "POSITION_OPEN",
        "breakout_type": breakout_type,
        "contract": contract,
    })
    save_state(state)

    entry_order = place_order(api, contract, "BUY", state, f"ORB_{breakout_type}_ENTRY")
    entry_price = entry_order["average_price"]
    state["entry_order"] = entry_order
    state["position_status"] = "OPEN"
    save_state(state)

    manage_open_position(api, contract, breakout_type, entry_price, today, state)


def manage_open_position(api, contract, breakout_type, entry_price, today, state):
    exit_time_dt = datetime.combine(today, EXIT_TIME, tzinfo=IST)
    
    # FETCH ITM OPTION ORB LEVELS (9:15 to 9:45) for stop loss & trailing
    itm_df = fetch_candles(api, OPTION_EXCHANGE, contract["token"], today)
    itm_df["time_obj"] = itm_df["timestamp"].dt.time
    itm_orb_candles = itm_df[(itm_df["time_obj"] >= ORB_START_TIME) & (itm_df["time_obj"] <= ORB_END_TIME)]
    
    if not itm_orb_candles.empty:
        itm_orb_high = float(itm_orb_candles["high"].max())
        itm_orb_low = float(itm_orb_candles["low"].min())
        itm_orb_midpoint = (itm_orb_high + itm_orb_low) / 2.0
        itm_orb_range = itm_orb_high - itm_orb_low
    else:
        itm_orb_range = entry_price * 0.1
        itm_orb_midpoint = entry_price - (0.5 * itm_orb_range)

    # Initial Stop Loss based on ITM ORB Midpoint / Range structure
    current_stop = itm_orb_midpoint if breakout_type == "BUY" else itm_orb_midpoint
    target_1_hit = False
    highest_mult_reached = 0.3
    last_check_minute = -1
    exit_reason = "EOD Square-off"
    last_processed_timestamp = None

    while now_ist() <= exit_time_dt:
        itm_df = fetch_candles(api, OPTION_EXCHANGE, contract["token"], today)
        if itm_df.empty:
            time.sleep(POLL_SECONDS)
            continue

        latest_candle = itm_df.iloc[-1]
        candle_ts = latest_candle["timestamp"]

        if candle_ts != last_processed_timestamp:
            last_processed_timestamp = candle_ts
            c_close = latest_candle["close"]
            c_high = latest_candle["high"]
            c_low = latest_candle["low"]
            h_time = candle_ts.time()

            if breakout_type == "BUY":
                fav_move_mult = (c_high - entry_price) / itm_orb_range if itm_orb_range > 0 else 0

                if not target_1_hit and fav_move_mult >= 0.3:
                    target_1_hit = True
                    current_stop = entry_price

                if target_1_hit:
                    current_mult_tier = 0.3 + int((fav_move_mult - 0.3) / 0.1) * 0.1
                    if current_mult_tier > highest_mult_reached:
                        highest_mult_reached = current_mult_tier
                        lock_in_offset = (highest_mult_reached - 0.3) * itm_orb_range
                        current_stop = entry_price + lock_in_offset

                # Unconditional 5-minute ratchet
                if current_stop < entry_price:
                    if h_time.minute % 5 == 0 and h_time.minute != last_check_minute:
                        last_check_minute = h_time.minute
                        current_stop += (0.05 * itm_orb_range)
                        if current_stop > entry_price:
                            current_stop = entry_price

                if c_close <= current_stop:
                    exit_reason = f"ITM Trailing Stop Hit (Stop: {current_stop:.2f})"
                    break

            else:  # SELL (PE)
                fav_move_mult = (entry_price - c_low) / itm_orb_range if itm_orb_range > 0 else 0

                if not target_1_hit and fav_move_mult >= 0.3:
                    target_1_hit = True
                    current_stop = entry_price

                if target_1_hit:
                    current_mult_tier = 0.3 + int((fav_move_mult - 0.3) / 0.1) * 0.1
                    if current_mult_tier > highest_mult_reached:
                        highest_mult_reached = current_mult_tier
                        lock_in_offset = (highest_mult_reached - 0.3) * itm_orb_range
                        current_stop = entry_price - lock_in_offset

                if current_stop > entry_price:
                    if h_time.minute % 5 == 0 and h_time.minute != last_check_minute:
                        last_check_minute = h_time.minute
                        current_stop -= (0.05 * itm_orb_range)
                        if current_stop < entry_price:
                            current_stop = entry_price

                if c_close >= current_stop:
                    exit_reason = f"ITM Trailing Stop Hit (Stop: {current_stop:.2f})"
                    break

        time.sleep(POLL_SECONDS)

    exit_order = place_order(api, contract, "SELL", state, exit_reason)
    state.update({
        "day_status": "EXITED",
        "exit_reason": exit_reason,
        "position_status": "CLOSED",
        "exit_order": exit_order,
    })
    save_state(state)
    log.info("Strategy execution completed. Exit reason: %s", exit_reason)


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

def main():
    log.info("Starting NIFTY ORB Strategy in %s mode.", "LIVE" if LIVE_TRADING == "YES" else "PAPER")
    instruments = load_instruments()
    api = None
    try:
        api = login()
        state = load_state()
        run_strategy(api, instruments, state)
    finally:
        if api is not None:
            try:
                api.terminateSession(CLIENT_CODE)
            except Exception:
                pass


if __name__ == "__main__":
    main()
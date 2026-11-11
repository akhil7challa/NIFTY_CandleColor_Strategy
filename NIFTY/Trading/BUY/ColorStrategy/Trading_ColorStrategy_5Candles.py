
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

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

# NO = paper mode. YES = actual live orders.
LIVE_TRADING = "YES"

INSTRUMENT_MASTER = Path(
    r"C:\Users\challa\Desktop\Challa\Personal\NIFTY"
    r"\AngelOne_Instruments\nifty_current_expiry.csv"
)

STATE_FILE = Path("nifty_live_state.json")
TRADE_LOG = Path("nifty_live_trades.csv")
LOG_FILE = Path("nifty_live.log")

SPOT_EXCHANGE = "NSE"
SPOT_SYMBOL = "Nifty 50"
SPOT_TOKEN = "99926000"

OPTION_EXCHANGE = "NFO"
CANDLE_INTERVAL = "FIVE_MINUTE"

LOTS = 1
PRODUCT_TYPE = "INTRADAY"

POLL_SECONDS = 5
CANDLE_GRACE_SECONDS = 10
FILL_TIMEOUT_SECONDS = 30

# Do not start a new opening trade after this time.
ENTRY_CUTOFF = dt_time(9, 34)

# Original opening candle times.
FIRST_THREE = ["09:15", "09:20", "09:25"]
FOURTH_CANDLE = "09:30"
FIFTH_CANDLE = "09:35"

RULES = {
    ("Red", "Red", "Green"): "Green",
    ("Green", "Green", "Red"): "Red",
    ("Red", "Green", "Red"): "Red",
    ("Green", "Red", "Green"): "Green",
    ("Red", "Red", "Red"): "Red",
    ("Green", "Green", "Green"): "Green",
}

# Optional CSV header overrides. Normally leave these empty.
# Set them if your CSV uses different column names.
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

log = logging.getLogger("nifty_strategy")


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ist():
    return datetime.now(IST)


def candle_color(row):
    opening = float(row["open"])
    closing = float(row["close"])

    if closing > opening:
        return "Green"
    if closing < opening:
        return "Red"
    return "Doji"


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
        raise RuntimeError(
            "Invalid state file. Inspect it and broker positions before restarting."
        )

    return state


def log_trade(**details):
    record = {
        "time": now_ist().isoformat(),
        "mode": "LIVE" if LIVE_TRADING else "PAPER",
        **details,
    }

    frame = pd.DataFrame([record])

    if TRADE_LOG.exists():
        frame.to_csv(
            TRADE_LOG,
            mode="a",
            header=False,
            index=False,
        )
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
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )


def api_read(fn, *args, **kwargs):
    """Retry read-only API requests; never use this to retry order submission."""

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

    raise RuntimeError(
        f"API read failed: {fn.__name__}: {last_error}"
    )


# ============================================================
# ANGEL ONE LOGIN
# ============================================================

def login():
    require_credentials()

    api = SmartConnect(api_key=API_KEY)
    otp = pyotp.TOTP(TOTP_SECRET).now()

    response = api.generateSession(
        CLIENT_CODE,
        PASSWORD,
        otp,
    )

    if (
        not isinstance(response, dict)
        or not response.get("status")
        or not response.get("data")
    ):
        raise RuntimeError(f"Login failed: {response}")

    log.info("Angel One login successful.")
    return api


# ============================================================
# CSV INSTRUMENT MASTER
# ============================================================

def find_column(df, override, candidates, description):
    if override:
        if override not in df.columns:
            raise ValueError(
                f"Configured {description} column '{override}' not found. "
                f"Available columns: {list(df.columns)}"
            )
        return override

    normalize = lambda value: "".join(
        ch for ch in str(value).lower() if ch.isalnum()
    )

    columns = {normalize(col): col for col in df.columns}

    for candidate in candidates:
        key = normalize(candidate)

        if key in columns:
            return columns[key]

    raise ValueError(
        f"Cannot detect the {description} column. "
        f"CSV columns: {list(df.columns)}. "
        f"Set the corresponding INSTRUMENT_*_COLUMN environment variable."
    )


def load_instruments():
    if not INSTRUMENT_MASTER.exists():
        raise FileNotFoundError(
            f"Instrument CSV not found: {INSTRUMENT_MASTER}"
        )

    df = pd.read_csv(
        INSTRUMENT_MASTER,
        dtype=str,
        sep=None,
        engine="python",
    )

    df.columns = [str(col).strip() for col in df.columns]

    if df.empty:
        raise RuntimeError("Instrument CSV contains no rows.")

    columns = {
        "token": find_column(
            df, CSV_TOKEN,
            ["token", "symboltoken", "symbol_token", "instrument_token"],
            "token",
        ),
        "symbol": find_column(
            df, CSV_SYMBOL,
            ["symbol", "tradingsymbol", "trading_symbol"],
            "symbol",
        ),
        "name": find_column(
            df, CSV_NAME,
            ["name", "underlying", "underlyingsymbol"],
            "underlying name",
        ),
        "expiry": find_column(
            df, CSV_EXPIRY,
            ["expiry", "expirydate", "expiry_date"],
            "expiry",
        ),
        "strike": find_column(
            df, CSV_STRIKE,
            ["strike", "strikeprice", "strike_price"],
            "strike",
        ),
        "lotsize": find_column(
            df, CSV_LOTSIZE,
            ["lotsize", "lot_size", "marketlot"],
            "lot size",
        ),
        "type": find_column(
            df, CSV_TYPE,
            ["instrumenttype", "instrument_type"],
            "instrument type",
        ),
        "exchange": find_column(
            df, CSV_EXCHANGE,
            ["exch_seg", "exchange", "exchange_segment"],
            "exchange",
        ),
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

    data["expiry"] = pd.to_datetime(
        data["expiry_raw"],
        errors="coerce",
        dayfirst=True,
    ).dt.date

    data["strike"] = pd.to_numeric(
        data["strike_raw"],
        errors="coerce",
    )

    # Standard Angel One master files often store strike x 100.
    valid_strikes = data["strike"].dropna()

    if not valid_strikes.empty and valid_strikes.median() > 100000:
        data["strike"] = data["strike"] / 100.0

    data["lot_size"] = pd.to_numeric(
        data["lotsize_raw"],
        errors="coerce",
    )

    nifty = (
        data["name"].eq("NIFTY")
        | data["symbol"].str.upper().str.startswith("NIFTY")
    )

    option = (
        data["type"].eq("OPTIDX")
        | data["symbol"].str.upper().str.endswith(("CE", "PE"))
    )

    nfo = data["exchange"].eq("NFO")

    options = data[
        nifty
        & option
        & nfo
        & data["expiry"].notna()
        & data["strike"].notna()
        & (data["strike"] > 0)
        & (data["lot_size"] > 0)
        & data["token"].ne("")
    ].copy()

    if options.empty:
        raise RuntimeError(
            "No NIFTY option rows passed validation. "
            "Check the CSV headers and contents before enabling live trading."
        )

    log.info("CSV columns mapped: %s", columns)
    log.info("Eligible NIFTY option contracts: %d", len(options))

    return options



def select_atm_contract(instruments, spot, option_type, today):
    eligible = instruments[
        (instruments["expiry"] >= today)
        & instruments["symbol"].str.upper().str.endswith(option_type)
    ].copy()

    if eligible.empty:
        raise RuntimeError(
            f"No unexpired NIFTY {option_type} contracts found."
        )

    # Select the nearest expiry
    expiry = min(eligible["expiry"])
    eligible = eligible[eligible["expiry"] == expiry]

    strikes = sorted(
        eligible["strike"].dropna().unique().tolist()
    )

    if not strikes:
        raise RuntimeError("No usable strikes found.")

    # Find current ATM strike
    atm_strike = min(
        strikes,
        key=lambda value: abs(float(value) - float(spot))
    )
    atm_strike = float(atm_strike)

    # Select ITM strike: CE = ATM - 100, PE = ATM + 100
    if option_type == "CE":
        target_strike = atm_strike - 100
    elif option_type == "PE":
        target_strike = atm_strike + 100
    else:
        raise ValueError(
            f"Invalid option_type: {option_type}. Use CE or PE."
        )

    # IMPORTANT: Filter using target_strike, NOT atm_strike
    selected = eligible[
        (eligible["strike"].astype(float) == target_strike)
    ]

    if len(selected) != 1:
        raise RuntimeError(
            f"Expected exactly one contract for {expiry} "
            f"ATM={atm_strike}, target ITM strike={target_strike} "
            f"{option_type}; found {len(selected)}. "
            "Check the instrument master for missing or duplicate rows."
        )

    row = selected.iloc[0]
    lot_size = int(row["lot_size"])

    result = {
        "symbol": row["symbol"],
        "token": row["token"],
        "expiry": expiry.isoformat(),
        "strike": target_strike,
        "option_type": option_type,
        "lot_size": lot_size,
        "quantity": lot_size * LOTS,
    }

    print(
    f"ATM={atm_strike} | Selected ITM strike={target_strike} "
    f"| Type={option_type} | Contract={result['symbol']}"
    )

    return result


# ============================================================
# NIFTY SPOT DATA
# ============================================================

def get_spot_ltp(api):
    response = api_read(
        api.ltpData,
        SPOT_EXCHANGE,
        SPOT_SYMBOL,
        SPOT_TOKEN,
    )

    data = response.get("data") if isinstance(response, dict) else None

    if not data or data.get("ltp") is None:
        raise RuntimeError(f"Unable to obtain NIFTY spot LTP: {response}")

    return float(data["ltp"])


def to_ist_timestamp(value):
    stamp = pd.Timestamp(value)

    if stamp.tzinfo is None:
        return stamp.tz_localize(IST)

    return stamp.tz_convert(IST)


def fetch_completed_candles(api, trading_day):
    start = datetime.combine(
        trading_day,
        dt_time(9, 0),
        tzinfo=IST,
    )

    end = min(
        now_ist(),
        datetime.combine(trading_day, dt_time(15, 30), tzinfo=IST),
    )

    params = {
        "exchange": SPOT_EXCHANGE,
        "symboltoken": SPOT_TOKEN,
        "interval": CANDLE_INTERVAL,
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": end.strftime("%Y-%m-%d %H:%M"),
    }

    response = api_read(api.getCandleData, params)
    rows = response.get("data") if isinstance(response, dict) else None

    if rows is None:
        raise RuntimeError(f"Historical candle request failed: {response}")

    if not rows:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "time", "color"]
        )

    df = pd.DataFrame(
        rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )

    df["timestamp"] = df["timestamp"].apply(to_ist_timestamp)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["timestamp", "open", "high", "low", "close"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last")

    # Treat API candle timestamps as candle START times.
    cutoff = pd.Timestamp(now_ist() - timedelta(seconds=CANDLE_GRACE_SECONDS))

    df = df[
        df["timestamp"] + pd.Timedelta(minutes=5) <= cutoff
    ].copy()

    df["time"] = df["timestamp"].dt.strftime("%H:%M")
    df["color"] = df.apply(candle_color, axis=1)

    return df.reset_index(drop=True)


def get_candle(df, candle_time):
    matches = df[df["time"] == candle_time]

    if matches.empty:
        return None

    return matches.iloc[-1]


def wait_for_candle(api, trading_day, candle_time):
    start_time = datetime.combine(
        trading_day,
        datetime.strptime(candle_time, "%H:%M").time(),
        tzinfo=IST,
    )

    available_time = start_time + timedelta(
        minutes=5,
        seconds=CANDLE_GRACE_SECONDS,
    )

    while now_ist() < available_time:
        remaining = (available_time - now_ist()).total_seconds()
        time.sleep(min(POLL_SECONDS, max(0.2, remaining)))

    deadline = time.time() + 90

    while time.time() < deadline:
        df = fetch_completed_candles(api, trading_day)
        candle = get_candle(df, candle_time)

        if candle is not None:
            return candle

        log.info("Waiting for completed %s candle from SmartAPI.", candle_time)
        time.sleep(POLL_SECONDS)

    raise RuntimeError(
        f"Could not obtain completed candle {candle_time}. "
        "If a position is open, check broker positions immediately."
    )


# ============================================================
# ORDER MANAGEMENT
# ============================================================

def place_order(api, contract, side, state, reason):
    if not LIVE_TRADING:
        log.warning(
            "PAPER MODE: %s %s quantity=%s; reason=%s",
            side,
            contract["symbol"],
            contract["quantity"],
            reason,
        )

        result = {
            "order_id": "PAPER",
            "average_price": 0.0,
            "quantity": contract["quantity"],
        }

        log_trade(
            action=side,
            symbol=contract["symbol"],
            quantity=contract["quantity"],
            reason=reason,
            status="SIMULATED",
        )

        return result

    # Save the intention before calling the broker. If the response is lost,
    # the program halts for manual reconciliation rather than resubmitting.
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
        "ordertag": "NIFTY5M",
    }

    # Do NOT automatically retry this call if it times out.
    try:
        response = api.placeOrderFullResponse(params)
    except Exception as exc:
        state["pending_order"]["status"] = "SUBMISSION_UNCONFIRMED"
        state["pending_order"]["error"] = str(exc)
        save_state(state)

        raise RuntimeError(
            "Order submission status is uncertain. Inspect the Angel One "
            "order book and positions before taking any further action."
        ) from exc

    if not isinstance(response, dict) or not response.get("status"):
        state["pending_order"]["status"] = "SUBMISSION_UNCONFIRMED"
        state["pending_order"]["response"] = str(response)
        save_state(state)

        raise RuntimeError(
            f"Order submission not confirmed: {response}. "
            "Check broker order book before retrying."
        )

    data = response.get("data") or {}
    order_id = (
        data.get("orderid")
        or data.get("orderId")
        or data.get("uniqueorderid")
    )

    if not order_id:
        state["pending_order"]["status"] = "SUBMISSION_UNCONFIRMED"
        state["pending_order"]["response"] = str(response)
        save_state(state)

        raise RuntimeError(
            "No order ID returned. Check the broker order book manually."
        )

    state["pending_order"]["order_id"] = str(order_id)
    state["pending_order"]["status"] = "SUBMITTED"
    save_state(state)

    # Verify fill through the broker order book.
    deadline = time.time() + FILL_TIMEOUT_SECONDS
    last_record = None

    while time.time() < deadline:
        book_response = api_read(api.orderBook)
        orders = book_response.get("data") or []

        for order in orders:
            current_id = str(
                order.get("orderid", order.get("orderId", ""))
            )

            if current_id == str(order_id):
                last_record = order
                status = str(
                    order.get("orderstatus", order.get("status", ""))
                ).lower()

                filled = int(float(order.get("filledshares") or 0))

                if status in {"rejected", "cancelled", "canceled"}:
                    state["pending_order"]["status"] = status.upper()
                    save_state(state)
                    raise RuntimeError(f"Order {order_id} {status}: {order}")

                if filled >= contract["quantity"] or status in {
                    "complete", "completed", "filled"
                }:
                    if filled not in (0, contract["quantity"]):
                        raise RuntimeError(
                            f"Partial fill {filled}/{contract['quantity']}. "
                            "Manual reconciliation is required."
                        )

                    average_price = float(order.get("averageprice") or 0)

                    state["pending_order"]["status"] = "FILLED"
                    state["pending_order"]["average_price"] = average_price
                    save_state(state)

                    log_trade(
                        action=side,
                        symbol=contract["symbol"],
                        quantity=contract["quantity"],
                        order_id=str(order_id),
                        average_price=average_price,
                        reason=reason,
                        status="FILLED",
                    )

                    log.info(
                        "%s filled: %s, qty=%s, average=%s",
                        side,
                        contract["symbol"],
                        contract["quantity"],
                        average_price,
                    )

                    return {
                        "order_id": str(order_id),
                        "average_price": average_price,
                        "quantity": contract["quantity"],
                    }

        time.sleep(2)

    raise RuntimeError(
        f"Fill not confirmed for order {order_id}. Last record={last_record}. "
        "Inspect order book and positions; do not blindly retry."
    )


# ============================================================
# STRATEGY
# ============================================================

def run_strategy(api, instruments, state):
    today = now_ist().date()
    today_key = today.isoformat()

    # Avoid duplicate runs after a completed trade or a no-trade result.
    if (
        state.get("trade_date") == today_key
        and state.get("day_status") in {"EXITED", "NO_TRADE"}
    ):
        log.info("Today's strategy already completed. Duplicate run blocked.")
        return

    # Any unresolved/open trade state must be reconciled manually.
    if state.get("pending_order") and state["pending_order"].get("status") not in {
        "CLOSED", "SIMULATED_CLOSED"
    }:
        raise RuntimeError(
            "State contains an unresolved order. Check the Angel One order book "
            "and positions before restarting."
        )

    if state.get("position_status") == "OPEN":
        raise RuntimeError(
            "State says a position is open. Reconcile with broker positions "
            "before restarting."
        )

    if now_ist().time() >= ENTRY_CUTOFF:
        log.warning("Opening setup window has passed. No trade today.")
        state.update({
            "trade_date": today_key,
            "day_status": "NO_TRADE",
        })
        save_state(state)
        return

    # Wait for market open if started early.
    market_open = datetime.combine(today, dt_time(9, 15), tzinfo=IST)

    while now_ist() < market_open:
        time.sleep(POLL_SECONDS)

    # Wait for all three completed opening candles.
    opening_df = None

    while now_ist().time() < ENTRY_CUTOFF:
        opening_df = fetch_completed_candles(api, today)
        opening = opening_df[opening_df["time"].isin(FIRST_THREE)]
        available = set(opening["time"].tolist())

        if all(t in available for t in FIRST_THREE):
            break

        log.info("Waiting for opening candles. Available: %s", sorted(available))
        time.sleep(POLL_SECONDS)
    else:
        state.update({
            "trade_date": today_key,
            "day_status": "NO_TRADE",
        })
        save_state(state)
        log.warning("Opening candles unavailable before cutoff. No trade.")
        return

    opening = (
        opening_df[opening_df["time"].isin(FIRST_THREE)]
        .drop_duplicates("time", keep="last")
        .set_index("time")
        .reindex(FIRST_THREE)
    )

    colors = [str(opening.loc[t, "color"]) for t in FIRST_THREE]
    prediction = RULES.get(tuple(colors))

    log.info(
        "Opening colors: %s -> %s -> %s | Prediction: %s",
        colors[0],
        colors[1],
        colors[2],
        prediction or "No Prediction",
    )

    if prediction is None:
        state.update({
            "trade_date": today_key,
            "day_status": "NO_TRADE",
            "colors": colors,
        })
        save_state(state)

        log_trade(
            action="NO_TRADE",
            reason="Pattern not in configured rules",
            colors=" -> ".join(colors),
            status="NO_TRADE",
        )
        return

    # Green prediction buys CE; Red prediction buys PE.
    spot = get_spot_ltp(api)
    option_type = "CE" if prediction == "Green" else "PE"

    contract = select_atm_contract(
        instruments,
        spot,
        option_type,
        today,
    )

    log.info(
        "Spot=%.2f | Contract=%s | Strike=%s | Expiry=%s | Quantity=%s",
        spot,
        contract["symbol"],
        contract["strike"],
        contract["expiry"],
        contract["quantity"],
    )

    # Persist the daily entry latch before order submission.
    if state.get("trade_date") == today_key and state.get("entry_attempted"):
        raise RuntimeError("Entry already attempted today. Duplicate blocked.")

    state.update({
        "trade_date": today_key,
        "day_status": "ENTRY_PENDING",
        "entry_attempted": True,
        "colors": colors,
        "prediction": prediction,
        "spot_at_selection": spot,
        "contract": contract,
    })
    save_state(state)

    entry = place_order(
        api,
        contract,
        "BUY",
        state,
        "three_candle_prediction",
    )

    state.update({
        "entry_order": entry,
        "position_status": "OPEN",
        "day_status": "POSITION_OPEN",
    })
    save_state(state)

    # Candle 4: exit on incorrect prediction OR doji.
    fourth = wait_for_candle(api, today, FOURTH_CANDLE)
    fourth_color = str(fourth["color"])

    log.info(
        "Fourth candle actual=%s | predicted=%s",
        fourth_color,
        prediction,
    )

    if fourth_color != prediction:
        if fourth_color == "Doji":
            exit_reason = "fourth_candle_doji"
        else:
            exit_reason = "fourth_prediction_incorrect"

        fifth_color = None

    else:
        # Candle 4 matched. Hold until candle 5 is complete.
        fifth = wait_for_candle(api, today, FIFTH_CANDLE)
        fifth_color = str(fifth["color"])
        exit_reason = "fifth_candle_close"

        log.info("Fifth candle closed. Actual color=%s", fifth_color)

    # Exit position.
    exit_order = place_order(
        api,
        contract,
        "SELL",
        state,
        exit_reason,
    )

    state.update({
        "exit_order": exit_order,
        "exit_reason": exit_reason,
        "fourth_actual": fourth_color,
        "position_status": "CLOSED",
        "day_status": "EXITED",
    })

    if fifth_color is not None:
        state["fifth_actual"] = fifth_color

    if state.get("pending_order"):
        state["pending_order"]["status"] = (
            "CLOSED" if LIVE_TRADING else "SIMULATED_CLOSED"
        )

    save_state(state)

    log_trade(
        action="EXIT",
        symbol=contract["symbol"],
        quantity=contract["quantity"],
        reason=exit_reason,
        fourth_actual=fourth_color,
        fifth_actual=fifth_color or "",
        status="CLOSED",
    )

    log.info("Strategy completed. Exit reason: %s", exit_reason)


# ============================================================
# MAIN
# ============================================================

def main():
    log.info(
        "Starting NIFTY strategy in %s mode.",
        "LIVE" if LIVE_TRADING else "PAPER",
    )

    if LOTS != 1:
        raise RuntimeError("Configuration is intended for one lot only.")

    if LIVE_TRADING:
        log.warning(
            "LIVE TRADING IS ENABLED. Real market orders can be submitted."
        )
    else:
        log.warning("PAPER MODE: this run will not submit real orders.")

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
                log.exception("Could not terminate SmartAPI session.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.warning(
            "Interrupted. Check the broker order book and positions "
            "before restarting."
        )
        raise
    except Exception:
        log.exception(
            "Strategy stopped. If an order was submitted, verify the broker "
            "order book and positions before restarting."
        )
        raise

import os
import time as time_module
import calendar
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
from SmartApi import SmartConnect


# ============================================================
# CONFIGURATION
# ============================================================

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50
#EXCHANGE = "BSE"
#SYMBOL_TOKEN = "99919000"  # SENSEX
#EXCHANGE = "NSE"
#SYMBOL_TOKEN = "99926009"  # BANK NIFTY

INTERVAL = "ONE_MINUTE"    # Switched to 1-minute candles for precise intra-candle execution

IST = ZoneInfo("Asia/Kolkata")

# Analyze every year starting from 2015.
START_YEAR = 2015

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

# ============================================================
# STRATEGY RULES
# ============================================================

ORB_START_TIME = time(9, 15)
ORB_END_TIME = time(9, 45)
TRADE_SCAN_START_TIME = time(9, 50)   # Scan starts after 9:45 candle
TRADE_DEADLINE_TIME = time(12, 0)    # Scan deadline at 12:00
TRADE_ENTRY_TIME = time(14, 0)       # Take trade at 14:00 if breakout happened
EXIT_TIME = time(15, 0)
MIN_ORB_RANGE_PCT = 0.0  # Minimum range required filter percentage

CANDLE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume"
]


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():
    if not all([API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET]):
        raise ValueError(
            "Missing credentials. Set correct API keys and secrets."
        )

    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()

    session_data = smart_api.generateSession(
        CLIENT_CODE,
        PASSWORD,
        totp
    )

    if not session_data or not session_data.get("status"):
        raise RuntimeError(
            f"SmartAPI login failed: {session_data}"
        )

    return smart_api


# ============================================================
# VECTORIZED TIMESTAMP CONVERSION
# ============================================================

def convert_dataframe_to_ist(df):
    if df.empty or "timestamp" not in df.columns:
        return df
    
    # Parse to datetime if not already
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    
    # Vectorized timezone localization/conversion
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="shift_forward")
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(IST)
        
    return df


# ============================================================
# FETCH HISTORICAL DATA WITH RETRIES
# ============================================================

def fetch_historical_data(smart_api, from_date, to_date):
    params = {
        "exchange": EXCHANGE,
        "symboltoken": SYMBOL_TOKEN,
        "interval": INTERVAL,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M"),
    }

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = smart_api.getCandleData(params)

            if (
                response
                and response.get("status")
                and response.get("data") is not None
            ):
                data = response["data"]
                if not data:
                    return pd.DataFrame(columns=CANDLE_COLUMNS)

                df = pd.DataFrame(data, columns=CANDLE_COLUMNS)
                return convert_dataframe_to_ist(df)

            last_error = RuntimeError(
                f"Historical API response: {response}"
            )

        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(f"   Attempt {attempt}/{MAX_RETRIES} failed: {last_error}")
            print(f"   Retrying in {delay} seconds...")
            time_module.sleep(delay)

    raise RuntimeError(
        f"Failed after {MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# FETCH ONE YEAR, MONTH BY MONTH (WITH CHUNKING SAFETY)
# ============================================================

def fetch_year_historical_data(smart_api, year, now_ist):
    all_data = []
    fetch_status = []
    today = now_ist.date()

    print("\n" + "=" * 90)
    print(f"FETCHING 1-MINUTE NIFTY 50 DATA FOR {year}")
    print("=" * 90)

    if year > today.year:
        for month in range(1, 13):
            fetch_status.append({
                "year": year,
                "month": month,
                "month_name": calendar.month_name[month],
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })
        return pd.DataFrame(columns=CANDLE_COLUMNS), pd.DataFrame(fetch_status)

    last_month = 12 if year < today.year else today.month

    for month in range(1, 13):
        month_name = calendar.month_name[month]

        if month > last_month:
            fetch_status.append({
                "year": year,
                "month": month,
                "month_name": month_name,
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })
            continue

        last_day = calendar.monthrange(year, month)[1]
        start_dt = datetime(year, month, 1, 9, 0, tzinfo=IST)
        end_dt = datetime(year, month, last_day, 15, 30, tzinfo=IST)

        if year == today.year and month == today.month:
            end_dt = min(end_dt, now_ist)

        if start_dt > now_ist:
            fetch_status.append({
                "year": year,
                "month": month,
                "month_name": month_name,
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })
            continue

        print(f"\nFetching {month_name} {year}")
        print(f"   From: {start_dt}")
        print(f"   To  : {end_dt}")

        try:
            month_df = fetch_historical_data(smart_api, start_dt, end_dt)

            if month_df.empty:
                fetch_status.append({
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "fetch_status": "NO DATA",
                    "candles_received": 0,
                    "error": "",
                })
                continue

            month_df = month_df[
                (month_df["timestamp"].dt.year == year)
                & (month_df["timestamp"].dt.month == month)
                & (month_df["timestamp"] <= end_dt)
            ].copy()

            if month_df.empty:
                fetch_status.append({
                    "year": year,
                    "month": month,
                    "month_name": month_name,
                    "fetch_status": "NO DATA",
                    "candles_received": 0,
                    "error": "",
                })
                continue

            count = len(month_df)
            print(f"   Candles received: {count}")
            all_data.append(month_df)

            fetch_status.append({
                "year": year,
                "month": month,
                "month_name": month_name,
                "fetch_status": "SUCCESS",
                "candles_received": count,
                "error": "",
            })

        except Exception as exc:
            error_text = str(exc)
            print(f"   ERROR: {error_text}")
            fetch_status.append({
                "year": year,
                "month": month,
                "month_name": month_name,
                "fetch_status": "FAILED",
                "candles_received": 0,
                "error": error_text,
            })

    status_df = pd.DataFrame(fetch_status)
    if not all_data:
        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    combined_df = pd.concat(all_data, ignore_index=True)
    combined_df = combined_df.drop_duplicates(subset=["timestamp"], keep="first")
    combined_df = combined_df.sort_values("timestamp").reset_index(drop=True)
    combined_df = convert_dataframe_to_ist(combined_df)
    combined_df = combined_df[combined_df["timestamp"] <= now_ist].copy()

    print(f"\nTotal 1-min candles for {year}: {len(combined_df)}")
    return combined_df, status_df


# ============================================================
# ANALYZE ONE TRADING DAY (USING 1-MIN FOR ACCURATE EXECUTION)
# ============================================================

def analyze_day(trading_date, day_df):
    day_df = day_df.sort_values("timestamp").copy()

    for col in ["open", "high", "low", "close", "volume"]:
        day_df[col] = pd.to_numeric(day_df[col], errors="coerce")

    day_df["time_obj"] = day_df["timestamp"].dt.time

    result_row = {
        "date": trading_date,
        "year": trading_date.year,
        "month": trading_date.month,
        "day_open": None,
        "orb_high": None,
        "orb_low": None,
        "orb_midpoint": None,
        "orb_range_pct": None,
        "trade_type": "No Trade",
        "entry_time": None,
        "entry_price": None,
        "stop_loss": None,
        "exit_price": None,
        "exit_reason": "No Breakout",
        "points": 0.0,
        "result": "No Trade"
    }

    if day_df.empty:
        result_row["exit_reason"] = "No Data"
        return result_row

    day_open = float(day_df.iloc[0]["open"])
    result_row["day_open"] = day_open

    # ORB range calculated from 09:15 to 09:45 using 1-minute bars
    orb_candles = day_df[
        (day_df["time_obj"] >= ORB_START_TIME) & 
        (day_df["time_obj"] <= ORB_END_TIME)
    ]

    if orb_candles.empty or len(orb_candles) < 15:
        result_row["exit_reason"] = "Insufficient ORB Data"
        return result_row

    orb_high = orb_candles["high"].max()
    orb_low = orb_candles["low"].min()
    orb_midpoint = (orb_high + orb_low) / 2.0

    result_row["orb_high"] = float(orb_high)
    result_row["orb_low"] = float(orb_low)
    result_row["orb_midpoint"] = float(orb_midpoint)

    orb_range = orb_high - orb_low
    orb_range_pct = (orb_range / day_open) * 100
    result_row["orb_range_pct"] = round(orb_range_pct, 3)

    if orb_range_pct < MIN_ORB_RANGE_PCT:
        result_row["exit_reason"] = f"ORB Range {orb_range_pct:.2f}% < {MIN_ORB_RANGE_PCT}% (Filtered)"
        return result_row

    # 1. Scan for breakout strictly between 9:50 and 12:00
    scan_candles = day_df[
        (day_df["time_obj"] >= TRADE_SCAN_START_TIME) & 
        (day_df["time_obj"] <= TRADE_DEADLINE_TIME)
    ]

    breakout_type = None
    for _, row in scan_candles.iterrows():
        c_high = row["high"]
        c_low = row["low"]

        if c_high > orb_high and c_low < orb_low:
            continue  # Ambiguous bar piercing both sides
        elif c_high > orb_high:
            breakout_type = "BUY"
            break
        elif c_low < orb_low:
            breakout_type = "SELL"
            break

    if not breakout_type:
        return result_row

    # 2. Check 14:00 entry candle existence
    entry_candle = day_df[day_df["time_obj"] == TRADE_ENTRY_TIME]
    if entry_candle.empty:
        result_row["exit_reason"] = "No 14:00 Entry Candle"
        return result_row

    entry_price = float(entry_candle.iloc[0]["close"])
    entry_time = TRADE_ENTRY_TIME

    # Initial Stop Loss & Target 1 Setup
    if breakout_type == "BUY":
        stop_loss = float(orb_low)
    else:
        stop_loss = float(orb_high)

    result_row["trade_type"] = breakout_type
    result_row["entry_time"] = str(entry_time)
    result_row["entry_price"] = entry_price
    result_row["stop_loss"] = stop_loss

    # ============================================================
    # HOLDING PERIOD & STEPPED TRAILING STOP-LOSS LOGIC
    # ============================================================

    holding_candles = day_df[
        (day_df["time_obj"] > entry_time) & 
        (day_df["time_obj"] <= EXIT_TIME)
    ]

    target_1_hit = False
    exit_price = None
    exit_reason = "EOD Square-off"
    current_stop = stop_loss
    highest_multiplier_reached = 0.5  # Track milestones starting from 0.5

    for _, row in holding_candles.iterrows():
        c_close = row["close"]
        c_high = row["high"]
        c_low = row["low"]
        h_time = row["time_obj"]

        if breakout_type == "BUY":
            favorable_move_mult = (c_high - entry_price) / orb_range if orb_range > 0 else 0

            # 1. Check if Target 1 (0.5) is hit for the first time
            if not target_1_hit and favorable_move_mult >= 0.5:
                target_1_hit = True
                current_stop = entry_price  # Move to break-even

            # 2. Once Target 1 is hit, check for higher 0.1 step milestones (0.6, 0.7, 0.8, 0.9, 1.0...)
            if target_1_hit:
                current_mult_tier = 0.5 + int((favorable_move_mult - 0.5) / 0.1) * 0.1
                
                if current_mult_tier > highest_multiplier_reached:
                    highest_multiplier_reached = current_mult_tier
                    lock_in_offset = (highest_multiplier_reached - 0.5) * orb_range
                    current_stop = entry_price + lock_in_offset

            # Check if stop loss is hit by current candle close
            if c_close <= current_stop:
                exit_price = float(current_stop)
                exit_reason = f"Trailing Stop Hit at {h_time} (Stop: {current_stop:.2f}, Tier: {highest_multiplier_reached:.1f})"
                break
                
        else: # SELL
            favorable_move_mult = (entry_price - c_low) / orb_range if orb_range > 0 else 0

            if not target_1_hit and favorable_move_mult >= 0.5:
                target_1_hit = True
                current_stop = entry_price  # Move to break-even

            if target_1_hit:
                current_mult_tier = 0.5 + int((favorable_move_mult - 0.5) / 0.1) * 0.1
                
                if current_mult_tier > highest_multiplier_reached:
                    highest_multiplier_reached = current_mult_tier
                    lock_in_offset = (highest_multiplier_reached - 0.5) * orb_range
                    current_stop = entry_price - lock_in_offset

            if c_close >= current_stop:
                exit_price = float(current_stop)
                exit_reason = f"Trailing Stop Hit at {h_time} (Stop: {current_stop:.2f}, Tier: {highest_multiplier_reached:.1f})"
                break

    if exit_price is None:
        if not holding_candles.empty:
            exit_price = float(holding_candles.iloc[-1]["close"])
        else:
            exit_price = entry_price

    # Calculate points for 100% position
    if breakout_type == "BUY":
        points = exit_price - entry_price
    else:
        points = entry_price - exit_price

    result_row["exit_price"] = round(exit_price, 2)
    result_row["exit_reason"] = exit_reason
    result_row["points"] = round(points, 2)
    result_row["result"] = "Win" if points > 0 else ("Loss" if points < 0 else "Breakeven")

    return result_row


# ============================================================
# BUILD MONTHLY & YEARLY SUMMARIES
# ============================================================

def build_monthly_summary(results_df, fetch_status_df):
    monthly_rows = []
    years = sorted(set(results_df["year"].unique()) | set(fetch_status_df["year"].unique()))

    for year in years:
        for month in range(1, 13):
            month_df = results_df[(results_df["year"] == year) & (results_df["month"] == month)]
            traded = month_df[month_df["result"].isin(["Win", "Loss", "Breakeven"])]

            wins = int((traded["result"] == "Win").sum())
            losses = int((traded["result"] == "Loss").sum())
            total_trades = len(traded)

            win_rate = (wins / total_trades * 100) if total_trades else 0.0
            total_points = float(traded["points"].sum())

            status_row = fetch_status_df[(fetch_status_df["year"] == year) & (fetch_status_df["month"] == month)]
            status = status_row.iloc[0]["fetch_status"] if not status_row.empty else "UNKNOWN"
            candles = int(status_row.iloc[0]["candles_received"]) if not status_row.empty else 0

            monthly_rows.append({
                "year": year,
                "month": month,
                "month_name": calendar.month_name[month],
                "fetch_status": status,
                "candles_received": candles,
                "trading_days_analyzed": len(month_df),
                "trades_taken": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate_percent": round(win_rate, 2),
                "total_points": round(total_points, 2),
                "no_trade": int((month_df["result"] == "No Trade").sum())
            })

    return pd.DataFrame(monthly_rows)


def build_yearly_summary(results_df):
    yearly_rows = []
    if results_df.empty:
        return pd.DataFrame()

    for year in sorted(results_df["year"].unique()):
        year_df = results_df[results_df["year"] == year]
        traded = year_df[year_df["result"].isin(["Win", "Loss", "Breakeven"])]

        wins = int((traded["result"] == "Win").sum())
        losses = int((traded["result"] == "Loss").sum())
        total_trades = len(traded)
        win_rate = (wins / total_trades * 100) if total_trades else 0.0
        
        gross_profit = float(traded[traded["points"] > 0]["points"].sum())
        gross_loss = float(abs(traded[traded["points"] < 0]["points"].sum()))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)
        total_points = float(traded["points"].sum())

        yearly_rows.append({
            "year": year,
            "trading_days_analyzed": len(year_df),
            "trades_taken": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_percent": round(win_rate, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": profit_factor,
            "total_points": round(total_points, 2),
            "no_trade": int(year_df["exit_reason"].str.contains("No Breakout", na=False).sum())
        })

    return pd.DataFrame(yearly_rows)


# ============================================================
# PERFORMANCE REPORTING
# ============================================================

def analyze_performance_summary(results_df):
    traded = results_df[results_df["result"].isin(["Win", "Loss", "Breakeven"])].copy()
    if traded.empty:
        print("\nNo trades available for performance analysis.")
        return

    gross_profit = float(traded[traded["points"] > 0]["points"].sum())
    gross_loss = float(abs(traded[traded["points"] < 0]["points"].sum()))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')

    print("\n" + "=" * 120)
    print("STRATEGY PERFORMANCE SUMMARY (1-MINUTE RESOLUTION - NO LOOK-AHEAD BIAS)")
    print("=" * 120)
    print(f"Total Traded Days                     : {len(traded)}")
    wins = int((traded["result"] == "Win").sum())
    win_rate = (wins / len(traded) * 100) if len(traded) else 0.0
    print(f"Winning Trades                        : {wins} ({win_rate:.2f}%)")
    print(f"Total Positive Points (Gross Profit)  : {gross_profit:.2f}")
    print(f"Total Negative Points (Gross Loss)    : {gross_loss:.2f}")
    print(f"Profit Factor                         : {profit_factor}")
    print(f"Total Net Points                      : {traded['points'].sum():.2f}")
    print("=" * 120)


# ============================================================
# MAIN
# ============================================================

def main():
    smart_api = None
    try:
        now_ist = datetime.now(IST)
        current_year = now_ist.year

        print("\n" + "=" * 100)
        print("NIFTY 50 ORB STRATEGY (1-MINUTE INTRA-CANDLE PRECISION)")
        print("=" * 100)
        print(f"Start year      : {START_YEAR}")
        print(f"Current year    : {current_year}")
        print(f"Interval        : {INTERVAL}")
        print("=" * 100)

        smart_api = login_to_smartapi()
        print("\nSmartAPI login successful.")

        all_daily_results = []
        all_fetch_status = []

        for year in range(START_YEAR, current_year + 1):
            raw_df, fetch_status_df = fetch_year_historical_data(smart_api, year, now_ist)
            all_fetch_status.append(fetch_status_df)

            if raw_df.empty:
                continue

            raw_df = convert_dataframe_to_ist(raw_df)
            raw_df = raw_df.sort_values("timestamp").copy()

            raw_df["trading_date"] = raw_df["timestamp"].dt.date
            daily_results = []

            for trading_date, day_df in raw_df.groupby("trading_date"):
                daily_results.append(analyze_day(trading_date, day_df))

            if daily_results:
                year_results = pd.DataFrame(daily_results)
                all_daily_results.append(year_results)
                print(f"\nAnalysis completed for {year}. Trading dates: {len(year_results)}")

        fetch_status_df = pd.concat(all_fetch_status, ignore_index=True) if all_fetch_status else pd.DataFrame()

        if not all_daily_results:
            print("\nNo historical analysis results available.")
            return

        results_df = pd.concat(all_daily_results, ignore_index=True)
        results_df["date"] = pd.to_datetime(results_df["date"])
        results_df = results_df.sort_values("date").reset_index(drop=True)

        monthly_df = build_monthly_summary(results_df, fetch_status_df)

        print("\n" + "=" * 130)
        print("MONTHLY PERFORMANCE SUMMARY")
        print("=" * 130)
        print(monthly_df[["year", "month_name", "trades_taken", "wins", "losses", "win_rate_percent", "total_points"]].to_string(index=False))

        yearly_df = build_yearly_summary(results_df)

        print("\n" + "=" * 130)
        print("YEARLY PERFORMANCE SUMMARY")
        print("=" * 130)
        print(yearly_df.to_string(index=False))

        # ========================================================
        # DETAILED OVERALL PERFORMANCE BREAKDOWN
        # ========================================================
        traded = results_df[results_df["result"].isin(["Win", "Loss", "Breakeven"])]
        wins = int((traded["result"] == "Win").sum())
        losses = int((traded["result"] == "Loss").sum())
        total_trades = len(traded)
        overall_win_rate = (wins / total_trades * 100) if total_trades else 0.0
        
        gross_profit = float(traded[traded["points"] > 0]["points"].sum())
        gross_loss = float(abs(traded[traded["points"] < 0]["points"].sum()))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')
        overall_points = float(traded["points"].sum())

        filtered_days = int(results_df["exit_reason"].str.contains("Filtered", na=False).sum())
        valid_orb_days = int((~results_df["exit_reason"].str.contains("Filtered|No Data|Insufficient", na=False)).sum())
        stop_loss_hits = int(results_df["exit_reason"].str.contains("Trailing Stop Hit", na=False).sum())

        print("\n" + "=" * 90)
        print(f"OVERALL SUMMARY REPORT (1-MIN): {START_YEAR} TO {current_year}")
        print("=" * 90)
        print(f"Total Trading Days Analyzed       : {len(results_df)}")
        print(f"Days within Expected ORB Range    : {valid_orb_days}")
        print(f"Days Filtered (Outside ORB Range) : {filtered_days}")
        print("-" * 90)
        print(f"Total Trades Taken                : {total_trades}")
        print(f"Winning Trades                    : {wins}")
        print(f"Losing Trades                     : {losses}")
        print(f"Trailing Stop Hits                : {stop_loss_hits}")
        print(f"Overall Win Rate (%)              : {overall_win_rate:.2f}%")
        print(f"Total Positive Points (Gross)     : {gross_profit:.2f}")
        print(f"Total Negative Points (Gross)     : {gross_loss:.2f}")
        print(f"Profit Factor                     : {profit_factor}")
        print(f"Total Points Captured (Net)       : {overall_points:.2f}")
        print("=" * 90)

        analyze_performance_summary(results_df)

    except Exception as exc:
        print(f"\nERROR: {exc}")

    finally:
        if smart_api is not None:
            try:
                smart_api.terminateSession(CLIENT_CODE)
                print("\nSmartAPI session terminated.")
            except Exception as exc:
                print(f"Could not terminate session: {exc}")


if __name__ == "__main__":
    main()
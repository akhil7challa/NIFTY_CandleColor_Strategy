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
INTERVAL = "FIVE_MINUTE"

IST = ZoneInfo("Asia/Kolkata")

# Analyze every year starting from 2015.
START_YEAR = 2015

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

# ============================================================
# STRATEGY RULES
# ============================================================

#1 NOTE: Strategy Specific Times & Filters (30-min ORB: 9:15 to 9:45)
    #ORB Window : 9:15 - 9:45
    #Scan Window : 9:50 - 12:00
    #Entry Time : 14:00
    #Exit Time : 15:00

#2 NOTE : The following MIN_ORB_RANGE_PCT levels and respective results are as follows:
    #ORB Range : 0%   -> Trades taken : 2407 -> Gain Points : 34335 -> Winning Percentage : 61.03%
    #ORB Range : 30%  -> Trades taken : 1911 -> Gain Points : 28736 -> Winning Percentage : 60.70%
    #ORB Range : 35%  -> Trades taken : 1617 -> Gain Points : 25381 -> Winning Percentage : 61.47%
    #ORB Range : 40%  -> Trades taken : 1354 -> Gain Points : 22206 -> Winning Percentage : 61.52%
    #ORB Range : 45%  -> Trades taken : 1109 -> Gain Points : 18962 -> Winning Percentage : 61.68%
    #ORB Range : 50%  -> Trades taken : 874  -> Gain Points : 16414 -> Winning Percentage : 62.93%


ORB_START_TIME = time(9, 15)
ORB_END_TIME = time(9, 45)
TRADE_SCAN_START_TIME = time(9, 50)  # Scan starts after 9:45 candle
TRADE_DEADLINE_TIME = time(12, 0)    # Scan deadline at 12:00
TRADE_ENTRY_TIME = time(14, 0)       # Take trade at 14:00 if breakout happened
EXIT_TIME = time(15, 0)
MIN_ORB_RANGE_PCT = 0.0

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
# TIMESTAMP CONVERSION
# ============================================================

def convert_to_ist(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(IST)
    return timestamp.tz_convert(IST)


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

                return pd.DataFrame(
                    data,
                    columns=CANDLE_COLUMNS
                )

            last_error = RuntimeError(
                f"Historical API response: {response}"
            )

        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(f"  Attempt {attempt}/{MAX_RETRIES} failed: {last_error}")
            print(f"  Retrying in {delay} seconds...")
            time_module.sleep(delay)

    raise RuntimeError(
        f"Failed after {MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# FETCH ONE YEAR, MONTH BY MONTH
# ============================================================

def fetch_year_historical_data(smart_api, year, now_ist):
    all_data = []
    fetch_status = []
    today = now_ist.date()

    print("\n" + "=" * 90)
    print(f"FETCHING NIFTY 50 DATA FOR {year}")
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
        print(f"  From: {start_dt}")
        print(f"  To  : {end_dt}")

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

            month_df["timestamp"] = pd.to_datetime(
                month_df["timestamp"], errors="coerce"
            )
            month_df = month_df.dropna(subset=["timestamp"]).copy()
            month_df["timestamp"] = month_df["timestamp"].apply(convert_to_ist)

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
            print(f"  Candles received: {count}")
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
            print(f"  ERROR: {error_text}")
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
    combined_df["timestamp"] = combined_df["timestamp"].apply(convert_to_ist)
    combined_df = combined_df[combined_df["timestamp"] <= now_ist].copy()

    print(f"\nTotal candles for {year}: {len(combined_df)}")
    return combined_df, status_df


# ============================================================
# ANALYZE ONE TRADING DAY (ORB SCAN + 14:00 ENTRY + MIDPOINT SL)
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

    orb_candles = day_df[
        (day_df["time_obj"] >= ORB_START_TIME) & 
        (day_df["time_obj"] <= ORB_END_TIME)
    ]

    if orb_candles.empty or len(orb_candles) < 5:
        result_row["exit_reason"] = "Insufficient ORB Data"
        return result_row

    orb_high = orb_candles["high"].max()
    orb_low = orb_candles["low"].min()
    orb_midpoint = (orb_high + orb_low) / 2.0

    result_row["orb_high"] = float(orb_high)
    result_row["orb_low"] = float(orb_low)
    result_row["orb_midpoint"] = float(orb_midpoint)

    orb_range_pct = ((orb_high - orb_low) / day_open) * 100
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
        c_close = row["close"]

        if c_close > orb_high:
            breakout_type = "BUY"
            break
        elif c_close < orb_low:
            breakout_type = "SELL"
            break

    if not breakout_type:
        return result_row

    # 2. If morning breakout happened, take trade at 14:00 candle close
    entry_candle = day_df[day_df["time_obj"] == TRADE_ENTRY_TIME]
    if entry_candle.empty:
        result_row["exit_reason"] = "No 14:00 Entry Candle"
        return result_row

    entry_price = float(entry_candle.iloc[0]["close"])
    entry_time = TRADE_ENTRY_TIME
    stop_loss = float(orb_midpoint)

    result_row["trade_type"] = breakout_type
    result_row["entry_time"] = str(entry_time)
    result_row["entry_price"] = entry_price
    result_row["stop_loss"] = stop_loss

    # 3. Holding period from after 14:00 up to EXIT_TIME (15:00)
    holding_candles = day_df[
        (day_df["time_obj"] > entry_time) & 
        (day_df["time_obj"] <= EXIT_TIME)
    ]

    exit_price = None
    exit_reason = "EOD Square-off"

    for _, row in holding_candles.iterrows():
        c_close = row["close"]
        h_time = row["time_obj"]

        if breakout_type == "BUY":
            if c_close <= stop_loss:
                exit_price = float(stop_loss)
                exit_reason = f"Stop Loss Hit (Close) at {h_time}"
                break
        else:
            if c_close >= stop_loss:
                exit_price = float(stop_loss)
                exit_reason = f"Stop Loss Hit (Close) at {h_time}"
                break

    if exit_price is None:
        if not holding_candles.empty:
            exit_price = float(holding_candles.iloc[-1]["close"])
        else:
            exit_price = entry_price

    result_row["exit_price"] = exit_price
    result_row["exit_reason"] = exit_reason

    if breakout_type == "BUY":
        points = exit_price - entry_price
    else:
        points = entry_price - exit_price

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
        total_points = float(traded["points"].sum())

        yearly_rows.append({
            "year": year,
            "trading_days_analyzed": len(year_df),
            "trades_taken": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate_percent": round(win_rate, 2),
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

    print("\n" + "=" * 120)
    print("STRATEGY PERFORMANCE SUMMARY (MORNING SCAN + 14:00 ENTRY EXECUTION)")
    print("=" * 120)
    print(f"Total Traded Days : {len(traded)}")
    wins = int((traded["result"] == "Win").sum())
    win_rate = (wins / len(traded) * 100) if len(traded) else 0.0
    print(f"Winning Trades    : {wins} ({win_rate:.2f}%)")
    print(f"Total Points      : {traded['points'].sum():.2f}")
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
        print("NIFTY 50 30-MIN ORB (9:50-12:00 SCAN) + 14:00 ENTRY BACKTEST")
        print("=" * 100)
        print(f"Start year      : {START_YEAR}")
        print(f"Current year    : {current_year}")
        print(f"Min ORB Range % : {MIN_ORB_RANGE_PCT}%")
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

            raw_df["timestamp"] = raw_df["timestamp"].apply(convert_to_ist)
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
        overall_points = float(traded["points"].sum())

        filtered_days = int(results_df["exit_reason"].str.contains("Filtered", na=False).sum())
        valid_orb_days = int((~results_df["exit_reason"].str.contains("Filtered|No Data|Insufficient", na=False)).sum())
        stop_loss_hits = int(results_df["exit_reason"].str.contains("Stop Loss Hit", na=False).sum())

        print("\n" + "=" * 90)
        print(f"OVERALL SUMMARY REPORT: {START_YEAR} TO {current_year}")
        print("=" * 90)
        print(f"Total Trading Days Analyzed       : {len(results_df)}")
        print(f"Days within Expected ORB Range    : {valid_orb_days}")
        print(f"Days Filtered (Outside ORB Range) : {filtered_days}")
        print("-" * 90)
        print(f"Total Trades Taken                : {total_trades}")
        print(f"Winning Trades                    : {wins}")
        print(f"Losing Trades                     : {losses}")
        print(f"Stop Loss Hits                    : {stop_loss_hits}")
        print(f"Overall Win Rate (%)              : {overall_win_rate:.2f}%")
        print(f"Total Points Captured             : {overall_points:.2f}")
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
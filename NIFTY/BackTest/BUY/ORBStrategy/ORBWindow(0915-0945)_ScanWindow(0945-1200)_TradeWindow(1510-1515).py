import os
import time as time_module
import calendar
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
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
#EXCHANGE = "NSE"
#SYMBOL_TOKEN = "2885"  # Equity Stock

INTERVAL = "ONE_MINUTE"
IST = ZoneInfo("Asia/Kolkata")
START_YEAR = 2026

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2


# ============================================================
# STRATEGY RULES
# ============================================================

ORB_START_TIME = time(9, 15)
ORB_END_TIME = time(9, 45)
TRADE_SCAN_START_TIME = time(9, 50)
TRADE_DEADLINE_TIME = time(12, 0)
TRADE_ENTRY_TIME = time(15, 9)
EXIT_TIME = time(15, 14)
MIN_ORB_RANGE_PCT = 0.0

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():
    if not all([API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET]):
        raise ValueError("Missing credentials. Set correct API keys and secrets.")

    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()
    session_data = smart_api.generateSession(CLIENT_CODE, PASSWORD, totp)

    if not session_data or not session_data.get("status"):
        raise RuntimeError(f"SmartAPI login failed: {session_data}")

    return smart_api


# ============================================================
# TIMEZONE & DATA CONVERSION
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


# ============================================================
# HISTORICAL DATA FETCHING (WITH LOGS)
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
            if response and response.get("status") and response.get("data") is not None:
                data = response["data"]
                if not data:
                    return pd.DataFrame(columns=CANDLE_COLUMNS)
                return convert_dataframe_to_ist(pd.DataFrame(data, columns=CANDLE_COLUMNS))
            last_error = RuntimeError(f"Historical API response: {response}")
        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(f"   Attempt {attempt}/{MAX_RETRIES} failed: {last_error}")
            print(f"   Retrying in {delay} seconds...")
            time_module.sleep(delay)

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts: {last_error}")


def fetch_year_historical_data(smart_api, year, now_ist):
    all_data, fetch_status = [], []
    today = now_ist.date()

    print("\n" + "=" * 90)
    print(f"FETCHING 1-MINUTE {SYMBOL_TOKEN} SYMBOL_TOKEN DATA FOR {year}")
    print("=" * 90)

    if year > today.year:
        return pd.DataFrame(columns=CANDLE_COLUMNS), pd.DataFrame(fetch_status)

    last_month = 12 if year < today.year else today.month

    for month in range(1, 13):
        month_name = calendar.month_name[month]
        if month > last_month:
            continue

        last_day = calendar.monthrange(year, month)[1]
        start_dt = datetime(year, month, 1, 9, 0, tzinfo=IST)
        end_dt = min(datetime(year, month, last_day, 15, 30, tzinfo=IST), now_ist)

        if start_dt > now_ist:
            continue

        print(f"\nFetching {month_name} {year}")
        print(f"   From: {start_dt}")
        print(f"   To  : {end_dt}")

        try:
            month_df = fetch_historical_data(smart_api, start_dt, end_dt)
            if month_df.empty:
                print(f"   No data received for {month_name} {year}")
                fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "NO DATA", "candles_received": 0})
                continue

            month_df = month_df[
                (month_df["timestamp"].dt.year == year) &
                (month_df["timestamp"].dt.month == month) &
                (month_df["timestamp"] <= end_dt)
            ].copy()

            if not month_df.empty:
                count = len(month_df)
                print(f"   Candles received: {count}")
                all_data.append(month_df)
                fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "SUCCESS", "candles_received": count})
            else:
                fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "NO DATA", "candles_received": 0})
            
            time_module.sleep(0.4)
        except Exception as exc:
            error_text = str(exc)
            print(f"   ERROR: {error_text}")
            fetch_status.append({"year": year, "month": month, "month_name": month_name, "fetch_status": "FAILED", "candles_received": 0, "error": error_text})

    status_df = pd.DataFrame(fetch_status)
    if not all_data:
        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    combined_df = pd.concat(all_data, ignore_index=True).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    print(f"\nTotal 1-min candles for {year}: {len(combined_df)}")
    return convert_dataframe_to_ist(combined_df), status_df


# ============================================================
# CORE TRADING LOGIC (TRAILING STOP-LOSS)
# ============================================================

def analyze_day(trading_date, day_df):
    day_df = day_df.sort_values("timestamp").copy()
    for col in ["open", "high", "low", "close", "volume"]:
        day_df[col] = pd.to_numeric(day_df[col], errors="coerce")

    day_df["time_obj"] = day_df["timestamp"].dt.time
    result_row = {
        "date": trading_date, "year": trading_date.year, "month": trading_date.month,
        "trade_type": "No Trade", "entry_price": None, "stop_loss": None,
        "exit_price": None, "exit_reason": "No Breakout", "points": 0.0, "result": "No Trade"
    }

    if day_df.empty:
        return result_row

    day_open = float(day_df.iloc[0]["open"])
    orb_candles = day_df[(day_df["time_obj"] >= ORB_START_TIME) & (day_df["time_obj"] <= ORB_END_TIME)]

    if len(orb_candles) < 15:
        result_row["exit_reason"] = "Insufficient ORB Data"
        return result_row

    orb_high = orb_candles["high"].max()
    orb_low = orb_candles["low"].min()
    orb_midpoint = (orb_high + orb_low) / 2.0
    orb_range_pct = ((orb_high - orb_low) / day_open) * 100

    if orb_range_pct < MIN_ORB_RANGE_PCT:
        result_row["exit_reason"] = "Filtered"
        return result_row

    scan_candles = day_df[(day_df["time_obj"] >= TRADE_SCAN_START_TIME) & (day_df["time_obj"] <= TRADE_DEADLINE_TIME)]
    breakout_type = None

    for _, row in scan_candles.iterrows():
        if row["high"] > orb_high and row["low"] < orb_low:
            continue
        elif row["high"] > orb_high:
            breakout_type = "BUY"
            break
        elif row["low"] < orb_low:
            breakout_type = "SELL"
            break

    if not breakout_type:
        return result_row

    entry_candle = day_df[day_df["time_obj"] == TRADE_ENTRY_TIME]
    if entry_candle.empty:
        return result_row

    entry_price = float(entry_candle.iloc[0]["close"])
    
    if breakout_type == "BUY":
        initial_sl = orb_midpoint if entry_price > orb_high else orb_low
    else:
        initial_sl = orb_midpoint if entry_price < orb_low else orb_high

    current_stop_loss = initial_sl
    initial_risk_distance = abs(entry_price - initial_sl)
    trailing_step = initial_risk_distance * 0.20

    result_row.update({
        "trade_type": breakout_type, 
        "entry_time": str(TRADE_ENTRY_TIME), 
        "entry_price": entry_price, 
        "stop_loss": initial_sl
    })

    holding_candles = day_df[(day_df["time_obj"] > TRADE_ENTRY_TIME) & (day_df["time_obj"] <= EXIT_TIME)]
    exit_price, exit_reason = None, "EOD Square-off"

    for _, row in holding_candles.iterrows():
        # 1. Check if current trailing stop-loss is hit on this minute's close
        if breakout_type == "BUY" and row["close"] <= current_stop_loss:
            exit_price, exit_reason = float(current_stop_loss), f"Trailing Stop Loss Hit at {row['time_obj']}"
            break
        elif breakout_type == "SELL" and row["close"] >= current_stop_loss:
            exit_price, exit_reason = float(current_stop_loss), f"Trailing Stop Loss Hit at {row['time_obj']}"
            break

        # 2. Ratchet stop-loss 20% of initial risk distance closer to entry on each 1-min close
        if breakout_type == "BUY":
            new_sl = current_stop_loss + trailing_step
            if new_sl <= entry_price:
                current_stop_loss = new_sl
        else:
            new_sl = current_stop_loss - trailing_step
            if new_sl >= entry_price:
                current_stop_loss = new_sl

    if exit_price is None:
        exit_price = float(holding_candles.iloc[-1]["close"]) if not holding_candles.empty else entry_price

    points = (exit_price - entry_price) if breakout_type == "BUY" else (entry_price - exit_price)
    result_row.update({
        "exit_price": round(exit_price, 2), 
        "exit_reason": exit_reason, 
        "points": round(points, 2), 
        "result": "Win" if points > 0 else ("Loss" if points < 0 else "Breakeven")
    })

    return result_row


# ============================================================
# ADVANCED ANALYTICS (STREAKS & RISK METRICS)
# ============================================================

def calculate_streaks(traded_df):
    max_win_streak, max_win_points = 0, 0.0
    max_win_dates = (None, None)
    max_loss_streak, max_loss_points = 0, 0.0
    max_loss_dates = (None, None)

    curr_win, curr_win_pts, curr_win_start = 0, 0.0, None
    curr_loss, curr_loss_pts, curr_loss_start = 0, 0.0, None

    for _, row in traded_df.iterrows():
        res, pts, dt_str = row["result"], float(row["points"]), row["date"].strftime("%Y-%m-%d")

        if res == "Win":
            curr_loss, curr_loss_pts, curr_loss_start = 0, 0.0, None
            if curr_win == 0:
                curr_win_start = dt_str
            curr_win += 1
            curr_win_pts += pts

            if curr_win > max_win_streak or (curr_win == max_win_streak and curr_win_pts > max_win_points):
                max_win_streak, max_win_points, max_win_dates = curr_win, curr_win_pts, (curr_win_start, dt_str)

        elif res == "Loss":
            curr_win, curr_win_pts, curr_win_start = 0, 0.0, None
            if curr_loss == 0:
                curr_loss_start = dt_str
            curr_loss += 1
            curr_loss_pts += pts

            if curr_loss > max_loss_streak or (curr_loss == max_loss_streak and curr_loss_pts < max_loss_points):
                max_loss_streak, max_loss_points, max_loss_dates = curr_loss, curr_loss_pts, (curr_loss_start, dt_str)
        else:
            curr_win = curr_loss = 0

    return {
        "max_win_streak": max_win_streak, "max_win_streak_points": round(max_win_points, 2), "max_win_dates": max_win_dates,
        "max_loss_streak": max_loss_streak, "max_loss_streak_points": round(max_loss_points, 2), "max_loss_dates": max_loss_dates,
    }


def analyze_advanced_metrics(results_df):
    traded = results_df[results_df["result"].isin(["Win", "Loss", "Breakeven"])].copy()
    if traded.empty:
        return

    cumulative = traded["points"].cumsum()
    max_drawdown = (cumulative - cumulative.cummax()).min()
    mean_pts, std_pts = traded["points"].mean(), traded["points"].std()
    sharpe_ratio = (mean_pts / std_pts) * np.sqrt(252) if std_pts > 0 else 0.0

    print("\n" + "=" * 90)
    print("ADVANCED RISK-ADJUSTED PERFORMANCE METRICS")
    print("=" * 90)
    print(f"Maximum Drawdown (MDD)       : {max_drawdown:.2f} points")
    print(f"Average Trade Return         : {mean_pts:.2f} points")
    print(f"Standard Deviation per Trade : {std_pts:.2f} points")
    print(f"Annualized Sharpe Ratio        : {sharpe_ratio:.2f}")
    print("=" * 90)


# ============================================================
# SUMMARIES BUILDERS
# ============================================================

def build_monthly_summary(results_df, fetch_status_df):
    monthly_rows = []
    years = sorted(set(results_df["year"].unique()) | set(fetch_status_df["year"].unique()))

    for year in years:
        for month in range(1, 13):
            m_df = results_df[(results_df["year"] == year) & (results_df["month"] == month)]
            traded = m_df[m_df["result"].isin(["Win", "Loss", "Breakeven"])]

            wins, losses, total = int((traded["result"] == "Win").sum()), int((traded["result"] == "Loss").sum()), len(traded)
            status_row = fetch_status_df[(fetch_status_df["year"] == year) & (fetch_status_df["month"] == month)]
            
            monthly_rows.append({
                "year": year, "month": month, "month_name": calendar.month_name[month],
                "fetch_status": status_row.iloc[0]["fetch_status"] if not status_row.empty else "UNKNOWN",
                "candles_received": int(status_row.iloc[0]["candles_received"]) if not status_row.empty else 0,
                "trading_days_analyzed": len(m_df), "trades_taken": total, "wins": wins, "losses": losses,
                "win_rate_percent": round((wins / total * 100) if total else 0.0, 2),
                "total_points": round(float(traded["points"].sum()), 2),
                "no_trade": int((m_df["result"] == "No Trade").sum())
            })

    return pd.DataFrame(monthly_rows)


def build_yearly_summary(results_df):
    yearly_rows = []
    if results_df.empty:
        return pd.DataFrame()

    for year in sorted(results_df["year"].unique()):
        y_df = results_df[results_df["year"] == year]
        traded = y_df[y_df["result"].isin(["Win", "Loss", "Breakeven"])]

        wins, losses, total = int((traded["result"] == "Win").sum()), int((traded["result"] == "Loss").sum()), len(traded)
        g_profit = float(traded[traded["points"] > 0]["points"].sum())
        g_loss = float(abs(traded[traded["points"] < 0]["points"].sum()))

        yearly_rows.append({
            "year": year, "trading_days_analyzed": len(y_df), "trades_taken": total, "wins": wins, "losses": losses,
            "win_rate_percent": round((wins / total * 100) if total else 0.0, 2),
            "gross_profit": round(g_profit, 2), "gross_loss": round(g_loss, 2),
            "profit_factor": round(g_profit / g_loss, 2) if g_loss > 0 else float('inf'),
            "total_points": round(float(traded["points"].sum()), 2),
            "no_trade": int(y_df["exit_reason"].str.contains("No Breakout", na=False).sum())
        })

    return pd.DataFrame(yearly_rows)


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    smart_api = None
    try:
        now_ist = datetime.now(IST)
        smart_api = login_to_smartapi()

        all_results, all_status = [], []
        for year in range(START_YEAR, now_ist.year + 1):
            raw_df, status_df = fetch_year_historical_data(smart_api, year, now_ist)
            all_status.append(status_df)

            if not raw_df.empty:
                raw_df["trading_date"] = raw_df["timestamp"].dt.date
                daily = [analyze_day(d_date, d_df) for d_date, d_df in raw_df.groupby("trading_date")]
                if daily:
                    all_results.append(pd.DataFrame(daily))
                    print(f"\nAnalysis completed for {year}. Trading dates: {len(daily)}")

        if not all_results:
            print("No data analyzed.")
            return

        results_df = pd.concat(all_results, ignore_index=True).sort_values("date").reset_index(drop=True)
        fetch_status_df = pd.concat(all_status, ignore_index=True) if all_status else pd.DataFrame()

        # 1. Monthly Summary
        monthly_df = build_monthly_summary(results_df, fetch_status_df)
        print("\n" + "=" * 130)
        print("MONTHLY PERFORMANCE SUMMARY")
        print("=" * 130)
        print(monthly_df[["year", "month_name", "trades_taken", "wins", "losses", "win_rate_percent", "total_points"]].to_string(index=False))

        # 2. Yearly Summary
        yearly_df = build_yearly_summary(results_df)
        print("\n" + "=" * 130)
        print("YEARLY PERFORMANCE SUMMARY")
        print("=" * 130)
        print(yearly_df.to_string(index=False))

        # 3. Overall & Performance Summary
        traded = results_df[results_df["result"].isin(["Win", "Loss", "Breakeven"])].copy()
        wins, losses, total_trades = int((traded["result"] == "Win").sum()), int((traded["result"] == "Loss").sum()), len(traded)
        overall_win_rate = (wins / total_trades * 100) if total_trades else 0.0
        
        gross_profit = float(traded[traded["points"] > 0]["points"].sum())
        gross_loss = float(abs(traded[traded["points"] < 0]["points"].sum()))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf')
        overall_points = float(traded["points"].sum())

        stop_loss_hits = int(results_df["exit_reason"].str.contains("Stop Loss Hit", na=False).sum())
        streaks = calculate_streaks(traded)
        win_dates_str = f"({streaks['max_win_dates'][0]} to {streaks['max_win_dates'][1]})" if streaks['max_win_streak'] > 0 else ""
        loss_dates_str = f"({streaks['max_loss_dates'][0]} to {streaks['max_loss_dates'][1]})" if streaks['max_loss_streak'] > 0 else ""

        print("\n" + "=" * 90)
        print(f"OVERALL SUMMARY REPORT: {START_YEAR} TO {now_ist.year}")
        print("=" * 90)
        print(f"Total Trading Days Analyzed       : {len(results_df)}")
        print(f"Total Trades Taken                : {total_trades}")
        print(f"Winning Trades                    : {wins}")
        print(f"Losing Trades                     : {losses}")
        print(f"Stop Loss Hits                    : {stop_loss_hits}")
        print(f"Overall Win Rate (%)              : {overall_win_rate:.2f}%")
        print(f"Total Positive Points (Gross)     : {gross_profit:.2f}")
        print(f"Total Negative Points (Gross)     : {gross_loss:.2f}")
        print(f"Profit Factor                     : {profit_factor}")
        print(f"Total Points Captured (Net)       : {overall_points:.2f}")
        print("-" * 90)
        print(f"Max Consecutive Wins              : {streaks['max_win_streak']} trades | +{streaks['max_win_streak_points']:.2f} pts {win_dates_str}")
        print(f"Max Consecutive Losses            : {streaks['max_loss_streak']} trades | {streaks['max_loss_streak_points']:.2f} pts {loss_dates_str}")
        print("=" * 90)

        print("\n" + "=" * 120)
        print("STRATEGY PERFORMANCE SUMMARY (1-MINUTE RESOLUTION - TRAILING STOP-LOSS)")
        print("=" * 120)
        print(f"Total Traded Days                     : {len(traded)}")
        print(f"Winning Trades                        : {wins} ({overall_win_rate:.2f}%)")
        print(f"Total Positive Points (Gross Profit)  : {gross_profit:.2f}")
        print(f"Total Negative Points (Gross Loss)    : {gross_loss:.2f}")
        print(f"Profit Factor                         : {profit_factor}")
        print(f"Total Net Points                      : {overall_points:.2f}")
        print("=" * 120)

        # 4. Advanced Risk Metrics
        analyze_advanced_metrics(results_df)

    except Exception as exc:
        print(f"\nERROR: {exc}")
    finally:
        if smart_api:
            try:
                smart_api.terminateSession(CLIENT_CODE)
                print("\nSmartAPI session terminated.")
            except:
                pass


if __name__ == "__main__":
    main()
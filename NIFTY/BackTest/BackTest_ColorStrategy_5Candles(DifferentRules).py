import time as time_module
import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
from SmartApi import SmartConnect


# ============================================================
# CONFIGURATION
# ============================================================

# Set these environment variables before running:
# SMARTAPI_API_KEY
# SMARTAPI_CLIENT_CODE
# SMARTAPI_PASSWORD
# SMARTAPI_TOTP_SECRET

import os

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50
INTERVAL = "FIVE_MINUTE"

IST = ZoneInfo("Asia/Kolkata")

START_YEAR = 2015

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

# NSE opening candles
FIRST_FOUR_TIMES = ["09:15", "09:20", "09:25", "09:30"]

FOURTH_CANDLE_TIME = "09:30"
FIFTH_CANDLE_TIME = "09:35"

CANDLE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume"
]


# ============================================================
# 4TH-CANDLE PREDICTION RULES
# First 3 candles -> predict 4th candle
# ============================================================

RULES_4TH = {
    ("Green", "Red", "Green"): "Green",
    ("Green", "Red", "Red"): "Green",
    ("Green", "Green", "Red"): "Red",
    ("Red", "Green", "Red"): "Red",
    ("Red", "Red", "Red"): "Red",
    ("Red", "Green", "Green"): "Red",
    ("Red", "Red", "Green"): "Red",
}


# ============================================================
# 5TH-CANDLE PREDICTION RULES
# First 4 candles -> predict 5th candle
# ============================================================

RULES_5TH = {
    ("Red", "Green", "Red", "Green"): "Green",
    ("Red", "Red", "Green", "Green"): "Green",
    ("Red", "Red", "Red", "Red"): "Green",
    ("Green", "Green", "Green", "Red"): "Green",
    ("Red", "Green", "Red", "Red"): "Red",
    ("Green", "Red", "Green", "Green"): "Red",
    ("Red", "Red", "Green", "Red"): "Red",
    ("Red", "Red", "Red", "Green"): "Red",
    ("Red", "Green", "Green", "Red"): "Red",
    ("Green", "Green", "Green", "Green"): "Red",
    ("Green", "Red", "Red", "Green"): "Red",
    ("Green", "Red", "Red", "Red"): "Red",
    ("Green", "Green", "Red", "Red"): "Red",
    ("Green", "Red", "Green", "Red"): "Red",
    ("Red", "Green", "Green", "Green"): "Red",
}


# ============================================================
# CANDLE COLOR AND METRICS
# ============================================================

def candle_color(row):
    if row["close"] > row["open"]:
        return "Green"
    elif row["close"] < row["open"]:
        return "Red"
    return "Doji"


def add_candle_metrics(df):
    df = df.copy()

    for column in ["open", "high", "low", "close", "volume"]:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column], errors="coerce"
            )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).copy()

    df["range"] = df["high"] - df["low"]
    df["body"] = (df["close"] - df["open"]).abs()
    df["color"] = df.apply(candle_color, axis=1)

    return df


# ============================================================
# PREDICTION HELPERS
# ============================================================

def predict_fourth_candle(first, second, third):
    return RULES_4TH.get(
        (first, second, third),
        "No Prediction"
    )


def predict_fifth_candle(first, second, third, fourth):
    return RULES_5TH.get(
        (first, second, third, fourth),
        "No Prediction"
    )


def describe_rule(colors, predicted):
    pattern = " -> ".join(colors)

    if predicted == "No Prediction":
        return f"{pattern} = No Rule"

    return f"{pattern} = {predicted}"


def calculate_prediction_result(predicted, actual, candle):
    """
    Calculate prediction result and directional points.

    Green prediction:
        points = actual close - actual open

    Red prediction:
        points = actual open - actual close

    Doji candles are excluded from accuracy and contribute
    zero points.
    """

    if actual == "Missing":
        return "Missing Target Candle", None

    if predicted == "No Prediction":
        return "No Prediction", None

    if actual == "Doji":
        return "Doji", 0.0

    candle_move = float(candle["close"] - candle["open"])

    if predicted == "Green":
        points = candle_move
    else:
        points = -candle_move

    result = "Correct" if predicted == actual else "Incorrect"

    return result, points


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():
    if not all([
        API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET
    ]):
        raise ValueError(
            "Missing SmartAPI credentials. Set these environment "
            "variables before running:\n"
            "SMARTAPI_API_KEY\n"
            "SMARTAPI_CLIENT_CODE\n"
            "SMARTAPI_PASSWORD\n"
            "SMARTAPI_TOTP_SECRET"
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

            print(
                f"  Attempt {attempt}/{MAX_RETRIES} failed: "
                f"{last_error}"
            )
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

        return (
            pd.DataFrame(columns=CANDLE_COLUMNS),
            pd.DataFrame(fetch_status)
        )

    last_month = 12 if year < today.year else today.month

    for month in range(1, 13):
        month_name = calendar.month_name[month]

        if month > last_month:
            print(f"\nSkipping {month_name} {year}: future month.")

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

        start_dt = datetime(
            year, month, 1, 9, 0, tzinfo=IST
        )

        end_dt = datetime(
            year, month, last_day, 15, 30, tzinfo=IST
        )

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
            month_df = fetch_historical_data(
                smart_api, start_dt, end_dt
            )

            if month_df.empty:
                print("  WARNING: No candles returned.")

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
                month_df["timestamp"],
                errors="coerce"
            )

            month_df = month_df.dropna(
                subset=["timestamp"]
            ).copy()

            month_df["timestamp"] = month_df[
                "timestamp"
            ].apply(convert_to_ist)

            # Keep only the requested month and year.
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

    combined_df = pd.concat(
        all_data, ignore_index=True
    )

    combined_df = combined_df.drop_duplicates(
        subset=["timestamp"], keep="first"
    )

    combined_df = combined_df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    combined_df["timestamp"] = combined_df[
        "timestamp"
    ].apply(convert_to_ist)

    combined_df = combined_df[
        combined_df["timestamp"] <= now_ist
    ].copy()

    print(f"\nTotal candles for {year}: {len(combined_df)}")

    return combined_df, status_df


# ============================================================
# ANALYZE ONE TRADING DAY
# ============================================================

def analyze_day(trading_date, day_df):
    day_df = day_df.sort_values("timestamp").copy()

    # Get the first four opening candles.
    first_four = (
        day_df[
            day_df["candle_time"].isin(FIRST_FOUR_TIMES)
        ]
        .drop_duplicates(
            subset=["candle_time"], keep="first"
        )
        .set_index("candle_time")
        .reindex(FIRST_FOUR_TIMES)
    )

    colors = []

    for candle_time in FIRST_FOUR_TIMES:
        color = first_four.loc[candle_time, "color"]

        if pd.isna(color):
            color = "Missing"

        colors.append(color)

    color1, color2, color3, color4 = colors

    # Fifth candle is the target for the 5th-candle prediction.
    fifth_rows = day_df[
        day_df["candle_time"] == FIFTH_CANDLE_TIME
    ]

    fifth = None

    if not fifth_rows.empty:
        fifth = fifth_rows.iloc[0]

    actual_fifth = fifth["color"] if fifth is not None else "Missing"

    # Initialize result row.
    result_row = {
        "date": trading_date,
        "year": trading_date.year,
        "month": trading_date.month,

        "candle_1_color": color1,
        "candle_2_color": color2,
        "candle_3_color": color3,
        "candle_4_color": color4,

        # 4th candle prediction
        "4th_rule_applied": "",
        "predicted_4th_color": "No Prediction",
        "actual_4th_color": "Missing",
        "4th_result": "Incomplete",
        "4th_open": None,
        "4th_high": None,
        "4th_low": None,
        "4th_close": None,
        "4th_points": None,

        # 5th candle prediction
        "5th_rule_applied": "",
        "predicted_5th_color": "No Prediction",
        "actual_5th_color": actual_fifth,
        "5th_result": "Incomplete",
        "5th_open": None,
        "5th_high": None,
        "5th_low": None,
        "5th_close": None,
        "5th_points": None,
    }

    # --------------------------------------------------------
    # 4TH CANDLE PREDICTION
    # First 3 candles predict the 09:30 candle.
    # --------------------------------------------------------

    if "Missing" in [color1, color2, color3]:
        result_row["4th_rule_applied"] = (
            "Incomplete: missing first three candles"
        )
        result_row["4th_result"] = "Incomplete"

    else:
        predicted_4th = predict_fourth_candle(
            color1, color2, color3
        )

        result_row["predicted_4th_color"] = predicted_4th

        result_row["4th_rule_applied"] = describe_rule(
            [color1, color2, color3],
            predicted_4th
        )

        fourth_rows = day_df[
            day_df["candle_time"] == FOURTH_CANDLE_TIME
        ]

        if fourth_rows.empty:
            result_row["4th_result"] = "Missing Target Candle"

        else:
            fourth = fourth_rows.iloc[0]
            actual_4th = fourth["color"]

            result_row.update({
                "actual_4th_color": actual_4th,
                "4th_open": float(fourth["open"]),
                "4th_high": float(fourth["high"]),
                "4th_low": float(fourth["low"]),
                "4th_close": float(fourth["close"]),
            })

            result, points = calculate_prediction_result(
                predicted_4th,
                actual_4th,
                fourth
            )

            result_row["4th_result"] = result
            result_row["4th_points"] = points

    # --------------------------------------------------------
    # 5TH CANDLE PREDICTION
    # First 4 candles predict the 09:35 candle.
    # --------------------------------------------------------

    if "Missing" in [color1, color2, color3, color4]:
        result_row["5th_rule_applied"] = (
            "Incomplete: missing one or more first four candles"
        )
        result_row["5th_result"] = "Incomplete"

    else:
        predicted_5th = predict_fifth_candle(
            color1, color2, color3, color4
        )

        result_row["predicted_5th_color"] = predicted_5th

        result_row["5th_rule_applied"] = describe_rule(
            [color1, color2, color3, color4],
            predicted_5th
        )

        if fifth is None:
            result_row["5th_result"] = "Missing Target Candle"

        else:
            result_row.update({
                "actual_5th_color": actual_fifth,
                "5th_open": float(fifth["open"]),
                "5th_high": float(fifth["high"]),
                "5th_low": float(fifth["low"]),
                "5th_close": float(fifth["close"]),
            })

            result, points = calculate_prediction_result(
                predicted_5th,
                actual_fifth,
                fifth
            )

            result_row["5th_result"] = result
            result_row["5th_points"] = points

    return result_row


# ============================================================
# SUMMARY METRICS
# ============================================================

def summarize_predictions(df, result_column, points_column):
    """
    Accuracy denominator includes only Correct and Incorrect.

    Doji, incomplete days, missing target candles and
    No Prediction days are excluded from accuracy.
    """

    scored = df[
        df[result_column].isin(["Correct", "Incorrect"])
    ]

    correct = int(
        (scored[result_column] == "Correct").sum()
    )

    incorrect = int(
        (scored[result_column] == "Incorrect").sum()
    )

    total = correct + incorrect

    accuracy = correct / total * 100 if total else 0.0

    points = float(
        scored[points_column].fillna(0).sum()
    )

    return {
        "correct": correct,
        "incorrect": incorrect,
        "scored_predictions": total,
        "accuracy_percent": round(accuracy, 2),
        "total_points": round(points, 2),
        "doji": int((df[result_column] == "Doji").sum()),
        "incomplete": int(
            (df[result_column] == "Incomplete").sum()
        ),
        "missing_target_candle": int(
            (
                df[result_column] == "Missing Target Candle"
            ).sum()
        ),
        "no_prediction": int(
            (df[result_column] == "No Prediction").sum()
        ),
    }


# ============================================================
# BUILD MONTHLY SUMMARY
# ============================================================

def build_monthly_summary(results_df, fetch_status_df):
    monthly_rows = []

    years = sorted(
        set(results_df["year"].unique())
        | set(fetch_status_df["year"].unique())
    )

    for year in years:
        for month in range(1, 13):
            month_df = results_df[
                (results_df["year"] == year)
                & (results_df["month"] == month)
            ]

            status_row = fetch_status_df[
                (fetch_status_df["year"] == year)
                & (fetch_status_df["month"] == month)
            ]

            if not status_row.empty:
                status = status_row.iloc[0]["fetch_status"]

                candles_received = int(
                    status_row.iloc[0]["candles_received"]
                )
            else:
                status = "UNKNOWN"
                candles_received = 0

            summary_4th = summarize_predictions(
                month_df,
                "4th_result",
                "4th_points"
            )

            summary_5th = summarize_predictions(
                month_df,
                "5th_result",
                "5th_points"
            )

            row = {
                "year": year,
                "month": month,
                "month_name": calendar.month_name[month],
                "fetch_status": status,
                "candles_received": candles_received,
                "trading_days_analyzed": len(month_df),
            }

            for key, value in summary_4th.items():
                row[f"4th_{key}"] = value

            for key, value in summary_5th.items():
                row[f"5th_{key}"] = value

            monthly_rows.append(row)

    return pd.DataFrame(monthly_rows)


# ============================================================
# BUILD YEARLY SUMMARY
# ============================================================

def build_yearly_summary(results_df):
    yearly_rows = []

    if results_df.empty:
        return pd.DataFrame()

    for year in sorted(results_df["year"].unique()):
        year_df = results_df[
            results_df["year"] == year
        ]

        summary_4th = summarize_predictions(
            year_df,
            "4th_result",
            "4th_points"
        )

        summary_5th = summarize_predictions(
            year_df,
            "5th_result",
            "5th_points"
        )

        row = {
            "year": year,
            "trading_days_analyzed": len(year_df),
        }

        for key, value in summary_4th.items():
            row[f"4th_{key}"] = value

        for key, value in summary_5th.items():
            row[f"5th_{key}"] = value

        yearly_rows.append(row)

    return pd.DataFrame(yearly_rows)


# ============================================================
# MAIN
# ============================================================

def main():
    smart_api = None

    try:
        now_ist = datetime.now(IST)
        current_year = now_ist.year

        print("\n" + "=" * 100)
        print("NIFTY 50 FOURTH AND FIFTH CANDLE HISTORICAL ANALYSIS")
        print("=" * 100)
        print(f"Start year       : {START_YEAR}")
        print(f"Current year     : {current_year}")
        print(f"Current IST time : {now_ist}")
        print(f"Analysis period  : {START_YEAR} to {current_year}")
        print("=" * 100)

        smart_api = login_to_smartapi()
        print("\nSmartAPI login successful.")

        all_daily_results = []
        all_fetch_status = []

        # ----------------------------------------------------
        # FETCH AND ANALYZE EVERY YEAR
        # ----------------------------------------------------

        for year in range(START_YEAR, current_year + 1):
            raw_df, fetch_status_df = fetch_year_historical_data(
                smart_api, year, now_ist
            )

            all_fetch_status.append(fetch_status_df)

            if raw_df.empty:
                print(
                    f"\nWARNING: No candles retrieved for {year}."
                )
                continue

            df = add_candle_metrics(raw_df)

            df["timestamp"] = df["timestamp"].apply(convert_to_ist)

            df = df.sort_values("timestamp").copy()

            df = df[
                (df["timestamp"].dt.year == year)
                & (df["timestamp"] <= now_ist)
            ].copy()

            df["candle_time"] = df[
                "timestamp"
            ].dt.strftime("%H:%M")

            df["trading_date"] = df[
                "timestamp"
            ].dt.date

            daily_results = []

            for trading_date, day_df in df.groupby("trading_date"):
                daily_results.append(
                    analyze_day(trading_date, day_df)
                )

            if daily_results:
                year_results = pd.DataFrame(daily_results)

                all_daily_results.append(year_results)

                print(f"\nAnalysis completed for {year}.")
                print(f"Trading dates: {len(year_results)}")

        # ----------------------------------------------------
        # COMBINE FETCH STATUS IN MEMORY
        # ----------------------------------------------------

        if all_fetch_status:
            fetch_status_df = pd.concat(
                all_fetch_status,
                ignore_index=True
            )
        else:
            fetch_status_df = pd.DataFrame()

        if not fetch_status_df.empty:
            fetch_status_df = fetch_status_df.sort_values(
                ["year", "month"]
            ).reset_index(drop=True)

        # No fetch-status CSV is written.

        # ----------------------------------------------------
        # CHECK ANALYSIS RESULTS
        # ----------------------------------------------------

        if not all_daily_results:
            print(
                "\nNo historical analysis results available. "
                "Check credentials, API access, and data availability."
            )
            return

        results_df = pd.concat(
            all_daily_results,
            ignore_index=True
        )

        results_df["date"] = pd.to_datetime(
            results_df["date"]
        )

        results_df = results_df.sort_values(
            "date"
        ).reset_index(drop=True)

        # No daily results CSV is written.
        # No rules report CSV is written.

        # ----------------------------------------------------
        # MONTHLY SUMMARY
        # ----------------------------------------------------

        monthly_df = build_monthly_summary(
            results_df,
            fetch_status_df
        )

        # No monthly summary CSV is written.

        print("\n" + "=" * 150)
        print("MONTHLY ACCURACY AND POINTS")
        print("=" * 150)

        monthly_columns = [
            "year",
            "month_name",
            "candles_received",
            "trading_days_analyzed",
            "4th_correct",
            "4th_incorrect",
            "4th_accuracy_percent",
            "4th_total_points",
            "5th_correct",
            "5th_incorrect",
            "5th_accuracy_percent",
            "5th_total_points",
        ]

        print(
            monthly_df[monthly_columns].to_string(index=False)
        )

        # ----------------------------------------------------
        # YEARLY SUMMARY
        # ----------------------------------------------------

        yearly_df = build_yearly_summary(results_df)

        # No yearly summary CSV is written.

        print("\n" + "=" * 150)
        print("YEARLY ACCURACY AND POINTS")
        print("=" * 150)

        print(yearly_df.to_string(index=False))

        # ----------------------------------------------------
        # OVERALL SUMMARY
        # ----------------------------------------------------

        summary_4th = summarize_predictions(
            results_df,
            "4th_result",
            "4th_points"
        )

        summary_5th = summarize_predictions(
            results_df,
            "5th_result",
            "5th_points"
        )

        print("\n" + "=" * 90)
        print(f"OVERALL SUMMARY: {START_YEAR} TO {current_year}")
        print("=" * 90)

        print("\n--- 4TH CANDLE PREDICTION (09:30) ---")
        print(f"Correct predictions      : {summary_4th['correct']}")
        print(f"Incorrect predictions    : {summary_4th['incorrect']}")
        print(f"Scored predictions       : {summary_4th['scored_predictions']}")
        print(f"Overall accuracy         : {summary_4th['accuracy_percent']:.2f}%")
        print(f"Total directional points : {summary_4th['total_points']:.2f}")
        print(f"Doji candles             : {summary_4th['doji']}")
        print(f"Incomplete days          : {summary_4th['incomplete']}")
        print(f"Missing target candles   : {summary_4th['missing_target_candle']}")
        print(f"No prediction days       : {summary_4th['no_prediction']}")

        print("\n--- 5TH CANDLE PREDICTION (09:35) ---")
        print(f"Correct predictions      : {summary_5th['correct']}")
        print(f"Incorrect predictions    : {summary_5th['incorrect']}")
        print(f"Scored predictions       : {summary_5th['scored_predictions']}")
        print(f"Overall accuracy         : {summary_5th['accuracy_percent']:.2f}%")
        print(f"Total directional points : {summary_5th['total_points']:.2f}")
        print(f"Doji candles             : {summary_5th['doji']}")
        print(f"Incomplete days          : {summary_5th['incomplete']}")
        print(f"Missing target candles   : {summary_5th['missing_target_candle']}")
        print(f"No prediction days       : {summary_5th['no_prediction']}")

        print(f"\nTrading dates analyzed   : {len(results_df)}")

        # ----------------------------------------------------
        # WARN ABOUT MISSING MONTHS
        # ----------------------------------------------------

        if not fetch_status_df.empty:
            failed_months = fetch_status_df[
                fetch_status_df["fetch_status"].isin(
                    ["FAILED", "NO DATA"]
                )
            ]

            if not failed_months.empty:
                print(
                    "\nWARNING: Some months have missing historical data."
                )

                print(
                    failed_months[
                        [
                            "year",
                            "month_name",
                            "fetch_status",
                            "candles_received",
                            "error",
                        ]
                    ].to_string(index=False)
                )

                print(
                    "\nAccuracy and points may be incomplete until "
                    "the missing data has been retrieved."
                )

        print("\nAll analysis completed. No CSV files were created.")

    except Exception as exc:
        print(f"\nERROR: {exc}")

    finally:
        if smart_api is not None:
            try:
                smart_api.terminateSession(CLIENT_CODE)
                print("\nSmartAPI session terminated.")
            except Exception as exc:
                print(
                    f"Could not terminate session: {exc}"
                )


if __name__ == "__main__":
    main()
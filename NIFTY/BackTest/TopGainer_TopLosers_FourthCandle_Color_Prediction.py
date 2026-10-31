import time as time_module
import calendar
from datetime import datetime
from zoneinfo import ZoneInfo
import os

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

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

EXCHANGE = "NSE"
SYMBOL_TOKEN = "1594"  # NIFTY 50
INTERVAL = "FIVE_MINUTE"

IST = ZoneInfo("Asia/Kolkata")

# Analyze every year starting from 2015.
START_YEAR = 2026

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

FIRST_THREE_TIMES = ["09:15", "09:20", "09:25"]
FOURTH_CANDLE_TIME = "09:30"

CANDLE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume"
]


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
# THREE-CANDLE PREDICTION RULES
# ============================================================

'''
("Green", "Red", "Green") => Predict GREEN when:

    1. 3rd Green candle has a strong bullish body                       => Mandatory
    2. 3rd Green closes above the 2nd Red candle's high                 => Mandatory
    3. 3rd candle has a small upper wick                                => Supporting
    4. 2nd Red candle has a weak body                                   => Supporting
    5. 1st Green candle has a strong body                               => Supporting


("Green", "Red", "Red") => Predict GREEN when:

    1. 3rd candle closes above the 2nd candle's low                     => Mandatory
    2. 3rd Red has a strong lower wick                                  => Mandatory
    3. 2nd Red has a strong lower wick                                  => Supporting
    4. 3rd Red has a small bearish body                                 => Supporting
    5. 3rd Red body is smaller than the 2nd Red body                    => Supporting


("Green", "Green", "Green") => Predict GREEN when:

    1. 3rd Green has a strong bullish body                              => Mandatory
    2. 3rd Green closes above the 2nd Green high                        => Mandatory
    3. 3rd Green has a small upper wick                                 => Supporting
    4. 2nd Green has a strong bullish body                              => Supporting
'''

RULES = {
    # ("Red", "Red", "Green"): "Red",
    # ("Green", "Green", "Red"): "Red",
    # ("Red", "Green", "Red"): "Red",
    # ("Red", "Red", "Red"): "Red",
    # ("Green", "Green", "Green"): "Red",
    # ("Red", "Green", "Green"): "Red",

    ("Green", "Red", "Green"): "Green",
    ("Green", "Red", "Red"): "Green",
    ("Green", "Green", "Green"): "Green",
}


def predict_fourth_candle(first, second, third):
    return RULES.get(
        (first, second, third),
        "No Prediction"
    )


def describe_rule(first, second, third, predicted):
    pattern = f"{first} -> {second} -> {third}"

    if predicted == "No Prediction":
        return f"{pattern} = No Rule"

    return f"{pattern} = {predicted}"


# ============================================================
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():

    if not all([
        API_KEY,
        CLIENT_CODE,
        PASSWORD,
        TOTP_SECRET
    ]):
        raise ValueError(
            "Missing credentials. Set these environment variables:\n"
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

            delay = RETRY_BASE_SECONDS * (
                2 ** (attempt - 1)
            )

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
                smart_api,
                start_dt,
                end_dt
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

        return (
            pd.DataFrame(columns=CANDLE_COLUMNS),
            status_df
        )

    combined_df = pd.concat(
        all_data,
        ignore_index=True
    )

    combined_df = combined_df.drop_duplicates(
        subset=["timestamp"],
        keep="first"
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

    first_three = (
        day_df[
            day_df["candle_time"].isin(FIRST_THREE_TIMES)
        ]
        .drop_duplicates(
            subset=["candle_time"],
            keep="first"
        )
        .set_index("candle_time")
        .reindex(FIRST_THREE_TIMES)
    )

    colors = []

    for candle_time in FIRST_THREE_TIMES:

        color = first_three.loc[candle_time, "color"]

        if pd.isna(color):
            color = "Missing"

        colors.append(color)

    color1, color2, color3 = colors

    result_row = {
        "date": trading_date,
        "year": trading_date.year,
        "month": trading_date.month,
        "candle_1_color": color1,
        "candle_2_color": color2,
        "candle_3_color": color3,
        "rule_applied": "Incomplete: missing first three candles",
        "predicted_4th_color": "No Prediction",
        "actual_4th_color": "Missing",
        "result": "Incomplete",
        "4th_open": None,
        "4th_high": None,
        "4th_low": None,
        "4th_close": None,
        "points": None,
    }

    if "Missing" in colors:
        return result_row

    predicted = predict_fourth_candle(
        color1,
        color2,
        color3
    )

    result_row["predicted_4th_color"] = predicted

    result_row["rule_applied"] = describe_rule(
        color1,
        color2,
        color3,
        predicted
    )

    fourth_rows = day_df[
        day_df["candle_time"] == FOURTH_CANDLE_TIME
    ]

    if fourth_rows.empty:

        result_row["result"] = "Missing Fourth Candle"

        return result_row

    fourth = fourth_rows.iloc[0]

    actual = fourth["color"]

    result_row.update({
        "actual_4th_color": actual,
        "4th_open": float(fourth["open"]),
        "4th_high": float(fourth["high"]),
        "4th_low": float(fourth["low"]),
        "4th_close": float(fourth["close"]),
    })

    if predicted == "No Prediction":

        result_row["result"] = "No Prediction"

        return result_row

    # Doji candles are excluded from accuracy and points.
    if actual == "Doji":

        result_row["result"] = "Doji"
        result_row["points"] = 0.0

        return result_row

    candle_move = float(
        fourth["close"] - fourth["open"]
    )

    points = (
        candle_move
        if predicted == "Green"
        else -candle_move
    )

    result_row["points"] = points

    result_row["result"] = (
        "Correct" if predicted == actual else "Incorrect"
    )

    return result_row


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

            scored = month_df[
                month_df["result"].isin(
                    ["Correct", "Incorrect"]
                )
            ]

            correct = int(
                (scored["result"] == "Correct").sum()
            )

            incorrect = int(
                (scored["result"] == "Incorrect").sum()
            )

            scored_total = correct + incorrect

            accuracy = (
                correct / scored_total * 100
                if scored_total else 0.0
            )

            total_points = float(
                scored["points"].fillna(0).sum()
            )

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

            monthly_rows.append({

                "year": year,
                "month": month,
                "month_name": calendar.month_name[month],

                "fetch_status": status,
                "candles_received": candles_received,

                "trading_days_analyzed": len(month_df),

                "correct": correct,
                "incorrect": incorrect,

                "scored_predictions": scored_total,

                "accuracy_percent": round(accuracy, 2),

                "total_points": round(total_points, 2),

                "doji": int(
                    (month_df["result"] == "Doji").sum()
                ),

                "incomplete": int(
                    (month_df["result"] == "Incomplete").sum()
                ),

                "missing_fourth_candle": int(
                    (
                        month_df["result"]
                        == "Missing Fourth Candle"
                    ).sum()
                ),

                "no_prediction": int(
                    (month_df["result"] == "No Prediction").sum()
                ),
            })

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

        scored = year_df[
            year_df["result"].isin(
                ["Correct", "Incorrect"]
            )
        ]

        correct = int(
            (scored["result"] == "Correct").sum()
        )

        incorrect = int(
            (scored["result"] == "Incorrect").sum()
        )

        total = correct + incorrect

        accuracy = (
            correct / total * 100
            if total else 0.0
        )

        points = float(
            scored["points"].fillna(0).sum()
        )

        yearly_rows.append({

            "year": year,

            "trading_days_analyzed": len(year_df),

            "correct": correct,
            "incorrect": incorrect,

            "scored_predictions": total,

            "accuracy_percent": round(accuracy, 2),

            "total_points": round(points, 2),

            "doji": int(
                (year_df["result"] == "Doji").sum()
            ),

            "incomplete": int(
                (year_df["result"] == "Incomplete").sum()
            ),

            "missing_fourth_candle": int(
                (
                    year_df["result"]
                    == "Missing Fourth Candle"
                ).sum()
            ),

            "no_prediction": int(
                (year_df["result"] == "No Prediction").sum()
            ),
        })

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
        print("NIFTY 50 FOURTH CANDLE HISTORICAL ANALYSIS")
        print("=" * 100)

        print(f"Start year       : {START_YEAR}")
        print(f"Current year     : {current_year}")
        print(f"Current IST time : {now_ist}")

        print(
            f"Analysis period  : {START_YEAR} to {current_year}"
        )

        print("=" * 100)

        smart_api = login_to_smartapi()

        print("\nSmartAPI login successful.")

        all_daily_results = []
        all_fetch_status = []

        # ----------------------------------------------------
        # FETCH AND ANALYZE EVERY YEAR FROM 2015 ONWARD
        # ----------------------------------------------------

        for year in range(START_YEAR, current_year + 1):

            raw_df, fetch_status_df = fetch_year_historical_data(
                smart_api,
                year,
                now_ist
            )

            all_fetch_status.append(fetch_status_df)

            if raw_df.empty:

                print(
                    f"\nWARNING: No candles retrieved for {year}."
                )

                continue

            df = add_candle_metrics(raw_df)

            df["timestamp"] = df["timestamp"].apply(
                convert_to_ist
            )

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
        # COMBINE FETCH STATUS IN MEMORY ONLY
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

        # ----------------------------------------------------
        # CHECK WHETHER ANY ANALYSIS RESULTS EXIST
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

        # ----------------------------------------------------
        # MONTHLY SUMMARY
        # ----------------------------------------------------

        monthly_df = build_monthly_summary(
            results_df,
            fetch_status_df
        )

        print("\n" + "=" * 130)
        print("MONTHLY ACCURACY AND POINTS")
        print("=" * 130)

        monthly_columns = [
            "year",
            "month_name",
            "candles_received",
            "trading_days_analyzed",
            "correct",
            "incorrect",
            "accuracy_percent",
            "total_points",
        ]

        print(
            monthly_df[monthly_columns].to_string(index=False)
        )

        # ----------------------------------------------------
        # YEARLY SUMMARY
        # ----------------------------------------------------

        yearly_df = build_yearly_summary(results_df)

        print("\n" + "=" * 130)
        print("YEARLY ACCURACY AND POINTS")
        print("=" * 130)

        print(
            yearly_df.to_string(index=False)
        )

        # ----------------------------------------------------
        # OVERALL SUMMARY: 2015 THROUGH CURRENT YEAR
        # ----------------------------------------------------

        scored = results_df[
            results_df["result"].isin(
                ["Correct", "Incorrect"]
            )
        ]

        correct_count = int(
            (scored["result"] == "Correct").sum()
        )

        incorrect_count = int(
            (scored["result"] == "Incorrect").sum()
        )

        scored_total = correct_count + incorrect_count

        overall_accuracy = (
            correct_count / scored_total * 100
            if scored_total else 0.0
        )

        overall_points = float(
            scored["points"].fillna(0).sum()
        )

        print("\n" + "=" * 90)

        print(
            f"OVERALL SUMMARY: {START_YEAR} TO {current_year}"
        )

        print("=" * 90)

        print(
            f"Correct predictions      : {correct_count}"
        )

        print(
            f"Incorrect predictions    : {incorrect_count}"
        )

        print(
            f"Scored predictions       : {scored_total}"
        )

        print(
            f"Overall accuracy         : {overall_accuracy:.2f}%"
        )

        print(
            f"Total directional points : {overall_points:.2f}"
        )

        print(
            f"Trading dates analyzed   : {len(results_df)}"
        )

        print(
            f"Doji candles             : "
            f"{(results_df['result'] == 'Doji').sum()}"
        )

        print(
            f"Incomplete days          : "
            f"{(results_df['result'] == 'Incomplete').sum()}"
        )

        print(
            f"Missing fourth candles   : "
            f"{(results_df['result'] == 'Missing Fourth Candle').sum()}"
        )

        print(
            f"No prediction days       : "
            f"{(results_df['result'] == 'No Prediction').sum()}"
        )

        # ----------------------------------------------------
        # WARN ABOUT MISSING MONTHS
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # COMPLETED
        # ----------------------------------------------------

        print("\n" + "=" * 90)
        print("HISTORICAL ANALYSIS COMPLETED")
        print("=" * 90)

        print("All summaries printed successfully.")
        print("No output files were generated.")

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


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":
    main()
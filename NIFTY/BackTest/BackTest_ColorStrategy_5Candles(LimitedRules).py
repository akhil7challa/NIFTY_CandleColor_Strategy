
import os
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

API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

EXCHANGE = "NSE"
SYMBOL_TOKEN = "99926000"  # NIFTY 50
INTERVAL = "FIVE_MINUTE"

IST = ZoneInfo("Asia/Kolkata")

START_DATE = datetime(2015, 1, 1, 0, 0, tzinfo=IST)

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

FIRST_THREE_TIMES = ["09:15", "09:20", "09:25"]
FOURTH_CANDLE_TIME = "09:30"
FIFTH_CANDLE_TIME = "09:35"

CANDLE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume"
]


# ============================================================
# PREDICTION RULES
# ============================================================

#NOTE : The following rules have been removed from FOURTH\FIFTH candle prediction. The removed patterns are: 
    #("Green", "Green", "Green"): "Red", form both 4th and 5th candle prediction
    #("Red", "Red", "Red"): "Red", form 5th candle prediction
    #("Green", "Red", "Red"): "Green", form 5th candle prediction
    #Overall ACCURACY : 55.91%
    #Overall Directional points : +9,081.86


# Rules used to predict the FOURTH candle.
#
# Removed from fourth-candle prediction:
# ("Green", "Green", "Green"): "Red"

RULES_4TH = {
    ("Red", "Red", "Green"): "Red",
    ("Green", "Green", "Red"): "Red",
    ("Red", "Green", "Red"): "Red",
    ("Green", "Red", "Green"): "Green",
    ("Red", "Red", "Red"): "Red",
    ("Red", "Green", "Green"): "Red",
    ("Green", "Red", "Red"): "Green",
}

# Rules used to predict the FIFTH candle.
# These are evaluated only after the fourth prediction is correct.
#
# Removed from fifth-candle prediction:
# ("Green", "Green", "Green"): "Red"
# ("Red", "Red", "Red"): "Red"
# ("Green", "Red", "Red"): "Green"

RULES_5TH = {
    ("Red", "Red", "Green"): "Red",
    ("Green", "Green", "Red"): "Red",
    ("Red", "Green", "Red"): "Red",
    ("Green", "Red", "Green"): "Green",
    ("Red", "Green", "Green"): "Red",
}


def predict_fourth_candle(first, second, third):
    return RULES_4TH.get(
        (first, second, third),
        "No Prediction"
    )


def predict_fifth_candle(first, second, third):
    return RULES_5TH.get(
        (first, second, third),
        "No Prediction"
    )


def describe_rule(first, second, third, predicted):
    pattern = f"{first} -> {second} -> {third}"

    if predicted == "No Prediction":
        return f"{pattern} = No Rule"

    return f"{pattern} = {predicted}"


# ============================================================
# CANDLE METRICS
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
# SMARTAPI LOGIN
# ============================================================

def login_to_smartapi():
    missing = []

    if not API_KEY:
        missing.append("SMARTAPI_API_KEY")
    if not CLIENT_CODE:
        missing.append("SMARTAPI_CLIENT_CODE")
    if not PASSWORD:
        missing.append("SMARTAPI_PASSWORD")
    if not TOTP_SECRET:
        missing.append("SMARTAPI_TOTP_SECRET")

    if missing:
        raise ValueError(
            "Missing environment variables: " + ", ".join(missing)
        )

    smart_api = SmartConnect(api_key=API_KEY)
    totp = pyotp.TOTP(TOTP_SECRET).now()

    session_data = smart_api.generateSession(
        CLIENT_CODE, PASSWORD, totp
    )

    if not session_data or not session_data.get("status"):
        raise RuntimeError(f"SmartAPI login failed: {session_data}")

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

                return pd.DataFrame(data, columns=CANDLE_COLUMNS)

            last_error = RuntimeError(
                f"Historical API response: {response}"
            )

        except Exception as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_SECONDS * (2 ** (attempt - 1))

            print(
                f"  Attempt {attempt}/{MAX_RETRIES} failed: {last_error}"
            )
            print(f"  Retrying in {delay} seconds...")
            time_module.sleep(delay)

    raise RuntimeError(
        f"Failed after {MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# FETCH DATA MONTH BY MONTH
# No files are written.
# ============================================================

def fetch_historical_range(smart_api, start_date):
    all_data = []
    fetch_status = []

    now_ist = datetime.now(IST)
    today = now_ist.date()

    periods = []
    year, month = start_date.year, start_date.month

    while (year, month) <= (today.year, today.month):
        periods.append((year, month))

        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    print("\n" + "=" * 90)
    print(f"FETCHING NIFTY 50 DATA: {start_date.date()} TO {today}")
    print(f"Current IST time: {now_ist}")
    print("=" * 90)

    for year, month in periods:
        month_name = calendar.month_name[month]
        last_day = calendar.monthrange(year, month)[1]

        start_dt = datetime(year, month, 1, 9, 0, tzinfo=IST)
        end_dt = datetime(year, month, last_day, 15, 30, tzinfo=IST)

        if (year, month) == (start_date.year, start_date.month):
            start_dt = max(start_dt, start_date)

        if (year, month) == (now_ist.year, now_ist.month):
            end_dt = min(end_dt, now_ist)

        print(f"\nFetching {month_name} {year}...")
        status_base = {
            "year": year,
            "month": month,
            "month_name": month_name,
        }

        try:
            month_df = fetch_historical_data(
                smart_api, start_dt, end_dt
            )

            if month_df.empty:
                print("  WARNING: No candles returned.")
                fetch_status.append({
                    **status_base,
                    "fetch_status": "NO DATA",
                    "candles_received": 0,
                    "error": "",
                })
                continue

            month_df["timestamp"] = pd.to_datetime(
                month_df["timestamp"], errors="coerce"
            )
            month_df = month_df.dropna(
                subset=["timestamp"]
            ).copy()

            month_df["timestamp"] = (
                month_df["timestamp"].apply(convert_to_ist)
            )

            month_df = month_df[
                (month_df["timestamp"].dt.year == year)
                & (month_df["timestamp"].dt.month == month)
                & (month_df["timestamp"] >= start_date)
                & (month_df["timestamp"] <= end_dt)
            ].copy()

            if month_df.empty:
                print("  No candles remained after date filtering.")
                fetch_status.append({
                    **status_base,
                    "fetch_status": "NO DATA",
                    "candles_received": 0,
                    "error": "",
                })
                continue

            print(f"  Candles received: {len(month_df)}")
            all_data.append(month_df)

            fetch_status.append({
                **status_base,
                "fetch_status": "SUCCESS",
                "candles_received": len(month_df),
                "error": "",
            })

        except Exception as exc:
            print(f"  ERROR: {exc}")

            fetch_status.append({
                **status_base,
                "fetch_status": "FAILED",
                "candles_received": 0,
                "error": str(exc),
            })

    status_df = pd.DataFrame(fetch_status)

    print("\n" + "=" * 90)
    print("FETCH STATUS")
    print("=" * 90)

    if not status_df.empty:
        print(status_df.to_string(index=False))

    if not all_data:
        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    combined_df = pd.concat(all_data, ignore_index=True)

    combined_df = combined_df.drop_duplicates(
        subset=["timestamp"], keep="first"
    )

    combined_df = combined_df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    combined_df["timestamp"] = (
        combined_df["timestamp"].apply(convert_to_ist)
    )

    combined_df = combined_df[
        (combined_df["timestamp"] >= start_date)
        & (combined_df["timestamp"] <= now_ist)
    ].copy()

    print(f"\nTotal candles retrieved: {len(combined_df)}")

    if not combined_df.empty:
        print(f"First candle: {combined_df['timestamp'].min()}")
        print(f"Last candle : {combined_df['timestamp'].max()}")

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
        .drop_duplicates(subset=["candle_time"], keep="first")
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
    pattern = (color1, color2, color3)

    result = {
        "date": trading_date,
        "year": trading_date.year,
        "month": trading_date.month,
        "candle_1_color": color1,
        "candle_2_color": color2,
        "candle_3_color": color3,
        "pattern": " -> ".join(colors),
        "rule_applied": "Incomplete: missing first three candles",
        "predicted_4th_color": "No Prediction",
        "actual_4th_color": "Missing",
        "result": "Incomplete",
        "4th_open": None,
        "4th_high": None,
        "4th_low": None,
        "4th_close": None,
        "predicted_5th_color": "Not Attempted",
        "actual_5th_color": "Missing",
        "5th_result": "Not Attempted",
        "5th_open": None,
        "5th_high": None,
        "5th_low": None,
        "5th_close": None,
        "5th_points": 0.0,
        "points": 0.0,
    }

    if "Missing" in colors:
        return result

    predicted4 = predict_fourth_candle(*pattern)
    result["predicted_4th_color"] = predicted4
    result["rule_applied"] = describe_rule(
        *pattern, predicted4
    )

    fourth_rows = day_df[
        day_df["candle_time"] == FOURTH_CANDLE_TIME
    ]

    if fourth_rows.empty:
        result["result"] = "Missing Fourth Candle"
        return result

    fourth = fourth_rows.iloc[0]
    actual4 = fourth["color"]

    result.update({
        "actual_4th_color": actual4,
        "4th_open": float(fourth["open"]),
        "4th_high": float(fourth["high"]),
        "4th_low": float(fourth["low"]),
        "4th_close": float(fourth["close"]),
    })

    if predicted4 == "No Prediction":
        result["result"] = "No Prediction"
        return result

    if actual4 == "Doji":
        result["result"] = "Doji"
        return result

    move4 = float(fourth["close"] - fourth["open"])
    points4 = move4 if predicted4 == "Green" else -move4
    result["points"] = points4

    result["result"] = (
        "Correct" if predicted4 == actual4 else "Incorrect"
    )

    # Fifth-candle analysis only follows a correct fourth prediction.
    if result["result"] != "Correct":
        result["5th_result"] = "Stopped: fourth prediction incorrect"
        return result

    predicted5 = predict_fifth_candle(*pattern)
    result["predicted_5th_color"] = predicted5

    # Removed fifth-candle rules must not produce a prediction.
    if predicted5 == "No Prediction":
        result["5th_result"] = "No Prediction: rule excluded"
        return result

    fifth_rows = day_df[
        day_df["candle_time"] == FIFTH_CANDLE_TIME
    ]

    if fifth_rows.empty:
        result["5th_result"] = "Missing Fifth Candle"
        return result

    fifth = fifth_rows.iloc[0]
    actual5 = fifth["color"]

    result.update({
        "actual_5th_color": actual5,
        "5th_open": float(fifth["open"]),
        "5th_high": float(fifth["high"]),
        "5th_low": float(fifth["low"]),
        "5th_close": float(fifth["close"]),
    })

    if actual5 == "Doji":
        result["5th_result"] = "Doji"
        return result

    move5 = float(fifth["close"] - fifth["open"])
    points5 = move5 if predicted5 == "Green" else -move5

    result["5th_points"] = points5
    result["points"] += points5

    result["5th_result"] = (
        "Correct" if predicted5 == actual5 else "Incorrect"
    )

    return result


# ============================================================
# ACCURACY REPORT FOR EACH RULE
# ============================================================

def print_rule_accuracy(results_df, rule_dict, candle_number):
    print("\n" + "=" * 115)
    print(f"CANDLE {candle_number} RULE-BY-RULE ACCURACY")
    print("=" * 115)

    result_column = (
        "result" if candle_number == 4 else "5th_result"
    )
    prediction_column = (
        "predicted_4th_color"
        if candle_number == 4
        else "predicted_5th_color"
    )
    points_column = "points" if candle_number == 4 else "5th_points"

    rows = []

    for pattern, prediction in rule_dict.items():
        pattern_text = " -> ".join(pattern)

        eligible = results_df[
            (results_df["pattern"] == pattern_text)
            & (results_df[prediction_column] == prediction)
            & (results_df[result_column].isin(["Correct", "Incorrect"]))
        ]

        correct = int((eligible[result_column] == "Correct").sum())
        incorrect = int((eligible[result_column] == "Incorrect").sum())
        total = correct + incorrect
        accuracy = correct / total * 100 if total else 0.0
        points = float(eligible[points_column].fillna(0).sum())

        rows.append({
            "Pattern": pattern_text,
            "Prediction": prediction,
            "Correct": correct,
            "Incorrect": incorrect,
            "Scored": total,
            "Accuracy %": round(accuracy, 2),
            "Directional Points": round(points, 2),
        })

    report = pd.DataFrame(rows)

    if report.empty:
        print("No rule results available.")
    else:
        print(report.to_string(index=False))

    scored = results_df[
        results_df[result_column].isin(["Correct", "Incorrect"])
    ]

    correct_total = int((scored[result_column] == "Correct").sum())
    incorrect_total = int((scored[result_column] == "Incorrect").sum())
    total = correct_total + incorrect_total
    accuracy = correct_total / total * 100 if total else 0.0

    print("-" * 115)
    print(
        f"CANDLE {candle_number} OVERALL: "
        f"Correct={correct_total}, Incorrect={incorrect_total}, "
        f"Scored={total}, Accuracy={accuracy:.2f}%"
    )


# ============================================================
# MONTHLY SUMMARY
# ============================================================

def print_monthly_summary(results_df, fetch_status_df):
    print("\n" + "=" * 145)
    print("MONTHLY SUMMARY")
    print("=" * 145)

    status_lookup = {}

    if not fetch_status_df.empty:
        for _, row in fetch_status_df.iterrows():
            status_lookup[(int(row["year"]), int(row["month"]))] = row

    periods = sorted(set(
        zip(results_df["year"].astype(int), results_df["month"].astype(int))
    ))

    rows = []

    for year, month in periods:
        month_df = results_df[
            (results_df["year"] == year)
            & (results_df["month"] == month)
        ]

        scored4 = month_df[
            month_df["result"].isin(["Correct", "Incorrect"])
        ]

        correct4 = int((scored4["result"] == "Correct").sum())
        incorrect4 = int((scored4["result"] == "Incorrect").sum())
        total4 = correct4 + incorrect4
        accuracy4 = correct4 / total4 * 100 if total4 else 0.0

        scored5 = month_df[
            month_df["5th_result"].isin(["Correct", "Incorrect"])
        ]

        correct5 = int((scored5["5th_result"] == "Correct").sum())
        incorrect5 = int((scored5["5th_result"] == "Incorrect").sum())
        total5 = correct5 + incorrect5
        accuracy5 = correct5 / total5 * 100 if total5 else 0.0

        status_row = status_lookup.get((year, month))
        fetch_status = (
            status_row["fetch_status"] if status_row is not None
            else "UNKNOWN"
        )
        candles = (
            int(status_row["candles_received"])
            if status_row is not None else 0
        )

        rows.append({
            "Year": year,
            "Month": calendar.month_name[month],
            "Fetch Status": fetch_status,
            "Candles": candles,
            "Days Analyzed": len(month_df),
            "4th Correct": correct4,
            "4th Incorrect": incorrect4,
            "4th Accuracy %": round(accuracy4, 2),
            "4th Points": round(
                scored4["points"].fillna(0).sum(), 2
            ),
            "5th Correct": correct5,
            "5th Incorrect": incorrect5,
            "5th Accuracy %": round(accuracy5, 2),
            "5th Points": round(
                scored5["5th_points"].fillna(0).sum(), 2
            ),
        })

    if rows:
        print(pd.DataFrame(rows).to_string(index=False))
    else:
        print("No monthly results available.")


# ============================================================
# OVERALL SUMMARY
# ============================================================

def print_overall_summary(results_df):
    print("\n" + "=" * 90)
    print("OVERALL SUMMARY")
    print("=" * 90)

    scored4 = results_df[
        results_df["result"].isin(["Correct", "Incorrect"])
    ]

    correct4 = int((scored4["result"] == "Correct").sum())
    incorrect4 = int((scored4["result"] == "Incorrect").sum())
    total4 = correct4 + incorrect4
    accuracy4 = correct4 / total4 * 100 if total4 else 0.0
    points4 = float(scored4["points"].fillna(0).sum())

    scored5 = results_df[
        results_df["5th_result"].isin(["Correct", "Incorrect"])
    ]

    correct5 = int((scored5["5th_result"] == "Correct").sum())
    incorrect5 = int((scored5["5th_result"] == "Incorrect").sum())
    total5 = correct5 + incorrect5
    accuracy5 = correct5 / total5 * 100 if total5 else 0.0
    points5 = float(scored5["5th_points"].fillna(0).sum())

    print(f"Trading dates analyzed            : {len(results_df)}")
    print()
    print("FOURTH-CANDLE PREDICTIONS")
    print(f"Correct predictions               : {correct4}")
    print(f"Incorrect predictions             : {incorrect4}")
    print(f"Scored predictions                : {total4}")
    print(f"Accuracy                           : {accuracy4:.2f}%")
    print(f"Directional points (4th candle)    : {points4:.2f}")
    print()
    print("FIFTH-CANDLE PREDICTIONS")
    print(f"Correct predictions               : {correct5}")
    print(f"Incorrect predictions             : {incorrect5}")
    print(f"Scored predictions                : {total5}")
    print(f"Accuracy                           : {accuracy5:.2f}%")
    print(f"Directional points (5th candle)    : {points5:.2f}")
    print()
    print("OTHER DAILY RESULTS")
    print(
        f"Doji fourth candles                : "
        f"{int((results_df['result'] == 'Doji').sum())}"
    )
    print(
        f"Missing first-three-candle days    : "
        f"{int((results_df['result'] == 'Incomplete').sum())}"
    )
    print(
        f"Missing fourth candles             : "
        f"{int((results_df['result'] == 'Missing Fourth Candle').sum())}"
    )
    print(
        f"No fourth-candle prediction        : "
        f"{int((results_df['result'] == 'No Prediction').sum())}"
    )
    print(
        f"Fifth-candle correct               : {correct5}"
    )
    print(
        f"Fifth-candle incorrect             : {incorrect5}"
    )
    print(
        f"Fifth-candle doji                  : "
        f"{int((results_df['5th_result'] == 'Doji').sum())}"
    )
    print(
        f"Fifth-candle missing               : "
        f"{int((results_df['5th_result'] == 'Missing Fifth Candle').sum())}"
    )
    print(
        f"Fifth-candle excluded-rule days    : "
        f"{int((results_df['5th_result'] == 'No Prediction: rule excluded').sum())}"
    )


# ============================================================
# MAIN
# ============================================================

def main():
    smart_api = None

    try:
        now_ist = datetime.now(IST)

        print(
            f"\nAnalyzing NIFTY 50 from {START_DATE.date()} "
            f"through {now_ist.date()}."
        )

        smart_api = login_to_smartapi()
        print("SmartAPI login successful.")

        raw_df, fetch_status_df = fetch_historical_range(
            smart_api, START_DATE
        )

        if raw_df.empty:
            print(
                "\nNo historical candles retrieved. Check credentials, "
                "API permissions, and historical data availability."
            )
            return

        raw_df["timestamp"] = raw_df["timestamp"].apply(convert_to_ist)

        df = add_candle_metrics(raw_df)
        df = df.sort_values("timestamp").copy()

        now_ist = datetime.now(IST)

        df = df[
            (df["timestamp"] >= START_DATE)
            & (df["timestamp"] <= now_ist)
        ].copy()

        df["candle_time"] = df["timestamp"].dt.strftime("%H:%M")
        df["trading_date"] = df["timestamp"].dt.date

        daily_results = []

        for trading_date, day_df in df.groupby("trading_date"):
            daily_results.append(analyze_day(trading_date, day_df))

        results_df = pd.DataFrame(daily_results)

        if results_df.empty:
            print("No daily analysis results available.")
            return

        results_df["date"] = pd.to_datetime(results_df["date"])
        results_df = results_df.sort_values("date").reset_index(drop=True)

        # No CSV files are generated anywhere in this script.

        print_monthly_summary(results_df, fetch_status_df)

        print_rule_accuracy(results_df, RULES_4TH, 4)
        print_rule_accuracy(results_df, RULES_5TH, 5)

        print_overall_summary(results_df)

        failed_months = fetch_status_df[
            fetch_status_df["fetch_status"].isin(["FAILED", "NO DATA"])
        ]

        if not failed_months.empty:
            print("\n" + "=" * 90)
            print("WARNING: MONTHS WITH MISSING OR FAILED DATA")
            print("=" * 90)
            print(failed_months.to_string(index=False))
            print(
                "\nResults may be incomplete for these months. "
                "A month marked NO DATA does not necessarily mean "
                "the market had no trading sessions."
            )

        print("\nAnalysis complete. No CSV files were generated.")

    except Exception as exc:
        print(f"\nERROR: {exc}")

    finally:
        if smart_api is not None:
            try:
                smart_api.terminateSession(CLIENT_CODE)
                print("SmartAPI session terminated.")
            except Exception as exc:
                print(f"Could not terminate session: {exc}")


if __name__ == "__main__":
    main()

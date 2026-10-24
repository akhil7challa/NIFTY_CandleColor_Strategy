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

SELECTED_YEAR = 2026

MAX_RETRIES = 4
RETRY_BASE_SECONDS = 2

OUTPUT_CSV = f"nifty_candle_daily_{SELECTED_YEAR}.csv"
MONTHLY_CSV = f"nifty_candle_monthly_{SELECTED_YEAR}.csv"
RULES_CSV = f"nifty_candle_rules_{SELECTED_YEAR}.csv"
FETCH_STATUS_CSV = f"nifty_fetch_status_{SELECTED_YEAR}.csv"

FIRST_THREE_TIMES = ["09:15", "09:20", "09:25"]
FOURTH_CANDLE_TIME = "09:30"
FIFTH_CANDLE_TIME = "09:35"

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
# ORIGINAL THREE-CANDLE PREDICTION RULES
# ============================================================

RULES = {
    ("Red", "Red", "Green"): "Green",
    ("Green", "Green", "Red"): "Red",
    ("Red", "Green", "Red"): "Red",
    ("Green", "Red", "Green"): "Green",
    ("Red", "Red", "Red"): "Red",
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
            "Missing environment variables: "
            + ", ".join(missing)
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
# FETCH SELECTED YEAR MONTH BY MONTH
# ============================================================

def fetch_year_historical_data(smart_api, year):
    all_data = []
    fetch_status = []

    now_ist = datetime.now(IST)
    today = now_ist.date()

    print("\n" + "=" * 85)
    print(f"FETCHING HISTORICAL DATA FOR {year}")
    print(f"Current IST date/time: {now_ist}")
    print("=" * 85)

    if year > today.year:
        print(
            f"Year {year} is in the future. "
            f"Current year is {today.year}; no requests made."
        )

        for month in range(1, 13):
            fetch_status.append({
                "month": month,
                "month_name": calendar.month_name[month],
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })

        status_df = pd.DataFrame(fetch_status)
        status_df.to_csv(FETCH_STATUS_CSV, index=False)

        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    last_month = 12 if year < today.year else today.month

    for month in range(1, 13):
        month_name = calendar.month_name[month]

        if month > last_month:
            print(f"\nSkipping {month_name} {year}: future month.")

            fetch_status.append({
                "month": month,
                "month_name": month_name,
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })
            continue

        last_day = calendar.monthrange(year, month)[1]

        start_dt = datetime(year, month, 1, 9, 0, tzinfo=IST)
        end_dt = datetime(
            year, month, last_day, 15, 30, tzinfo=IST
        )

        if year == today.year and month == today.month:
            end_dt = min(end_dt, now_ist)

        if start_dt > now_ist:
            print(f"\nSkipping {month_name} {year}: future period.")

            fetch_status.append({
                "month": month,
                "month_name": month_name,
                "fetch_status": "FUTURE - SKIPPED",
                "candles_received": 0,
                "error": "",
            })
            continue

        print(f"\nFetching {month_name} {year}...")
        print(f"  From: {start_dt}")
        print(f"  To  : {end_dt}")

        try:
            month_df = fetch_historical_data(
                smart_api, start_dt, end_dt
            )

            if month_df.empty:
                print("  WARNING: API returned zero candles.")

                fetch_status.append({
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
                print("  No candles remained after date filtering.")

                fetch_status.append({
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
                "month": month,
                "month_name": month_name,
                "fetch_status": "FAILED",
                "candles_received": 0,
                "error": error_text,
            })

    status_df = pd.DataFrame(fetch_status).sort_values(
        "month"
    ).reset_index(drop=True)

    status_df.to_csv(FETCH_STATUS_CSV, index=False)

    print("\n" + "=" * 85)
    print("HISTORICAL DATA FETCH STATUS")
    print("=" * 85)
    print(status_df.to_string(index=False))

    print(
        f"\nFetch status saved to: "
        f"{os.path.abspath(FETCH_STATUS_CSV)}"
    )

    if not all_data:
        return pd.DataFrame(columns=CANDLE_COLUMNS), status_df

    combined_df = pd.concat(all_data, ignore_index=True)

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

    print("\nCombined historical data:")
    print(f"Total candles: {len(combined_df)}")

    if not combined_df.empty:
        print(f"First candle: {combined_df['timestamp'].min()}")
        print(f"Last candle : {combined_df['timestamp'].max()}")

    return combined_df, status_df


# ============================================================
# FOURTH-CANDLE CONFIRMATION
# ============================================================

def confirm_fourth_candle(predicted, third, fourth):
    """
    GREEN confirmation:
      1. Fourth candle is Green.
      2. Fourth close > third close.
      3. Fourth body > 50% of third body.

    RED confirmation:
      1. Fourth candle is Red.
      2. Fourth close < third close.
      3. Fourth body > 50% of third body.

    Returns (confirmed, reason).
    """

    third_body = abs(
        float(third["close"] - third["open"])
    )
    fourth_body = abs(
        float(fourth["close"] - fourth["open"])
    )

    required_body = 0.50 * third_body

    if predicted == "Green":
        if fourth["color"] != "Green":
            return False, "Fourth candle is not Green"

        if float(fourth["close"]) <= float(third["close"]):
            return False, "Fourth close is not above third close"

        if fourth_body <= required_body:
            return False, (
                "Fourth body is not greater than 50% "
                "of third body"
            )

        return True, "GREEN confirmation passed"

    if predicted == "Red":
        if fourth["color"] != "Red":
            return False, "Fourth candle is not Red"

        if float(fourth["close"]) >= float(third["close"]):
            return False, "Fourth close is not below third close"

        if fourth_body <= required_body:
            return False, (
                "Fourth body is not greater than 50% "
                "of third body"
            )

        return True, "RED confirmation passed"

    return False, "No initial prediction"


# ============================================================
# FIFTH-CANDLE PREDICTION RULE
# ============================================================

def predict_fifth_candle(third, fourth):
    """
    Predict the fifth candle using the actual third and
    fourth candle colors.

    Third Green + Fourth Green -> Fifth Green
    Third Red   + Fourth Red   -> Fifth Red
    Different colors           -> No prediction
    Doji combination           -> No prediction
    """

    third_color = third["color"]
    fourth_color = fourth["color"]

    if third_color == "Green" and fourth_color == "Green":
        return "Green"

    if third_color == "Red" and fourth_color == "Red":
        return "Red"

    return "No"


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
            subset=["candle_time"], keep="first"
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

        "first_three_highest": None,
        "first_three_lowest": None,
        "first_three_midpoint": None,
        "midpoint_filter": "Not Checked",
        "midpoint_filter_reason": "Not Checked",

        "3rd_body": None,
        "4th_body": None,
        "required_4th_body": None,

        "fourth_confirmation": "Not Checked",
        "fourth_confirmation_reason": "Not Checked",

        "predicted_5th_color": "No",
        "actual_5th_color": "Missing",

        "5th_open": None,
        "5th_high": None,
        "5th_low": None,
        "5th_close": None,

        "5th_result": "Not Attempted",
        "5th_points": 0.0,
        "points": None,
    }

    # Require all first three candles.
    if "Missing" in colors:
        return result_row

    # Predict the fourth candle using the original rules.
    predicted = predict_fourth_candle(
        color1, color2, color3
    )

    result_row["predicted_4th_color"] = predicted

    result_row["rule_applied"] = describe_rule(
        color1, color2, color3, predicted
    )

    if predicted == "No Prediction":
        result_row["result"] = "No Prediction"
        return result_row

    third = first_three.loc["09:25"]

    fourth_rows = day_df[
        day_df["candle_time"] == FOURTH_CANDLE_TIME
    ]

    if fourth_rows.empty:
        result_row["result"] = "Missing Fourth Candle"
        result_row["fourth_confirmation"] = "Not Checked"
        result_row["fourth_confirmation_reason"] = (
            "Fourth candle missing"
        )
        return result_row

    fourth = fourth_rows.iloc[0]
    actual_fourth = fourth["color"]

    third_body = abs(
        float(third["close"] - third["open"])
    )
    fourth_body = abs(
        float(fourth["close"] - fourth["open"])
    )

    # --------------------------------------------------------
    # NEW RULE: FIRST-THREE-CANDLE MIDPOINT
    # --------------------------------------------------------

    first_three_highest = float(
        first_three["high"].max()
    )
    first_three_lowest = float(
        first_three["low"].min()
    )

    first_three_midpoint = (
        first_three_highest + first_three_lowest
    ) / 2.0

    result_row.update({
        "actual_4th_color": actual_fourth,
        "4th_open": float(fourth["open"]),
        "4th_high": float(fourth["high"]),
        "4th_low": float(fourth["low"]),
        "4th_close": float(fourth["close"]),

        "first_three_highest": first_three_highest,
        "first_three_lowest": first_three_lowest,
        "first_three_midpoint": first_three_midpoint,

        "3rd_body": third_body,
        "4th_body": fourth_body,
        "required_4th_body": 0.50 * third_body,
    })

    fourth_close = float(fourth["close"])

    if predicted == "Red":
        midpoint_passed = (
            fourth_close < first_three_midpoint
        )

        midpoint_reason = (
            "Passed: fourth close below midpoint"
            if midpoint_passed
            else (
                "Failed: predicted Red but fourth close "
                "is not below midpoint"
            )
        )

    elif predicted == "Green":
        midpoint_passed = (
            fourth_close > first_three_midpoint
        )

        midpoint_reason = (
            "Passed: fourth close above midpoint"
            if midpoint_passed
            else (
                "Failed: predicted Green but fourth close "
                "is not above midpoint"
            )
        )

    else:
        midpoint_passed = False
        midpoint_reason = "No valid prediction"

    result_row["midpoint_filter"] = (
        "Passed" if midpoint_passed else "Failed"
    )
    result_row["midpoint_filter_reason"] = midpoint_reason

    # If midpoint filter fails, reject the prediction.
    if not midpoint_passed:
        result_row["result"] = "No Prediction"
        result_row["points"] = 0.0
        result_row["fourth_confirmation"] = "Failed"
        result_row["fourth_confirmation_reason"] = (
            "Rejected by first-three-candle midpoint filter"
        )
        result_row["predicted_5th_color"] = "No"
        result_row["5th_result"] = (
            "No: first-three-candle midpoint filter failed"
        )
        return result_row

    # A fourth-candle Doji is not a correct prediction.
    if actual_fourth == "Doji":
        result_row["result"] = "Doji"
        result_row["points"] = 0.0
        result_row["fourth_confirmation"] = "Failed"
        result_row["fourth_confirmation_reason"] = (
            "Fourth candle is Doji"
        )
        result_row["5th_result"] = (
            "No: fourth candle is Doji"
        )
        return result_row

    # Calculate fourth-candle directional points.
    fourth_move = float(
        fourth["close"] - fourth["open"]
    )

    fourth_points = (
        fourth_move if predicted == "Green" else -fourth_move
    )

    result_row["points"] = fourth_points

    result_row["result"] = (
        "Correct" if predicted == actual_fourth else "Incorrect"
    )

    # Continue only if the fourth prediction is correct.
    if result_row["result"] != "Correct":
        result_row["fourth_confirmation"] = "Failed"
        result_row["fourth_confirmation_reason"] = (
            "Fourth candle direction does not match prediction"
        )
        result_row["5th_result"] = (
            "No: fourth prediction incorrect"
        )
        return result_row

    # Apply the original fourth-candle confirmation conditions.
    confirmed, confirmation_reason = confirm_fourth_candle(
        predicted, third, fourth
    )

    result_row["fourth_confirmation"] = (
        "Passed" if confirmed else "Failed"
    )
    result_row["fourth_confirmation_reason"] = (
        confirmation_reason
    )

    if not confirmed:
        result_row["predicted_5th_color"] = "No"
        result_row["5th_result"] = (
            "No: fourth candle failed confirmation"
        )
        return result_row

    # Predict the fifth candle if the third and fourth
    # candle colors match.
    fifth_prediction = predict_fifth_candle(third, fourth)

    if fifth_prediction == "No":
        result_row["predicted_5th_color"] = "No"
        result_row["5th_result"] = (
            "No: third and fourth candle colors differ"
        )
        return result_row

    result_row["predicted_5th_color"] = fifth_prediction

    # --------------------------------------------------------
    # FETCH ACTUAL FIFTH CANDLE
    # --------------------------------------------------------

    fifth_rows = day_df[
        day_df["candle_time"] == FIFTH_CANDLE_TIME
    ]

    if fifth_rows.empty:
        result_row["5th_result"] = "Missing Fifth Candle"
        return result_row

    fifth = fifth_rows.iloc[0]
    actual_fifth = fifth["color"]

    result_row.update({
        "actual_5th_color": actual_fifth,
        "5th_open": float(fifth["open"]),
        "5th_high": float(fifth["high"]),
        "5th_low": float(fifth["low"]),
        "5th_close": float(fifth["close"]),
    })

    # Stop on a fifth-candle Doji.
    if actual_fifth == "Doji":
        result_row["5th_result"] = "Doji"
        result_row["5th_points"] = 0.0
        return result_row

    # Calculate fifth-candle points using the prediction.
    fifth_move = float(
        fifth["close"] - fifth["open"]
    )

    fifth_points = (
        fifth_move
        if fifth_prediction == "Green"
        else -fifth_move
    )

    result_row["5th_points"] = fifth_points

    # Combined points include fourth and fifth candles.
    result_row["points"] = fourth_points + fifth_points

    result_row["5th_result"] = (
        "Correct"
        if actual_fifth == fifth_prediction
        else "Incorrect"
    )

    return result_row


# ============================================================
# MONTHLY SUMMARY
# ============================================================

def build_monthly_summary(results_df, fetch_status_df, year):
    monthly_rows = []

    for month in range(1, 13):
        month_df = results_df[
            results_df["month"] == month
        ]

        # Fourth-candle accuracy.
        scored = month_df[
            month_df["result"].isin(["Correct", "Incorrect"])
        ]

        correct_count = int(
            (scored["result"] == "Correct").sum()
        )

        incorrect_count = int(
            (scored["result"] == "Incorrect").sum()
        )

        scored_total = correct_count + incorrect_count

        accuracy = (
            correct_count / scored_total * 100
            if scored_total else 0.0
        )

        total_points = float(
            scored["points"].fillna(0).sum()
        )

        status_row = fetch_status_df[
            fetch_status_df["month"] == month
        ]

        if not status_row.empty:
            status = status_row.iloc[0]["fetch_status"]
            candles_received = int(
                status_row.iloc[0]["candles_received"]
            )
        else:
            status = "UNKNOWN"
            candles_received = 0

        fifth_predictions = month_df[
            month_df["predicted_5th_color"].isin(["Green", "Red"])
        ]

        fifth_scored = fifth_predictions[
            fifth_predictions["5th_result"].isin(
                ["Correct", "Incorrect"]
            )
        ]

        fifth_correct = int(
            (fifth_scored["5th_result"] == "Correct").sum()
        )

        fifth_incorrect = int(
            (fifth_scored["5th_result"] == "Incorrect").sum()
        )

        fifth_scored_total = fifth_correct + fifth_incorrect

        fifth_accuracy = (
            fifth_correct / fifth_scored_total * 100
            if fifth_scored_total else 0.0
        )

        monthly_rows.append({
            "year": year,
            "month": month,
            "month_name": calendar.month_name[month],

            "fetch_status": status,
            "candles_received": candles_received,
            "trading_days_analyzed": len(month_df),

            "correct": correct_count,
            "incorrect": incorrect_count,
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
                (month_df["result"] == "Missing Fourth Candle").sum()
            ),

            "no_prediction": int(
                (month_df["result"] == "No Prediction").sum()
            ),

            "fifth_candle_extended": len(fifth_predictions),
            "fifth_correct": fifth_correct,
            "fifth_incorrect": fifth_incorrect,
            "fifth_scored_predictions": fifth_scored_total,
            "fifth_accuracy_percent": round(fifth_accuracy, 2),

            "fifth_confirmation_failed": int(
                month_df["5th_result"].astype(str).str.startswith(
                    "No:"
                ).sum()
            ),
        })

    return pd.DataFrame(monthly_rows)


# ============================================================
# MAIN
# ============================================================

def main():
    smart_api = None

    try:
        print(
            f"\nAnalyzing NIFTY 50 for selected year "
            f"{SELECTED_YEAR}"
        )

        smart_api = login_to_smartapi()
        print("SmartAPI login successful.")

        raw_df, fetch_status_df = fetch_year_historical_data(
            smart_api,
            SELECTED_YEAR
        )

        if raw_df.empty:
            print(
                "\nNo historical candles retrieved. "
                "Check credentials, API permissions, and data availability."
            )
            return

        raw_df["timestamp"] = raw_df[
            "timestamp"
        ].apply(convert_to_ist)

        df = add_candle_metrics(raw_df)
        df = df.sort_values("timestamp").copy()

        now_ist = datetime.now(IST)

        df = df[
            (df["timestamp"].dt.year == SELECTED_YEAR)
            & (df["timestamp"] <= now_ist)
        ].copy()

        df["candle_time"] = df["timestamp"].dt.strftime("%H:%M")
        df["trading_date"] = df["timestamp"].dt.date

        # Analyze every trading day.
        daily_results = []

        for trading_date, day_df in df.groupby("trading_date"):
            daily_results.append(
                analyze_day(trading_date, day_df)
            )

        results_df = pd.DataFrame(daily_results)

        if results_df.empty:
            print("No daily analysis results available.")
            return

        results_df["date"] = pd.to_datetime(results_df["date"])

        results_df = results_df.sort_values(
            "date"
        ).reset_index(drop=True)

        # ----------------------------------------------------
        # SAVE DAILY RESULTS
        # ----------------------------------------------------

        results_df.to_csv(OUTPUT_CSV, index=False)

        # ----------------------------------------------------
        # SAVE RULES REPORT
        # ----------------------------------------------------

        rule_columns = [
            "date",
            "candle_1_color",
            "candle_2_color",
            "candle_3_color",
            "rule_applied",

            "predicted_4th_color",
            "actual_4th_color",
            "result",
            "4th_open",
            "4th_high",
            "4th_low",
            "4th_close",

            "first_three_highest",
            "first_three_lowest",
            "first_three_midpoint",
            "midpoint_filter",
            "midpoint_filter_reason",

            "3rd_body",
            "4th_body",
            "required_4th_body",
            "fourth_confirmation",
            "fourth_confirmation_reason",

            "predicted_5th_color",
            "actual_5th_color",
            "5th_result",
            "5th_open",
            "5th_high",
            "5th_low",
            "5th_close",
            "5th_points",

            "points",
        ]

        results_df[rule_columns].to_csv(
            RULES_CSV, index=False
        )

        # ----------------------------------------------------
        # MONTHLY SUMMARY
        # ----------------------------------------------------

        monthly_df = build_monthly_summary(
            results_df,
            fetch_status_df,
            SELECTED_YEAR
        )

        monthly_df.to_csv(MONTHLY_CSV, index=False)

        print("\n" + "=" * 130)
        print(f"MONTHLY ACCURACY AND POINTS - {SELECTED_YEAR}")
        print("=" * 130)

        monthly_columns = [
            "month_name",
            "candles_received",
            "trading_days_analyzed",
            "correct",
            "incorrect",
            "accuracy_percent",
            "total_points",
            "fifth_candle_extended",
            "fifth_correct",
            "fifth_incorrect",
            "fifth_accuracy_percent",
            "fifth_confirmation_failed",
        ]

        print(
            monthly_df[monthly_columns].to_string(index=False)
        )

        # ----------------------------------------------------
        # YEARLY SUMMARY: FOURTH-CANDLE PREDICTIONS
        # ----------------------------------------------------

        scored = results_df[
            results_df["result"].isin(["Correct", "Incorrect"])
        ]

        correct_count = int(
            (scored["result"] == "Correct").sum()
        )

        incorrect_count = int(
            (scored["result"] == "Incorrect").sum()
        )

        scored_total = correct_count + incorrect_count

        yearly_accuracy = (
            correct_count / scored_total * 100
            if scored_total else 0.0
        )

        yearly_points = float(
            scored["points"].fillna(0).sum()
        )

        # ----------------------------------------------------
        # YEARLY SUMMARY: FIFTH-CANDLE PREDICTIONS
        # ----------------------------------------------------

        fifth_predictions = results_df[
            results_df["predicted_5th_color"].isin(
                ["Green", "Red"]
            )
        ]

        fifth_scored = fifth_predictions[
            fifth_predictions["5th_result"].isin(
                ["Correct", "Incorrect"]
            )
        ]

        fifth_extended_count = len(fifth_predictions)

        fifth_correct_count = int(
            (fifth_scored["5th_result"] == "Correct").sum()
        )

        fifth_incorrect_count = int(
            (fifth_scored["5th_result"] == "Incorrect").sum()
        )

        fifth_scored_total = (
            fifth_correct_count + fifth_incorrect_count
        )

        fifth_yearly_accuracy = (
            fifth_correct_count / fifth_scored_total * 100
            if fifth_scored_total else 0.0
        )

        fifth_confirmation_failed_count = int(
            results_df["5th_result"].astype(str).str.startswith(
                "No:"
            ).sum()
        )

        print("\n" + "=" * 80)
        print(f"YEARLY SUMMARY - {SELECTED_YEAR}")
        print("=" * 80)

        print(f"Correct 4th-candle predictions : {correct_count}")
        print(f"Incorrect 4th-candle predictions: {incorrect_count}")
        print(f"Scored 4th-candle predictions  : {scored_total}")
        print(f"Fourth-candle accuracy         : {yearly_accuracy:.2f}%")
        print(f"Total directional points       : {yearly_points:.2f}")
        print(f"Trading dates analyzed         : {len(results_df)}")

        print("-" * 80)

        print(f"Fifth-candle predictions       : {fifth_extended_count}")
        print(f"Fifth candle correct           : {fifth_correct_count}")
        print(f"Fifth candle incorrect         : {fifth_incorrect_count}")
        print(f"Scored fifth-candle predictions: {fifth_scored_total}")
        print(f"Fifth-candle accuracy          : {fifth_yearly_accuracy:.2f}%")
        print(
            f"Fifth extension rejected       : "
            f"{fifth_confirmation_failed_count}"
        )

        # ----------------------------------------------------
        # WARN ABOUT MISSING DATA
        # ----------------------------------------------------

        failed_months = monthly_df[
            monthly_df["fetch_status"].isin(["FAILED", "NO DATA"])
        ]

        if not failed_months.empty:
            print(
                "\nWARNING: Some months have missing historical data:"
            )

            print(
                failed_months[
                    [
                        "month_name",
                        "fetch_status",
                        "candles_received",
                    ]
                ].to_string(index=False)
            )

            print(
                "Yearly results may be incomplete until missing "
                "historical data is resolved."
            )

        # ----------------------------------------------------
        # OUTPUT FILES
        # ----------------------------------------------------

        print("\n" + "=" * 80)
        print("OUTPUT FILES")
        print("=" * 80)

        print(f"Daily results : {os.path.abspath(OUTPUT_CSV)}")
        print(f"Monthly report: {os.path.abspath(MONTHLY_CSV)}")
        print(f"Rules report  : {os.path.abspath(RULES_CSV)}")
        print(f"Fetch status  : {os.path.abspath(FETCH_STATUS_CSV)}")

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
import os
import itertools
import numpy as np
import pandas as pd

# ============================================================
# CONFIGURATION
# ============================================================

CSV_PATH = r".\Historic_5Min_CandlesData_2016_2026.csv"


# ============================================================
# 1. LOAD HISTORICAL DATA
# ============================================================

if not os.path.exists(CSV_PATH):
    raise FileNotFoundError(
        f"CSV file not found: {CSV_PATH}\n"
        "Update CSV_PATH at the top of this script."
    )

df = pd.read_csv(CSV_PATH)

print("=" * 90)
print("NIFTY: FIRST FIVE 5-MINUTE CANDLE COLOR ANALYSIS")
print("=" * 90)

print("\nOriginal columns:", df.columns.tolist())


def find_column(*candidates, required=True):
    lookup = {
        str(c).strip().lower().replace(" ", "_"): c
        for c in df.columns
    }

    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "_")
        if key in lookup:
            return lookup[key]

    if required:
        raise ValueError(
            f"Could not find column from {candidates}.\n"
            f"Available columns: {df.columns.tolist()}"
        )

    return None


date_col = find_column(
    "timestamp", "datetime", "date_time", "date"
)
open_col = find_column("open")
high_col = find_column("high")
low_col = find_column("low")
close_col = find_column("close")

df = df.rename(
    columns={
        date_col: "datetime",
        open_col: "open",
        high_col: "high",
        low_col: "low",
        close_col: "close",
    }
)

df["datetime"] = pd.to_datetime(
    df["datetime"], errors="coerce"
)

for col in ["open", "high", "low", "close"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

original_rows = len(df)

df = (
    df.dropna(
        subset=["datetime", "open", "high", "low", "close"]
    )
    .sort_values("datetime")
    .drop_duplicates(subset=["datetime"])
    .reset_index(drop=True)
)

print(f"\nOriginal rows: {original_rows:,}")
print(f"Valid unique candles: {len(df):,}")
print(
    f"Invalid/duplicate rows removed: "
    f"{original_rows - len(df):,}"
)

if df.empty:
    raise ValueError("No valid historical candles found.")


# ============================================================
# 2. IDENTIFY THE FIRST FIVE CANDLES
# ============================================================

# Assumes timestamps are in Indian exchange local time.
#
# Candle 1: 09:15-09:20
# Candle 2: 09:20-09:25
# Candle 3: 09:25-09:30
# Candle 4: 09:30-09:35
# Candle 5: 09:35-09:40

df["trading_date"] = df["datetime"].dt.date
df["time"] = df["datetime"].dt.strftime("%H:%M")

opening_times = [
    "09:15",
    "09:20",
    "09:25",
    "09:30",
    "09:35",
]

time_to_number = {
    time: number
    for number, time in enumerate(opening_times, start=1)
}

opening = df[df["time"].isin(opening_times)].copy()

opening["candle_number"] = opening["time"].map(
    time_to_number
)

opening = opening.sort_values(
    ["trading_date", "candle_number", "datetime"]
)

# Keep one candle for each expected time per trading day.
opening = opening.drop_duplicates(
    subset=["trading_date", "candle_number"],
    keep="first"
)

# Green = close >= open
# Red = close < open
opening["color"] = np.where(
    opening["close"] >= opening["open"],
    "Green",
    "Red"
)

# Keep only dates with all five expected opening candles.
counts = opening.groupby("trading_date")[
    "candle_number"
].nunique()

all_dates = sorted(df["trading_date"].unique())
complete_dates = counts[counts == 5].index

complete = opening[
    opening["trading_date"].isin(complete_dates)
].copy()

if complete.empty:
    raise ValueError(
        "No trading days contain all five expected candles "
        "(09:15, 09:20, 09:25, 09:30, 09:35). "
        "Check the CSV timestamps."
    )

# Create one row per trading day with five candle colors.
color_pivot = complete.pivot(
    index="trading_date",
    columns="candle_number",
    values="color"
)

color_pivot = color_pivot.rename(
    columns={
        i: f"candle_{i}"
        for i in range(1, 6)
    }
).reset_index()

# Create one row per trading day with candle OHLC values.
price_pivot = complete.pivot(
    index="trading_date",
    columns="candle_number",
    values=["open", "high", "low", "close"]
)

price_pivot.columns = [
    f"{field}_candle_{number}"
    for field, number in price_pivot.columns
]

price_pivot = price_pivot.reset_index()

daily = color_pivot.merge(
    price_pivot,
    on="trading_date",
    how="left"
)

color_columns = [
    f"candle_{i}" for i in range(1, 6)
]

print(f"\nTrading dates in source data: {len(all_dates):,}")
print(f"Complete five-candle days: {len(daily):,}")
print(
    f"Dates excluded due to missing opening candles: "
    f"{len(all_dates) - len(daily):,}"
)


# ============================================================
# 3. OVERALL RED/GREEN COUNTS FOR EACH CANDLE
# ============================================================

print("\n" + "=" * 90)
print("1. OVERALL CANDLE COLOR SUMMARY")
print("=" * 90)

total_days = len(daily)

print(
    f"\n{'Candle':<15}"
    f"{'Green Count':>15}"
    f"{'Green %':>13}"
    f"{'Red Count':>15}"
    f"{'Red %':>13}"
    f"{'Total':>12}"
)

print("-" * 83)

for i, col in enumerate(color_columns, start=1):
    green = int((daily[col] == "Green").sum())
    red = int((daily[col] == "Red").sum())

    green_pct = green / total_days * 100
    red_pct = red / total_days * 100

    print(
        f"{'Candle ' + str(i):<15}"
        f"{green:>15,}"
        f"{green_pct:>12.2f}%"
        f"{red:>15,}"
        f"{red_pct:>12.2f}%"
        f"{total_days:>12,}"
    )


# ============================================================
# 4. ALL 32 FIVE-CANDLE COLOR COMBINATIONS
# ============================================================

print("\n" + "=" * 90)
print("2. ALL FIVE-CANDLE COLOR COMBINATIONS")
print("=" * 90)

# Each combination contains five colors.
# Example: Green-Red-Green-Red-Green.

combinations = list(
    itertools.product(["Green", "Red"], repeat=5)
)

print(
    f"\n{'Combination':<40}"
    f"{'Count':>12}"
    f"{'Percentage':>15}"
)

print("-" * 67)

for combination in combinations:
    mask = pd.Series(True, index=daily.index)

    for i, color in enumerate(combination, start=1):
        mask &= daily[f"candle_{i}"].eq(color)

    count = int(mask.sum())
    percentage = count / total_days * 100

    label = "-".join(combination)

    print(
        f"{label:<40}"
        f"{count:>12,}"
        f"{percentage:>14.2f}%"
    )


# ============================================================
# 5. CANDLE-TO-CANDLE TRANSITIONS
# ============================================================

print("\n" + "=" * 90)
print("3. CANDLE-TO-CANDLE TRANSITION COUNTS")
print("=" * 90)

for i in range(1, 5):
    first_col = f"candle_{i}"
    next_col = f"candle_{i + 1}"

    transition = pd.crosstab(
        daily[first_col],
        daily[next_col]
    ).reindex(
        index=["Red", "Green"],
        columns=["Red", "Green"],
        fill_value=0
    )

    print(f"\nCandle {i} -> Candle {i + 1}")
    print(transition.to_string())


# ============================================================
# 6. CONDITIONAL PROBABILITIES
# ============================================================

print("\n" + "=" * 90)
print("4. CONDITIONAL COLOR PROBABILITIES")
print("=" * 90)

# For each candle from 2 to 5, examine the color of the
# previous candle and all possible preceding color sequences.
#
# Example:
# If Candles 1, 2, and 3 are Green-Red-Green,
# how often is Candle 4 Green versus Red?

for target_number in range(2, 6):
    previous_columns = [
        f"candle_{i}"
        for i in range(1, target_number)
    ]

    target_column = f"candle_{target_number}"

    print(
        f"\n{'-' * 75}\n"
        f"P(CANDLE {target_number} COLOR | "
        f"PREVIOUS CANDLE COLORS)\n"
        f"{'-' * 75}"
    )

    for previous_colors in itertools.product(
        ["Green", "Red"],
        repeat=target_number - 1
    ):
        mask = pd.Series(True, index=daily.index)

        for col, color in zip(
            previous_columns, previous_colors
        ):
            mask &= daily[col].eq(color)

        subset = daily.loc[mask, target_column]
        observations = len(subset)

        green = int((subset == "Green").sum())
        red = int((subset == "Red").sum())

        pattern = "-".join(previous_colors)

        if observations == 0:
            print(
                f"Previous: {pattern:<30} "
                f"Observations: 0 | No data"
            )
            continue

        print(
            f"Previous: {pattern:<30} "
            f"N={observations:>6,} | "
            f"Next Green: {green / observations * 100:>6.2f}% "
            f"({green:,}) | "
            f"Next Red: {red / observations * 100:>6.2f}% "
            f"({red:,})"
        )


# ============================================================
# 7. PREVIOUS TRADING DAY'S CLOSE
# ============================================================

print("\n" + "=" * 90)
print("5. GAP-UP / GAP-DOWN / GAP-NEUTRAL ANALYSIS")
print("=" * 90)

# Find the final available candle close for each trading day.
# This assumes the source contains the full intraday session.

daily_closes = (
    df.sort_values("datetime")
    .groupby("trading_date", as_index=False)
    .tail(1)[["trading_date", "close"]]
    .sort_values("trading_date")
    .reset_index(drop=True)
)

daily_closes["previous_day_close"] = (
    daily_closes["close"].shift(1)
)

# Match the previous available trading session's close.
daily = daily.merge(
    daily_closes[
        ["trading_date", "previous_day_close"]
    ],
    on="trading_date",
    how="left"
)

daily["opening_price"] = daily["open_candle_1"]

daily["gap_points"] = (
    daily["opening_price"] - daily["previous_day_close"]
)

daily["gap_pct"] = (
    daily["gap_points"]
    / daily["previous_day_close"]
    * 100
)

daily["gap_direction"] = np.select(
    [
        daily["gap_points"] > 0,
        daily["gap_points"] < 0,
    ],
    [
        "Gap Up",
        "Gap Down",
    ],
    default="Gap Neutral"
)

# Exclude the first historical session from gap analysis.
gap_daily = daily.dropna(
    subset=["previous_day_close", "gap_points"]
).copy()

for direction in ["Gap Up", "Gap Down", "Gap Neutral"]:
    subset = gap_daily[
        gap_daily["gap_direction"] == direction
    ]

    n = len(subset)

    print("\n" + "-" * 90)
    print(f"{direction.upper()} — {n:,} DAYS")
    print("-" * 90)

    if n == 0:
        print("No observations.")
        continue

    # Red/green counts for each of the five candles.
    for i in range(1, 6):
        col = f"candle_{i}"

        green = int((subset[col] == "Green").sum())
        red = int((subset[col] == "Red").sum())

        print(
            f"Candle {i}: "
            f"Green {green:,} ({green / n * 100:.2f}%) | "
            f"Red {red:,} ({red / n * 100:.2f}%)"
        )

    # All 32 combinations for each gap direction.
    print("\nFive-candle combinations:")

    for combination in combinations:
        mask = pd.Series(True, index=subset.index)

        for i, color in enumerate(combination, start=1):
            mask &= subset[f"candle_{i}"].eq(color)

        count = int(mask.sum())
        percentage = count / n * 100

        print(
            f"  {'-'.join(combination):<40}"
            f"{count:>8,} days "
            f"({percentage:.2f}%)"
        )


# ============================================================
# 8. OPENING GAP SIZE STATISTICS
# ============================================================

print("\n" + "=" * 90)
print("6. OPENING GAP SIZE STATISTICS")
print("=" * 90)

if not gap_daily.empty:
    gap_summary = (
        gap_daily.groupby("gap_direction")
        .agg(
            total_days=("gap_points", "size"),
            average_gap_points=("gap_points", "mean"),
            median_gap_points=("gap_points", "median"),
            average_gap_pct=("gap_pct", "mean"),
            median_gap_pct=("gap_pct", "median"),
        )
        .reindex(["Gap Up", "Gap Down", "Gap Neutral"])
        .round(4)
    )

    print(gap_summary.to_string())
else:
    print("No gap observations available.")


# ============================================================
# 9. PRICE BODY AND RANGE STATISTICS
# ============================================================

print("\n" + "=" * 90)
print("7. FIRST FIVE CANDLES: PRICE STATISTICS")
print("=" * 90)

for i in range(1, 6):
    open_name = f"open_candle_{i}"
    high_name = f"high_candle_{i}"
    low_name = f"low_candle_{i}"
    close_name = f"close_candle_{i}"

    body = daily[close_name] - daily[open_name]
    candle_range = daily[high_name] - daily[low_name]

    print(f"\nCandle {i}")

    print(f"  Average body (points):  {body.mean():.4f}")
    print(f"  Median body (points):   {body.median():.4f}")
    print(f"  Average range (points): {candle_range.mean():.4f}")
    print(f"  Median range (points):  {candle_range.median():.4f}")


# ============================================================
# 10. FINAL CONSOLE SUMMARY
# ============================================================

print("\n" + "=" * 90)
print("ANALYSIS COMPLETED")
print("=" * 90)

print(f"Valid unique 5-minute candles: {len(df):,}")
print(f"Complete five-candle days: {len(daily):,}")
print(f"Days included in gap analysis: {len(gap_daily):,}")

print("\nOpening candle definitions:")
print("  Candle 1: 09:15-09:20")
print("  Candle 2: 09:20-09:25")
print("  Candle 3: 09:25-09:30")
print("  Candle 4: 09:30-09:35")
print("  Candle 5: 09:35-09:40")

print("\nColor definitions:")
print("  Green: close >= open")
print("  Red:   close < open")

print("\nGap definitions:")
print("  Gap Up:      opening price > previous trading day's close")
print("  Gap Down:    opening price < previous trading day's close")
print("  Gap Neutral: opening price = previous trading day's close")

print("\nNo output files or folders are created.")
print("Historical patterns do not guarantee future results.")
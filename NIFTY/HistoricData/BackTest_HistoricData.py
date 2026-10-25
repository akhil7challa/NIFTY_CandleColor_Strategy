
import os
import numpy as np
import pandas as pd

from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

# ============================================================
# CONFIGURATION
# ============================================================

CSV_PATH = r".\Historic_5Min_CandlesData_2016_2026.csv"
OUT_DIR = "nifty_rule_conditions"

MIN_CONDITION_OCCURRENCES = 50
TEST_RATIO = 0.30

os.makedirs(OUT_DIR, exist_ok=True)


# ============================================================
# 1. LOAD HISTORICAL DATA
# ============================================================

if not os.path.exists(CSV_PATH):
    raise FileNotFoundError(
        f"CSV file not found: {CSV_PATH}\n"
        "Update CSV_PATH at the top of this script."
    )

df = pd.read_csv(CSV_PATH)

print("=" * 75)
print("NIFTY RED-RED-GREEN CONDITION ANALYSIS")
print("=" * 75)
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


date_col = find_column("timestamp", "datetime", "date_time", "date")
open_col = find_column("open")
high_col = find_column("high")
low_col = find_column("low")
close_col = find_column("close")
volume_col = find_column(
    "volume", "vol", "qty", "quantity", required=False
)

rename_map = {
    date_col: "datetime",
    open_col: "open",
    high_col: "high",
    low_col: "low",
    close_col: "close",
}

if volume_col is not None:
    rename_map[volume_col] = "volume"

df = df.rename(columns=rename_map)

if "volume" not in df.columns:
    df["volume"] = np.nan

df["datetime"] = pd.to_datetime(
    df["datetime"], errors="coerce"
)

for col in ["open", "high", "low", "close", "volume"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df = (
    df.dropna(subset=["datetime", "open", "high", "low", "close"])
    .sort_values("datetime")
    .drop_duplicates(subset=["datetime"])
    .reset_index(drop=True)
)

if len(df) < 100:
    raise ValueError("Not enough valid OHLC candles in the CSV.")

print("\nValid candles:", f"{len(df):,}")
print("First candle:", df["datetime"].iloc[0])
print("Last candle:", df["datetime"].iloc[-1])


# ============================================================
# 2. CALCULATE CANDLE FEATURES
# ============================================================

body = df["close"] - df["open"]
candle_range = df["high"] - df["low"]
safe_open = df["open"].replace(0, np.nan)

# Green when close >= open; Red when close < open
df["color"] = np.where(body >= 0, "Green", "Red")

df["body_pct"] = 100 * body.abs() / safe_open
df["range_pct"] = 100 * candle_range / safe_open

df["upper_wick_pct"] = (
    100
    * (df["high"] - df[["open", "close"]].max(axis=1))
    / safe_open
)

df["lower_wick_pct"] = (
    100
    * (df[["open", "close"]].min(axis=1) - df["low"])
    / safe_open
)

# Volume ratio based only on previous candles
volume_average = (
    df["volume"]
    .shift(1)
    .rolling(window=20, min_periods=5)
    .mean()
)

df["volume_ratio"] = (
    df["volume"] / volume_average.replace(0, np.nan)
)

valid_volume_ratios = int(df["volume_ratio"].notna().sum())

print("\nValid volume values:", int(df["volume"].notna().sum()))
print("Valid volume-ratio values:", valid_volume_ratios)

# Use body and wick features regardless of volume availability
features_to_use = [
    "body_pct",
    "range_pct",
    "upper_wick_pct",
    "lower_wick_pct",
]

# Add volume only if there are enough valid ratios
if valid_volume_ratios >= 100:
    features_to_use.append("volume_ratio")
    print("Volume analysis: ENABLED")
else:
    print("Volume analysis: DISABLED (insufficient valid ratios)")


# ============================================================
# 3. BUILD CANDLE 1, CANDLE 2, CANDLE 3 FEATURES
# ============================================================

feature_names = []

# Each row represents Candle 4:
# Candle 1 = 3 rows earlier
# Candle 2 = 2 rows earlier
# Candle 3 = 1 row earlier
# Current row = Candle 4 outcome

for candle_num, shift in [(1, 3), (2, 2), (3, 1)]:

    df[f"c{candle_num}_color"] = df["color"].shift(shift)

    for feature in features_to_use:
        feature_name = f"c{candle_num}_{feature}"
        df[feature_name] = df[feature].shift(shift)
        feature_names.append(feature_name)


# ============================================================
# 4. FILTER RED -> RED -> GREEN
# ============================================================

pattern_mask = (
    (df["c1_color"] == "Red")
    & (df["c2_color"] == "Red")
    & (df["c3_color"] == "Green")
)

data = df.loc[pattern_mask].copy()

# Success: Candle 4 closes Red
# Failure: Candle 4 closes Green
data["success"] = (data["color"] == "Red").astype(int)

print("\n" + "=" * 75)
print("PATTERN DIAGNOSTICS")
print("=" * 75)

print("Matching Red-Red-Green patterns:", f"{len(data):,}")

if len(data) == 0:
    print("\nLast 15 candle colors:")
    print(df[["datetime", "color"]].tail(15).to_string(index=False))
    raise ValueError(
        "No matching patterns found. Check the data and candle colors."
    )

# Replace infinite values, then drop rows missing only selected features
data = data.replace([np.inf, -np.inf], np.nan)

print("\nMissing values by feature:")
print(data[feature_names].isna().sum().to_string())

usable = data.dropna(
    subset=feature_names + ["success"]
).copy()

print("\nUsable patterns:", f"{len(usable):,}")
print("Excluded patterns:", f"{len(data) - len(usable):,}")

if len(usable) < 100:
    raise ValueError(
        "Fewer than 100 usable patterns remain. "
        "Review the missing-value report."
    )

data = usable.sort_values("datetime").reset_index(drop=True)


# ============================================================
# 5. OVERALL SUCCESS AND FAILURE RATES
# ============================================================

total = len(data)
successes = int(data["success"].sum())
failures = total - successes

success_rate = 100 * successes / total
failure_rate = 100 * failures / total

print("\n" + "=" * 75)
print("OVERALL RESULTS")
print("=" * 75)

print(f"Pattern: Red -> Red -> Green")
print(f"Total usable occurrences: {total:,}")
print(f"Success (Candle 4 Red): {successes:,} ({success_rate:.2f}%)")
print(f"Failure (Candle 4 Green): {failures:,} ({failure_rate:.2f}%)")


# ============================================================
# 6. INDIVIDUAL CONDITION ANALYSIS
# ============================================================

condition_rows = []

for feature in feature_names:

    try:
        buckets = pd.qcut(
            data[feature],
            q=4,
            labels=["Lowest", "Low-Mid", "High-Mid", "Highest"],
            duplicates="drop",
        )
    except (ValueError, TypeError):
        print(f"Skipping feature that cannot be grouped: {feature}")
        continue

    temp = pd.DataFrame({
        "bucket": buckets,
        "success": data["success"].values,
    })

    for bucket, group in temp.groupby("bucket", observed=True):

        count = len(group)

        if count < MIN_CONDITION_OCCURRENCES:
            continue

        wins = int(group["success"].sum())
        losses = count - wins

        condition_rows.append({
            "feature": feature,
            "condition": str(bucket),
            "occurrences": count,
            "successes_red": wins,
            "failures_green": losses,
            "success_rate_pct": round(100 * wins / count, 2),
            "failure_rate_pct": round(100 * losses / count, 2),
        })

condition_report = pd.DataFrame(condition_rows)

print("\n" + "=" * 75)
print("INDIVIDUAL CONDITIONS: ALL RESULTS")
print("=" * 75)

if not condition_report.empty:

    condition_report = condition_report.sort_values(
        ["success_rate_pct", "occurrences"],
        ascending=[False, False],
    )

    # Print every condition in the console
    print(condition_report.to_string(index=False))

    condition_report.to_csv(
        os.path.join(OUT_DIR, "individual_conditions.csv"),
        index=False,
    )

    failure_report = condition_report.sort_values(
        ["failure_rate_pct", "occurrences"],
        ascending=[False, False],
    )

    failure_report.to_csv(
        os.path.join(OUT_DIR, "conditions_ranked_by_failure.csv"),
        index=False,
    )

else:
    print("No condition groups met the minimum occurrence threshold.")


# ============================================================
# 7. CHRONOLOGICAL TRAIN/TEST SPLIT
# ============================================================

data = data.sort_values("datetime").reset_index(drop=True)

split_index = int(len(data) * (1 - TEST_RATIO))

train = data.iloc[:split_index].copy()
test = data.iloc[split_index:].copy()

print("\n" + "=" * 75)
print("DECISION TREE TEST")
print("=" * 75)

print("Training patterns:", f"{len(train):,}")
print("Testing patterns:", f"{len(test):,}")

if len(train) < 100 or len(test) < 50:
    raise ValueError("Insufficient training or testing rows.")

X_train = train[feature_names]
y_train = train["success"]

X_test = test[feature_names]
y_test = test["success"]

tree = DecisionTreeClassifier(
    max_depth=3,
    min_samples_leaf=max(100, int(len(train) * 0.02)),
    random_state=42,
)

tree.fit(X_train, y_train)

predictions = tree.predict(X_test)
probabilities = tree.predict_proba(X_test)[:, 1]

accuracy = accuracy_score(y_test, predictions)
test_baseline = 100 * y_test.mean()
test_accuracy = 100 * accuracy

print(f"\nTest-period Red rate: {test_baseline:.2f}%")
print(f"Decision-tree accuracy: {test_accuracy:.2f}%")
print(f"Difference vs baseline: {test_accuracy - test_baseline:+.2f} percentage points")

print("\nClassification report:")
print(
    classification_report(
        y_test,
        predictions,
        labels=[0, 1],
        target_names=["Failure: Green", "Success: Red"],
        zero_division=0,
    )
)

print("Confusion matrix (actual rows, predicted columns):")
print(confusion_matrix(y_test, predictions, labels=[0, 1]))


# ============================================================
# 8. PRINT DECISION-TREE CONDITIONS
# ============================================================

rules = export_text(
    tree,
    feature_names=feature_names,
    decimals=4,
)

print("\n" + "=" * 75)
print("DECISION-TREE CONDITION COMBINATIONS")
print("=" * 75)
print(rules)

with open(
    os.path.join(OUT_DIR, "decision_tree_rules.txt"),
    "w",
    encoding="utf-8",
) as f:
    f.write(rules)


# ============================================================
# 9. SAVE TEST PREDICTIONS
# ============================================================

test_results = test[
    ["datetime", "success"] + feature_names
].copy()

test_results["actual_candle4_color"] = np.where(
    test_results["success"] == 1, "Red", "Green"
)

test_results["predicted_candle4_color"] = np.where(
    predictions == 1, "Red", "Green"
)

test_results["predicted_red_probability_pct"] = (
    probabilities * 100
).round(2)

test_results["correct_prediction"] = (
    test_results["success"].to_numpy() == predictions
)

test_results.to_csv(
    os.path.join(OUT_DIR, "test_predictions.csv"),
    index=False,
)


# ============================================================
# 10. SAVE OVERALL SUMMARY
# ============================================================

summary = pd.DataFrame([{
    "pattern": "Red-Red-Green",
    "target": "Candle 4 Red",
    "total_usable_patterns": total,
    "successes": successes,
    "failures": failures,
    "overall_success_rate_pct": round(success_rate, 2),
    "overall_failure_rate_pct": round(failure_rate, 2),
    "training_patterns": len(train),
    "testing_patterns": len(test),
    "test_baseline_success_rate_pct": round(test_baseline, 2),
    "decision_tree_test_accuracy_pct": round(test_accuracy, 2),
    "accuracy_minus_baseline_pp": round(
        test_accuracy - test_baseline, 2
    ),
}])

summary.to_csv(
    os.path.join(OUT_DIR, "overall_summary.csv"),
    index=False,
)


# ============================================================
# 11. FINISHED
# ============================================================

print("\n" + "=" * 75)
print("ANALYSIS COMPLETED")
print("=" * 75)

print("Output folder:", os.path.abspath(OUT_DIR))
print("\nFiles saved:")

for filename in [
    "individual_conditions.csv",
    "conditions_ranked_by_failure.csv",
    "decision_tree_rules.txt",
    "test_predictions.csv",
    "overall_summary.csv",
]:
    print(" -", filename)

print("\nSuccess = Candle 4 Red.")
print("Failure = Candle 4 Green.")
print("Compare decision-tree accuracy against the test-period baseline.")
print("Historical patterns do not guarantee future performance.")

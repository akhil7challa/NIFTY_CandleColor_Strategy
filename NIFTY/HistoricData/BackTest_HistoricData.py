
import os
import warnings
import joblib
import numpy as np
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)

warnings.filterwarnings("ignore")

# ============================================================
# CONFIGURATION
# ============================================================

CSV_PATH = r".\Historic_5Min_CandlesData_2016_2026.csv"

OUTPUT_DIR = "nifty_ml_results"

MODEL_PATH = os.path.join(
    OUTPUT_DIR, "nifty_candle_model.joblib"
)

PREDICTIONS_PATH = os.path.join(
    OUTPUT_DIR, "historical_predictions.csv"
)

RULE_REPORT_PATH = os.path.join(
    OUTPUT_DIR, "rule_validation_report.csv"
)

TRAIN_RATIO = 0.80
RANDOM_STATE = 42

# Four input candles predict the fifth candle.
WINDOW_SIZE = 4

COLUMN_ALIASES = {
    "open": ["open", "o"],
    "high": ["high", "h"],
    "low": ["low", "l"],
    "close": ["close", "closing", "c"],
    "volume": ["volume", "vol", "v"],
    "datetime": [
        "datetime", "date_time", "timestamp",
        "date", "time", "dateandtime"
    ],
}


# ============================================================
# YOUR EXISTING RULES
#
# These rules use Candle 2, Candle 3 and Candle 4
# to predict Candle 5.
# ============================================================

RULES = {
    ("Red", "Red", "Green"): "Red",
    ("Green", "Green", "Red"): "Red",
    ("Red", "Green", "Red"): "Red",
    ("Green", "Red", "Green"): "Green",
    ("Red", "Red", "Red"): "Red",
    ("Green", "Green", "Green"): "Red",
    ("Red", "Green", "Green"): "Red",
    ("Green", "Red", "Red"): "Green",
}


# ============================================================
# LOAD AND CLEAN CSV
# ============================================================

def load_data(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"CSV file not found: {os.path.abspath(path)}\n"
            "Update CSV_PATH to the correct file location."
        )

    df = None

    for encoding in [
        "utf-8-sig", "utf-8", "cp1252", "latin1"
    ]:
        try:
            df = pd.read_csv(path, encoding=encoding)
            break
        except UnicodeDecodeError:
            continue

    if df is None:
        raise ValueError("Could not decode the CSV file.")

    # Normalize column names.
    df.columns = [
        str(c).strip().lower().replace(" ", "").replace("_", "")
        for c in df.columns
    ]

    # Duplicate column names can cause ambiguous selections.
    if df.columns.duplicated().any():
        duplicates = df.columns[df.columns.duplicated()].tolist()
        raise ValueError(
            f"Duplicate columns after normalization: {duplicates}"
        )

    normalized_aliases = {
        key: [
            alias.lower().replace(" ", "").replace("_", "")
            for alias in aliases
        ]
        for key, aliases in COLUMN_ALIASES.items()
    }

    columns = {}

    for required in ["open", "high", "low", "close"]:
        match = next(
            (
                col for col in df.columns
                if col in normalized_aliases[required]
            ),
            None,
        )

        if match is None:
            raise ValueError(
                f"Missing required column: {required}\n"
                f"Available columns: {list(df.columns)}"
            )

        columns[required] = match

    for optional in ["volume", "datetime"]:
        match = next(
            (
                col for col in df.columns
                if col in normalized_aliases[optional]
            ),
            None,
        )

        if match is not None:
            columns[optional] = match

    df = df.rename(
        columns={
            original: standard
            for standard, original in columns.items()
        }
    )

    numeric_columns = ["open", "high", "low", "close"]

    if "volume" in df.columns:
        numeric_columns.append("volume")

    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=numeric_columns).copy()

    # Validate OHLC prices.
    df = df[
        (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
        & (
            df["high"]
            >= df[["open", "close", "low"]].max(axis=1)
        )
        & (
            df["low"]
            <= df[["open", "close", "high"]].min(axis=1)
        )
    ].copy()

    # Sort chronologically before creating sequences.
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(
            df["datetime"],
            errors="coerce",
            dayfirst=True,
        )

        df = df.dropna(subset=["datetime"])
        df = df.sort_values("datetime")

    df = df.drop_duplicates().reset_index(drop=True)

    if len(df) < 100:
        raise ValueError(
            f"Only {len(df)} valid rows found. "
            "Check the CSV data."
        )

    print("\n========== DATA SUMMARY ==========")
    print(f"File: {os.path.abspath(path)}")
    print(f"Valid candles: {len(df):,}")

    if "datetime" in df.columns:
        print(f"Start: {df['datetime'].min()}")
        print(f"End:   {df['datetime'].max()}")

    print(f"Columns: {list(df.columns)}")

    return df


# ============================================================
# CANDLE LABELS AND FEATURES
# ============================================================

NUMERIC_FEATURES = [
    "body_pct",
    "range_pct",
    "upper_wick_pct",
    "lower_wick_pct",
    "log_volume",
]


def prepare_candles(df):
    df = df.copy()

    # A doji (Close == Open) is classified as Red.
    df["color"] = np.where(
        df["close"] > df["open"],
        "Green",
        "Red",
    )

    safe_open = df["open"].replace(0, np.nan)

    df["body_pct"] = (
        (df["close"] - df["open"]) / safe_open * 100
    )

    df["range_pct"] = (
        (df["high"] - df["low"]) / safe_open * 100
    )

    df["upper_wick_pct"] = (
        (
            df["high"]
            - df[["open", "close"]].max(axis=1)
        )
        / safe_open
        * 100
    )

    df["lower_wick_pct"] = (
        (
            df[["open", "close"]].min(axis=1)
            - df["low"]
        )
        / safe_open
        * 100
    )

    if "volume" in df.columns:
        df["log_volume"] = np.log1p(
            df["volume"].clip(lower=0)
        )
    else:
        df["log_volume"] = 0.0

    return df


# ============================================================
# BUILD FEATURES FOR FOUR INPUT CANDLES
#
# Candle 1 + Candle 2 + Candle 3 + Candle 4
#                         |
#                         v
#                  Predict Candle 5
#
# The ML model uses all four candles.
# The rules use only Candle 2, Candle 3 and Candle 4.
# ============================================================

def make_feature_row(previous):
    if len(previous) != WINDOW_SIZE:
        raise ValueError(
            f"Expected {WINDOW_SIZE} input candles."
        )

    row = {}

    for position, (_, candle) in enumerate(
        previous.iterrows(),
        start=1,
    ):
        row[f"color_{position}"] = (
            1 if candle["color"] == "Green" else 0
        )

        for feature in NUMERIC_FEATURES:
            row[f"{feature}_{position}"] = float(
                candle[feature]
            )

    first_close = float(previous.iloc[0]["close"])
    last_close = float(previous.iloc[-1]["close"])

    row["close_change_pct_1_to_4"] = (
        (last_close - first_close) / first_close * 100
    )

    return row


def build_sequences(df):
    feature_rows = []
    metadata = []

    # i is the target candle index.
    # i-4, i-3, i-2, i-1 are input candles.
    # i is the candle being predicted.
    for i in range(WINDOW_SIZE, len(df)):
        previous = df.iloc[i - WINDOW_SIZE:i]
        target = df.iloc[i]

        # All four input candle colors.
        colors = tuple(previous["color"].tolist())

        # The existing rules use the final three input candles.
        rule_colors = colors[-3:]

        feature_rows.append(make_feature_row(previous))

        metadata_row = {
            "target_index": i,
            "target_datetime": (
                target["datetime"]
                if "datetime" in df.columns
                else i
            ),
            "actual_candle_5": target["color"],
            "rule_prediction": RULES.get(rule_colors),
            "pattern": " -> ".join(rule_colors),
            "input_pattern_4": " -> ".join(colors),
            "target_open": target["open"],
            "target_high": target["high"],
            "target_low": target["low"],
            "target_close": target["close"],
        }

        # Keep explicit color columns for rule validation.
        for position, color in enumerate(colors, start=1):
            metadata_row[
                f"candle_{position}_color"
            ] = color

        metadata.append(metadata_row)

    X = pd.DataFrame(feature_rows)
    meta = pd.DataFrame(metadata)

    X = X.replace([np.inf, -np.inf], np.nan).fillna(0)

    y = meta["actual_candle_5"].map({
        "Red": 0,
        "Green": 1,
    })

    if y.isna().any():
        raise ValueError("Unexpected target labels found.")

    y = y.astype(int)

    return X, y, meta


# ============================================================
# VALIDATE THE EIGHT EXISTING RULES
# ============================================================

def validate_rules(meta, start, end):
    test = meta.iloc[start:end].copy()

    results = []

    for pattern, expected in RULES.items():
        pattern_name = " -> ".join(pattern)

        matching = test[
            (test["candle_2_color"] == pattern[0])
            & (test["candle_3_color"] == pattern[1])
            & (test["candle_4_color"] == pattern[2])
        ]

        count = len(matching)

        correct = int(
            (
                matching["actual_candle_5"] == expected
            ).sum()
        )

        accuracy = (
            correct / count * 100
            if count else np.nan
        )

        results.append({
            "pattern": pattern_name,
            "rule_prediction": expected,
            "occurrences": count,
            "correct_predictions": correct,
            "incorrect_predictions": count - correct,
            "accuracy_pct": (
                round(accuracy, 2)
                if not np.isnan(accuracy)
                else np.nan
            ),
        })

    report = pd.DataFrame(results)

    total = len(test)
    covered = test["rule_prediction"].notna()
    covered_count = int(covered.sum())

    correct_all = int(
        (
            test.loc[covered, "rule_prediction"]
            == test.loc[covered, "actual_candle_5"]
        ).sum()
    )

    overall_accuracy = (
        correct_all / covered_count * 100
        if covered_count else 0.0
    )

    print("\n========== RULE VALIDATION ==========")
    print(report.to_string(index=False))

    print(f"\nTest sequences: {total:,}")
    print(f"Rule-covered sequences: {covered_count:,}")

    print(
        "Rule coverage: "
        f"{covered_count / total * 100:.2f}%"
        if total else "Rule coverage: 0.00%"
    )

    print(
        "Rule accuracy on covered sequences: "
        f"{overall_accuracy:.2f}%"
    )

    return report


# ============================================================
# TRAIN AND EVALUATE RANDOM FOREST
# ============================================================

def train_model(X, y, meta):
    n = len(X)
    split = int(n * TRAIN_RATIO)

    if split < 50 or (n - split) < 20:
        raise ValueError(
            "Not enough sequences for chronological "
            "train/test evaluation."
        )

    X_train = X.iloc[:split]
    X_test = X.iloc[split:]

    y_train = y.iloc[:split]
    y_test = y.iloc[split:]

    model = RandomForestClassifier(
        n_estimators=400,
        max_depth=12,
        min_samples_leaf=10,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )

    print("\n========== TRAINING ML MODEL ==========")
    print(f"Input candles per prediction: {WINDOW_SIZE}")
    print(f"Training sequences: {len(X_train):,}")
    print(f"Testing sequences:  {len(X_test):,}")

    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    probabilities = model.predict_proba(X_test)

    accuracy = accuracy_score(y_test, predictions)

    print("\n========== ML TEST RESULTS ==========")
    print(f"Test accuracy: {accuracy * 100:.2f}%")

    print("\nClassification report:")

    print(
        classification_report(
            y_test,
            predictions,
            labels=[0, 1],
            target_names=["Red", "Green"],
            zero_division=0,
        )
    )

    print("Confusion matrix (actual rows, predicted columns):")

    print(
        confusion_matrix(
            y_test,
            predictions,
            labels=[0, 1],
        )
    )

    # Save historical out-of-sample test predictions.
    test_meta = meta.iloc[split:].copy().reset_index(drop=True)

    test_meta["ml_prediction"] = np.where(
        predictions == 1,
        "Green",
        "Red",
    )

    classes = list(model.classes_)

    red_index = classes.index(0)
    green_index = classes.index(1)

    test_meta["ml_probability_red"] = probabilities[:, red_index]
    test_meta["ml_probability_green"] = probabilities[:, green_index]

    test_meta["ml_correct"] = (
        test_meta["actual_candle_5"]
        == test_meta["ml_prediction"]
    )

    test_meta["rule_correct"] = (
        test_meta["actual_candle_5"]
        == test_meta["rule_prediction"]
    )

    # Leave rule correctness blank where no rule exists.
    test_meta.loc[
        test_meta["rule_prediction"].isna(),
        "rule_correct",
    ] = np.nan

    test_meta.to_csv(PREDICTIONS_PATH, index=False)

    # Feature importance.
    importance = pd.DataFrame({
        "feature": X.columns,
        "importance": model.feature_importances_,
    }).sort_values(
        "importance",
        ascending=False,
    )

    print("\nTop 15 ML features:")
    print(importance.head(15).to_string(index=False))

    # Compare rules and ML on the same test period.
    covered = test_meta["rule_prediction"].notna()

    if covered.any():
        rule_acc = (
            test_meta.loc[covered, "rule_correct"].mean() * 100
        )

        print(
            "\nRule accuracy on covered test sequences: "
            f"{rule_acc:.2f}%"
        )

    print(f"ML accuracy on test sequences: {accuracy * 100:.2f}%")

    # Save the model and its feature schema.
    joblib.dump(
        {
            "model": model,
            "feature_columns": list(X.columns),
            "rules": RULES,
            "window_size": WINDOW_SIZE,
        },
        MODEL_PATH,
    )

    print(f"\nModel saved to: {MODEL_PATH}")
    print(f"Test predictions saved to: {PREDICTIONS_PATH}")

    return model, test_meta


# ============================================================
# PREDICT THE FIFTH CANDLE AFTER THE LATEST FOUR CANDLES
# ============================================================

def predict_next_candle(df, X, model):
    if len(df) < WINDOW_SIZE:
        raise ValueError(
            f"At least {WINDOW_SIZE} candles are required."
        )

    latest_four = df.iloc[-WINDOW_SIZE:]

    # Build features from the latest four KNOWN candles.
    latest_row = make_feature_row(latest_four)

    latest_features = pd.DataFrame(
        [latest_row],
        columns=X.columns,
    ).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0)

    latest_colors = tuple(latest_four["color"].tolist())

    # Rules use Candle 2, Candle 3 and Candle 4.
    rule_colors = latest_colors[-3:]
    rule_prediction = RULES.get(rule_colors)

    ml_prediction = int(model.predict(latest_features)[0])
    probabilities = model.predict_proba(latest_features)[0]

    classes = list(model.classes_)

    red_probability = probabilities[classes.index(0)]
    green_probability = probabilities[classes.index(1)]

    ml_label = "Green" if ml_prediction == 1 else "Red"

    print("\n========== NEXT CANDLE PREDICTION ==========")

    if "datetime" in df.columns:
        print("Latest known candle time:", latest_four.iloc[-1]["datetime"])

    print("\nInput candle colors:")

    for position, color in enumerate(latest_colors, start=1):
        print(f"Candle {position}: {color}")

    print("\nRule pattern (Candles 2, 3, 4):", rule_colors)
    print("Rule-based Candle 5 prediction:", rule_prediction or "No rule")

    print("\nML prediction for Candle 5:", ml_label)
    print(f"ML probability Red:   {red_probability * 100:.2f}%")
    print(f"ML probability Green: {green_probability * 100:.2f}%")

    print(
        "\nIMPORTANT: The probabilities are model estimates, "
        "not guarantees or necessarily calibrated probabilities."
    )

    print(
        "This prediction is for the candle immediately after "
        "the latest candle in your CSV."
    )


# ============================================================
# MAIN
# ============================================================

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df = load_data(CSV_PATH)
    df = prepare_candles(df)

    X, y, meta = build_sequences(df)

    if len(X) < 100:
        raise ValueError(
            "Insufficient sequences after creating four-candle "
            "input windows."
        )

    split = int(len(X) * TRAIN_RATIO)

    if split < 50 or len(X) - split < 20:
        raise ValueError(
            "Insufficient sequences for model evaluation."
        )

    # Validate rules on the chronological holdout period.
    report = validate_rules(meta, split, len(meta))

    report.to_csv(RULE_REPORT_PATH, index=False)

    print(f"\nRule report saved to: {RULE_REPORT_PATH}")

    model, test_meta = train_model(X, y, meta)

    # Predict the candle after the latest four known candles.
    predict_next_candle(df, X, model)


if __name__ == "__main__":
    main()

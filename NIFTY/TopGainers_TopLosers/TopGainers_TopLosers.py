
from pathlib import Path
from datetime import datetime
from io import BytesIO
import os
import time
import requests
import pandas as pd
import pyotp

from SmartApi import SmartConnect


# ============================================================
# CONFIGURATION
# ============================================================

# Angel One credentials are read from Windows environment variables.
API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"

# Output directory
OUT = Path("AngelOne_Market_Movers")
OUT.mkdir(parents=True, exist_ok=True)

# Angel One instrument master
INSTRUMENT_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Official Nifty Indices constituent files
INDEX_URLS = {
    "NIFTY 50": (
        "https://www.niftyindices.com/"
        "IndexConstituent/ind_nifty50list.csv"
    ),
    "NIFTY NEXT 50": (
        "https://www.niftyindices.com/"
        "IndexConstituent/ind_niftynext50list.csv"
    ),
}

TOP_N = 5

# Keep requests within manageable batches.
BATCH_SIZE = 40

# Delay between quote requests.
REQUEST_DELAY = 1.5


# ============================================================
# VALIDATE CREDENTIALS
# ============================================================

def validate_credentials():

    credentials = {
        "ANGEL_API_KEY": API_KEY,
        "ANGEL_CLIENT_CODE": CLIENT_CODE,
        "ANGEL_PASSWORD": PASSWORD,
        "ANGEL_TOTP_SECRET": TOTP_SECRET,
    }

    missing = [
        name for name, value in credentials.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
            + "\nPlease configure your Angel One credentials."
        )


# ============================================================
# ANGEL ONE LOGIN
# ============================================================

def angel_login():

    print("\nConnecting to Angel One SmartAPI...")

    smart_api = SmartConnect(api_key=API_KEY)

    totp = pyotp.TOTP(TOTP_SECRET).now()

    response = smart_api.generateSession(
        CLIENT_CODE,
        PASSWORD,
        totp,
    )

    if not isinstance(response, dict):
        raise RuntimeError(
            "Invalid Angel One login response."
        )

    if not response.get("status"):

        raise RuntimeError(
            "Angel One login failed: "
            + str(response.get("message", response))
        )

    print("Angel One login successful.")

    return smart_api


# ============================================================
# DOWNLOAD INSTRUMENT MASTER
# ============================================================

def download_instrument_master():

    print("\nDownloading Angel One instrument master...")

    response = requests.get(
        INSTRUMENT_URL,
        timeout=120,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list) or not data:

        raise RuntimeError(
            "Angel One instrument master is empty or invalid."
        )

    df = pd.DataFrame(data)

    aliases = {
        "symbol": "tradingsymbol",
        "token": "symboltoken",
        "exch_seg": "exchange",
    }

    for old, new in aliases.items():

        if old in df.columns and new not in df.columns:
            df.rename(columns={old: new}, inplace=True)

    required = [
        "tradingsymbol",
        "symboltoken",
        "exchange",
    ]

    missing = [
        col for col in required
        if col not in df.columns
    ]

    if missing:
        raise RuntimeError(
            f"Missing instrument master columns: {missing}"
        )

    df["tradingsymbol"] = (
        df["tradingsymbol"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["symboltoken"] = (
        df["symboltoken"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["exchange"] = (
        df["exchange"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # Only NSE equity instruments.
    df = df[
        df["exchange"].eq("NSE")
        & df["tradingsymbol"].str.endswith("-EQ")
    ].copy()

    # Base stock symbol -> Angel One trading symbol/token.
    instrument_map = {}

    for _, row in df.iterrows():

        symbol = row["tradingsymbol"][:-3]

        if symbol and row["symboltoken"]:
            instrument_map[symbol] = {
                "tradingsymbol": row["tradingsymbol"],
                "symboltoken": row["symboltoken"],
            }

    print(
        f"Angel One NSE equity instruments: {len(instrument_map):,}"
    )

    return instrument_map


# ============================================================
# DOWNLOAD INDEX CONSTITUENTS
# ============================================================

def download_constituents():

    print("\nDownloading NIFTY index constituents...")

    session = requests.Session()

    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*",
    })

    index_data = {}

    for index_name, url in INDEX_URLS.items():

        print(f"\nDownloading {index_name} constituents...")

        response = session.get(
            url,
            timeout=60,
        )

        response.raise_for_status()

        df = pd.read_csv(
            BytesIO(response.content)
        )

        # Normalize column names.
        df.columns = [
            str(col).strip().upper()
            for col in df.columns
        ]

        if "SYMBOL" not in df.columns:

            raise RuntimeError(
                f"SYMBOL column missing in {index_name} constituent file. "
                f"Available columns: {list(df.columns)}"
            )

        symbols = (
            df["SYMBOL"]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
            .unique()
            .tolist()
        )

        if not symbols:

            raise RuntimeError(
                f"No constituents found for {index_name}."
            )

        index_data[index_name] = symbols

        print(
            f"{index_name}: {len(symbols)} constituents found."
        )

    return index_data


# ============================================================
# MATCH CONSTITUENTS WITH ANGEL ONE TOKENS
# ============================================================

def prepare_index_instruments(index_data, instrument_map):

    print("\nMatching index constituents with Angel One tokens...")

    matched = {}

    for index_name, symbols in index_data.items():

        instruments = []
        missing = []

        for symbol in symbols:

            instrument = instrument_map.get(symbol)

            if instrument is None:

                missing.append(symbol)

            else:

                instruments.append({
                    "Index": index_name,
                    "Symbol": symbol,
                    "TradingSymbol": instrument["tradingsymbol"],
                    "SymbolToken": instrument["symboltoken"],
                })

        if missing:

            print(
                f"\nWARNING: {len(missing)} unmatched "
                f"symbols in {index_name}:"
            )

            print(", ".join(missing))

        if not instruments:

            raise RuntimeError(
                f"No Angel One instruments matched for {index_name}."
            )

        matched[index_name] = instruments

        print(
            f"{index_name}: {len(instruments)} instruments matched."
        )

    return matched


# ============================================================
# FETCH ANGEL ONE FULL MARKET QUOTES
# ============================================================

def fetch_market_quotes(smart_api, instruments):

    all_quotes = []

    batches = [
        instruments[i:i + BATCH_SIZE]
        for i in range(0, len(instruments), BATCH_SIZE)
    ]

    print(
        f"\nFetching FULL quotes in {len(batches)} batches..."
    )

    for batch_number, batch in enumerate(batches, start=1):

        tokens = [
            item["SymbolToken"]
            for item in batch
        ]

        print(
            f"Fetching batch {batch_number}/{len(batches)} "
            f"({len(tokens)} stocks)..."
        )

        try:

            response = smart_api.getMarketData(
                "FULL",
                {
                    "NSE": tokens
                },
            )

            if not isinstance(response, dict):

                raise RuntimeError(
                    "Invalid market quote response."
                )

            if not response.get("status"):

                raise RuntimeError(
                    str(response.get("message", response))
                )

            data = response.get("data") or {}

            fetched = data.get("fetched", [])

            if not isinstance(fetched, list):

                raise RuntimeError(
                    "Unexpected FULL quote response format."
                )

            for quote in fetched:

                if not isinstance(quote, dict):
                    continue

                token = str(
                    quote.get("symbolToken", "")
                )

                instrument = next(
                    (
                        item for item in batch
                        if item["SymbolToken"] == token
                    ),
                    None,
                )

                if instrument is None:
                    continue

                ltp = pd.to_numeric(
                    quote.get("ltp"),
                    errors="coerce",
                )

                previous_close = pd.to_numeric(
                    quote.get("close"),
                    errors="coerce",
                )

                if pd.isna(ltp) or pd.isna(previous_close):
                    continue

                if previous_close <= 0:
                    continue

                # Calculate change from previous close.
                change = float(ltp) - float(previous_close)

                change_percent = (
                    change / float(previous_close)
                ) * 100

                all_quotes.append({

                    "Index": instrument["Index"],

                    "Symbol": instrument["Symbol"],

                    "Trading_Symbol": instrument["TradingSymbol"],

                    "Symbol_Token": token,

                    "Last_Price": float(ltp),

                    "Previous_Close": float(previous_close),

                    "Change": round(change, 2),

                    "Change_Percent": round(
                        change_percent,
                        2,
                    ),

                    "Open": quote.get("open"),

                    "Day_High": quote.get("high"),

                    "Day_Low": quote.get("low"),

                    "Average_Price": quote.get("avgPrice"),

                    "Traded_Volume": quote.get("tradeVolume"),

                    "Last_Trade_Quantity": quote.get("lastTradeQty"),

                    "Exchange_Update_Time": quote.get("exchFeedTime"),

                    "Downloaded_At": datetime.now().isoformat(
                        timespec="seconds"
                    ),

                })

            print(
                f"Batch {batch_number}: "
                f"{len(fetched)} quotes received."
            )

        except Exception as exc:

            print(
                f"WARNING: Batch {batch_number} failed: {exc}"
            )

        if batch_number < len(batches):
            time.sleep(REQUEST_DELAY)

    if not all_quotes:

        raise RuntimeError(
            "No valid market quotes received from Angel One."
        )

    return pd.DataFrame(all_quotes)


# ============================================================
# FIND TOP GAINERS AND LOSERS
# ============================================================

def calculate_market_movers(df):

    gainers = []
    losers = []

    for index_name in INDEX_URLS.keys():

        index_df = df[
            df["Index"] == index_name
        ].copy()

        if index_df.empty:

            print(
                f"WARNING: No quotes available for {index_name}."
            )

            continue

        # Top 10 gainers.
        top_gainers = (
            index_df.sort_values(
                "Change_Percent",
                ascending=False,
            )
            .head(TOP_N)
            .copy()
        )

        # Top 10 losers.
        top_losers = (
            index_df.sort_values(
                "Change_Percent",
                ascending=True,
            )
            .head(TOP_N)
            .copy()
        )

        gainers.append(top_gainers)
        losers.append(top_losers)

        print("\n" + "=" * 65)
        print(index_name)
        print("=" * 65)

        print("\nTOP GAINERS")

        print(
            top_gainers[
                [
                    "Symbol",
                    "Last_Price",
                    "Change",
                    "Change_Percent",
                ]
            ].to_string(index=False)
        )

        print("\nTOP LOSERS")

        print(
            top_losers[
                [
                    "Symbol",
                    "Last_Price",
                    "Change",
                    "Change_Percent",
                ]
            ].to_string(index=False)
        )

    if not gainers or not losers:

        raise RuntimeError(
            "Unable to calculate gainers and losers."
        )

    final_gainers = pd.concat(
        gainers,
        ignore_index=True,
    )

    final_losers = pd.concat(
        losers,
        ignore_index=True,
    )

    return final_gainers, final_losers


# ============================================================
# SAVE CSV FILES
# ============================================================

def save_output(gainers, losers):

    current_date = datetime.now().strftime("%Y-%m-%d")

    gainers_file = OUT / f"Top_Gainers_{current_date}.csv"
    losers_file = OUT / f"Top_Losers_{current_date}.csv"

    gainers.to_csv(
        gainers_file,
        index=False,
    )

    losers.to_csv(
        losers_file,
        index=False,
    )

    print("\n" + "=" * 65)
    print("CSV FILES SAVED SUCCESSFULLY")
    print("=" * 65)

    print(f"\nTop Gainers: {gainers_file.resolve()}")
    print(f"Top Losers:  {losers_file.resolve()}")

    print(f"\nTotal gainers: {len(gainers)}")
    print(f"Total losers: {len(losers)}")


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)
    print("ANGEL ONE - NIFTY 50 & NIFTY NEXT 50")
    print("CURRENT TOP GAINERS AND TOP LOSERS")
    print("=" * 65)

    validate_credentials()

    smart_api = None

    try:

        # 1. Angel One login.
        smart_api = angel_login()

        # 2. Download Angel One instrument master.
        instrument_map = download_instrument_master()

        # 3. Download current index constituents.
        index_data = download_constituents()

        # 4. Match constituent symbols with Angel tokens.
        matched = prepare_index_instruments(
            index_data,
            instrument_map,
        )

        # 5. Combine all index instruments.
        all_instruments = []

        for instruments in matched.values():
            all_instruments.extend(instruments)

        # 6. Fetch Angel One FULL market quotes.
        market_df = fetch_market_quotes(
            smart_api,
            all_instruments,
        )

        print(
            f"\nTotal valid quotes received: {len(market_df)}"
        )

        # 7. Calculate top gainers and losers.
        gainers, losers = calculate_market_movers(
            market_df
        )

        # 8. Save CSV files.
        save_output(
            gainers,
            losers,
        )

        print("\nDOWNLOAD COMPLETED SUCCESSFULLY.")

    finally:

        if smart_api is not None:

            try:

                smart_api.terminateSession(
                    CLIENT_CODE
                )

                print("\nAngel One session terminated.")

            except Exception:
                pass


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        print("\nDOWNLOAD FAILED:")
        print(exc)

        print(
            "\nPlease check Angel One credentials, "
            "API permissions and internet connection."
        )

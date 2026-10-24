
import requests
import pandas as pd
from io import StringIO
from pathlib import Path

# ============================================================
# CONFIGURATION
# ============================================================

NIFTY50_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
]

ANGEL_URL = (
    "https://margincalculator.angelone.in/"
    "OpenAPI_File/files/OpenAPIScripMaster.json"
)

OUTPUT_FILE = "nifty50_tokens.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/json,*/*",
    "Referer": "https://www.niftyindices.com/",
}


# ============================================================
# DOWNLOAD NIFTY 50 CONSTITUENTS
# ============================================================

def download_nifty50():
    for url in NIFTY50_URLS:
        try:
            print(f"\nDownloading NIFTY 50 list:\n{url}")

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=30,
            )
            response.raise_for_status()

            df = pd.read_csv(StringIO(response.text))
            df.columns = df.columns.str.strip()

            if "Symbol" not in df.columns:
                print("Symbol column not found. Trying next URL.")
                continue

            if "Company Name" not in df.columns:
                df["Company Name"] = df["Symbol"]

            df["Symbol"] = (
                df["Symbol"].astype(str).str.strip().str.upper()
            )

            print(f"Downloaded {len(df)} constituents.")
            return df[["Company Name", "Symbol"]]

        except Exception as exc:
            print(f"Download failed: {exc}")

    # Offline fallback
    local_file = Path(__file__).with_name("ind_nifty50list.csv")

    if local_file.exists():
        print(f"\nUsing local CSV: {local_file}")

        df = pd.read_csv(local_file)
        df.columns = df.columns.str.strip()

        if "Symbol" not in df.columns:
            raise RuntimeError(
                "Local CSV does not contain a Symbol column."
            )

        if "Company Name" not in df.columns:
            df["Company Name"] = df["Symbol"]

        df["Symbol"] = (
            df["Symbol"].astype(str).str.strip().str.upper()
        )

        return df[["Company Name", "Symbol"]]

    raise RuntimeError(
        "Unable to download NIFTY 50 constituents. "
        "Save the official CSV as ind_nifty50list.csv "
        "beside this script and try again."
    )


# ============================================================
# DOWNLOAD ANGEL ONE INSTRUMENT MASTER
# ============================================================

def download_instruments():
    print("\nDownloading Angel One instrument master...")

    response = requests.get(
        ANGEL_URL,
        headers=HEADERS,
        timeout=90,
    )
    response.raise_for_status()

    instruments = pd.DataFrame(response.json())

    if instruments.empty:
        raise RuntimeError("Angel One instrument master is empty.")

    instruments.columns = (
        instruments.columns.str.strip().str.lower()
    )

    required = {"token", "symbol", "exch_seg"}
    missing = required - set(instruments.columns)

    if missing:
        raise RuntimeError(
            f"Missing columns: {missing}\n"
            f"Available columns: {instruments.columns.tolist()}"
        )

    for col in ["token", "symbol", "exch_seg"]:
        instruments[col] = (
            instruments[col].astype(str).str.strip()
        )

    instruments["symbol"] = instruments["symbol"].str.upper()
    instruments["exch_seg"] = instruments["exch_seg"].str.lower()

    print(f"Total instrument records: {len(instruments)}")

    print("\nExchange segments:")
    print(instruments["exch_seg"].value_counts().to_string())

    # Confirm the HDFC Bank record
    hdfc = instruments[
        (instruments["symbol"] == "HDFCBANK-EQ")
        & (instruments["exch_seg"] == "nse")
    ]

    print("\nHDFC Bank verification:")
    if not hdfc.empty:
        print(hdfc[["symbol", "token", "exch_seg"]].to_string(index=False))
    else:
        print("HDFCBANK-EQ was not found in NSE segment.")

    return instruments


# ============================================================
# MATCH SYMBOLS AND TOKENS
# ============================================================

def match_tokens(nifty, instruments):

    # FIX: Your instrument master uses 'nse', not 'nse_cm'.
    nse = instruments[
        instruments["exch_seg"].eq("nse")
    ].copy()

    print(f"\nNSE instruments found: {len(nse)}")

    # Select standard equity instruments ending in -EQ
    equity = nse[
        nse["symbol"].str.endswith("-EQ")
    ].copy()

    # Example: HDFCBANK-EQ -> HDFCBANK
    equity["NSE_Symbol"] = equity["symbol"].str[:-3]

    # Keep one record per underlying trading symbol
    equity = equity.drop_duplicates(subset=["NSE_Symbol"])

    nifty["Symbol"] = (
        nifty["Symbol"].astype(str).str.strip().str.upper()
    )

    # Match the NIFTY constituent symbols
    result = nifty.merge(
        equity[["NSE_Symbol", "symbol", "token"]],
        left_on="Symbol",
        right_on="NSE_Symbol",
        how="left",
    )

    result = result.rename(columns={
        "Company Name": "Company",
        "Symbol": "NSE_Symbol",
        "symbol": "Trading_Symbol",
        "token": "Symbol_Token",
    })

    # Final output columns
    output = result[
        ["Company", "NSE_Symbol", "Trading_Symbol", "Symbol_Token"]
    ].copy()

    return output


# ============================================================
# MAIN
# ============================================================

def main():
    try:
        nifty = download_nifty50()
        instruments = download_instruments()

        result = match_tokens(nifty, instruments)

        output_path = Path(__file__).with_name(OUTPUT_FILE)
        result.to_csv(output_path, index=False)

        matched = result["Symbol_Token"].notna().sum()
        total = len(result)

        print("\n" + "=" * 90)
        print("NIFTY 50 SYMBOL TOKENS - ANGEL ONE")
        print("=" * 90)
        print(result.to_string(index=False))

        print("\n" + "=" * 90)
        print(f"Matched:   {matched} / {total}")
        print(f"Unmatched: {total - matched}")
        print(f"CSV file:  {output_path}")
        print("=" * 90)

        if matched < total:
            print("\nSymbols without tokens:")
            print(
                result.loc[
                    result["Symbol_Token"].isna(),
                    "NSE_Symbol"
                ].to_list()
            )

        if matched == 0:
            print(
                "\nWARNING: No matches found. "
                "Check the NSE segment and HDFC Bank diagnostic output."
            )

    except requests.RequestException as exc:
        print(f"\nNetwork error: {exc}")

    except Exception as exc:
        print(f"\nError: {exc}")


if __name__ == "__main__":
    main()

import time
from datetime import datetime, timedelta
import pandas as pd
import pyotp
from SmartApi import SmartConnect

# ==================== CONFIGURATION ====================
API_KEY = "MpxkUXqu"
CLIENT_CODE = "AAAD431807"
PASSWORD = "1606"
TOTP_SECRET = "H2FHHI2DVFW5HK64J3GQWMVZ3E"
CSV_FILE_PATH = r"C:\Users\challa\Desktop\Challa\Personal\NSE\SymbolTokens\nifty50_tokens.csv"  # Path to your CSV file
# =======================================================

def initialize_smart_api():
    """Initializes and authenticates the SmartAPI connection."""
    print("Connecting to Angel One SmartAPI...")
    smartApi = SmartConnect(api_key=API_KEY)
    
    totp = pyotp.TOTP(TOTP_SECRET).now()
    session = smartApi.generateSession(CLIENT_CODE, PASSWORD, totp)
    
    if session['status'] == False:
        raise Exception(f"Login Failed: {session['message']}")
    
    print("Login Successful!")
    return smartApi

def fetch_candles(smartApi, symbol_token, exchange, interval, days_back):
    """Fetches historical candle data based on the interval and lookback period."""
    to_date = datetime.now()
    from_date = to_date - timedelta(days=days_back)
    
    historicParam = {
        "exchange": exchange,
        "symboltoken": str(symbol_token),
        "interval": interval,
        "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
        "todate": to_date.strftime("%Y-%m-%d %H:%M")
    }
    
    try:
        response = smartApi.getCandleData(historicParam)
        if response and response.get('status') and response.get('data'):
            df = pd.DataFrame(response['data'], columns=['date', 'open', 'high', 'low', 'close', 'volume'])
            df['date'] = pd.to_datetime(df['date'])
            df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
            df.set_index('date', inplace=True)
            return df
    except Exception as e:
        print(f"Error fetching {interval} data for token {symbol_token}: {e}")
    
    return pd.DataFrame()

def detect_trend(df, window=3):
    """
    Analyzes market structure using Higher High - Higher Lower (HH-HL) 
    or Lower Low - Lower High (LL-LH).
    """
    if df is None or len(df) < window * 2:
        return "Insufficient Data"
    
    highs = df['high'].values
    lows = df['low'].values
    
    swing_highs = []
    swing_lows = []
    
    # Identify local swing highs and lows
    for i in range(window, len(df) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i-window:i+window+1]):
            swing_lows.append(lows[i])
            
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "Uptrend (Weak)" if df['close'].iloc[-1] > df['close'].iloc[0] else "Downtrend (Weak)"
    
    last_sh, prev_sh = swing_highs[-1], swing_highs[-2]
    last_sl, prev_sl = swing_lows[-1], swing_lows[-2]
    
    if last_sh > prev_sh and last_sl > prev_sl:
        return "Uptrend (HH - HL)"
    elif last_sh < prev_sh and last_sl < prev_sl:
        return "Downtrend (LH - LL)"
    else:
        return "Sideways / Consolidation"

def main():
    smartApi = initialize_smart_api()
    
    try:
        df_stocks = pd.read_csv(CSV_FILE_PATH)
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return
    
    results = []
    delay_between_requests = 0.33  # Rate limit safety (~3 requests per second)

    print(f"\nProcessing {len(df_stocks)} stocks from {CSV_FILE_PATH}...\n")
    print(f"{'Company':<20} | {'1H Trend (100C)':<22} | {'15M Trend (100C)':<22} | {'5M Trend (150C)':<22}")
    print("-" * 92)

    for index, row in df_stocks.iterrows():
        company = str(row.get('Company', 'Unknown'))
        symbol = str(row.get('NSE_Symbol', ''))
        token = str(row.get('Symbol_Token', ''))
        
        if not token or token == 'nan':
            continue

        # --- 1. 1 Hour Data (Last 100 Candles) ---
        # ~25 calendar days covers ~100 trading hours
        df_1h = fetch_candles(smartApi, token, "NSE", "ONE_HOUR", days_back=25)
        if not df_1h.empty:
            df_1h = df_1h.tail(100)
        trend_1h = detect_trend(df_1h, window=3)
        time.sleep(delay_between_requests)

        # --- 2. 15 Minutes Data (Last 100 Candles) ---
        # ~7 calendar days covers ~100 15-minute candles
        df_15m = fetch_candles(smartApi, token, "NSE", "FIFTEEN_MINUTE", days_back=7)
        if not df_15m.empty:
            df_15m = df_15m.tail(100)
        trend_15m = detect_trend(df_15m, window=3)
        time.sleep(delay_between_requests)

        # --- 3. 5 Minutes Data (Last 150 Candles) ---
        # ~5 calendar days covers ~300+ 5-minute candles
        df_5m = fetch_candles(smartApi, token, "NSE", "FIVE_MINUTE", days_back=5)
        if not df_5m.empty:
            df_5m = df_5m.tail(150)
        trend_5m = detect_trend(df_5m, window=3)
        time.sleep(delay_between_requests)

        # Print live console row
        print(f"{company[:18]:<20} | {trend_1h:<22} | {trend_15m:<22} | {trend_5m:<22}")

        results.append({
            "Company": company,
            "NSE_Symbol": symbol,
            "Trend_1H_100C": trend_1h,
            "Trend_15M_100C": trend_15m,
            "Trend_5M_150C": trend_5m
        })

    # Save summary report to CSV
    output_df = pd.DataFrame(results)
    output_df.to_csv("stock_short_timeframe_trends.csv", index=False)
    print("\nProcessing complete! Results saved to 'stock_short_timeframe_trends.csv'.")

if __name__ == "__main__":
    main()
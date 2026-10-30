
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas_market_calendars as mcal

# Your Python script
SCRIPT_PATH = r".\TopGainers_TopLosers.py"

# NSE timezone
IST = ZoneInfo("Asia/Kolkata")

def is_nse_trading_day():
    today = datetime.now(IST).date()

    nse = mcal.get_calendar("NSE")

    schedule = nse.schedule(
        start_date=today,
        end_date=today
    )

    return not schedule.empty


def run_script():
    if is_nse_trading_day():
        print("NSE trading day. Executing script...")

        subprocess.run(
            [sys.executable, SCRIPT_PATH],
            check=True
        )
    else:
        print("NSE holiday or weekend. Skipping.")


if __name__ == "__main__":
    run_script()

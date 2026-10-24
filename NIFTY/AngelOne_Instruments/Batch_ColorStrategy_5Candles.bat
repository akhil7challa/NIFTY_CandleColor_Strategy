
@echo off
setlocal

title NIFTY Trading Strategy Launcher

REM ============================================================
REM NIFTY Strategy Launcher
REM Starts Trading_ColorStrategy_5Candles.py at 09:10 IST
REM Monday through Friday.
REM
REM Save this BAT file in the same folder as the Python script.
REM ============================================================

cd /d "%~dp0"

REM --- Python script filename ---
set "SCRIPT=Trading_ColorStrategy_5Candles.py"

REM --- Check that the Python script exists ---
if not exist "%SCRIPT%" (
    echo ERROR: Cannot find "%SCRIPT%" in:
    echo %CD%
    echo.
    pause
    exit /b 2
)

echo ============================================================
echo NIFTY Trading Strategy Launcher
echo ============================================================
echo.
echo Waiting for 09:30 India Standard Time on weekdays.
echo Press Ctrl+C to cancel.
echo.

REM --- Wait until 09:30 IST using Windows time-zone data ---
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$tz = [TimeZoneInfo]::FindSystemTimeZoneById('India Standard Time');" ^
  "while ($true) {" ^
  "  $now = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::Now, $tz);" ^
  "  if ($now.DayOfWeek -eq 'Saturday' -or $now.DayOfWeek -eq 'Sunday') {" ^
  "    Start-Sleep -Seconds 60; continue" ^
  "  };" ^
  "  if ($now.TimeOfDay -ge [TimeSpan]::Parse('09:30:15') -and $now.TimeOfDay -lt [TimeSpan]::Parse('09:31:00')) { exit 0 };" ^
  "  if ($now.TimeOfDay -ge [TimeSpan]::Parse('09:31:00')) { exit 1 };" ^
  "  Start-Sleep -Seconds 10;" ^
  "}"

if errorlevel 1 (
    echo Outside the allowed start time. Not starting strategy.
    pause
    exit /b 1
)

echo.
echo Starting strategy at the scheduled time...
echo.

REM --- Launch the strategy ---
python "%SCRIPT%"

set "EXITCODE=%ERRORLEVEL%"

echo.
echo ============================================================
echo Strategy process ended with exit code %EXITCODE%.
echo ============================================================
pause

exit /b %EXITCODE%

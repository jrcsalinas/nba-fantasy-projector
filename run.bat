@echo off
rem Starts NBA Fantasy Projector: loads the latest data, starts the daily updater, and opens the
rem dashboard. Double-click this file, or run it from a terminal. Close both windows to stop.
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed. Install it from https://docs.astral.sh/uv/ and run this again.
  pause
  exit /b 1
)

echo Installing dependencies...
uv sync
if errorlevel 1 (
  echo Setup failed. See the messages above.
  pause
  exit /b 1
)

echo Loading the latest game data...
uv run python -m nba_projector.jobs.refresh --latest
if errorlevel 1 echo Could not load new data; continuing with whatever is already saved.

echo Starting the daily updater in a new window. It updates after each day's last game.
start "NBA Projector - daily updater" cmd /k uv run python -m nba_projector.jobs.refresh --daemon

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000/"

echo.
echo Dashboard: http://127.0.0.1:8000/   (close this window and the updater window to stop)
echo.
uv run uvicorn nba_projector.main:app --port 8000
pause

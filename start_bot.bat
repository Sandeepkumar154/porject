@echo off
title Groww Trading Bot v2 - Live Engine
echo ===================================================
echo     GROWW TRADING BOT v2 - AUTO-SCANNER
echo ===================================================
echo.
echo Starting Automated 8-Shield Scanner & Telegram Engine...
echo.
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause

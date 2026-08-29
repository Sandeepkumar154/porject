@echo off
title Master Trading Plan v2 — Mobile Server
cd /d "%~dp0"
echo ======================================================================
echo   Starting Master Trading Plan v2 Mobile Dashboard Server...
echo ======================================================================
echo.
python server.py
pause

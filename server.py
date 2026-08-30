"""
server.py — FastAPI Mobile Web Server for Master Trading Plan v2.

Provides a responsive, high-speed mobile web dashboard and REST APIs:
  - Live 8-Shield Scanner
  - Global Market Check (0-5 score)
  - FII/DII Sentiment
  - Instant Trade Setups (Entry, SL, T1, T2, Sizing)
  - Mobile Backtester & Performance Visualizer
  - Telegram Alert Triggering
"""

import math
import os
import socket
from datetime import datetime, time, date
from pathlib import Path
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

from config import StrategyConfig
from global_check import check_global_markets, GlobalMarketResult
from fii_dii import check_fii_dii, FIIDIIResult
from indicators import (
    compute_all_indicators_15m, compute_vwap_5m,
    shift_indicators, merge_15m_indicators_to_5m, compute_atr,
    COL_SUPERTREND, COL_VWAP, COL_RSI, COL_ADX,
)
from signals import (
    score_bar, assess_nifty_direction, get_current_window,
)
from backtest_engine import run_backtest
from report import calculate_metrics
from live_scanner import fetch_live_data
from telegram_bot import (
    send_telegram_message, send_trade_signal_alert,
    send_global_morning_alert,
)

app = FastAPI(title="Master Trading Plan v2 Mobile Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATES_DIR.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def sanitize(val: Any, default: float = 0.0) -> Any:
    """Ensure floats are JSON-serializable (replace NaN / Inf)."""
    if val is None:
        return default
    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or math.isinf(val):
            return default
        return float(val)
    if isinstance(val, (int, np.integer)):
        return int(val)
    return val


def get_local_ip() -> str:
    """Get LAN IP address for mobile connection."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ═══════════════════════════════════════════════════════════════════
# REST API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    """Health check endpoint for Render."""
    return {"status": "ok"}


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Serve mobile-optimized dashboard."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"local_ip": get_local_ip()},
    )


@app.get("/api/status")
async def get_system_status():
    """Get system and market status."""
    now = datetime.now()
    ct = now.time()
    config = StrategyConfig()
    window = get_current_window(ct, config)

    market_open = (time(9, 15) <= ct <= time(15, 30)) and (now.weekday() < 5)

    return {
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "is_market_open": market_open,
        "window_name": window.window_name,
        "window_active": window.is_active,
        "window_number": window.window_number,
        "max_trades": window.max_trades,
        "total_capital": config.total_capital,
        "local_ip": get_local_ip(),
    }


@app.get("/api/global")
async def get_global_check():
    """Get Layer 1 Global Market Check."""
    config = StrategyConfig()
    today = date.today()
    res = check_global_markets(config, today)

    return {
        "global_score": sanitize(res.global_score, 3),
        "can_trade": bool(res.can_trade),
        "position_size_multiplier": sanitize(res.position_size_multiplier, 1.0),
        "description": str(res.description),
        "gift_nifty_gap": sanitize(res.gift_nifty_gap),
        "gift_nifty_signal": str(res.gift_nifty_signal),
        "us_dow_change": sanitize(res.us_dow_change),
        "us_nasdaq_change": sanitize(res.us_nasdaq_change),
        "us_sp500_change": sanitize(res.us_sp500_change),
        "us_signal": str(res.us_signal),
        "crude_change": sanitize(res.crude_change),
        "crude_signal": str(res.crude_signal),
        "usdinr_change": sanitize(res.usdinr_change),
        "currency_signal": str(res.currency_signal),
        "asia_nikkei_change": sanitize(res.asia_nikkei_change),
        "asia_hangseng_change": sanitize(res.asia_hangseng_change),
        "asia_signal": str(res.asia_signal),
        "sector_bias": res.sector_bias or [],
    }


@app.get("/api/fii")
async def get_fii_dii():
    """Get Layer 2 FII/DII data."""
    config = StrategyConfig()
    today = date.today()
    res = check_fii_dii(today, config, str(Path(__file__).parent))

    return {
        "scenario": sanitize(res.scenario, 3),
        "scenario_name": str(res.scenario_name),
        "signal": str(res.signal),
        "score_bonus": sanitize(res.score_bonus),
        "fii_net_cr": sanitize(res.fii_net_cr),
        "dii_net_cr": sanitize(res.dii_net_cr),
        "fii_action": str(res.fii_action),
        "dii_action": str(res.dii_action),
        "can_trade": bool(res.can_trade),
        "description": str(res.description),
    }


@app.get("/api/scan")
async def run_live_scan(symbols: Optional[str] = None):
    """Run real-time 8-shield scan on watchlist."""
    config = StrategyConfig()
    now = datetime.now()
    ct = now.time()
    window = get_current_window(ct, config)

    if symbols:
        stock_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        stock_list = config.watchlist[:10]

    global_res = check_global_markets(config, now.date())
    fii_res = check_fii_dii(now.date(), config, str(Path(__file__).parent))

    nifty_df = fetch_live_data("^NSEI", "5m", "2d")
    nifty_dir = assess_nifty_direction(nifty_df.tail(6), config)

    cards = []

    for sym in stock_list:
        try:
            df_15m = fetch_live_data(f"{sym}.NS", "15m", "5d")
            df_5m = fetch_live_data(f"{sym}.NS", "5m", "5d")
            if df_15m.empty or df_5m.empty or len(df_15m) < 20 or len(df_5m) < 20:
                continue

            df_15m = compute_all_indicators_15m(df_15m, config)
            df_15m = shift_indicators(df_15m)
            df_5m = compute_vwap_5m(df_5m)
            merged = merge_15m_indicators_to_5m(df_5m, df_15m)
            if merged.empty:
                continue

            latest_bar = merged.iloc[-1]
            prev_bars = df_5m[df_5m.index.date < now.date()]
            prev_avg_vol = prev_bars["Volume"].mean() if not prev_bars.empty else 0

            sig = score_bar(
                latest_bar, config, prev_avg_vol,
                global_score=global_res.global_score,
                fii_bonus=fii_res.score_bonus,
            )

            close_p = sanitize(latest_bar.get("Close", 0))
            vwap_p = sanitize(latest_bar.get(COL_VWAP, 0))
            rsi_val = sanitize(latest_bar.get(COL_RSI, 0))
            adx_val = sanitize(latest_bar.get(COL_ADX, 0))
            st_val = sanitize(latest_bar.get(COL_SUPERTREND, 0))

            shields_info = {}
            for sname, sres in sig.shields.items():
                shields_info[sname] = {
                    "passed": bool(sres.passed),
                    "reason": str(sres.reason),
                    "points": sanitize(sres.points),
                }

            sl_p = round(st_val - config.indicators.supertrend.sl_buffer, 2)
            sl_dist = round(close_p - sl_p, 2)
            if sl_dist <= 0:
                sl_dist = round(close_p * 0.01, 2)
                sl_p = round(close_p - sl_dist, 2)

            risk_pct = config.get_risk_pct(sig.score)
            risk_amount = config.total_capital * risk_pct
            qty = max(1, int(risk_amount / sl_dist)) if sl_dist > 0 else 1

            t1_p = round(close_p + (config.targets.t1_multiplier * sl_dist), 2)
            t2_p = round(close_p + (config.targets.t2_multiplier * sl_dist), 2)

            cards.append({
                "symbol": sym,
                "score": sanitize(sig.score),
                "base_score": sanitize(sig.base_score),
                "bonus_score": sanitize(sig.bonus_score),
                "grade": str(sig.recommendation),
                "all_shields_pass": bool(sig.all_shields_pass),
                "is_entry": bool(sig.is_entry),
                "price": close_p,
                "vwap": vwap_p,
                "rsi": rsi_val,
                "adx": adx_val,
                "sl": sl_p,
                "sl_risk": sl_dist,
                "t1": t1_p,
                "t2": t2_p,
                "qty": qty,
                "risk_amount": sanitize(risk_amount),
                "risk_pct": sanitize(risk_pct * 100),
                "shields": shields_info,
                "bonuses": [{"name": b.name, "points": sanitize(b.points), "reason": str(b.reason)} for b in sig.bonuses],
            })
        except Exception as e:
            print(f"  [SERVER SCAN ERROR] {sym}: {e}")

    grade_order = {"ELITE": 0, "STRONG": 1, "AVERAGE": 2, "SKIP": 3}
    cards.sort(key=lambda x: (grade_order.get(x["grade"], 9), -x["score"]))

    return {
        "timestamp": now.strftime("%H:%M:%S"),
        "window": window.window_name,
        "nifty_change": sanitize(nifty_dir.change_pct),
        "nifty_direction": str(nifty_dir.direction),
        "global_score": sanitize(global_res.global_score),
        "stocks": cards,
    }


class BacktestRequest(BaseModel):
    symbols: Optional[List[str]] = None
    capital: Optional[float] = 100000.0
    period: Optional[str] = "60d"


@app.post("/api/backtest")
async def run_mobile_backtest(req: BacktestRequest):
    """Execute backtest from mobile."""
    config = StrategyConfig()
    if req.capital:
        config.total_capital = req.capital
    if req.period:
        config.data_period = req.period

    symbols = req.symbols if req.symbols else config.watchlist[:6]

    trades, engine = run_backtest(config=config, symbols=symbols)
    metrics = calculate_metrics(trades, engine.daily_summaries, config.total_capital)

    trades_list = []
    for t in trades:
        trades_list.append({
            "symbol": t.symbol,
            "entry_time": str(t.entry_time)[:16] if t.entry_time else "",
            "entry_price": sanitize(t.entry_price),
            "exit_time": str(t.exit_time)[:16] if t.exit_time else "",
            "exit_price": sanitize(t.avg_exit_price),
            "qty": t.total_qty,
            "exit_reason": str(t.exit_reason),
            "net_pnl": sanitize(t.net_pnl),
            "score": sanitize(t.score),
            "grade": str(t.grade),
            "window": str(t.window),
            "is_winner": bool(t.is_winner),
        })

    clean_metrics = {}
    for k, v in metrics.items():
        if isinstance(v, (float, np.floating, int, np.integer)):
            clean_metrics[k] = sanitize(v)
        else:
            clean_metrics[k] = v

    return {
        "metrics": clean_metrics,
        "trades": trades_list,
        "skipped_days": engine.skipped_days,
    }


@app.post("/api/telegram/test")
async def trigger_test_telegram():
    """Send test alert to user's Telegram."""
    success = send_telegram_message(
        "🚀 <b>Master Trading Plan v2 Mobile Portal Connected!</b>\n"
        "You will now receive real-time ELITE & STRONG trade signals on your mobile phone."
    )
    return {"success": success}


if __name__ == "__main__":
    local_ip = get_local_ip()
    port = 8000
    print("\n" + "=" * 70)
    print("  📱 MASTER TRADING PLAN v2 — MOBILE DASHBOARD SERVER")
    print("=" * 70)
    print(f"  PC Local URL   : http://localhost:{port}")
    print(f"  📱 Mobile URL  : http://{local_ip}:{port}")
    print(f"  (Connect your phone to the same WiFi and open the Mobile URL)")
    print("=" * 70 + "\n")

    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)

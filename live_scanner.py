"""
live_scanner.py — Live Paper Trading Scanner for Master Trading Plan v2.

Monitors NSE stocks in real-time during market hours (9:15 AM - 3:30 PM IST):
  - Layer 1: Global Market Check & Score
  - Layer 2: FII/DII Sentiment
  - Layer 5: 8 Technical Shields
  - Layer 6: Dynamic Position Sizing (ELITE/STRONG/AVERAGE)
  - Active Trading Window (Prime, Momentum, Continuation, Dead Zone)

Usage:
    python live_scanner.py
    python live_scanner.py --symbols SBIN RELIANCE HCLTECH
    python live_scanner.py --once
"""

import argparse
import os
import sys
import time as time_module
from datetime import datetime, time, date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

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
    COL_EMA_9, COL_EMA_21, COL_EMA_50,
)
from signals import (
    score_bar, assess_nifty_direction, get_current_window,
)

IST = "Asia/Kolkata"


def fetch_live_data(ticker: str, interval: str = "5m", period: str = "5d") -> pd.DataFrame:
    """Fetch recent data for live scanning."""
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(period=period, interval=interval)
        if df.empty:
            return df
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC").tz_convert(IST)
        else:
            df.index = df.index.tz_convert(IST)
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].copy()
        df.dropna(inplace=True)
        return df
    except Exception as e:
        print(f"  [ERROR] Fetch failed for {ticker}: {e}")
        return pd.DataFrame()


def print_signal_card(symbol: str, signal, bar, window, global_res: GlobalMarketResult,
                      fii_res: FIIDIIResult, config: StrategyConfig, atr_val: float = 0.0):
    """Print signal card for 8 shields."""
    score = signal.score
    grade = signal.recommendation
    close = bar.get("Close", 0)
    vwap = bar.get(COL_VWAP, 0)
    rsi = bar.get(COL_RSI, 0)
    adx = bar.get(COL_ADX, 0)

    print(f"\n  {'~' * 60}")
    print(f"  {symbol:<12} | Score: {score:.0f}/48 | [{grade:<7}] | Window: {window.window_name}")
    print(f"  {'~' * 60}")
    print(f"    Price  : Rs.{close:,.2f}  |  VWAP: Rs.{vwap:,.2f} ({'ABOVE' if close > vwap else 'BELOW'})")
    print(f"    RSI    : {rsi:.1f}         |  ADX : {adx:.1f}")

    print(f"    8 Shields:")
    for name, shield in signal.shields.items():
        icon = "PASS" if shield.passed else "FAIL"
        print(f"      [{icon}] {name:<14}: {shield.reason}")

    if signal.bonuses:
        print(f"    Bonuses (+{signal.bonus_score:.0f} pts):")
        for b in signal.bonuses:
            print(f"      + {b.name:<18} (+{b.points:.0f} pts): {b.reason}")

    if signal.is_entry:
        st_val = bar.get(COL_SUPERTREND, 0)
        sl = st_val - config.indicators.supertrend.sl_buffer
        sl_dist = close - sl
        if sl_dist <= 0:
            sl_dist = close * 0.01
            sl = close - sl_dist

        risk_pct = config.get_risk_pct(score)
        risk_amount = config.total_capital * risk_pct
        qty = max(1, int(risk_amount / sl_dist))

        t1 = close + (config.targets.t1_multiplier * sl_dist)
        t2 = close + (config.targets.t2_multiplier * sl_dist)

        print(f"\n    >>> 🎯 TRADE SETUP ({grade}):")
        print(f"    Entry      : Rs.{close:,.2f}")
        print(f"    Stop Loss  : Rs.{sl:,.2f} (Risk: Rs.{sl_dist:,.2f} / share)")
        print(f"    Target 1   : Rs.{t1:,.2f} (Exit 60% -> SL to Breakeven)")
        if not window.t1_only:
            print(f"    Target 2   : Rs.{t2:,.2f} (Exit remaining 40%)")
        print(f"    Quantity   : {qty} shares (Risk: Rs.{risk_amount:,.0f} = {risk_pct*100:.1f}%)")


def scan_once(symbols: List[str], config: StrategyConfig) -> Dict:
    """Run one scan across all symbols."""
    now = datetime.now()
    ct = now.time()

    print(f"\n  Scan Time: {now.strftime('%H:%M:%S')} IST")
    print(f"  {'=' * 60}")

    # Window detection
    window = get_current_window(ct, config)
    print(f"  Current Window : {window.window_name} (Active: {window.is_active})")

    # Layer 1: Global check
    global_res = check_global_markets(config, now.date())
    print(f"  Global Check   : Score {global_res.global_score}/5 | {global_res.description}")

    # Layer 2: FII/DII
    fii_res = check_fii_dii(now.date(), config, str(Path(__file__).parent))
    print(f"  FII/DII Check  : {fii_res.description}")

    # Nifty 50
    nifty_df = fetch_live_data("^NSEI", "5m", "2d")
    nifty_dir = assess_nifty_direction(nifty_df.tail(6), config)
    print(f"  Nifty 50       : {nifty_dir.change_pct:+.2f}% | {nifty_dir.direction}")

    results = {}

    for symbol in symbols:
        try:
            df_15m = fetch_live_data(f"{symbol}.NS", "15m", "5d")
            df_5m = fetch_live_data(f"{symbol}.NS", "5m", "5d")

            if df_15m.empty or df_5m.empty or len(df_15m) < 20 or len(df_5m) < 20:
                continue

            df_15m = compute_all_indicators_15m(df_15m, config)
            df_15m = shift_indicators(df_15m)
            df_5m = compute_vwap_5m(df_5m)
            df_merged = merge_15m_indicators_to_5m(df_5m, df_15m)
            atr_series = compute_atr(df_15m, 14)
            atr_val = float(atr_series.iloc[-1]) if not atr_series.empty and not pd.isna(atr_series.iloc[-1]) else 0.0

            if df_merged.empty:
                continue

            latest_bar = df_merged.iloc[-1]
            prev_bars = df_5m[df_5m.index.date < now.date()]
            prev_avg_vol = prev_bars["Volume"].mean() if not prev_bars.empty else 0

            signal = score_bar(
                latest_bar, config, prev_avg_vol,
                global_score=global_res.global_score,
                fii_bonus=fii_res.score_bonus,
            )

            results[symbol] = signal
            print_signal_card(symbol, signal, latest_bar, window, global_res, fii_res, config, atr_val)

        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")

    return results


def run_scanner(symbols: Optional[List[str]] = None,
                interval_sec: int = 60,
                capital: float = 100000.0):
    """Run live scanner loop."""
    config = StrategyConfig()
    config.total_capital = capital
    if symbols is None:
        symbols = config.watchlist[:8]

    print("\n" + "=" * 70)
    print("  🏆 MASTER TRADING PLAN v2 — LIVE MARKET SCANNER")
    print("  8 Shields | 3 Windows | 48-Point Scoring")
    print(f"  Watching: {', '.join(symbols)}")
    print(f"  Capital : Rs.{capital:,.0f}")
    print("=" * 70 + "\n")

    scan_num = 0
    try:
        while True:
            scan_num += 1
            print(f"\n{'#' * 70}")
            print(f"  SCAN #{scan_num}")
            print(f"{'#' * 70}")

            results = scan_once(symbols, config)

            entries = [s for s, r in results.items() if r.is_entry]
            if entries:
                print(f"\n  🔥 ACTIONABLE ENTRIES: {', '.join(entries)}")
            else:
                print(f"\n  No valid 8-shield entry signals this scan.")

            now_time = datetime.now().time()
            if now_time > time(15, 30):
                print("\n  Market closed. Scanner finished.")
                break

            print(f"\n  Next scan in {interval_sec}s (Ctrl+C to stop)...")
            time_module.sleep(interval_sec)

    except KeyboardInterrupt:
        print(f"\n\n  Scanner stopped by user. Total scans: {scan_num}")


def main():
    parser = argparse.ArgumentParser(description="Live Market Scanner v2")
    parser.add_argument("--symbols", "-s", nargs="+", default=None)
    parser.add_argument("--interval", "-i", type=int, default=60)
    parser.add_argument("--capital", "-c", type=float, default=100000.0)
    parser.add_argument("--once", action="store_true")

    args = parser.parse_args()

    if args.once:
        config = StrategyConfig()
        config.total_capital = args.capital
        symbols = args.symbols or config.watchlist[:8]
        scan_once(symbols, config)
    else:
        run_scanner(args.symbols, args.interval, args.capital)


if __name__ == "__main__":
    main()

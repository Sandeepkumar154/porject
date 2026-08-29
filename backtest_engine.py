"""
backtest_engine.py — 6-Layer Event-Driven Backtesting Engine.

Orchestrates:
  Layer 1: Global market check → position size multiplier
  Layer 2: FII/DII check → score bonus + position adjustment
  Layer 3: News (manual input, bonuses applied if available)
  Layer 4: Stock data preparation + indicator computation
  Layer 5: 8-shield scoring per bar
  Layer 6: Risk management (daily/weekly/monthly)

Day-first iteration with 3 windows + dead zone.
"""

from datetime import datetime, time, date
from typing import List, Optional, Dict, Tuple

import pandas as pd
import numpy as np

from config import StrategyConfig
from data_fetcher import (
    fetch_stock_data, fetch_nifty_data, fetch_vix_data,
    get_trading_days, get_day_data, get_previous_day_data,
)
from indicators import (
    compute_all_indicators_15m, compute_vwap_5m,
    shift_indicators, merge_15m_indicators_to_5m, compute_atr,
    COL_SUPERTREND, COL_VWAP,
)
from signals import (
    score_bar, assess_nifty_direction, check_5min_confirmation,
    check_vwap_pullback, get_current_window, check_window2_conditions,
    check_window3_conditions, NiftyDirection,
)
from position_manager import (
    create_position, check_position, finalize_trade,
    Position, TradeResult, ExitReason, _exit_all,
)
from risk_manager import RiskManager
from global_check import check_global_markets, GlobalMarketResult
from fii_dii import check_fii_dii, FIIDIIResult


class BacktestEngine:
    """6-Layer Professional Backtesting Engine."""

    def __init__(self, config: StrategyConfig,
                 groww_token: Optional[str] = None):
        self.config = config
        self.groww_token = groww_token
        self.risk_manager = RiskManager(config)
        self.all_trades: List[TradeResult] = []
        self.skipped_days: List[Dict] = []
        self.daily_summaries: List[Dict] = []

    def run(self, symbols: Optional[List[str]] = None) -> List[TradeResult]:
        """Run the full 6-layer backtest."""
        if symbols is None:
            symbols = self.config.watchlist

        print("=" * 70)
        print("  6-LAYER PROFESSIONAL BACKTEST ENGINE")
        print("=" * 70)
        print(f"  Symbols    : {', '.join(symbols)}")
        print(f"  Capital    : Rs.{self.config.total_capital:,.0f}")
        print(f"  Shields    : 8 (ALL must pass)")
        print(f"  Windows    : 3 (Prime + Momentum + Continuation)")
        print(f"  Max Score  : 48 points")
        print("=" * 70)

        # Fetch Nifty + VIX
        print("\n  Fetching market data...")
        nifty_5m = fetch_nifty_data("5m", self.config, self.groww_token)
        vix_daily = fetch_vix_data(self.config)

        vix_lookup: Dict[date, float] = {}
        if not vix_daily.empty:
            for dt, row in vix_daily.iterrows():
                d = dt.date() if hasattr(dt, 'date') else dt
                vix_lookup[d] = row["Close"]

        # Layer 1: Fetch global market data
        print("  Running Layer 1: Global Market Check...")
        # (Global check is done per-day in the loop below)

        # Prepare all stock data
        stock_data: Dict[str, Dict] = {}
        all_days = set()

        for symbol in symbols:
            print(f"\n  Preparing: {symbol}")
            prepared = self._prepare_stock(symbol)
            if prepared:
                stock_data[symbol] = prepared
                all_days.update(get_trading_days(prepared["merged"]))

        if not stock_data:
            print("\n  [ERROR] No data. Aborting.")
            return []

        sorted_days = sorted(all_days)
        print(f"\n  Trading days: {len(sorted_days)}")

        # Process day by day
        for tday in sorted_days:
            self._process_day(tday, stock_data, nifty_5m, vix_lookup)

        print(f"\n{'=' * 70}")
        print(f"  BACKTEST COMPLETE — {len(self.all_trades)} trades")
        print(f"{'=' * 70}\n")

        return self.all_trades

    def _prepare_stock(self, symbol: str) -> Optional[Dict]:
        """Fetch and prepare indicators for one stock."""
        df_15m = fetch_stock_data(symbol, "15m", self.config, self.groww_token)
        if df_15m.empty:
            return None
        df_5m = fetch_stock_data(symbol, "5m", self.config, self.groww_token)
        if df_5m.empty:
            return None

        df_15m = compute_all_indicators_15m(df_15m, self.config)
        df_15m = shift_indicators(df_15m)
        df_5m = compute_vwap_5m(df_5m)
        merged = merge_15m_indicators_to_5m(df_5m, df_15m)

        # Compute ATR on 15m for position sizing
        atr_series = compute_atr(df_15m, period=14)

        if merged.empty:
            return None
        return {"merged": merged, "raw_5m": df_5m, "atr": atr_series}

    def _process_day(self, trading_date: date,
                     stock_data: Dict[str, Dict],
                     nifty_5m: pd.DataFrame,
                     vix_lookup: Dict[date, float]) -> None:
        """Process one trading day across all stocks."""

        vix = vix_lookup.get(trading_date, 0.0)

        # Risk manager daily check
        can_trade = self.risk_manager.start_new_day(trading_date, vix)
        if not can_trade:
            self.skipped_days.append({
                "date": trading_date, "symbol": "ALL",
                "reason": self.risk_manager.skip_reason,
            })
            return

        # Layer 1: Global check
        try:
            global_result = check_global_markets(self.config, trading_date)
        except Exception:
            global_result = GlobalMarketResult()

        if not global_result.can_trade:
            self.skipped_days.append({
                "date": trading_date, "symbol": "ALL",
                "reason": f"Global: {global_result.description}",
            })
            self.risk_manager.end_day()
            return

        # Layer 2: FII/DII
        try:
            fii_result = check_fii_dii(trading_date, self.config,
                                        str(__import__('pathlib').Path(__file__).parent))
        except Exception:
            fii_result = FIIDIIResult()

        if not fii_result.can_trade:
            self.skipped_days.append({
                "date": trading_date, "symbol": "ALL",
                "reason": f"FII/DII: {fii_result.description}",
            })
            self.risk_manager.end_day()
            return

        # Nifty direction
        nifty_day = get_day_data(nifty_5m, trading_date)
        nifty_early = nifty_day.head(6) if len(nifty_day) >= 6 else nifty_day
        nifty_dir = assess_nifty_direction(nifty_early, self.config)

        if not nifty_dir.can_trade:
            self.skipped_days.append({
                "date": trading_date, "symbol": "ALL",
                "reason": nifty_dir.description,
            })
            self.risk_manager.end_day()
            return

        # Process each stock
        day_trades: List[TradeResult] = []

        for symbol, data in stock_data.items():
            trades = self._process_stock_day(
                symbol, trading_date, data,
                nifty_dir, global_result, fii_result,
            )
            day_trades.extend(trades)

        # End day
        self.risk_manager.end_day()

        if day_trades:
            pnl = sum(t.net_pnl for t in day_trades)
            self.daily_summaries.append({
                "date": trading_date,
                "trades": len(day_trades),
                "pnl": pnl,
                "global_score": global_result.global_score,
                "fii_scenario": fii_result.scenario,
                "vix": vix,
            })

    def _process_stock_day(self, symbol: str, trading_date: date,
                           data: Dict, nifty_dir: NiftyDirection,
                           global_result: GlobalMarketResult,
                           fii_result: FIIDIIResult) -> List[TradeResult]:
        """Process one stock on one day with 3 windows."""

        df_merged = data["merged"]
        df_5m = data["raw_5m"]
        day_data = get_day_data(df_merged, trading_date)
        if day_data.empty or len(day_data) < 5:
            return []

        prev_day = get_previous_day_data(df_5m, trading_date)
        prev_avg_vol = prev_day["Volume"].mean() if prev_day is not None and not prev_day.empty else 0

        day_open = day_data.iloc[0]["Open"]
        gap_pct = 0.0
        if prev_day is not None and not prev_day.empty:
            prev_close = prev_day.iloc[-1]["Close"]
            if prev_close > 0:
                gap_pct = (day_open - prev_close) / prev_close * 100

        active_pos: Optional[Position] = None
        trades: List[TradeResult] = []

        for i, (bar_time, bar) in enumerate(day_data.iterrows()):
            ct = bar_time.time()

            # Manage open position
            if active_pos and not active_pos.is_closed:
                vwap = bar.get(COL_VWAP, np.nan)
                reason = check_position(active_pos, bar, vwap, bar_time, self.config)
                if reason:
                    tr = finalize_trade(active_pos, self.config)
                    self.all_trades.append(tr)
                    trades.append(tr)
                    wn = {"PRIME": 1, "MOMENTUM": 2, "CONTINUATION": 3}.get(active_pos.window, 1)
                    self.risk_manager.record_trade_result(tr.net_pnl, wn)
                    print(f"    [EXIT] {symbol} @ Rs.{tr.avg_exit_price:.2f} | "
                          f"{reason} | P&L: Rs.{tr.net_pnl:+.2f}")
                    active_pos = None
                continue

            if active_pos:
                continue

            # Determine window
            window = get_current_window(ct, self.config)
            if not window.is_active:
                continue

            # Risk check
            stock_gain = ((bar["Close"] - day_open) / day_open * 100) if day_open > 0 else 0
            can_enter, reason = self.risk_manager.can_enter_trade(
                bar_time, stock_gain, window.window_number
            )
            if not can_enter:
                continue

            # Score with all layers
            signal = score_bar(
                bar, self.config, prev_avg_vol,
                global_score=global_result.global_score,
                fii_bonus=fii_result.score_bonus,
                gap_pct=gap_pct,
            )

            # All 8 shields must pass
            if not signal.all_shields_pass:
                continue
            if not signal.is_entry:
                continue

            # Nifty min score check
            if signal.score < nifty_dir.min_score_required:
                continue

            # Window-specific checks
            if window.window_number == 2:
                if not check_window2_conditions(bar, day_data, i):
                    continue
            elif window.window_number == 3:
                if not check_window3_conditions(bar, day_open, self.config):
                    continue

            # VWAP pullback
            recent = day_data.iloc[max(0, i - 5):i]
            if not check_vwap_pullback(recent, bar, self.config):
                continue

            # 5-min confirmation
            day_5m = get_day_data(df_5m, trading_date)
            avg_vol_5m = day_5m["Volume"].iloc[:max(1, i)].tail(10).mean() if not day_5m.empty else 0
            if not check_5min_confirmation(bar, avg_vol_5m):
                continue

            # ENTRY
            st_val = bar.get(COL_SUPERTREND, np.nan)
            if pd.isna(st_val):
                continue

            # Get ATR for target validation
            atr_val = 0.0
            if data.get("atr") is not None and not data["atr"].empty:
                atr_val = float(data["atr"].iloc[-1]) if not pd.isna(data["atr"].iloc[-1]) else 0

            # Apply position size adjustments
            size_factor = self.risk_manager.get_position_size_factor()
            size_factor *= global_result.position_size_multiplier
            size_factor *= fii_result.position_adjustment

            active_pos = create_position(
                symbol=symbol, entry_time=bar_time,
                entry_price=bar["Close"],
                supertrend_value=st_val, config=self.config,
                score=signal.score, shields_passed=8,
                window=window.window_name, atr_value=atr_val,
            )

            # Apply size factor
            if size_factor < 1.0:
                active_pos.total_qty = max(1, int(active_pos.total_qty * size_factor))
                active_pos.qty_t1 = max(1, int(active_pos.total_qty * 0.6))
                active_pos.qty_t2 = active_pos.total_qty - active_pos.qty_t1
                active_pos.remaining_qty = active_pos.total_qty

            print(f"    [{window.window_name}] {symbol} @ Rs.{bar['Close']:.2f} | "
                  f"Score: {signal.score:.0f} ({signal.recommendation}) | "
                  f"SL: Rs.{active_pos.sl_price:.2f} | Qty: {active_pos.total_qty}")

        # Force exit open position
        if active_pos and not active_pos.is_closed:
            last = day_data.iloc[-1]
            _exit_all(active_pos, day_data.index[-1], last["Close"], ExitReason.TIME_EXIT)
            tr = finalize_trade(active_pos, self.config)
            self.all_trades.append(tr)
            trades.append(tr)
            wn = {"PRIME": 1, "MOMENTUM": 2, "CONTINUATION": 3}.get(active_pos.window, 1)
            self.risk_manager.record_trade_result(tr.net_pnl, wn)
            print(f"    [TIME EXIT] {symbol} @ Rs.{last['Close']:.2f} | P&L: Rs.{tr.net_pnl:+.2f}")

        return trades


def run_backtest(config: Optional[StrategyConfig] = None,
                 symbols: Optional[List[str]] = None,
                 groww_token: Optional[str] = None) -> Tuple[List[TradeResult], BacktestEngine]:
    """Convenience wrapper."""
    if config is None:
        config = StrategyConfig()
    engine = BacktestEngine(config, groww_token)
    trades = engine.run(symbols)
    return trades, engine

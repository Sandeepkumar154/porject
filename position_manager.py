"""
position_manager.py — Trade lifecycle with dynamic position sizing.

Handles:
  - Dynamic qty based on score tier (ELITE 1.5%, STRONG 1%, AVERAGE 0.5%)
  - ATR-validated targets
  - 60/40 partial exits at T1/T2
  - Trailing SL to breakeven after T1
  - Exit triggers: SL, VWAP breach, Supertrend flip, ADX drop, time exit
  - Indian MIS transaction costs
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import List, Optional

import pandas as pd
import numpy as np

from config import StrategyConfig


class ExitReason(Enum):
    STOP_LOSS = "SL"
    TRAILING_SL = "TRAILING_SL"
    TARGET_1 = "T1"
    TARGET_2 = "T2"
    VWAP_BREACH = "VWAP_BREACH"
    TIME_EXIT = "TIME_EXIT"
    SUPERTREND_FLIP = "ST_FLIP"
    ADX_DROP = "ADX_DROP"
    NIFTY_DROP = "NIFTY_DROP"
    MANUAL = "MANUAL"


@dataclass
class FillRecord:
    timestamp: datetime = None
    price: float = 0.0
    quantity: int = 0
    side: str = "BUY"
    reason: str = ""


@dataclass
class Position:
    symbol: str = ""
    entry_time: datetime = None
    entry_price: float = 0.0
    total_qty: int = 0
    remaining_qty: int = 0
    sl_price: float = 0.0
    original_sl: float = 0.0
    t1_price: float = 0.0
    t2_price: float = 0.0
    qty_t1: int = 0
    qty_t2: int = 0
    t1_hit: bool = False
    t2_hit: bool = False
    is_closed: bool = False
    close_time: datetime = None
    exit_reason: str = ""
    score: float = 0.0
    grade: str = "AVERAGE"
    shields_passed: int = 0
    risk_pct: float = 0.01
    window: str = "PRIME"
    fills: List[FillRecord] = field(default_factory=list)


@dataclass
class TradeResult:
    symbol: str = ""
    entry_time: datetime = None
    entry_price: float = 0.0
    exit_time: datetime = None
    avg_exit_price: float = 0.0
    total_qty: int = 0
    sl_price: float = 0.0
    t1_price: float = 0.0
    t2_price: float = 0.0
    exit_reason: str = ""
    gross_pnl: float = 0.0
    total_costs: float = 0.0
    net_pnl: float = 0.0
    score: float = 0.0
    grade: str = ""
    shields_passed: int = 0
    window: str = ""
    is_winner: bool = False


def create_position(symbol: str, entry_time: datetime,
                    entry_price: float, supertrend_value: float,
                    config: StrategyConfig, score: float = 18.0,
                    shields_passed: int = 8, window: str = "PRIME",
                    atr_value: float = 0.0) -> Position:
    """
    Create a position with dynamic sizing based on score.

    Position sizing:
      ELITE (35+)   → 1.5% risk
      STRONG (25-34) → 1.0% risk
      AVERAGE (18-24) → 0.5% risk
    """
    grade = config.get_trade_grade(score)
    risk_pct = config.get_risk_pct(score)

    # SL = Supertrend minus buffer
    sl_buffer = config.indicators.supertrend.sl_buffer
    sl_price = supertrend_value - sl_buffer

    sl_distance = entry_price - sl_price
    if sl_distance <= 0:
        sl_distance = entry_price * 0.01
        sl_price = entry_price - sl_distance

    # Dynamic position sizing: qty = risk_amount / sl_distance
    risk_amount = config.total_capital * risk_pct
    total_qty = max(1, int(risk_amount / sl_distance))

    # Cap at capital available
    max_qty = int(config.total_capital / entry_price)
    total_qty = min(total_qty, max_qty)

    # Targets based on R:R (T1 = 2x SL distance, T2 = 4x SL distance)
    t1_dist = config.targets.t1_multiplier * sl_distance
    t2_dist = config.targets.t2_multiplier * sl_distance
    t1_price = entry_price + t1_dist
    t2_price = entry_price + t2_dist

    # Daily ATR cap (ensure targets don't exceed daily ATR of ~2% of price)
    daily_atr = atr_value * 5 if atr_value > 0 else (entry_price * 0.02)
    max_target = entry_price + (config.targets.max_atr_overall * daily_atr)
    t1_price = min(t1_price, max_target)
    t2_price = min(t2_price, max_target)

    # Window 3 = T1 only
    w3 = config.windows.window3
    is_t1_only = (window == w3.name)

    # Partial exit quantities
    qty_t1 = max(1, int(total_qty * config.targets.t1_exit_pct))
    qty_t2 = total_qty - qty_t1 if not is_t1_only else 0
    if is_t1_only:
        qty_t1 = total_qty

    return Position(
        symbol=symbol, entry_time=entry_time, entry_price=entry_price,
        total_qty=total_qty, remaining_qty=total_qty,
        sl_price=sl_price, original_sl=sl_price,
        t1_price=t1_price, t2_price=t2_price,
        qty_t1=qty_t1, qty_t2=qty_t2,
        score=score, grade=grade, shields_passed=shields_passed,
        risk_pct=risk_pct, window=window,
        fills=[FillRecord(entry_time, entry_price, total_qty, "BUY", "ENTRY")],
    )


def check_position(position: Position, bar: pd.Series,
                   vwap: float, bar_time: datetime,
                   config: StrategyConfig) -> Optional[str]:
    """
    Check position against all exit conditions. Priority order:
      1. Gap SL (open below SL)
      2. Stop Loss hit
      3. T1 partial exit
      4. T2 full exit
      5. VWAP breach
      6. Supertrend flip
      7. ADX drop below 20
      8. Time exit (3:00 PM)
    """
    if position.is_closed:
        return None

    bar_low = bar.get("Low", 0)
    bar_high = bar.get("High", 0)
    bar_close = bar.get("Close", 0)
    bar_open = bar.get("Open", 0)

    # CHECK 1: Gap below SL
    if bar_open < position.sl_price:
        _exit_all(position, bar_time, bar_open, ExitReason.STOP_LOSS)
        return ExitReason.STOP_LOSS.value

    # CHECK 2: SL hit
    if bar_low <= position.sl_price:
        reason = ExitReason.TRAILING_SL if position.t1_hit else ExitReason.STOP_LOSS
        _exit_all(position, bar_time, position.sl_price, reason)
        return reason.value

    # CHECK 3: T1 hit (partial exit)
    if not position.t1_hit and bar_high >= position.t1_price:
        position.fills.append(FillRecord(
            bar_time, position.t1_price, position.qty_t1,
            "SELL", ExitReason.TARGET_1.value,
        ))
        position.remaining_qty -= position.qty_t1
        position.t1_hit = True
        position.sl_price = position.entry_price  # Move SL to breakeven

        if position.remaining_qty <= 0:
            _close_position(position, bar_time, ExitReason.TARGET_1)
            return ExitReason.TARGET_1.value

    # CHECK 4: T2 hit
    if position.t1_hit and bar_high >= position.t2_price:
        if position.remaining_qty > 0:
            position.fills.append(FillRecord(
                bar_time, position.t2_price, position.remaining_qty,
                "SELL", ExitReason.TARGET_2.value,
            ))
            position.remaining_qty = 0
        _close_position(position, bar_time, ExitReason.TARGET_2)
        return ExitReason.TARGET_2.value

    # CHECK 5: VWAP breach (0.1% buffer)
    if not pd.isna(vwap) and vwap > 0:
        if bar_close < vwap * 0.999:
            _exit_all(position, bar_time, bar_close, ExitReason.VWAP_BREACH)
            return ExitReason.VWAP_BREACH.value

    # CHECK 6: Supertrend flip
    st_dir = bar.get("SUPERTd_10_3.0", np.nan)
    if not pd.isna(st_dir) and st_dir < 0:
        _exit_all(position, bar_time, bar_close, ExitReason.SUPERTREND_FLIP)
        return ExitReason.SUPERTREND_FLIP.value

    # CHECK 7: ADX drop below 20
    adx = bar.get("ADX_14", np.nan)
    if not pd.isna(adx) and adx < 20:
        _exit_all(position, bar_time, bar_close, ExitReason.ADX_DROP)
        return ExitReason.ADX_DROP.value

    # CHECK 8: Time exit
    current_time = bar_time.time() if hasattr(bar_time, 'time') else bar_time
    if isinstance(current_time, time):
        if current_time >= config.windows.mandatory_exit_time:
            _exit_all(position, bar_time, bar_close, ExitReason.TIME_EXIT)
            return ExitReason.TIME_EXIT.value

    # Trailing SL: after T1, move SL up with new highs
    if position.t1_hit and bar_high > position.entry_price:
        new_sl = bar_low - 1  # Below current bar's low
        if new_sl > position.sl_price:
            position.sl_price = new_sl

    return None


def _exit_all(position: Position, exit_time: datetime,
              exit_price: float, reason: ExitReason) -> None:
    """Exit all remaining shares."""
    if position.remaining_qty > 0:
        position.fills.append(FillRecord(
            exit_time, exit_price, position.remaining_qty,
            "SELL", reason.value,
        ))
        position.remaining_qty = 0
    _close_position(position, exit_time, reason)


def _close_position(position: Position, close_time: datetime,
                    reason: ExitReason) -> None:
    """Mark position as closed."""
    position.is_closed = True
    position.close_time = close_time

    # Determine combined exit reason
    sell_fills = [f for f in position.fills if f.side == "SELL"]
    reasons = set(f.reason for f in sell_fills)
    if len(reasons) > 1:
        position.exit_reason = "+".join(sorted(reasons))
    elif reasons:
        position.exit_reason = reasons.pop()
    else:
        position.exit_reason = reason.value


def finalize_trade(position: Position,
                   config: StrategyConfig) -> TradeResult:
    """Convert closed Position to TradeResult with costs."""
    sell_fills = [f for f in position.fills if f.side == "SELL"]

    total_sell_value = sum(f.price * f.quantity for f in sell_fills)
    total_sell_qty = sum(f.quantity for f in sell_fills)
    avg_exit = total_sell_value / total_sell_qty if total_sell_qty > 0 else 0

    buy_value = position.entry_price * position.total_qty
    gross_pnl = total_sell_value - buy_value

    # Transaction costs (Indian MIS)
    cc = config.costs
    num_orders = 1 + len(sell_fills)
    brokerage = min(cc.brokerage_per_order * num_orders, buy_value * 0.0025)
    turnover = buy_value + total_sell_value
    stt = total_sell_value * cc.stt_rate
    exchange_fees = turnover * cc.exchange_rate
    gst = (brokerage + exchange_fees) * cc.gst_rate
    sebi = turnover * cc.sebi_rate
    stamp = buy_value * cc.stamp_duty_rate
    total_costs = brokerage + stt + exchange_fees + gst + sebi + stamp

    net_pnl = gross_pnl - total_costs

    return TradeResult(
        symbol=position.symbol,
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=position.close_time,
        avg_exit_price=round(avg_exit, 2),
        total_qty=position.total_qty,
        sl_price=position.original_sl,
        t1_price=position.t1_price,
        t2_price=position.t2_price,
        exit_reason=position.exit_reason,
        gross_pnl=round(gross_pnl, 2),
        total_costs=round(total_costs, 2),
        net_pnl=round(net_pnl, 2),
        score=position.score,
        grade=position.grade,
        shields_passed=position.shields_passed,
        window=position.window,
        is_winner=net_pnl > 0,
    )

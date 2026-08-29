"""
risk_manager.py — Layer 6: Risk + Position Sizing Rules.

Enforces:
  - Daily: max 4 trades, 3% loss limit, 3 consecutive losses → stop
  - Weekly: 6% loss → no trades next 2 days
  - Monthly: 10% loss → 1 week break
  - Good day: 5% up → reduce 50%, 8% up → stop
  - Per-window trade limits
  - VIX > 20 → skip day
"""

from dataclasses import dataclass, field
from datetime import date, time, datetime, timedelta
from typing import Dict, Optional, Tuple

from config import StrategyConfig


@dataclass
class DailyRiskState:
    trading_date: date = None
    trades_today: int = 0
    trades_per_window: Dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0})
    consecutive_losses: int = 0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    is_stopped: bool = False
    stop_reason: str = ""


@dataclass
class MultiDayRiskState:
    weekly_pnl: float = 0.0
    weekly_pnl_pct: float = 0.0
    monthly_pnl: float = 0.0
    monthly_pnl_pct: float = 0.0
    consecutive_losing_days: int = 0
    forced_skip_until: Optional[date] = None
    week_start: Optional[date] = None
    month_start: Optional[date] = None


class RiskManager:
    """Manages all risk rules across daily, weekly, monthly horizons."""

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.daily = DailyRiskState()
        self.multi = MultiDayRiskState()
        self.skip_reason = ""

    def start_new_day(self, trading_date: date,
                      vix_value: float = 0.0) -> bool:
        """Initialize for a new trading day. Returns True if trading allowed."""
        self.skip_reason = ""

        # VIX check
        if vix_value > self.config.risk.vix_threshold:
            self.skip_reason = f"VIX {vix_value:.1f} > {self.config.risk.vix_threshold} — skip"
            return False

        # Forced skip (weekly/monthly loss triggered)
        if self.multi.forced_skip_until and trading_date < self.multi.forced_skip_until:
            self.skip_reason = f"Forced skip until {self.multi.forced_skip_until}"
            return False

        # Consecutive losing days check
        rc = self.config.risk
        if self.multi.consecutive_losing_days >= 3:
            self.skip_reason = f"{self.multi.consecutive_losing_days} consecutive losing days — day off"
            self.multi.consecutive_losing_days = 0  # Reset after skip
            return False

        # Reset weekly tracking
        if self.multi.week_start is None or (trading_date - self.multi.week_start).days >= 7:
            self.multi.week_start = trading_date
            self.multi.weekly_pnl = 0.0
            self.multi.weekly_pnl_pct = 0.0

        # Reset monthly tracking
        if self.multi.month_start is None or trading_date.month != self.multi.month_start.month:
            self.multi.month_start = trading_date
            self.multi.monthly_pnl = 0.0
            self.multi.monthly_pnl_pct = 0.0

        # Weekly loss check
        if self.multi.weekly_pnl_pct < -self.config.risk.max_weekly_loss_pct * 100:
            self.skip_reason = f"Weekly loss {self.multi.weekly_pnl_pct:.1f}% exceeds limit"
            self.multi.forced_skip_until = trading_date + timedelta(days=2)
            return False

        # Monthly loss check
        if self.multi.monthly_pnl_pct < -self.config.risk.max_monthly_loss_pct * 100:
            self.skip_reason = f"Monthly loss {self.multi.monthly_pnl_pct:.1f}% exceeds limit"
            self.multi.forced_skip_until = trading_date + timedelta(days=7)
            return False

        # Initialize daily state
        self.daily = DailyRiskState(trading_date=trading_date)
        return True

    def can_enter_trade(self, bar_time: datetime,
                        stock_gain_pct: float = 0.0,
                        window_number: int = 1) -> Tuple[bool, str]:
        """Check if a new trade is allowed right now."""

        # Daily stopped
        if self.daily.is_stopped:
            return False, self.daily.stop_reason

        # Max trades per day
        if self.daily.trades_today >= self.config.risk.max_trades_per_day:
            return False, f"Max {self.config.risk.max_trades_per_day} trades/day reached"

        # Per-window limit
        window_trades = self.daily.trades_per_window.get(window_number, 0)
        if window_number == 1 and window_trades >= 2:
            return False, "Window 1 max 2 trades reached"
        elif window_number in (2, 3) and window_trades >= 1:
            return False, f"Window {window_number} max 1 trade reached"

        # Consecutive losses today
        if self.daily.consecutive_losses >= self.config.risk.max_consecutive_losses:
            self.daily.is_stopped = True
            self.daily.stop_reason = f"{self.daily.consecutive_losses} consecutive losses — stopped"
            return False, self.daily.stop_reason

        # Daily loss limit (3%)
        cap = self.config.total_capital
        max_loss = cap * self.config.risk.max_daily_loss_pct
        if self.daily.daily_pnl < -max_loss:
            self.daily.is_stopped = True
            self.daily.stop_reason = f"Daily loss Rs.{abs(self.daily.daily_pnl):.0f} > {self.config.risk.max_daily_loss_pct*100:.0f}% limit"
            return False, self.daily.stop_reason

        # Good day: up 8% → stop
        if self.daily.daily_pnl_pct >= self.config.risk.good_day_stop_pct * 100:
            self.daily.is_stopped = True
            self.daily.stop_reason = f"Up {self.daily.daily_pnl_pct:.1f}% — protecting profit"
            return False, self.daily.stop_reason

        # Stock already up 15%
        if stock_gain_pct > self.config.stock_selection.max_intraday_gain:
            return False, f"Stock up {stock_gain_pct:.1f}% — skip"

        return True, ""

    def get_position_size_factor(self) -> float:
        """Get position size adjustment for good day rule."""
        if self.daily.daily_pnl_pct >= self.config.risk.good_day_reduce_pct * 100:
            return 0.5  # Reduce 50% after 5% profit
        if self.daily.consecutive_losses >= 2 and self.config.risk.reduce_after_2_losses:
            return 0.5  # Half size after 2 consecutive losses
        return 1.0

    def record_trade_result(self, pnl: float, window_number: int = 1) -> None:
        """Record a completed trade."""
        self.daily.trades_today += 1
        self.daily.trades_per_window[window_number] = (
            self.daily.trades_per_window.get(window_number, 0) + 1
        )
        self.daily.daily_pnl += pnl

        cap = self.config.total_capital
        self.daily.daily_pnl_pct = (self.daily.daily_pnl / cap * 100) if cap > 0 else 0

        if pnl <= 0:
            self.daily.consecutive_losses += 1
        else:
            self.daily.consecutive_losses = 0

    def end_day(self) -> DailyRiskState:
        """End the trading day and update multi-day tracking."""
        state = self.daily

        # Update multi-day P&L
        self.multi.weekly_pnl += state.daily_pnl
        self.multi.monthly_pnl += state.daily_pnl

        cap = self.config.total_capital
        self.multi.weekly_pnl_pct = (self.multi.weekly_pnl / cap * 100) if cap > 0 else 0
        self.multi.monthly_pnl_pct = (self.multi.monthly_pnl / cap * 100) if cap > 0 else 0

        # Consecutive losing days
        if state.daily_pnl < 0:
            self.multi.consecutive_losing_days += 1
        else:
            self.multi.consecutive_losing_days = 0

        return state

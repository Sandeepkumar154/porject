"""
global_check.py — Layer 1: Global Market Check.

Fetches and scores 5 global indicators:
  1. Gift Nifty (Nifty gap proxy)
  2. US Markets (Dow, NASDAQ, S&P 500)
  3. Crude Oil (WTI)
  4. USD/INR
  5. Asian Markets (Nikkei, Hang Seng)

Global Score: 0-5 → determines position sizing.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Dict, Optional

import pandas as pd
import numpy as np

from config import StrategyConfig

# Cache for global market data
_global_cache: Dict[str, pd.DataFrame] = {}


@dataclass
class GlobalMarketResult:
    gift_nifty_gap: float = 0.0
    gift_nifty_signal: str = "FLAT"
    us_dow_change: float = 0.0
    us_nasdaq_change: float = 0.0
    us_sp500_change: float = 0.0
    us_signal: str = "NEUTRAL"
    crude_change: float = 0.0
    crude_signal: str = "FLAT"
    crude_favored: List[str] = field(default_factory=list)
    crude_avoid: List[str] = field(default_factory=list)
    usdinr_change: float = 0.0
    currency_signal: str = "NEUTRAL"
    currency_favored: List[str] = field(default_factory=list)
    asia_nikkei_change: float = 0.0
    asia_hangseng_change: float = 0.0
    asia_signal: str = "MIXED"
    global_score: int = 3
    position_size_multiplier: float = 1.0
    can_trade: bool = True
    description: str = ""
    sector_bias: List[str] = field(default_factory=list)


def _fetch_daily(ticker: str, period: str = "60d") -> pd.DataFrame:
    """Fetch daily data with caching."""
    if ticker in _global_cache:
        return _global_cache[ticker]
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if not df.empty:
            _global_cache[ticker] = df
        return df
    except Exception:
        return pd.DataFrame()


def _get_pct_change(df: pd.DataFrame, target_date: date) -> float:
    """Get % change for a specific date or nearest previous."""
    if df.empty:
        return 0.0
    try:
        df_dates = df.copy()
        df_dates["dt"] = df_dates.index.date if hasattr(df_dates.index, 'date') else df_dates.index
        # Find the row on or before target_date
        mask = df_dates["dt"] <= target_date
        if not mask.any():
            return 0.0
        recent = df_dates[mask].iloc[-1]
        prev_idx = df_dates[mask].index[-1]
        loc = df_dates.index.get_loc(prev_idx)
        if loc == 0:
            return 0.0
        prev = df_dates.iloc[loc - 1]
        if prev["Close"] == 0:
            return 0.0
        return (recent["Close"] - prev["Close"]) / prev["Close"] * 100
    except Exception:
        return 0.0


def check_global_markets(config: StrategyConfig,
                         trading_date: date = None) -> GlobalMarketResult:
    """Run full global market check for a trading day."""
    if trading_date is None:
        trading_date = date.today()

    result = GlobalMarketResult()
    score = 0

    # 1. Gift Nifty proxy (Nifty gap from previous close)
    nifty = _fetch_daily("^NSEI")
    if not nifty.empty:
        try:
            nifty_dates = nifty.index.date
            mask = nifty_dates <= trading_date
            if mask.sum() >= 2:
                rows = nifty[mask]
                today_open = rows.iloc[-1]["Open"]
                prev_close = rows.iloc[-2]["Close"]
                gap_pts = today_open - prev_close
                result.gift_nifty_gap = gap_pts

                gc = config.global_check.gift_nifty
                if gap_pts > gc.strong_bullish:
                    result.gift_nifty_signal = "BULLISH"
                    score += 1
                elif gap_pts > -gc.flat_range:
                    result.gift_nifty_signal = "FLAT"
                    score += 0.5
                elif gap_pts > gc.half_size:
                    result.gift_nifty_signal = "CAUTIOUS"
                elif gap_pts > gc.no_trade:
                    result.gift_nifty_signal = "HALF_SIZE"
                else:
                    result.gift_nifty_signal = "NO_TRADE"
                    result.can_trade = False
        except Exception:
            pass

    # 2. US Markets (previous night)
    prev_day = trading_date - timedelta(days=1)
    result.us_dow_change = _get_pct_change(_fetch_daily("^DJI"), prev_day)
    result.us_nasdaq_change = _get_pct_change(_fetch_daily("^IXIC"), prev_day)
    result.us_sp500_change = _get_pct_change(_fetch_daily("^GSPC"), prev_day)

    us_avg = (result.us_dow_change + result.us_nasdaq_change + result.us_sp500_change) / 3
    uc = config.global_check.us_market
    if us_avg > uc.positive_threshold:
        result.us_signal = "POSITIVE"
        score += 1
    elif us_avg > uc.reduce_all_threshold:
        result.us_signal = "NEUTRAL"
        score += 0.5
    else:
        result.us_signal = "NEGATIVE"

    # 3. Crude Oil
    result.crude_change = _get_pct_change(_fetch_daily("CL=F"), prev_day)
    if result.crude_change > 1.5:
        result.crude_signal = "UP_SHARP"
        result.crude_favored = ["ONGC", "BPCL", "OIL"]
        result.crude_avoid = ["INDIGO", "ASIANPAINT", "MRF"]
        score += 1
    elif result.crude_change < -1.5:
        result.crude_signal = "DOWN_SHARP"
        result.crude_favored = ["INDIGO", "ASIANPAINT", "MARICO"]
        result.crude_avoid = ["ONGC", "BPCL", "OIL"]
        score += 0.5
    else:
        result.crude_signal = "FLAT"
        score += 0.5

    # 4. USD/INR
    result.usdinr_change = _get_pct_change(_fetch_daily("USDINR=X"), prev_day)
    if result.usdinr_change > 0.2:  # Rupee weakening
        result.currency_signal = "RUPEE_WEAK"
        result.currency_favored = ["INFY", "TCS", "WIPRO", "HCLTECH"]
        score += 0.5
    elif result.usdinr_change < -0.2:  # Rupee strengthening
        result.currency_signal = "RUPEE_STRONG"
        result.currency_favored = ["HINDUNILVR", "MARICO", "ITC"]
        score += 1
    else:
        result.currency_signal = "NEUTRAL"
        score += 0.5

    # 5. Asian Markets
    result.asia_nikkei_change = _get_pct_change(_fetch_daily("^N225"), trading_date)
    result.asia_hangseng_change = _get_pct_change(_fetch_daily("^HSI"), trading_date)
    nikkei_pos = result.asia_nikkei_change > 0
    hsi_pos = result.asia_hangseng_change > 0
    if nikkei_pos and hsi_pos:
        result.asia_signal = "BOTH_GREEN"
        score += 1
    elif nikkei_pos or hsi_pos:
        result.asia_signal = "MIXED"
        score += 0.5
    else:
        result.asia_signal = "BOTH_RED"

    # Final global score
    result.global_score = int(round(score))

    # Position size multiplier
    gc = config.global_check
    if result.global_score >= gc.full_trading_min:
        result.position_size_multiplier = 1.0
    elif result.global_score >= gc.careful_min:
        result.position_size_multiplier = 1.0
    elif result.global_score >= gc.reduce_25_min:
        result.position_size_multiplier = 0.75
    elif result.global_score >= gc.half_size_min:
        result.position_size_multiplier = 0.50
    else:
        result.position_size_multiplier = 0.0
        result.can_trade = False

    # Sector bias
    result.sector_bias = list(set(
        result.crude_favored + result.currency_favored
    ))

    result.description = (
        f"Global {result.global_score}/5 | "
        f"Nifty:{result.gift_nifty_signal} | "
        f"US:{result.us_signal} | "
        f"Crude:{result.crude_signal} | "
        f"INR:{result.currency_signal} | "
        f"Asia:{result.asia_signal}"
    )

    return result

"""
data_fetcher.py — Multi-source data fetcher.

Sources: Groww API (primary) → yfinance (fallback).
Supports caching, IST timezone, Nifty/VIX data.
"""

import os
import pickle
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Set

import pandas as pd
import numpy as np

from config import StrategyConfig

IST = "Asia/Kolkata"
CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_key(symbol: str, interval: str) -> Path:
    safe = symbol.replace("^", "_").replace("/", "_")
    return CACHE_DIR / f"{safe}_{interval}.pkl"


def _load_cache(key: Path, max_age_hours: int = 12) -> Optional[pd.DataFrame]:
    if key.exists():
        import time
        age = time.time() - key.stat().st_mtime
        if age < max_age_hours * 3600:
            with open(key, "rb") as f:
                return pickle.load(f)
    return None


def _save_cache(key: Path, df: pd.DataFrame) -> None:
    with open(key, "wb") as f:
        pickle.dump(df, f)


def _fetch_yfinance(ticker: str, interval: str = "15m",
                    period: str = "60d") -> pd.DataFrame:
    """Fetch from yfinance."""
    import yfinance as yf
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
        print(f"  [WARN] yfinance error for {ticker}: {e}")
        return pd.DataFrame()


def _fetch_groww(symbol: str, interval: str, period: str,
                 groww_token: str) -> pd.DataFrame:
    """Fetch from Groww API."""
    try:
        from growwapi import GrowwAPI
        groww = GrowwAPI(access_token=groww_token)

        int_map = {
            "5m": "CANDLE_INTERVAL_MIN_5",
            "15m": "CANDLE_INTERVAL_MIN_15",
        }
        ci = getattr(groww, int_map.get(interval, "CANDLE_INTERVAL_MIN_15"), None)
        if ci is None:
            return pd.DataFrame()

        from datetime import datetime
        end = datetime.now()
        days = int(period.replace("d", ""))
        start = end - timedelta(days=days)

        candles = groww.get_historical_candles(
            exchange=groww.EXCHANGE_NSE,
            segment=groww.SEGMENT_CASH,
            groww_symbol=f"NSE-{symbol}",
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            candle_interval=ci,
        )
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)
        df.index = pd.to_datetime(df["timestamp"])
        if df.index.tz is None:
            df.index = df.index.tz_localize(IST)
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        df.dropna(inplace=True)
        return df
    except Exception as e:
        print(f"  [WARN] Groww error for {symbol}: {e}")
        return pd.DataFrame()


def fetch_stock_data(symbol: str, interval: str = "15m",
                     config: Optional[StrategyConfig] = None,
                     groww_token: Optional[str] = None) -> pd.DataFrame:
    """Fetch stock data. Groww first → yfinance fallback. Cached."""
    period = config.data_period if config else "60d"

    ck = _cache_key(symbol, interval)
    cached = _load_cache(ck)
    if cached is not None:
        return cached

    df = pd.DataFrame()

    # Try Groww first
    if groww_token:
        df = _fetch_groww(symbol, interval, period, groww_token)

    # Fallback to yfinance
    if df.empty:
        ticker = f"{symbol}.NS"
        df = _fetch_yfinance(ticker, interval, period)

    if not df.empty:
        _save_cache(ck, df)

    return df


def fetch_nifty_data(interval: str = "5m",
                     config: Optional[StrategyConfig] = None,
                     groww_token: Optional[str] = None) -> pd.DataFrame:
    """Fetch Nifty 50 data."""
    period = config.data_period if config else "60d"
    ck = _cache_key("NIFTY50", interval)
    cached = _load_cache(ck)
    if cached is not None:
        return cached

    df = _fetch_yfinance("^NSEI", interval, period)
    if not df.empty:
        _save_cache(ck, df)
    return df


def fetch_vix_data(config: Optional[StrategyConfig] = None) -> pd.DataFrame:
    """Fetch India VIX daily data."""
    ck = _cache_key("INDIAVIX", "1d")
    cached = _load_cache(ck)
    if cached is not None:
        return cached

    period = config.data_period if config else "60d"
    df = _fetch_yfinance("^INDIAVIX", "1d", period)
    if not df.empty:
        _save_cache(ck, df)
    return df


def get_trading_days(df: pd.DataFrame) -> Set[date]:
    """Get unique trading dates from a DataFrame."""
    if df.empty:
        return set()
    return set(df.index.date)


def get_day_data(df: pd.DataFrame, trading_date: date) -> pd.DataFrame:
    """Get all bars for a specific date."""
    if df.empty:
        return pd.DataFrame()
    mask = df.index.date == trading_date
    return df[mask].copy()


def get_previous_day_data(df: pd.DataFrame,
                          trading_date: date) -> Optional[pd.DataFrame]:
    """Get bars from the previous trading day."""
    if df.empty:
        return None
    all_dates = sorted(set(df.index.date))
    try:
        idx = all_dates.index(trading_date)
        if idx == 0:
            return None
        prev_date = all_dates[idx - 1]
        mask = df.index.date == prev_date
        return df[mask].copy()
    except ValueError:
        return None

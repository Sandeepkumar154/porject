"""
indicators.py — Technical indicators for 8-Shield system.

Computes all 8 indicators using pandas_ta:
  1. RSI(14)
  2. Supertrend(10,3)
  3. VWAP (daily reset)
  4. MACD(12,26,9)
  5. Bollinger Bands(20,2)
  6. ADX(14)
  7. EMA 9, 21, 50
  8. Entry Candle Volume Ratio
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
from config import StrategyConfig

# Column name constants
COL_RSI = "RSI_14"
COL_SUPERTREND = "SUPERT_10_3.0"
COL_SUPERTREND_DIR = "SUPERTd_10_3.0"
COL_VWAP = "VWAP_D"
COL_MACD = "MACD_12_26_9"
COL_MACD_SIGNAL = "MACDs_12_26_9"
COL_MACD_HIST = "MACDh_12_26_9"
COL_BB_UPPER = "BBU_20_2.0"
COL_BB_MIDDLE = "BBM_20_2.0"
COL_BB_LOWER = "BBL_20_2.0"
COL_ADX = "ADX_14"
COL_DI_PLUS = "DMP_14"
COL_DI_MINUS = "DMN_14"
COL_EMA_9 = "EMA_9"
COL_EMA_21 = "EMA_21"
COL_EMA_50 = "EMA_50"
COL_VOL_RATIO = "VOL_RATIO"


def compute_all_indicators_15m(df: pd.DataFrame,
                                config: StrategyConfig) -> pd.DataFrame:
    """Compute all 8 indicators on 15-minute data."""
    df_out = df.copy()

    # 1. RSI
    print(f"  Computing RSI({config.indicators.rsi.period})...")
    try:
        r = df_out.ta.rsi(length=config.indicators.rsi.period)
        df_out[COL_RSI] = r if r is not None else np.nan
    except Exception as e:
        print(f"  [WARN] RSI failed: {e}")
        df_out[COL_RSI] = np.nan

    # 2. Supertrend
    sp = config.indicators.supertrend
    print(f"  Computing Supertrend({sp.period}, {sp.multiplier})...")
    try:
        st = df_out.ta.supertrend(length=sp.period, multiplier=sp.multiplier)
        if st is not None:
            st_col = [c for c in st.columns if c.startswith(f"SUPERT_{sp.period}_") or c == f"SUPERT_{sp.period}_{sp.multiplier}"]
            std_col = [c for c in st.columns if c.startswith(f"SUPERTd_{sp.period}_") or c == f"SUPERTd_{sp.period}_{sp.multiplier}"]
            df_out[COL_SUPERTREND] = st[st_col[0]] if st_col else np.nan
            df_out[COL_SUPERTREND_DIR] = st[std_col[0]] if std_col else np.nan
        else:
            df_out[COL_SUPERTREND] = np.nan
            df_out[COL_SUPERTREND_DIR] = np.nan
    except Exception as e:
        print(f"  [WARN] Supertrend failed: {e}")
        df_out[COL_SUPERTREND] = np.nan
        df_out[COL_SUPERTREND_DIR] = np.nan

    # 3. VWAP
    print("  Computing VWAP...")
    try:
        v = df_out.ta.vwap()
        if v is not None:
            df_out[COL_VWAP] = v
        else:
            raise ValueError("vwap returned None")
    except Exception:
        tp = (df_out["High"] + df_out["Low"] + df_out["Close"]) / 3
        df_out[COL_VWAP] = (tp * df_out["Volume"]).cumsum() / df_out["Volume"].cumsum()

    # 4. MACD
    mc = config.indicators.macd
    print(f"  Computing MACD({mc.fast}, {mc.slow}, {mc.signal})...")
    try:
        m = df_out.ta.macd(fast=mc.fast, slow=mc.slow, signal=mc.signal)
        if m is not None:
            m_cols = [c for c in m.columns if c.startswith("MACD_")]
            s_cols = [c for c in m.columns if c.startswith("MACDs_")]
            h_cols = [c for c in m.columns if c.startswith("MACDh_")]
            df_out[COL_MACD] = m[m_cols[0]] if m_cols else np.nan
            df_out[COL_MACD_SIGNAL] = m[s_cols[0]] if s_cols else np.nan
            df_out[COL_MACD_HIST] = m[h_cols[0]] if h_cols else np.nan
        else:
            df_out[COL_MACD] = df_out[COL_MACD_SIGNAL] = df_out[COL_MACD_HIST] = np.nan
    except Exception as e:
        print(f"  [WARN] MACD failed: {e}")
        df_out[COL_MACD] = df_out[COL_MACD_SIGNAL] = df_out[COL_MACD_HIST] = np.nan

    # 5. Bollinger Bands
    bc = config.indicators.bollinger
    print(f"  Computing Bollinger Bands({bc.period}, {bc.std_dev})...")
    try:
        bb = df_out.ta.bbands(length=bc.period, std=bc.std_dev)
        if bb is not None:
            bbu_cols = [c for c in bb.columns if c.startswith(f"BBU_{bc.period}_")]
            bbm_cols = [c for c in bb.columns if c.startswith(f"BBM_{bc.period}_")]
            bbl_cols = [c for c in bb.columns if c.startswith(f"BBL_{bc.period}_")]
            df_out[COL_BB_UPPER] = bb[bbu_cols[0]] if bbu_cols else np.nan
            df_out[COL_BB_MIDDLE] = bb[bbm_cols[0]] if bbm_cols else np.nan
            df_out[COL_BB_LOWER] = bb[bbl_cols[0]] if bbl_cols else np.nan
        else:
            df_out[COL_BB_UPPER] = df_out[COL_BB_MIDDLE] = df_out[COL_BB_LOWER] = np.nan
    except Exception as e:
        print(f"  [WARN] BB failed: {e}")
        df_out[COL_BB_UPPER] = df_out[COL_BB_MIDDLE] = df_out[COL_BB_LOWER] = np.nan

    # 6. ADX
    ac = config.indicators.adx
    print(f"  Computing ADX({ac.period})...")
    try:
        adx = df_out.ta.adx(length=ac.period)
        if adx is not None:
            adx_cols = [c for c in adx.columns if c.startswith(f"ADX_{ac.period}")]
            dmp_cols = [c for c in adx.columns if c.startswith(f"DMP_{ac.period}")]
            dmn_cols = [c for c in adx.columns if c.startswith(f"DMN_{ac.period}")]
            df_out[COL_ADX] = adx[adx_cols[0]] if adx_cols else np.nan
            df_out[COL_DI_PLUS] = adx[dmp_cols[0]] if dmp_cols else np.nan
            df_out[COL_DI_MINUS] = adx[dmn_cols[0]] if dmn_cols else np.nan
        else:
            df_out[COL_ADX] = df_out[COL_DI_PLUS] = df_out[COL_DI_MINUS] = np.nan
    except Exception as e:
        print(f"  [WARN] ADX failed: {e}")
        df_out[COL_ADX] = df_out[COL_DI_PLUS] = df_out[COL_DI_MINUS] = np.nan

    # 7. EMAs
    ec = config.indicators.ema
    print(f"  Computing EMA({ec.fast}, {ec.medium}, {ec.slow})...")
    try:
        e9 = df_out.ta.ema(length=ec.fast)
        e21 = df_out.ta.ema(length=ec.medium)
        e50 = df_out.ta.ema(length=ec.slow)
        df_out[COL_EMA_9] = e9 if e9 is not None else np.nan
        df_out[COL_EMA_21] = e21 if e21 is not None else np.nan
        df_out[COL_EMA_50] = e50 if e50 is not None else np.nan
    except Exception as e:
        print(f"  [WARN] EMA failed: {e}")
        df_out[COL_EMA_9] = df_out[COL_EMA_21] = df_out[COL_EMA_50] = np.nan

    # 8. Volume Ratio
    vl = config.indicators.entry_volume.lookback_candles
    print(f"  Computing Volume Ratio({vl})...")
    try:
        avg = df_out["Volume"].rolling(vl).mean()
        df_out[COL_VOL_RATIO] = df_out["Volume"] / avg.replace(0, np.nan)
    except Exception:
        df_out[COL_VOL_RATIO] = np.nan

    return df_out


def compute_vwap_5m(df: pd.DataFrame) -> pd.DataFrame:
    """Compute VWAP on 5-minute data with daily reset and compute 5m volume ratio."""
    df_out = df.copy()
    try:
        dates = df_out.index.date
        tp = (df_out["High"] + df_out["Low"] + df_out["Close"]) / 3
        tp_v = tp * df_out["Volume"]
        groups = df_out.groupby(dates)
        df_out[COL_VWAP] = groups.apply(
            lambda g: (tp_v.loc[g.index].cumsum() /
                       df_out["Volume"].loc[g.index].cumsum())
        ).droplevel(0)
    except Exception:
        tp = (df_out["High"] + df_out["Low"] + df_out["Close"]) / 3
        df_out[COL_VWAP] = (tp * df_out["Volume"]).cumsum() / df_out["Volume"].cumsum()

    # 5m volume ratio
    try:
        avg_5m = df_out["Volume"].rolling(10).mean()
        df_out["VOL_RATIO_5M"] = df_out["Volume"] / avg_5m.replace(0, np.nan)
    except Exception:
        df_out["VOL_RATIO_5M"] = np.nan

    return df_out


def shift_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Shift indicator columns by 1 bar to prevent lookahead. OHLCV untouched."""
    df_out = df.copy()
    keep = {"Open", "High", "Low", "Close", "Volume"}
    for col in df_out.columns:
        if col not in keep:
            df_out[col] = df_out[col].shift(1)
    return df_out


def merge_15m_indicators_to_5m(df_5m: pd.DataFrame,
                                df_15m: pd.DataFrame) -> pd.DataFrame:
    """Merge 15-min indicators onto 5-min bars via merge_asof."""
    d5 = df_5m.copy()
    d15 = df_15m.copy()

    d5["__ts__"] = d5.index
    d15["__ts__"] = d15.index

    drop_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"]
                 if c in d15.columns]
    if COL_VWAP in d15.columns:
        drop_cols.append(COL_VWAP)
    d15_clean = d15.drop(columns=drop_cols)

    merged = pd.merge_asof(
        d5.sort_values("__ts__"),
        d15_clean.sort_values("__ts__"),
        on="__ts__",
        direction="backward",
    )
    merged.index = merged["__ts__"]
    merged.index.name = d5.index.name
    merged.drop(columns=["__ts__"], inplace=True)
    return merged


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range."""
    try:
        atr = df.ta.atr(length=period)
        if atr is not None:
            return atr
    except Exception:
        pass
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

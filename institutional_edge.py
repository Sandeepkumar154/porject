import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional


def compute_volume_profile(df: pd.DataFrame, num_bins: int = 24) -> Dict[str, Any]:
    """
    Fixed-Range Volume Profile (FRVP) across the previous trading session + today's open
    (inspired by institutional FRVP / Order-Flow workflow).
    Returns POC (Point of Control), VAH/VAL (70% Value Area), LVN zones, and Order-Flow Delta.
    """
    if df is None or len(df) < 10:
        return {
            'poc': 0.0, 'vah': 0.0, 'val': 0.0,
            'lvn_support': 0.0, 'lvn_resistance': 0.0,
            'bar_delta': 0.0, 'delta_flip': False, 'vp_bias': 'NEUTRAL'
        }

    work = df.copy()
    if 'date' not in work.columns:
        work['date'] = work.index.date

    u_dates = sorted(work['date'].unique())
    # Previous day 9:15 AM through current day latest bar (matching FRVP methodology)
    if len(u_dates) >= 2:
        vp_df = work[work['date'].isin(u_dates[-2:])]
    else:
        vp_df = work

    low_min = float(vp_df['Low'].min())
    high_max = float(vp_df['High'].max())
    curr_price = float(vp_df['Close'].iloc[-1])

    if high_max <= low_min:
        return {
            'poc': curr_price, 'vah': curr_price, 'val': curr_price,
            'lvn_support': 0.0, 'lvn_resistance': 0.0,
            'bar_delta': 0.0, 'delta_flip': False, 'vp_bias': 'NEUTRAL'
        }

    bins = np.linspace(low_min, high_max, num_bins + 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2.0
    vol_profile = np.zeros(num_bins, dtype=float)

    highs = vp_df['High'].values
    lows = vp_df['Low'].values
    vols = vp_df['Volume'].values

    for h, l, v in zip(highs, lows, vols):
        if pd.isna(v) or v <= 0:
            continue
        # Distribute candle volume proportionally across price bins touched
        mask = (bin_centers >= l) & (bin_centers <= h)
        cnt = int(np.sum(mask))
        if cnt > 0:
            vol_profile[mask] += float(v) / cnt
        else:
            idx = int(np.argmin(np.abs(bin_centers - (h + l) / 2.0)))
            vol_profile[idx] += float(v)

    total_vol = float(np.sum(vol_profile))
    if total_vol <= 0:
        return {
            'poc': curr_price, 'vah': curr_price, 'val': curr_price,
            'lvn_support': 0.0, 'lvn_resistance': 0.0,
            'bar_delta': 0.0, 'delta_flip': False, 'vp_bias': 'NEUTRAL'
        }

    # Point of Control (POC) = price bin with maximum institutional volume
    poc_idx = int(np.argmax(vol_profile))
    poc_price = float(bin_centers[poc_idx])
    poc_vol = float(vol_profile[poc_idx])

    # Value Area (70% of total volume expanding around POC)
    target_va_vol = 0.70 * total_vol
    cum_vol = poc_vol
    lo_idx, hi_idx = poc_idx, poc_idx
    while cum_vol < target_va_vol and (lo_idx > 0 or hi_idx < num_bins - 1):
        left_vol = vol_profile[lo_idx - 1] if lo_idx > 0 else -1.0
        right_vol = vol_profile[hi_idx + 1] if hi_idx < num_bins - 1 else -1.0
        if right_vol >= left_vol and hi_idx < num_bins - 1:
            hi_idx += 1
            cum_vol += right_vol
        elif lo_idx > 0:
            lo_idx -= 1
            cum_vol += left_vol
        else:
            break

    val_price = float(bin_centers[lo_idx])
    vah_price = float(bin_centers[hi_idx])

    # Low Volume Nodes (LVN = bins with < 35% of POC volume, excluding extreme edges)
    lvn_indices = [i for i in range(1, num_bins - 1) if vol_profile[i] < 0.35 * poc_vol]
    lvn_prices = [float(bin_centers[i]) for i in lvn_indices]
    lvn_below = [p for p in lvn_prices if p < curr_price]
    lvn_above = [p for p in lvn_prices if p > curr_price]
    lvn_support = max(lvn_below) if lvn_below else val_price
    lvn_resistance = min(lvn_above) if lvn_above else vah_price

    # Order-Flow Bar Delta Approximation & Absorption Flip
    # Delta = Volume * ((Close - Low) - (High - Close)) / (High - Low)
    deltas = []
    for k in range(max(0, len(vp_df) - 3), len(vp_df)):
        bh = float(vp_df['High'].iloc[k])
        bl = float(vp_df['Low'].iloc[k])
        bc = float(vp_df['Close'].iloc[k])
        bv = float(vp_df['Volume'].iloc[k])
        rng = max(0.01, bh - bl)
        d_val = bv * ((bc - bl) - (bh - bc)) / rng
        deltas.append(d_val)

    curr_delta = deltas[-1] if deltas else 0.0
    prev_delta = deltas[-2] if len(deltas) >= 2 else 0.0
    delta_flip = (prev_delta <= 0 and curr_delta > 0) or (curr_delta > 0 and len(deltas) >= 2 and deltas[-1] > deltas[-2])

    if curr_price > vah_price and curr_price > poc_price:
        vp_bias = 'ABOVE_VALUE_BREAKOUT'
    elif curr_price >= poc_price:
        vp_bias = 'BULLISH_VALUE_AREA'
    else:
        vp_bias = 'BELOW_POC_SUPPLY'

    return {
        'poc': round(poc_price, 2),
        'vah': round(vah_price, 2),
        'val': round(val_price, 2),
        'lvn_support': round(lvn_support, 2),
        'lvn_resistance': round(lvn_resistance, 2),
        'bar_delta': round(curr_delta, 1),
        'delta_flip': bool(delta_flip),
        'vp_bias': vp_bias
    }


def estimate_monte_carlo_t1_odds(entry_p: float, sl_p: float, t1_p: float, df: pd.DataFrame, sims: int = 400) -> float:
    """
    Fast vectorized Monte Carlo path simulation using the stock's actual recent 15m log returns
    to estimate probability (%) of hitting Target 1 before Stop Loss within the trading session (16 bars).
    """
    if df is None or len(df) < 20 or entry_p <= sl_p or t1_p <= entry_p:
        return 50.0

    closes = df['Close'].dropna().values
    rets = np.diff(np.log(closes[-40:])) if len(closes) >= 41 else np.diff(np.log(closes))
    if len(rets) < 5:
        return 50.0

    # Slight upward bias capped cleanly to prevent unrealistic optimism
    mu = float(np.mean(rets))
    sigma = max(0.001, float(np.std(rets)))

    rng = np.random.default_rng(42)
    # Simulate 400 paths over 22 x 15m candles (~5.5 hours intraday session)
    rand_rets = rng.normal(loc=mu * 0.5, scale=sigma, size=(sims, 22))
    paths = entry_p * np.exp(np.cumsum(rand_rets, axis=1))

    wins = 0
    for p_arr in paths:
        hit_t1 = np.where(p_arr >= t1_p)[0]
        hit_sl = np.where(p_arr <= sl_p)[0]
        t1_bar = hit_t1[0] if len(hit_t1) > 0 else 999
        sl_bar = hit_sl[0] if len(hit_sl) > 0 else 999
        if t1_bar < sl_bar:
            wins += 1

    return round((wins / sims) * 100.0, 1)


def run_bull_bear_judge(
    candidate: Dict[str, Any],
    df: Optional[pd.DataFrame],
    vp: Dict[str, Any],
    news_info: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Adversarial 3-Agent Trade Debate (Bull vs Bear vs Judge) to eliminate confirmation bias.
    - BULL AGENT: Argues FOR the trade (RS, RVOL, PDH, VP Breakout, Monte Carlo odds).
    - BEAR AGENT: Tries to KILL the trade (Earnings/Results risk, Overextension, Upper Wick Trap, Supply Walls).
    - THE JUDGE: Decides TAKE or SKIP with transparent reasoning.
    """
    sym = candidate.get('symbol', 'UNKNOWN')
    price = float(candidate.get('price', 0.0))
    sl = float(candidate.get('sl', 0.0))
    t1 = float(candidate.get('t1', 0.0))
    sl_risk = max(0.01, price - sl)
    rvol = float(candidate.get('rvol', 1.0))
    rs = float(candidate.get('relative_strength', 0.0))
    gain_pct = float(candidate.get('stock_gain_pct', 0.0))
    pdh = float(candidate.get('pdh', 0.0))

    mc_odds = estimate_monte_carlo_t1_odds(price, sl, t1, df)

    # 1. BULL AGENT ARGUMENTS
    bull_score = 40
    bull_reasons = []

    if rs >= 0.4:
        bull_score += 15
        bull_reasons.append(f"RS +{rs:.2f}% vs Nifty")
    if rvol >= 1.5:
        bull_score += 15
        bull_reasons.append(f"RVOL {rvol:.1f}x Surge")
    if pdh > 0 and price > pdh:
        bull_score += 15
        bull_reasons.append(f"Above PDH ₹{pdh:.1f}")
    if vp.get('vp_bias') == 'ABOVE_VALUE_BREAKOUT':
        bull_score += 10
        bull_reasons.append(f"Above VAH ₹{vp['vah']:.1f}")
    if vp.get('delta_flip'):
        bull_score += 10
        bull_reasons.append("Positive Delta Absorption")
    if mc_odds >= 55.0:
        bull_score += 10
        bull_reasons.append(f"Monte Carlo {mc_odds:.0f}% T1 Odds")

    bull_score = min(100, bull_score)

    # 2. BEAR AGENT COUNTER-ARGUMENTS (Job: KILL THE TRADE!)
    bear_risk = 0
    bear_reasons = []

    # Bear Check A: Corporate Results / Bearish Headline Risk
    sentiment_lbl = str(news_info.get('sentiment_label', 'NEUTRAL')).upper()
    headline = str(news_info.get('key_headline', '')).lower()
    risk_keywords = ['results', 'earnings', 'quarterly', 'q1', 'q2', 'q3', 'q4', 'penalty', 'sebi', 'fraud', 'probe', 'downgrade']
    if sentiment_lbl == 'BEARISH':
        bear_risk += 50
        bear_reasons.append("BEARISH News Sentiment")
    elif any(k in headline for k in risk_keywords):
        bear_risk += 40
        bear_reasons.append("Event/Results Volatility Risk Today")

    # Bear Check B: Already Overextended (Chasing > +2.6% morning move)
    if gain_pct > 2.6:
        bear_risk += 35
        bear_reasons.append(f"Overextended +{gain_pct:.1f}% from Open")

    # Bear Check C: Volume Profile / POC / VAH Resistance Overhead
    poc = float(vp.get('poc', 0.0))
    vah = float(vp.get('vah', 0.0))
    if poc > price and (poc - price) < (1.0 * sl_risk):
        bear_risk += 30
        bear_reasons.append(f"Heavy Supply POC @ ₹{poc:.1f}")
    elif vah > price and (vah - price) < (0.8 * sl_risk):
        bear_risk += 20
        bear_reasons.append(f"VAH Ceiling @ ₹{vah:.1f}")

    # Bear Check D: Upper Wick Rejection / Negative Order-Flow Trap
    if df is not None and len(df) >= 1:
        b_h = float(df['High'].iloc[-1])
        b_l = float(df['Low'].iloc[-1])
        b_o = float(df['Open'].iloc[-1])
        b_c = float(df['Close'].iloc[-1])
        b_rng = max(0.01, b_h - b_l)
        upper_wick_ratio = (b_h - max(b_o, b_c)) / b_rng
        if upper_wick_ratio > 0.45:
            bear_risk += 35
            bear_reasons.append(f"Selling Wick Rejection ({upper_wick_ratio*100:.0f}%)")
    if vp.get('bar_delta', 0.0) < 0 and not vp.get('delta_flip', False):
        bear_risk += 25
        bear_reasons.append("Negative Buying Delta")

    # Bear Check E: Weak Monte Carlo Odds
    if mc_odds < 45.0:
        bear_risk += 25
        bear_reasons.append(f"Low Monte Carlo T1 Odds ({mc_odds:.0f}%)")

    bear_risk = min(100, bear_risk)

    # 3. THE JUDGE FINAL VERDICT
    # Take ONLY if Bull convincingly outweighs Bear AND Bear Risk < 45
    net_conviction = bull_score - bear_risk
    verdict = "TAKE" if (bear_risk < 45 and net_conviction >= 30 and mc_odds >= 42.0) else "SKIP"

    return {
        'symbol': sym,
        'verdict': verdict,
        'bull_score': bull_score,
        'bear_risk': bear_risk,
        'net_conviction': net_conviction,
        'mc_odds': mc_odds,
        'bull_summary': ", ".join(bull_reasons) if bull_reasons else "Standard 8-Shield Setup",
        'bear_summary': ", ".join(bear_reasons) if bear_reasons else "No major structural flaws found",
        'vp': vp
    }

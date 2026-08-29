"""
signals.py — 8-Shield Scoring System + Bonus Points.

Layer 5 of the Master Trading Plan.
ALL 8 shields must pass for entry. No exceptions.

Shields (2 pts each, 16 base):
  1. RSI(14)           → 45-68 range
  2. Supertrend(10,3)  → Must be GREEN (bullish)
  3. VWAP              → Price ABOVE VWAP
  4. MACD(12,26,9)     → Histogram positive
  5. Bollinger(20,2)   → NOT at upper band
  6. ADX(14)           → Above 20
  7. EMA Ladder        → EMA9 > EMA21 > EMA50
  8. Entry Volume      → Volume > 1.5x avg

Bonus points (up to 32 additional):
  Volume shock, FII/DII, news, sector, OI, beta, etc.

Max score: 48 points
Decision: 35+ ELITE, 25-34 STRONG, 18-24 AVERAGE, <18 SKIP
"""

from dataclasses import dataclass, field
from datetime import date, time, datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from config import StrategyConfig


# ═══════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ShieldResult:
    """Result of a single shield check."""
    name: str
    passed: bool
    value: float = 0.0
    reason: str = ""
    points: float = 0.0


@dataclass
class BonusResult:
    """A single bonus point item."""
    name: str
    points: float = 0.0
    reason: str = ""


@dataclass
class SignalResult:
    """Complete signal assessment for one bar."""
    shields: Dict[str, ShieldResult] = field(default_factory=dict)
    bonuses: List[BonusResult] = field(default_factory=list)
    base_score: float = 0.0     # Shield points only (max 16)
    bonus_score: float = 0.0    # Bonus points
    score: float = 0.0          # Total (max 48)
    all_shields_pass: bool = False
    recommendation: str = "SKIP"  # ELITE / STRONG / AVERAGE / SKIP
    is_entry: bool = False


@dataclass
class NiftyDirection:
    """Nifty 50 direction assessment."""
    change_pct: float = 0.0
    direction: str = "FLAT"          # POSITIVE / FLAT / NEGATIVE
    can_trade: bool = True
    description: str = ""
    min_score_required: float = 18.0  # Default


@dataclass
class WindowContext:
    """Context about the current trading window."""
    window_name: str = "NONE"        # PRIME / MOMENTUM / CONTINUATION / DEAD / NONE
    window_number: int = 0           # 1, 2, 3, or 0
    is_active: bool = False
    max_trades: int = 0
    extra_conditions: List[str] = field(default_factory=list)
    t1_only: bool = False


# ═══════════════════════════════════════════════════════════════════
# SHIELD CHECKS (8 shields, 2 pts each)
# ═══════════════════════════════════════════════════════════════════

def check_shield_rsi(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 1: RSI must be between 45 and 68."""
    rsi = bar.get("RSI_14", np.nan)
    if pd.isna(rsi):
        return ShieldResult("RSI", False, 0, "RSI data not available")

    cfg = config.indicators.rsi
    if rsi >= cfg.danger_zone:
        return ShieldResult("RSI", False, rsi,
                            f"RSI={rsi:.1f} DANGER ZONE (>{cfg.danger_zone})")
    if cfg.min_val <= rsi <= cfg.max_val:
        pts = config.scoring.shield_points
        return ShieldResult("RSI", True, rsi,
                            f"RSI={rsi:.1f} in range ({cfg.min_val}-{cfg.max_val})",
                            pts)
    if rsi < cfg.min_val:
        return ShieldResult("RSI", False, rsi,
                            f"RSI={rsi:.1f} too low (needs >{cfg.min_val})")
    return ShieldResult("RSI", False, rsi,
                        f"RSI={rsi:.1f} too high (needs <{cfg.max_val})")


def check_shield_supertrend(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 2: Supertrend must be GREEN (bullish)."""
    direction = bar.get("SUPERTd_10_3.0", np.nan)
    if pd.isna(direction):
        return ShieldResult("Supertrend", False, 0, "Supertrend data not available")

    if direction > 0:  # +1 = bullish
        return ShieldResult("Supertrend", True, float(direction),
                            "Supertrend is BULLISH (green)",
                            config.scoring.shield_points)
    return ShieldResult("Supertrend", False, float(direction),
                        "Supertrend is BEARISH (red)")


def check_shield_vwap(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 3: Price must be ABOVE VWAP."""
    close = bar.get("Close", np.nan)
    vwap = bar.get("VWAP_D", np.nan)
    if pd.isna(close) or pd.isna(vwap) or vwap == 0:
        return ShieldResult("VWAP", False, 0, "VWAP data not available")

    pct_above = (close - vwap) / vwap * 100
    if close > vwap:
        return ShieldResult("VWAP", True, pct_above,
                            f"Price Rs.{close:.2f} > VWAP Rs.{vwap:.2f} (+{pct_above:.2f}%)",
                            config.scoring.shield_points)
    return ShieldResult("VWAP", False, pct_above,
                        f"Price Rs.{close:.2f} < VWAP Rs.{vwap:.2f} ({pct_above:.2f}%)")


def check_shield_macd(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 4: MACD histogram must be POSITIVE."""
    hist = bar.get("MACDh_12_26_9", np.nan)
    macd = bar.get("MACD_12_26_9", np.nan)
    signal = bar.get("MACDs_12_26_9", np.nan)

    if pd.isna(hist):
        return ShieldResult("MACD", False, 0, "MACD data not available")

    if hist > 0 and (pd.isna(macd) or pd.isna(signal) or macd > signal):
        return ShieldResult("MACD", True, float(hist),
                            f"MACD bullish (hist={hist:.2f})",
                            config.scoring.shield_points)
    return ShieldResult("MACD", False, float(hist),
                        f"MACD bearish (hist={hist:.2f})")


def check_shield_bollinger(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 5: Price must NOT be touching upper Bollinger Band."""
    close = bar.get("Close", np.nan)
    upper = bar.get("BBU_20_2.0", np.nan)

    if pd.isna(close) or pd.isna(upper) or upper == 0:
        return ShieldResult("Bollinger", False, 0, "Bollinger data not available")

    proximity = abs(close - upper) / upper
    threshold = config.indicators.bollinger.proximity_pct

    if close < upper and proximity > threshold:
        return ShieldResult("Bollinger", True, proximity,
                            f"Price Rs.{close:.2f} safely below upper BB Rs.{upper:.2f}",
                            config.scoring.shield_points)
    if close >= upper:
        return ShieldResult("Bollinger", False, proximity,
                            f"Price Rs.{close:.2f} at/above upper BB Rs.{upper:.2f} — stretched")
    return ShieldResult("Bollinger", False, proximity,
                        f"Price Rs.{close:.2f} too close to upper BB Rs.{upper:.2f}")


def check_shield_adx(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 6: ADX must be above 20."""
    adx = bar.get("ADX_14", np.nan)
    if pd.isna(adx):
        return ShieldResult("ADX", False, 0, "ADX data not available")

    cfg = config.indicators.adx
    if adx >= cfg.min_threshold:
        label = "forming" if adx < cfg.good_trend else (
            "strong" if adx < cfg.strong_trend else "very strong")
        return ShieldResult("ADX", True, float(adx),
                            f"ADX={adx:.1f} — trend {label}",
                            config.scoring.shield_points)
    return ShieldResult("ADX", False, float(adx),
                        f"ADX={adx:.1f} — no trend (needs >{cfg.min_threshold})")


def check_shield_ema_ladder(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 7: EMA9 > EMA21 > EMA50, and price holding above trend."""
    close = bar.get("Close", np.nan)
    ema9 = bar.get("EMA_9", np.nan)
    ema21 = bar.get("EMA_21", np.nan)
    ema50 = bar.get("EMA_50", np.nan)

    if any(pd.isna(v) for v in [close, ema9, ema21, ema50]):
        return ShieldResult("EMA_Ladder", False, 0, "EMA data not available")

    ladder_ok = (ema9 >= ema21 and ema21 >= ema50)
    price_above = (close >= min(ema9, ema21))

    if ladder_ok and price_above:
        return ShieldResult("EMA_Ladder", True, 1.0,
                            f"EMA aligned: Price>{ema9:.1f}>={ema21:.1f}>={ema50:.1f}",
                            config.scoring.shield_points)

    if not ladder_ok:
        return ShieldResult("EMA_Ladder", False, 0,
                            f"EMA not aligned: EMA9={ema9:.1f}, EMA21={ema21:.1f}, EMA50={ema50:.1f}")
    return ShieldResult("EMA_Ladder", False, 0,
                        f"Price Rs.{close:.2f} below EMA21 Rs.{ema21:.2f}")


def check_shield_entry_volume(bar: pd.Series, config: StrategyConfig) -> ShieldResult:
    """Shield 8: Entry candle volume must be above average."""
    vol_15m = bar.get("VOL_RATIO", np.nan)
    vol_5m = bar.get("VOL_RATIO_5M", np.nan)

    valid_vols = [v for v in [vol_15m, vol_5m] if not pd.isna(v) and v > 0]
    if not valid_vols:
        return ShieldResult("Entry_Volume", False, 0, "Volume ratio data not available")

    best_ratio = max(valid_vols)
    threshold = config.indicators.entry_volume.min_multiplier
    if best_ratio >= threshold:
        label = "strong" if best_ratio >= config.indicators.entry_volume.strong_multiplier else "normal"
        return ShieldResult("Entry_Volume", True, float(best_ratio),
                            f"Volume {best_ratio:.1f}x average ({label} entry)",
                            config.scoring.shield_points)
    return ShieldResult("Entry_Volume", False, float(best_ratio),
                        f"Volume {best_ratio:.1f}x average (needs >={threshold:.1f}x)")


# ═══════════════════════════════════════════════════════════════════
# BONUS POINT CALCULATIONS
# ═══════════════════════════════════════════════════════════════════

def calculate_bonuses(bar: pd.Series, config: StrategyConfig,
                      prev_day_avg_vol: float = 0,
                      global_score: int = 3,
                      fii_bonus: float = 0,
                      news_tier: Optional[str] = None,
                      in_lists_count: int = 0,
                      oi_signal: Optional[str] = None,
                      beta: float = 1.0,
                      stock_day_gain_pct: float = 0.0,
                      gap_pct: float = 0.0) -> List[BonusResult]:
    """Calculate all bonus points for a bar."""
    bonuses = []
    cfg = config.scoring

    # Volume shock (> 3x previous day average)
    volume = bar.get("Volume", 0)
    if prev_day_avg_vol > 0 and volume > 3 * prev_day_avg_vol:
        bonuses.append(BonusResult("Volume Shock 3x", cfg.volume_shock_3x,
                                    f"Volume {volume/prev_day_avg_vol:.1f}x prev day avg"))

    # RSI sweet spot (55-65)
    rsi = bar.get("RSI_14", np.nan)
    if not pd.isna(rsi):
        rsi_cfg = config.indicators.rsi
        if rsi_cfg.sweet_spot_min <= rsi <= rsi_cfg.sweet_spot_max:
            bonuses.append(BonusResult("RSI Sweet Spot", cfg.rsi_sweet_spot,
                                        f"RSI={rsi:.1f} in sweet spot 55-65"))

    # ADX strong trend (> 35)
    adx = bar.get("ADX_14", np.nan)
    if not pd.isna(adx) and adx >= config.indicators.adx.strong_trend:
        bonuses.append(BonusResult("ADX Strong", cfg.adx_strong,
                                    f"ADX={adx:.1f} — very strong trend"))

    # MACD fresh crossover (histogram just turned positive)
    hist = bar.get("MACDh_12_26_9", np.nan)
    if not pd.isna(hist) and 0 < hist < 0.5:
        bonuses.append(BonusResult("MACD Fresh Cross", cfg.macd_fresh_crossover,
                                    f"MACD hist just turned positive ({hist:.2f})"))

    # FII/DII bonus (passed from engine)
    if fii_bonus != 0:
        bonuses.append(BonusResult("FII/DII", fii_bonus,
                                    f"FII/DII bonus: {fii_bonus:+.0f} pts"))

    # News catalyst
    if news_tier == "A":
        bonuses.append(BonusResult("News Tier A", cfg.news_tier_a, "Tier A news catalyst"))
    elif news_tier == "B":
        bonuses.append(BonusResult("News Tier B", cfg.news_tier_b, "Tier B news catalyst"))
    elif news_tier == "C":
        bonuses.append(BonusResult("News Tier C", cfg.news_tier_c, "Tier C news catalyst"))

    # In multiple morning lists
    if in_lists_count >= 3:
        bonuses.append(BonusResult("In 3+ Lists", 4.0,
                                    f"Stock in {in_lists_count} morning lists"))
    elif in_lists_count >= 2:
        bonuses.append(BonusResult("In 2+ Lists", cfg.in_multiple_lists,
                                    f"Stock in {in_lists_count} morning lists"))

    # OI signal
    if oi_signal == "LONG_BUILDUP":
        bonuses.append(BonusResult("OI Long Buildup", cfg.oi_long_buildup,
                                    "Price up + OI up = long buildup"))
    elif oi_signal == "SHORT_COVERING":
        bonuses.append(BonusResult("OI Short Cover", 1.0,
                                    "Price up + OI down = short covering"))

    # Beta
    if beta >= config.stock_selection.beta_best:
        bonuses.append(BonusResult("High Beta", cfg.beta_above_1_5,
                                    f"Beta {beta:.2f} — best for intraday"))
    elif beta >= config.stock_selection.beta_good:
        bonuses.append(BonusResult("Good Beta", cfg.beta_above_1_0,
                                    f"Beta {beta:.2f} — good"))

    # ATR check
    atr_pct = bar.get("ATR_PCT", np.nan)
    if not pd.isna(atr_pct):
        if atr_pct >= config.stock_selection.atr_high_pct:
            bonuses.append(BonusResult("High ATR", cfg.atr_high, f"ATR {atr_pct:.1f}% of price"))
        elif atr_pct < config.stock_selection.atr_mod_pct:
            bonuses.append(BonusResult("Low ATR", cfg.atr_low_penalty,
                                        f"ATR {atr_pct:.1f}% — slow mover"))

    # Gap up bonus
    if 0.5 <= gap_pct <= 2.0:
        bonuses.append(BonusResult("Gap Up", cfg.gap_up_small,
                                    f"Gap up {gap_pct:.1f}% (bullish open)"))

    return bonuses


# ═══════════════════════════════════════════════════════════════════
# MAIN SCORING FUNCTION
# ═══════════════════════════════════════════════════════════════════

def score_bar(bar: pd.Series, config: StrategyConfig,
              prev_day_avg_vol: float = 0,
              global_score: int = 3,
              fii_bonus: float = 0,
              news_tier: Optional[str] = None,
              in_lists_count: int = 0,
              oi_signal: Optional[str] = None,
              beta: float = 1.0,
              gap_pct: float = 0.0) -> SignalResult:
    """
    Run all 8 shields and calculate total score with bonuses.

    ALL 8 shields must pass for entry. Score determines position sizing.

    Returns SignalResult with full breakdown.
    """
    result = SignalResult()

    # Run all 8 shields
    shield_checks = [
        ("RSI", check_shield_rsi),
        ("Supertrend", check_shield_supertrend),
        ("VWAP", check_shield_vwap),
        ("MACD", check_shield_macd),
        ("Bollinger", check_shield_bollinger),
        ("ADX", check_shield_adx),
        ("EMA_Ladder", check_shield_ema_ladder),
        ("Entry_Volume", check_shield_entry_volume),
    ]

    for name, check_fn in shield_checks:
        shield = check_fn(bar, config)
        result.shields[name] = shield
        result.base_score += shield.points

    # Check if ALL shields pass
    result.all_shields_pass = all(s.passed for s in result.shields.values())

    # Calculate bonuses (only if all shields pass)
    if result.all_shields_pass:
        result.bonuses = calculate_bonuses(
            bar, config, prev_day_avg_vol, global_score,
            fii_bonus, news_tier, in_lists_count, oi_signal,
            beta, gap_pct=gap_pct,
        )
        result.bonus_score = sum(b.points for b in result.bonuses)

    # Total score
    result.score = result.base_score + result.bonus_score

    # Decision
    grade = config.get_trade_grade(result.score)
    result.recommendation = grade
    result.is_entry = (result.all_shields_pass and
                       result.score >= config.scoring.average_min)

    return result


# ═══════════════════════════════════════════════════════════════════
# NIFTY DIRECTION CHECK
# ═══════════════════════════════════════════════════════════════════

def assess_nifty_direction(nifty_bars: pd.DataFrame,
                           config: StrategyConfig) -> NiftyDirection:
    """
    Assess Nifty 50 direction from early-morning 5-min bars.

    Used to determine if market environment supports long trades.
    """
    if nifty_bars is None or nifty_bars.empty or len(nifty_bars) < 2:
        return NiftyDirection(
            change_pct=0.0, direction="UNKNOWN",
            can_trade=True, description="Nifty data not available — cautious",
            min_score_required=25.0,
        )

    first_open = nifty_bars.iloc[0]["Open"]
    last_close = nifty_bars.iloc[-1]["Close"]
    if first_open == 0:
        return NiftyDirection(0, "UNKNOWN", True, "Nifty open=0", 25.0)

    change_pct = (last_close - first_open) / first_open * 100

    if change_pct > 0.2:
        return NiftyDirection(change_pct, "POSITIVE", True,
                              f"Nifty +{change_pct:.2f}% — POSITIVE",
                              config.scoring.average_min)
    elif change_pct >= -0.5:
        return NiftyDirection(change_pct, "FLAT", True,
                              f"Nifty {change_pct:+.2f}% — FLAT (normal/careful)",
                              config.scoring.average_min)
    elif change_pct >= -0.7:
        return NiftyDirection(change_pct, "MILDLY_NEGATIVE", True,
                              f"Nifty {change_pct:+.2f}% — mildly negative (strong only)",
                              config.scoring.strong_min)
    else:
        return NiftyDirection(change_pct, "NEGATIVE", False,
                              f"Nifty {change_pct:+.2f}% — NEGATIVE (no trade)",
                              999.0)


# ═══════════════════════════════════════════════════════════════════
# TRADING WINDOW DETECTION
# ═══════════════════════════════════════════════════════════════════

def get_current_window(current_time: time,
                       config: StrategyConfig) -> WindowContext:
    """
    Determine which trading window is currently active.

    Returns WindowContext with window details.
    """
    wc = config.windows

    # No-trade zone (9:15-9:30)
    if wc.no_trade_start <= current_time < wc.no_trade_end:
        return WindowContext("NO_TRADE", 0, False, 0)

    # Window 1: Prime Time (9:30-10:30)
    w1 = wc.window1
    if w1.start <= current_time < w1.end:
        return WindowContext(w1.name, 1, True, w1.max_trades,
                             w1.extra_conditions, w1.t1_only)

    # Gap between W1 and W2 (10:30-11:00) — no new trades
    if time(10, 30) <= current_time < time(11, 0):
        return WindowContext("GAP_1_2", 0, False, 0)

    # Window 2: Momentum (11:00-12:00)
    w2 = wc.window2
    if w2.start <= current_time < w2.end:
        return WindowContext(w2.name, 2, True, w2.max_trades,
                             w2.extra_conditions, w2.t1_only)

    # Dead Zone (12:00-2:00)
    if wc.dead_zone_start <= current_time < wc.dead_zone_end:
        return WindowContext("DEAD_ZONE", 0, False, 0)

    # Window 3: Continuation (2:00-2:45)
    w3 = wc.window3
    if w3.start <= current_time < w3.end:
        return WindowContext(w3.name, 3, True, w3.max_trades,
                             w3.extra_conditions, w3.t1_only)

    # Wind-down zone (2:45-3:00)
    if time(14, 45) <= current_time <= time(15, 0):
        return WindowContext("WIND_DOWN", 0, False, 0)

    return WindowContext("CLOSED", 0, False, 0)


# ═══════════════════════════════════════════════════════════════════
# 5-MINUTE ENTRY CONFIRMATIONS
# ═══════════════════════════════════════════════════════════════════

def check_5min_confirmation(bar: pd.Series,
                            avg_vol_5m: float = 0) -> bool:
    """
    Confirm entry on 5-minute bar:
    - Current candle must be GREEN (close > open)
    - Volume should be reasonable
    """
    close = bar.get("Close", 0)
    open_p = bar.get("Open", 0)
    volume = bar.get("Volume", 0)

    if close <= open_p:
        return False  # Red candle

    if avg_vol_5m > 0 and volume < avg_vol_5m * 0.5:
        return False  # Very low volume

    return True


def check_vwap_pullback(recent_bars: pd.DataFrame,
                        current_bar: pd.Series,
                        config: StrategyConfig) -> bool:
    """
    Check for VWAP pullback bounce pattern:
    - Recent bars should show price dipping toward VWAP
    - Current bar bouncing back up from VWAP
    """
    if recent_bars.empty:
        return True  # No history = allow

    vwap = current_bar.get("VWAP_D", np.nan)
    close = current_bar.get("Close", np.nan)

    if pd.isna(vwap) or pd.isna(close):
        return True

    # Check if any recent bar touched or came close to VWAP
    touched_vwap = False
    for _, rb in recent_bars.iterrows():
        rb_low = rb.get("Low", np.nan)
        rb_vwap = rb.get("VWAP_D", np.nan)
        if not pd.isna(rb_low) and not pd.isna(rb_vwap):
            if rb_low <= rb_vwap * 1.002:  # Within 0.2% of VWAP
                touched_vwap = True
                break

    # Current bar must be above VWAP (bounce)
    if close > vwap and touched_vwap:
        return True

    # Also allow if price is simply above VWAP
    if close > vwap * 1.001:
        return True

    return False


def check_window2_conditions(bar: pd.Series, day_data: pd.DataFrame,
                              bar_index: int) -> bool:
    """
    Extra conditions for Window 2 (Momentum: 11:00 AM - 12:00 PM):
    - Stock making new recent highs (fresh momentum)
    - Volume above average
    """
    close = bar.get("Close", np.nan)
    if pd.isna(close):
        return False

    # Check if breaking out of recent 30-min range (last 6 5-min bars)
    if bar_index >= 6:
        recent_highs = day_data.iloc[max(0, bar_index - 6):bar_index]["High"]
        if not recent_highs.empty and close < recent_highs.max() * 0.998:
            return False  # Stalled, not breaking out

    # Volume check
    vol_ratio = bar.get("VOL_RATIO", np.nan)
    vol_5m = bar.get("VOL_RATIO_5M", np.nan)
    best_vol = max([v for v in [vol_ratio, vol_5m] if not pd.isna(v)] or [0])
    if best_vol < 1.2:
        return False

    return True


def check_window3_conditions(bar: pd.Series, day_open: float,
                              config: StrategyConfig) -> bool:
    """
    Extra conditions for Window 3 (Continuation: 2:00 PM - 2:45 PM):
    - Stock holding positive intraday momentum (up >= 1.0% on day or up from open)
    - RSI still below 65 (not overbought)
    - Supertrend still green
    """
    close = bar.get("Close", np.nan)
    if pd.isna(close) or day_open <= 0:
        return False

    # Stock holding gains
    day_gain = (close - day_open) / day_open * 100
    w3 = config.windows.window3
    if day_gain < w3.require_stock_up_pct:
        return False

    # RSI below 65
    rsi = bar.get("RSI_14", np.nan)
    if not pd.isna(rsi) and rsi > w3.max_rsi:
        return False

    # Supertrend green
    st_dir = bar.get("SUPERTd_10_3.0", np.nan)
    if not pd.isna(st_dir) and st_dir < 0:
        return False

    # Above VWAP
    vwap = bar.get("VWAP_D", np.nan)
    if not pd.isna(vwap) and close < vwap:
        return False

    return True

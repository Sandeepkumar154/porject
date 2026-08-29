"""
config.py — Master Trading Plan v2 Configuration.

6-Layer / 8-Shield / 48-Point Professional Intraday System
NSE + BSE — All liquid stocks, no sector bias

Layers:
  1. Global Market Check (Gift Nifty, US, Crude, USD/INR, Asia)
  2. FII/DII Activity
  3. News Catalyst
  4. Stock Selection (Volume + Volatility + OI)
  5. 8 Technical Shields
  6. Risk + Position Sizing
"""

from dataclasses import dataclass, field
from datetime import time
from typing import List, Optional


# ═══════════════════════════════════════════════════════════════════
# LAYER 5 — 8 SHIELDS CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RSIConfig:
    period: int = 14
    min_val: float = 45.0
    max_val: float = 68.0
    sweet_spot_min: float = 55.0
    sweet_spot_max: float = 65.0
    danger_zone: float = 70.0  # Above this = skip always


@dataclass
class SupertrendConfig:
    period: int = 10
    multiplier: float = 3.0
    sl_buffer: float = 2.0  # SL = supertrend - Rs.2


@dataclass
class VWAPConfig:
    breach_buffer_pct: float = 0.001  # 0.1% below = breach


@dataclass
class MACDConfig:
    fast: int = 12
    slow: int = 26
    signal: int = 9


@dataclass
class BollingerConfig:
    period: int = 20
    std_dev: float = 2.0
    proximity_pct: float = 0.002  # Within 0.2% of upper band = too close


@dataclass
class ADXConfig:
    period: int = 14
    min_threshold: float = 20.0    # Below 20 = no trend, skip
    good_trend: float = 25.0      # 25-35 = good
    strong_trend: float = 35.0    # Above 35 = very strong


@dataclass
class EMAConfig:
    fast: int = 9
    medium: int = 21
    slow: int = 50


@dataclass
class EntryVolumeConfig:
    lookback_candles: int = 10
    min_multiplier: float = 1.2   # 1.2x+ = normal entry
    strong_multiplier: float = 2.0  # 2x+ = strong entry


@dataclass
class IndicatorConfig:
    rsi: RSIConfig = field(default_factory=RSIConfig)
    supertrend: SupertrendConfig = field(default_factory=SupertrendConfig)
    vwap: VWAPConfig = field(default_factory=VWAPConfig)
    macd: MACDConfig = field(default_factory=MACDConfig)
    bollinger: BollingerConfig = field(default_factory=BollingerConfig)
    adx: ADXConfig = field(default_factory=ADXConfig)
    ema: EMAConfig = field(default_factory=EMAConfig)
    entry_volume: EntryVolumeConfig = field(default_factory=EntryVolumeConfig)


# ═══════════════════════════════════════════════════════════════════
# LAYER 1 — GLOBAL MARKET CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class GiftNiftyConfig:
    strong_bullish: float = 50.0     # Above +50 pts
    flat_range: float = 50.0         # Between +/-50
    cautious: float = -50.0          # Below -50
    half_size: float = -150.0        # Below -150 = 1 trade, half size
    no_trade: float = -300.0         # Below -300 = NO TRADES


@dataclass
class USMarketConfig:
    positive_threshold: float = 0.5   # US up > 0.5% = positive
    avoid_it_threshold: float = -1.0  # US down > 1% = avoid IT
    reduce_all_threshold: float = -2.0  # US down > 2% = reduce 50%


@dataclass
class GlobalConfig:
    gift_nifty: GiftNiftyConfig = field(default_factory=GiftNiftyConfig)
    us_market: USMarketConfig = field(default_factory=USMarketConfig)
    # Global Score thresholds
    full_trading_min: int = 5        # All 5 positive
    careful_min: int = 3             # 3-4 positive
    reduce_25_min: int = 2           # 2-3 positive
    half_size_min: int = 1           # 1-2 positive
    no_trade_below: int = 1          # All negative = skip


# ═══════════════════════════════════════════════════════════════════
# LAYER 2 — FII/DII CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FIIDIIConfig:
    strong_buy_threshold: float = 3000.0   # > Rs.3000 Cr = very strong
    normal_buy_threshold: float = 1000.0   # Rs.1000-3000 = normal
    mild_sell_threshold: float = -1000.0   # Sold < 1000 = mild caution
    strong_sell_threshold: float = -3000.0 # Sold > 3000 = avoid all


# ═══════════════════════════════════════════════════════════════════
# TRADING WINDOWS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class WindowConfig:
    """Configuration for a single trading window."""
    name: str = ""
    start: time = field(default_factory=lambda: time(9, 30))
    end: time = field(default_factory=lambda: time(10, 30))
    max_trades: int = 2
    reentry_allowed: bool = True
    extra_conditions: List[str] = field(default_factory=list)
    # Window-specific overrides
    min_volume_multiplier: float = 1.5
    require_new_highs: bool = False
    require_stock_up_pct: float = 0.0
    max_rsi: float = 68.0
    t1_only: bool = False  # Only target T1 (no T2)


@dataclass
class TradingWindowsConfig:
    # No-trade zone: 9:15-9:30 (opening chaos)
    no_trade_start: time = field(default_factory=lambda: time(9, 15))
    no_trade_end: time = field(default_factory=lambda: time(9, 30))

    # Window 1: Prime Time
    window1: WindowConfig = field(default_factory=lambda: WindowConfig(
        name="PRIME",
        start=time(9, 30),
        end=time(10, 30),
        max_trades=2,
        reentry_allowed=True,
        min_volume_multiplier=1.5,
    ))

    # Window 2: Momentum
    window2: WindowConfig = field(default_factory=lambda: WindowConfig(
        name="MOMENTUM",
        start=time(11, 0),
        end=time(12, 0),
        max_trades=1,
        reentry_allowed=False,
        require_new_highs=True,
        min_volume_multiplier=2.0,
        extra_conditions=["new_15m_highs", "volume_2x", "nifty_positive", "sector_positive"],
    ))

    # Dead Zone: 12:00-2:00 PM — NO TRADES
    dead_zone_start: time = field(default_factory=lambda: time(12, 0))
    dead_zone_end: time = field(default_factory=lambda: time(14, 0))

    # Window 3: Continuation
    window3: WindowConfig = field(default_factory=lambda: WindowConfig(
        name="CONTINUATION",
        start=time(14, 0),
        end=time(14, 45),
        max_trades=1,
        reentry_allowed=False,
        require_stock_up_pct=1.0,
        max_rsi=65.0,
        t1_only=True,
        extra_conditions=["stock_up_1pct", "rsi_below_65", "supertrend_green", "above_vwap_all_day"],
    ))

    # Mandatory exit
    mandatory_exit_time: time = field(default_factory=lambda: time(15, 0))
    wind_down_time: time = field(default_factory=lambda: time(14, 55))

    # Max trades total per day
    max_trades_per_day: int = 4


# ═══════════════════════════════════════════════════════════════════
# SCORING SYSTEM — 48 POINTS MAX
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ScoringConfig:
    """Complete 48-point scoring system."""
    # Base: each shield = 2 pts × 8 shields = 16
    shield_points: float = 2.0

    # BONUS POINTS
    volume_shock_3x: float = 3.0       # Volume > 3x avg
    tier1_stock: float = 2.0           # Daily value > 500 Cr
    in_multiple_lists: float = 2.0     # Stock in 2+ morning lists
    sector_outperforming: float = 2.0  # Sector > Nifty
    oi_long_buildup: float = 3.0       # OI long buildup
    news_tier_a: float = 4.0           # Tier A news catalyst
    news_tier_b: float = 2.0           # Tier B news
    news_tier_c: float = 1.0           # Tier C news
    fii_dii_both_buying: float = 3.0   # Both FII+DII buying
    fii_buying_only: float = 2.0       # FII buying, DII neutral
    dii_buying_only: float = 1.0       # DII buying, FII neutral
    fii_selling_dii_neutral: float = -2.0  # Penalty
    stock_outperforms_sector: float = 1.0
    rsi_sweet_spot: float = 1.0        # RSI 55-65
    adx_strong: float = 1.0           # ADX > 35
    macd_fresh_crossover: float = 1.0
    vwap_support_coincide: float = 2.0
    beta_above_1_5: float = 2.0
    beta_above_1_0: float = 1.0
    gap_up_small: float = 1.0         # Gap 0.5% to 2%
    atr_high: float = 1.0             # ATR > 2% of price
    atr_low_penalty: float = -1.0     # ATR < 1%

    # DECISION THRESHOLDS
    elite_min: float = 35.0     # 35-48 = ELITE TRADE
    strong_min: float = 25.0    # 25-34 = STRONG TRADE
    average_min: float = 18.0   # 18-24 = AVERAGE TRADE
    skip_below: float = 18.0    # Below 18 = SKIP


# ═══════════════════════════════════════════════════════════════════
# TARGETS & POSITION SIZING
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TargetConfig:
    t1_multiplier: float = 2.0    # T1 = entry + 2×SL distance
    t2_multiplier: float = 4.0    # T2 = entry + 4×SL distance
    t1_exit_pct: float = 0.60     # Exit 60% at T1
    t2_exit_pct: float = 0.40     # Exit 40% at T2
    max_atr_t1: float = 0.50      # T1 must be within 50% of ATR
    max_atr_t2: float = 1.00      # T2 must be within 100% of ATR
    max_atr_overall: float = 1.50  # Never target beyond 1.5x ATR


@dataclass
class PositionSizingConfig:
    """Dynamic position sizing based on score tier."""
    elite_risk_pct: float = 0.015    # 1.5% of capital
    strong_risk_pct: float = 0.010   # 1.0% of capital
    average_risk_pct: float = 0.005  # 0.5% of capital


# ═══════════════════════════════════════════════════════════════════
# LAYER 6 — RISK MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RiskConfig:
    # Daily limits
    max_trades_per_day: int = 4
    max_daily_loss_pct: float = 0.03    # 3% of capital → stop
    max_consecutive_losses: int = 3      # 3 consecutive → stop for day

    # Weekly limits
    max_weekly_loss_pct: float = 0.06   # 6% → no trades next 2 days

    # Monthly limits
    max_monthly_loss_pct: float = 0.10  # 10% → 1 week break

    # Good day rules
    good_day_reduce_pct: float = 0.05   # Up 5% → reduce size 50%
    good_day_stop_pct: float = 0.08     # Up 8% → stop trading

    # After consecutive losses
    reduce_after_2_losses: bool = True   # Half size after 2 losses

    # VIX
    vix_threshold: float = 20.0


# ═══════════════════════════════════════════════════════════════════
# TRANSACTION COSTS (Indian MIS)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CostConfig:
    brokerage_per_order: float = 20.0
    stt_rate: float = 0.000625
    exchange_rate: float = 0.0000345
    gst_rate: float = 0.18
    sebi_rate: float = 0.000001
    stamp_duty_rate: float = 0.00003


# ═══════════════════════════════════════════════════════════════════
# STOCK SELECTION (LAYER 4)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StockSelectionConfig:
    # Volume tiers (daily traded value in Crores)
    tier1_min_value_cr: float = 500.0   # Ultra High +2 pts
    tier2_min_value_cr: float = 100.0   # High +1 pt
    tier3_min_value_cr: float = 20.0    # Moderate (need 3x vol shock)
    # Below 20 Cr = never trade

    # Beta thresholds
    beta_best: float = 1.5     # +2 pts
    beta_good: float = 1.0     # +1 pt
    # Below 1.0 = skip

    # ATR thresholds (% of price)
    atr_high_pct: float = 2.0   # +1 pt
    atr_mod_pct: float = 1.0    # 0 pts
    # Below 1% = -1 pt, skip

    # Gap limits
    min_gap_pct: float = -1.0   # Skip if gap down > 1%
    max_gap_pct: float = 4.0    # Skip if gap up > 4%

    # Intraday gain limit
    max_intraday_gain: float = 15.0  # Skip if already up 15%


# ═══════════════════════════════════════════════════════════════════
# MASTER CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

@dataclass
class StrategyConfig:
    """Master configuration for the 6-Layer Professional System."""

    # Capital
    total_capital: float = 100000.0
    data_period: str = "60d"

    # Sub-configs
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    global_check: GlobalConfig = field(default_factory=GlobalConfig)
    fii_dii: FIIDIIConfig = field(default_factory=FIIDIIConfig)
    windows: TradingWindowsConfig = field(default_factory=TradingWindowsConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    targets: TargetConfig = field(default_factory=TargetConfig)
    position_sizing: PositionSizingConfig = field(default_factory=PositionSizingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    stock_selection: StockSelectionConfig = field(default_factory=StockSelectionConfig)

    # Default watchlist (Tier 1 + Tier 2 stocks)
    watchlist: List[str] = field(default_factory=lambda: [
        "SBIN", "RELIANCE", "HDFCBANK", "ICICIBANK", "TATAMOTORS",
        "BAJFINANCE", "AXISBANK", "INFY", "TCS", "TATASTEEL",
        "HINDALCO", "COALINDIA", "NTPC", "HCLTECH", "LT",
    ])

    # Sector-specific stock maps (for global context alignment)
    sector_stocks: dict = field(default_factory=lambda: {
        "IT": ["INFY", "TCS", "WIPRO", "HCLTECH", "TECHM"],
        "OIL": ["ONGC", "BPCL", "OIL", "IOC"],
        "FMCG": ["HINDUNILVR", "MARICO", "ITC"],
        "AUTO": ["TATAMOTORS", "MARUTI", "BAJAJ-AUTO", "M&M"],
        "BANK": ["SBIN", "HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK"],
        "METAL": ["TATASTEEL", "HINDALCO", "JSWSTEEL", "COALINDIA"],
        "PHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB"],
    })

    def get_risk_pct(self, score: float) -> float:
        """Get risk percentage based on trade score."""
        if score >= self.scoring.elite_min:
            return self.position_sizing.elite_risk_pct
        elif score >= self.scoring.strong_min:
            return self.position_sizing.strong_risk_pct
        elif score >= self.scoring.average_min:
            return self.position_sizing.average_risk_pct
        return 0.0  # Below threshold = no trade

    def get_trade_grade(self, score: float) -> str:
        """Get trade grade label from score."""
        if score >= self.scoring.elite_min:
            return "ELITE"
        elif score >= self.scoring.strong_min:
            return "STRONG"
        elif score >= self.scoring.average_min:
            return "AVERAGE"
        return "SKIP"

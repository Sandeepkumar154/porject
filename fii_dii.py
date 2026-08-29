"""
fii_dii.py — Layer 2: FII/DII Activity Tracking.

Reads FII/DII data from CSV and determines trading sentiment.
6 scenarios from both-buying to both-selling.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from config import StrategyConfig


@dataclass
class FIIDIIResult:
    fii_net_cr: float = 0.0
    dii_net_cr: float = 0.0
    fii_action: str = "NEUTRAL"
    dii_action: str = "NEUTRAL"
    scenario: int = 3
    scenario_name: str = "FII Neutral, DII Buying"
    signal: str = "NEUTRAL"
    score_bonus: float = 0.0
    position_adjustment: float = 1.0
    can_trade: bool = True
    description: str = "No FII/DII data — assuming neutral"


def _classify_action(net_cr: float) -> str:
    """Classify as BUYING / NEUTRAL / SELLING."""
    if net_cr > 500:
        return "BUYING"
    elif net_cr < -500:
        return "SELLING"
    return "NEUTRAL"


def _determine_scenario(fii_action: str, dii_action: str,
                        fii_net: float, config: StrategyConfig) -> FIIDIIResult:
    """Determine scenario 1-6."""
    result = FIIDIIResult(fii_net_cr=fii_net, fii_action=fii_action,
                          dii_action=dii_action)

    # Scenario 1: Both Buying
    if fii_action == "BUYING" and dii_action == "BUYING":
        result.scenario = 1
        result.scenario_name = "Both Buying"
        result.signal = "VERY_BULLISH"
        result.score_bonus = 3.0
        result.position_adjustment = 1.0

    # Scenario 2: FII Buying, DII Neutral
    elif fii_action == "BUYING" and dii_action == "NEUTRAL":
        result.scenario = 2
        result.scenario_name = "FII Buying"
        result.signal = "BULLISH"
        result.score_bonus = 2.0
        result.position_adjustment = 1.0

    # Scenario 3: FII Neutral, DII Buying
    elif fii_action == "NEUTRAL" and dii_action == "BUYING":
        result.scenario = 3
        result.scenario_name = "DII Buying"
        result.signal = "MILDLY_BULLISH"
        result.score_bonus = 1.0
        result.position_adjustment = 1.0

    # Scenario 4: FII Selling, DII Buying
    elif fii_action == "SELLING" and dii_action == "BUYING":
        result.scenario = 4
        result.scenario_name = "Mixed (FII Sell, DII Buy)"
        result.signal = "MIXED"
        result.score_bonus = 0.0
        result.position_adjustment = 0.75

    # Scenario 5: FII Selling, DII Neutral
    elif fii_action == "SELLING" and dii_action == "NEUTRAL":
        result.scenario = 5
        result.scenario_name = "FII Selling"
        result.signal = "BEARISH"
        result.score_bonus = -2.0
        result.position_adjustment = 0.50

    # Scenario 6: Both Selling
    elif fii_action == "SELLING" and dii_action == "SELLING":
        result.scenario = 6
        result.scenario_name = "Both Selling — Distribution"
        result.signal = "DISTRIBUTION"
        result.score_bonus = 0.0
        result.position_adjustment = 0.0
        result.can_trade = False

    # Other combinations (neutral-neutral, etc.)
    else:
        result.scenario = 3
        result.scenario_name = "Neutral"
        result.signal = "NEUTRAL"
        result.score_bonus = 0.0
        result.position_adjustment = 1.0

    # FII amount scale bonus
    fc = config.fii_dii
    if fii_net > fc.strong_buy_threshold:
        result.description = f"FII Rs.{fii_net:+,.0f}Cr (VERY STRONG) | {result.scenario_name}"
    elif fii_net > fc.normal_buy_threshold:
        result.description = f"FII Rs.{fii_net:+,.0f}Cr (bullish) | {result.scenario_name}"
    elif fii_net < fc.strong_sell_threshold:
        result.description = f"FII Rs.{fii_net:+,.0f}Cr (HEAVY SELL) | {result.scenario_name}"
        result.can_trade = False
    else:
        result.description = f"FII Rs.{fii_net:+,.0f}Cr | {result.scenario_name}"

    return result


def check_fii_dii(trading_date: date, config: StrategyConfig,
                  data_dir: str = None) -> FIIDIIResult:
    """
    Check FII/DII activity for the previous trading day.

    Reads from fii_dii_data.csv in data_dir (or project dir).
    """
    if data_dir is None:
        data_dir = str(Path(__file__).parent)

    csv_path = Path(data_dir) / "fii_dii_data.csv"

    if not csv_path.exists():
        return FIIDIIResult(description="No fii_dii_data.csv found — neutral")

    try:
        df = pd.read_csv(csv_path, parse_dates=["date"])
        df["date"] = df["date"].dt.date

        # Look for previous trading day's data
        prev_date = trading_date - timedelta(days=1)
        # Search up to 5 days back (weekends/holidays)
        for delta in range(1, 6):
            check_date = trading_date - timedelta(days=delta)
            row = df[df["date"] == check_date]
            if not row.empty:
                r = row.iloc[0]
                fii_net = r.get("fii_buy_cr", 0) - r.get("fii_sell_cr", 0)
                dii_net = r.get("dii_buy_cr", 0) - r.get("dii_sell_cr", 0)

                fii_action = _classify_action(fii_net)
                dii_action = _classify_action(dii_net)

                result = _determine_scenario(fii_action, dii_action,
                                             fii_net, config)
                result.dii_net_cr = dii_net
                return result

        return FIIDIIResult(description=f"No FII/DII data near {trading_date} — neutral")

    except Exception as e:
        return FIIDIIResult(description=f"Error reading FII/DII data: {e}")

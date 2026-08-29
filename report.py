"""
report.py — Comprehensive Performance Reporting for Master Trading Plan v2.

Calculates and displays:
  - Overall metrics (Win rate, Gross/Net P&L, Transaction costs, Profit factor, Sharpe ratio)
  - Risk & Drawdown metrics (Max drawdown, drawdown duration, max consecutive losing days)
  - Window breakdown (Window 1 Prime vs Window 2 Momentum vs Window 3 Continuation)
  - Grade/Score tier breakdown (ELITE vs STRONG vs AVERAGE)
  - Exit reason statistics
  - Per-stock performance table
  - Monthly P&L breakdown
  - CSV export
"""

import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from position_manager import TradeResult


def calculate_metrics(trades: List[TradeResult],
                      daily_summaries: Optional[List[Dict]] = None,
                      initial_capital: float = 100000.0) -> Dict:
    """Calculate all performance metrics."""
    if not trades:
        return {"total_trades": 0}

    total_trades = len(trades)
    winners = [t for t in trades if t.net_pnl > 0]
    losers = [t for t in trades if t.net_pnl <= 0]

    num_winners = len(winners)
    num_losers = len(losers)
    win_rate = (num_winners / total_trades) * 100 if total_trades > 0 else 0.0

    gross_pnl = sum(t.gross_pnl for t in trades)
    total_costs = sum(t.total_costs for t in trades)
    net_pnl = sum(t.net_pnl for t in trades)
    return_on_capital = (net_pnl / initial_capital) * 100 if initial_capital > 0 else 0.0

    total_win_amount = sum(t.net_pnl for t in winners)
    total_loss_amount = abs(sum(t.net_pnl for t in losers))

    avg_win = total_win_amount / num_winners if num_winners > 0 else 0.0
    avg_loss = (total_loss_amount / num_losers) if num_losers > 0 else 0.0
    profit_factor = (total_win_amount / total_loss_amount) if total_loss_amount > 0 else float("inf")
    risk_reward = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    # Running equity & Max Drawdown
    equity_curve = [initial_capital]
    for t in trades:
        equity_curve.append(equity_curve[-1] + t.net_pnl)

    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        dd_pct = (dd / peak) * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    # Daily returns & Sharpe ratio
    daily_pnls = defaultdict(float)
    for t in trades:
        if t.entry_time:
            d = t.entry_time.date() if hasattr(t.entry_time, "date") else t.entry_time
            daily_pnls[d] += t.net_pnl

    daily_values = list(daily_pnls.values())
    if len(daily_values) > 1:
        daily_mean = np.mean(daily_values)
        daily_std = np.std(daily_values, ddof=1)
        sharpe_ratio = (daily_mean / daily_std * np.sqrt(252)) if daily_std > 0 else 0.0
    else:
        sharpe_ratio = 0.0

    # Consecutive losing days
    sorted_days = sorted(daily_pnls.keys())
    max_consec_losing_days = 0
    current_consec_losing_days = 0
    for d in sorted_days:
        if daily_pnls[d] < 0:
            current_consec_losing_days += 1
            if current_consec_losing_days > max_consec_losing_days:
                max_consec_losing_days = current_consec_losing_days
        else:
            current_consec_losing_days = 0

    best_day_val = max(daily_values) if daily_values else 0.0
    worst_day_val = min(daily_values) if daily_values else 0.0
    winning_days = sum(1 for v in daily_values if v > 0)
    losing_days = sum(1 for v in daily_values if v <= 0)

    # Window breakdown
    window_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        w_name = t.window or "UNKNOWN"
        window_stats[w_name]["trades"] += 1
        if t.net_pnl > 0:
            window_stats[w_name]["wins"] += 1
        window_stats[w_name]["pnl"] += t.net_pnl

    # Grade breakdown
    grade_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        g_name = t.grade or "UNKNOWN"
        grade_stats[g_name]["trades"] += 1
        if t.net_pnl > 0:
            grade_stats[g_name]["wins"] += 1
        grade_stats[g_name]["pnl"] += t.net_pnl

    # Exit reasons
    exit_counts = defaultdict(int)
    for t in trades:
        exit_counts[t.exit_reason] += 1

    # Per stock
    stock_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "scores": []})
    for t in trades:
        stock_stats[t.symbol]["trades"] += 1
        if t.net_pnl > 0:
            stock_stats[t.symbol]["wins"] += 1
        stock_stats[t.symbol]["pnl"] += t.net_pnl
        stock_stats[t.symbol]["scores"].append(t.score)

    # Monthly breakdown
    monthly_pnls = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        if t.entry_time:
            m_key = t.entry_time.strftime("%Y-%m") if hasattr(t.entry_time, "strftime") else str(t.entry_time)[:7]
            monthly_pnls[m_key]["trades"] += 1
            if t.net_pnl > 0:
                monthly_pnls[m_key]["wins"] += 1
            monthly_pnls[m_key]["pnl"] += t.net_pnl

    return {
        "total_trades": total_trades,
        "winners": num_winners,
        "losers": num_losers,
        "win_rate": win_rate,
        "gross_pnl": gross_pnl,
        "total_costs": total_costs,
        "net_pnl": net_pnl,
        "return_on_capital": return_on_capital,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "risk_reward": risk_reward,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "max_consec_losing_days": max_consec_losing_days,
        "total_trading_days": len(daily_values),
        "winning_days": winning_days,
        "losing_days": losing_days,
        "best_day": best_day_val,
        "worst_day": worst_day_val,
        "window_stats": dict(window_stats),
        "grade_stats": dict(grade_stats),
        "exit_counts": dict(exit_counts),
        "stock_stats": dict(stock_stats),
        "monthly_stats": dict(monthly_pnls),
    }


def print_report(metrics: Dict) -> None:
    """Print beautifully formatted console report."""
    if not metrics or metrics.get("total_trades", 0) == 0:
        print("\n  [REPORT] No trades executed in this backtest.\n")
        return

    print("\n" + "=" * 70)
    print("  🏆 MASTER TRADING PLAN v2 — BACKTEST PERFORMANCE REPORT")
    print("=" * 70)

    # 1. Overall
    pnl_sign = "+" if metrics["net_pnl"] >= 0 else ""
    pnl_tag = "[PROFIT]" if metrics["net_pnl"] >= 0 else "[LOSS]"
    print("\n  -- OVERALL PERFORMANCE --")
    print("  " + "-" * 66)
    print(f"    Total Trades          : {metrics['total_trades']}")
    print(f"    Win / Loss Count      : {metrics['winners']} W / {metrics['losers']} L")
    print(f"    Win Rate              : {metrics['win_rate']:.2f}%")
    print(f"    Gross P&L             : Rs.{metrics['gross_pnl']:+,.2f}")
    print(f"    Transaction Costs     : Rs.{metrics['total_costs']:,.2f}")
    print(f"    Net P&L {pnl_tag:<14}: Rs.{pnl_sign}{metrics['net_pnl']:,.2f} ({metrics['return_on_capital']:+.2f}% on capital)")
    print(f"    Profit Factor         : {metrics['profit_factor']:.2f}")
    print(f"    Sharpe Ratio (ann.)   : {metrics['sharpe_ratio']:.2f}")

    # 2. Averages & R:R
    print("\n  -- TRADE AVERAGES & RISK:REWARD --")
    print("  " + "-" * 66)
    print(f"    Avg Winner            : Rs.+{metrics['avg_win']:,.2f}")
    print(f"    Avg Loser             : Rs.-{metrics['avg_loss']:,.2f}")
    print(f"    Realized R:R Ratio    : 1:{metrics['risk_reward']:.2f}")
    print(f"    Max Drawdown          : Rs.{metrics['max_drawdown']:,.2f} ({metrics['max_drawdown_pct']:.2f}%)")
    print(f"    Max Consec Losing Days: {metrics['max_consec_losing_days']} day(s)")

    # 3. Daily Stats
    print("\n  -- DAILY SUMMARY --")
    print("  " + "-" * 66)
    print(f"    Total Trading Days    : {metrics['total_trading_days']}")
    print(f"    Winning / Losing Days : {metrics['winning_days']} W / {metrics['losing_days']} L")
    print(f"    Best Day P&L          : Rs.{metrics['best_day']:+,.2f}")
    print(f"    Worst Day P&L         : Rs.{metrics['worst_day']:+,.2f}")

    # 4. Window Breakdown
    print("\n  -- WIN RATE BY TRADING WINDOW --")
    print("  " + "-" * 66)
    print(f"    {'Window':<18} {'Trades':<8} {'Wins':<8} {'Win Rate':<12} {'Net P&L':<14}")
    print("    " + "-" * 62)
    for w_name, data in metrics["window_stats"].items():
        wr = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
        print(f"    {w_name:<18} {data['trades']:<8} {data['wins']:<8} {wr:>6.1f}%      Rs.{data['pnl']:+,.2f}")

    # 5. Grade / Score Tier Breakdown
    print("\n  -- PERFORMANCE BY TRADE GRADE (SCORE TIER) --")
    print("  " + "-" * 66)
    print(f"    {'Grade Tier':<18} {'Trades':<8} {'Wins':<8} {'Win Rate':<12} {'Net P&L':<14}")
    print("    " + "-" * 62)
    for g_name, data in metrics["grade_stats"].items():
        wr = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
        print(f"    {g_name:<18} {data['trades']:<8} {data['wins']:<8} {wr:>6.1f}%      Rs.{data['pnl']:+,.2f}")

    # 6. Exit Reasons
    print("\n  -- EXIT REASON BREAKDOWN --")
    print("  " + "-" * 66)
    for reason, count in sorted(metrics["exit_counts"].items(), key=lambda x: x[1], reverse=True):
        pct = (count / metrics["total_trades"]) * 100
        print(f"    {reason:<22}: {count:>3} ({pct:>5.1f}%)")

    # 7. Per-Stock Breakdown
    print("\n  -- PER-STOCK BREAKDOWN --")
    print("  " + "-" * 66)
    print(f"    {'Symbol':<14} {'Trades':<8} {'Wins':<8} {'Win Rate':<10} {'Net P&L':<14} {'Avg Score':<10}")
    print("    " + "-" * 62)
    for sym, data in sorted(metrics["stock_stats"].items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
        avg_sc = np.mean(data["scores"]) if data["scores"] else 0.0
        print(f"    {sym:<14} {data['trades']:<8} {data['wins']:<8} {wr:>5.1f}%    Rs.{data['pnl']:>+10,.2f}   {avg_sc:>5.1f}/48")

    # 8. Monthly Breakdown
    if metrics["monthly_stats"]:
        print("\n  -- MONTHLY P&L BREAKDOWN --")
        print("  " + "-" * 66)
        print(f"    {'Month':<12} {'Trades':<8} {'Wins':<8} {'Win Rate':<10} {'Net P&L':<14}")
        print("    " + "-" * 56)
        for m_key, data in sorted(metrics["monthly_stats"].items()):
            wr = (data["wins"] / data["trades"] * 100) if data["trades"] > 0 else 0
            print(f"    {m_key:<12} {data['trades']:<8} {data['wins']:<8} {wr:>5.1f}%    Rs.{data['pnl']:>+10,.2f}")

    print("\n" + "=" * 70 + "\n")


def export_trades_csv(trades: List[TradeResult],
                      output_path: str) -> None:
    """Export list of trades to CSV."""
    if not trades:
        return

    records = []
    for t in trades:
        d = asdict(t)
        # Format timestamps
        if d.get("entry_time"):
            d["entry_time"] = str(d["entry_time"])
        if d.get("exit_time"):
            d["exit_time"] = str(d["exit_time"])
        records.append(d)

    df = pd.DataFrame(records)
    df.to_csv(output_path, index=False)
    print(f"  Trade log exported to: {output_path}")


def generate_report(trades: List[TradeResult],
                    daily_summaries: Optional[List[Dict]] = None,
                    output_dir: Optional[str] = None,
                    initial_capital: float = 100000.0) -> Dict:
    """Generate metrics, print report, and save CSV."""
    metrics = calculate_metrics(trades, daily_summaries, initial_capital)
    print_report(metrics)

    if output_dir and trades:
        os.makedirs(output_dir, exist_ok=True)
        csv_file = os.path.join(output_dir, "backtest_trades.csv")
        export_trades_csv(trades, csv_file)

    return metrics

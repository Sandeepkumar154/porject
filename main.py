"""
main.py — CLI entry point for Master Trading Plan v2.

6-Layer / 8-Shield / 48-Point Intraday System.

Usage:
    python main.py
    python main.py --symbols SBIN RELIANCE HCLTECH INFY
    python main.py --capital 200000
    python main.py --groww
    python main.py --period 60d
"""

import argparse
import os
import sys
import time as time_module
from pathlib import Path

# Load .env file
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"  [ENV] Loaded credentials from {env_path}")
except ImportError:
    pass

from config import StrategyConfig
from backtest_engine import run_backtest
from report import generate_report


def _get_groww_token() -> str:
    """Get Groww API access token from environment or TOTP login."""
    token = os.environ.get("GROWW_ACCESS_TOKEN", "").strip()
    if token and token != "paste_your_access_token_here":
        print("  [GROWW] Using access token from environment")
        return token

    api_key = os.environ.get("GROWW_API_KEY", "").strip()
    api_secret = os.environ.get("GROWW_API_SECRET", "").strip()

    if (api_key and api_key != "paste_your_api_key_here" and
            api_secret and api_secret != "paste_your_api_secret_here"):
        print("  [GROWW] Attempting TOTP login with API key + secret...")
        try:
            import pyotp
            from growwapi import GrowwAPI

            totp = pyotp.TOTP(api_secret).now()
            token = GrowwAPI.get_access_token(api_key=api_key, totp=totp)

            if token:
                print("  [GROWW] Login successful! Access token obtained.")
                return token
            else:
                print("  [GROWW] Login returned no token. Check credentials.")
        except Exception as e:
            print(f"  [GROWW] TOTP login failed: {e}")

    return ""


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Master Trading Plan v2 — 6-Layer / 8-Shield Backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                                      # Default Tier 1 stocks
  python main.py --symbols SBIN RELIANCE HCLTECH      # Custom watchlist
  python main.py --capital 200000                     # Rs.2,00,000 capital
  python main.py --groww                              # Use Groww API data
        """,
    )

    parser.add_argument(
        "--symbols", "-s",
        nargs="+",
        default=None,
        help="Stock symbols to backtest (default: Tier 1 + Tier 2 list in config)",
    )

    parser.add_argument(
        "--capital", "-c",
        type=float,
        default=None,
        help="Total trading capital in INR (default: 1,00,000)",
    )

    parser.add_argument(
        "--period", "-p",
        type=str,
        default=None,
        help="Data lookback period (default: 60d)",
    )

    parser.add_argument(
        "--groww",
        action="store_true",
        default=False,
        help="Use Groww API for data (reads credentials from .env)",
    )

    parser.add_argument(
        "--groww-token",
        type=str,
        default=None,
        help="Groww API access token directly",
    )

    parser.add_argument(
        "--output-dir", "-o",
        type=str,
        default=None,
        help="Output directory for CSV trade log",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # Build config
    config = StrategyConfig()

    if args.capital:
        config.total_capital = args.capital
    if args.period:
        config.data_period = args.period

    symbols = args.symbols if args.symbols else None

    # Groww token
    groww_token = None
    if args.groww_token:
        groww_token = args.groww_token
    elif args.groww:
        groww_token = _get_groww_token()
        if not groww_token:
            print("\n  [WARNING] Groww credentials not found/failed. Using yfinance.\n")

    start_time = time_module.time()

    try:
        trades, engine = run_backtest(
            config=config,
            symbols=symbols,
            groww_token=groww_token,
        )
    except KeyboardInterrupt:
        print("\n\n  [INTERRUPTED] Backtest stopped by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    elapsed = time_module.time() - start_time

    output_dir = args.output_dir or os.path.dirname(os.path.abspath(__file__))
    metrics = generate_report(
        trades,
        daily_summaries=engine.daily_summaries,
        output_dir=output_dir,
        initial_capital=config.total_capital,
    )

    print(f"  Backtest completed in {elapsed:.1f} seconds")

    if engine.skipped_days:
        print(f"\n  Skipped {len(engine.skipped_days)} day(s):")
        for skip in engine.skipped_days[:10]:
            print(f"     {skip['date']} | {skip['symbol']:<12} | {skip['reason']}")
        if len(engine.skipped_days) > 10:
            print(f"     ... and {len(engine.skipped_days) - 10} more")

    print("\n  Done!\n")
    return metrics


if __name__ == "__main__":
    main()

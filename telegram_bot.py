"""
telegram_bot.py — Instant Telegram Alerts for Master Trading Plan v2.

Sends real-time trade signals, exit alerts, and daily market summaries
directly to your Telegram app on your mobile phone.

Setup:
  1. Open Telegram on your phone and search for @BotFather
  2. Send /newbot, choose a name (e.g. MyNSETradingBot)
  3. Copy the HTTP API Token to .env as TELEGRAM_BOT_TOKEN
  4. Search for @userinfobot or start a chat with your bot, get your Chat ID
  5. Copy your Chat ID to .env as TELEGRAM_CHAT_ID
"""

import os
from pathlib import Path
from typing import Dict, List, Optional
import requests

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass


def _get_telegram_creds() -> tuple:
    """Get Telegram credentials from environment."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def send_telegram_message(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a message to the configured Telegram chat.

    Returns True if successful, False otherwise.
    """
    token, chat_id = _get_telegram_creds()
    if not token or not chat_id or token.startswith("your_"):
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=8)
        return resp.status_code == 200
    except Exception as e:
        print(f"  [TELEGRAM] Failed to send alert: {e}")
        return False


def send_trade_signal_alert(symbol: str, score: float, grade: str,
                            entry_price: float, sl_price: float,
                            t1_price: float, t2_price: float,
                            qty: int, window_name: str,
                            shields_dict: Optional[Dict] = None) -> bool:
    """Send formatted trade setup alert to mobile Telegram."""
    emoji = "🔥" if grade == "ELITE" else ("🎯" if grade == "STRONG" else "🟡")
    sl_dist = entry_price - sl_price
    t1_gain = t1_price - entry_price

    msg = (
        f"{emoji} <b>NEW INTRADAY TRADE SIGNAL: {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Grade:</b> {grade} ({score:.0f}/48 pts)\n"
        f"<b>Window:</b> {window_name}\n\n"
        f"📍 <b>Entry Price:</b> ₹{entry_price:,.2f}\n"
        f"🛑 <b>Stop Loss:</b> ₹{sl_price:,.2f} (Risk: ₹{sl_dist:,.2f})\n"
        f"🎯 <b>Target 1:</b> ₹{t1_price:,.2f} (+₹{t1_gain:,.2f}) — <i>Exit 60% & SL->Cost</i>\n"
        f"🚀 <b>Target 2:</b> ₹{t2_price:,.2f} — <i>Exit remaining 40%</i>\n"
        f"📦 <b>Position Size:</b> {qty} shares\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if shields_dict:
        msg += "<b>8 Shields Breakdown:</b>\n"
        for name, res in shields_dict.items():
            icon = "✅" if getattr(res, "passed", False) or (isinstance(res, dict) and res.get("passed")) else "❌"
            msg += f"{icon} {name}\n"

    return send_telegram_message(msg)


def send_exit_alert(symbol: str, exit_price: float, net_pnl: float,
                    reason: str) -> bool:
    """Send trade exit notification to mobile Telegram."""
    emoji = "💰" if net_pnl > 0 else "🛑"
    pnl_str = f"+₹{net_pnl:,.2f}" if net_pnl > 0 else f"-₹{abs(net_pnl):,.2f}"

    msg = (
        f"{emoji} <b>TRADE CLOSED: {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Exit Price:</b> ₹{exit_price:,.2f}\n"
        f"<b>Exit Reason:</b> {reason}\n"
        f"<b>Net P&L:</b> <b>{pnl_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    return send_telegram_message(msg)


def send_global_morning_alert(global_res) -> bool:
    """Send 8:00 AM Global Market Summary alert to mobile Telegram."""
    msg = (
        f"🌍 <b>8:00 AM GLOBAL MARKET BRIEFING</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Global Score:</b> {global_res.global_score}/5\n"
        f"<b>Trading Plan:</b> {global_res.description}\n\n"
        f"• <b>Gift Nifty Gap:</b> {global_res.gift_nifty_gap:+.0f} pts ({global_res.gift_nifty_signal})\n"
        f"• <b>US Markets:</b> Dow {global_res.us_dow_change:+.2f}%, NASDAQ {global_res.us_nasdaq_change:+.2f}%\n"
        f"• <b>Crude Oil:</b> {global_res.crude_change:+.2f}% ({global_res.crude_signal})\n"
        f"• <b>USD/INR:</b> {global_res.usdinr_change:+.2f}%\n"
        f"• <b>Asian Markets:</b> Nikkei {global_res.asia_nikkei_change:+.2f}%, Hang Seng {global_res.asia_hangseng_change:+.2f}%\n"
    )
    if global_res.sector_bias:
        msg += f"\n🎯 <b>Favored Sectors/Stocks:</b> {', '.join(global_res.sector_bias)}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━"
    return send_telegram_message(msg)

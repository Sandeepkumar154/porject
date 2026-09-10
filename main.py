import os
import socket
import json
import asyncio
import urllib.request
import urllib.parse
import ssl
from datetime import datetime
import pytz
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import yfinance as yf

IST = pytz.timezone('Asia/Kolkata')

from engine import (
    scan_watchlist, scan_stock, run_backtest, get_current_window,
    is_market_open, DEFAULT_WATCHLIST, WINDOWS,
    scan_swing_candidates, SWING_WATCHLIST_50,
    check_nifty_regime
)

from self_tune import log_trade, run_weekly_tune, load_tuned_params, load_trade_log, load_tune_history
from sentiment import get_market_sentiment, fetch_india_vix

app = FastAPI(title='Master Trading Plan v2 — Improved', version='2.1.0')

# Environment variables: rigorously validate to reject dummy/truncated tokens from Render env
_HARDCODED_TOKEN = '8649513530:AAHgwOOrmHz9WNrWw-b3OUQtBevM-zSDAXk'
_env_tok = (os.environ.get('TELEGRAM_BOT_TOKEN') or '').strip()
if len(_env_tok) >= 40 and ':' in _env_tok:
    TELEGRAM_BOT_TOKEN = _env_tok
else:
    TELEGRAM_BOT_TOKEN = _HARDCODED_TOKEN

_SANDEEP_CHAT_ID = '1221493262'
_env_chat = (os.environ.get('TELEGRAM_CHAT_ID') or '').strip()
if _env_chat and _env_chat.lstrip('-').isdigit() and not _env_chat.startswith('864951'):
    TELEGRAM_CHAT_ID = _env_chat
else:
    TELEGRAM_CHAT_ID = _SANDEEP_CHAT_ID

TOTAL_CAPITAL = float(os.environ.get('TOTAL_CAPITAL') or '5000')

POSITIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'active_positions.json')
ALERTED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'alerted_today.json')

def _load_alerted_entries() -> set:
    """Load alerted entries for today to prevent duplicates across restarts."""
    if not os.path.exists(ALERTED_FILE):
        return set()
    try:
        with open(ALERTED_FILE, 'r') as f:
            data = json.load(f)
        today_str = datetime.now(IST).strftime('%Y-%m-%d')
        if data.get('date') == today_str:
            return set(data.get('alerts', []))
        return set()
    except Exception:
        return set()

def _save_alerted_entries(alerted_set: set):
    """Save alerted entries for today."""
    try:
        today_str = datetime.now(IST).strftime('%Y-%m-%d')
        with open(ALERTED_FILE, 'w') as f:
            json.dump({'date': today_str, 'alerts': list(alerted_set)}, f)
    except Exception:
        pass

alerted_entries_today = _load_alerted_entries()

def _load_active_positions() -> dict:
    """Load active tracked intraday positions from file."""
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE, 'r') as f:
            positions = json.load(f)
        today_str = datetime.now(IST).strftime('%Y-%m-%d')
        valid = {}
        for sym, pos in positions.items():
            if pos.get('entry_date') == today_str:
                valid[sym] = pos
        return valid
    except Exception as e:
        print(f"Error loading active positions: {e}")
        return {}

def _save_active_positions(positions: dict):
    """Save active tracked positions to file."""
    try:
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2)
    except Exception as e:
        print(f"Error saving active positions: {e}")

active_positions = _load_active_positions()

# ============================================================
# QUIET TRADING & CAPITAL PROTECTION LIMITS (Anti-Spam Shield)
# User Capital: ₹5,000 | Max Risk: ₹150
# ============================================================
MAX_ACTIVE_POSITIONS = 1      # Only 1 trade at a time for ₹5,000 capital (no simultaneous clutter)
MAX_DAILY_TRADES = 1          # Strictly maximum 1 intraday call per day (prevents overtrading & fee drag)
MIN_COOLDOWN_MINUTES = 30     # 30-minute quiet period between signals
last_alert_time = None

class BacktestRequest(BaseModel):
    symbols: Optional[List[str]] = None
    capital: Optional[float] = 100000.0
    period: Optional[str] = '60d'

def _get_local_ip() -> str:
    """Get local IP for mobile access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def _send_telegram_message(text: str) -> bool:
    """Send message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': 'HTML'
        }).encode('utf-8')
        req = urllib.request.Request(
            url, 
            data=data, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
            return response.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def _send_telegram_alert(entries: list):
    """
    Send short, crisp intraday trading signal.
    QUIET & DISCIPLINED:
    - Blocked during Dead Zone (12:00-14:00 IST).
    - Blocked if user already has 1 open trade (Max 1 active trade for ₹5,000 capital).
    - Blocked if 2 trades already alerted today (Max 2 trades/day).
    - Minimum 30-min cooldown between alerts.
    - Picks ONLY the single #1 best stock (never batch blast multiple stocks).
    - Strictly score 15 or 16 only.
    """
    global last_alert_time
    
    now = datetime.now(IST)
    today_str = now.strftime('%Y-%m-%d')
    
    # 1. STRICT DEAD ZONE BLOCK: Never trade between 12:00 and 14:00 IST
    if 12 <= now.hour < 14:
        return
        
    # 2. Capital Protection: If already holding an active trade, DO NOT ENTER ANOTHER!
    if len(active_positions) >= MAX_ACTIVE_POSITIONS:
        return
        
    # 3. Anti-Overtrading: Max 2 trades per day
    if len(alerted_entries_today) >= MAX_DAILY_TRADES:
        return
        
    # 4. Cooldown: Minimum 30 minutes between alerts
    if last_alert_time is not None:
        elapsed = (now - last_alert_time).total_seconds() / 60.0
        if elapsed < MIN_COOLDOWN_MINUTES:
            return
            
    # 5. Check VIX — block if extreme fear
    try:
        vix_data = fetch_india_vix()
        vix_can_trade = vix_data.get('can_trade', True)
        vix_multiplier = vix_data.get('position_multiplier', 1.0)
    except Exception:
        vix_can_trade = True
        vix_multiplier = 1.0
    
    if not vix_can_trade:
        return

    # 6. Filter candidates strictly scoring 15 or 16 and not alerted today
    valid_candidates = []
    for entry in entries:
        symbol = entry.get('symbol', 'UNKNOWN')
        score = entry.get('score', 0)
        alert_key = f"{symbol}_{today_str}"
        if score >= 15 and alert_key not in alerted_entries_today:
            valid_candidates.append(entry)
            
    if not valid_candidates:
        return
        
    # 7. INSTITUTIONAL VOLUME PRIORITIZATION: Pick the single stock with highest Relative Volume (RVOL)
    valid_candidates.sort(key=lambda x: (x.get('rvol', 0.0), x.get('score', 0), x.get('t2_profit', 0)), reverse=True)
    best_entry = valid_candidates[0]
    
    symbol = best_entry.get('symbol', 'UNKNOWN')
    price = best_entry.get('price', 0)
    sl = best_entry.get('sl', 0)
    t1 = best_entry.get('t1', 0)
    t2 = best_entry.get('t2', 0)
    qty = best_entry.get('qty', 0)
    grade = best_entry.get('grade', 'NONE')
    score = best_entry.get('score', 0)
    rvol = best_entry.get('rvol', 1.0)
    vol_label = best_entry.get('vol_label', 'High Volume')
    
    if vix_multiplier < 1.0:
        qty = max(1, int(qty * vix_multiplier))
        
    t1_profit = round(qty * (t1 - price), 0)
    t2_profit = round(qty * (t2 - price), 0)
    net_t2_profit = round(best_entry.get('net_t2_profit', t2_profit - 40.0), 0)
    orb_high = best_entry.get('orb_high', price)
    risk_amt = round(best_entry.get('risk_amount', qty * (price - sl)), 0)
    
    nifty_info = check_nifty_regime()
    nifty_desc = nifty_info.get('description', 'Bullish Above VWAP')
        
    alert_key = f"{symbol}_{today_str}"
    alerted_entries_today.add(alert_key)
    
    # INSTITUTIONAL 360-DEGREE FORMAT WITH VOLUME SURGE
    text = f"⚡ <b>BUY {symbol}</b> (MIS Intraday)\n\n"
    text += f"🔊 <b>Volume Surge:</b> {rvol:.1f}x Avg ({vol_label})\n"
    text += f"🌍 <b>Nifty 50:</b> {nifty_desc}\n"
    text += f"💥 <b>Setup:</b> 15m ORB Breakout (> ₹{orb_high:.2f})\n\n"
    text += f"💰 Buy Price: <b>₹{price:.2f}</b>\n"
    text += f"🛑 Stop-Loss: <b>₹{sl:.2f}</b> (Risk: ₹{risk_amt:.0f})\n"
    text += f"🎯 Target 1: <b>₹{t1:.2f}</b> (+₹{t1_profit:.0f})\n"
    text += f"🎯 Target 2: <b>₹{t2:.2f}</b> (+₹{t2_profit:.0f})\n"
    text += f"📦 Quantity: <b>{qty} shares</b> (5x MIS)\n\n"
    text += f"💵 <b>Net Target Profit: +₹{net_t2_profit:.0f}</b> (After ₹40 brokerage)\n"
    text += f"🛡️ Grade: <b>{grade}</b> ({score:.0f}/16)"
    
    _send_telegram_message(text)
    last_alert_time = now
    
    # Track position in active monitoring for live target & stop-loss triggers
    active_positions[symbol] = {
        'symbol': symbol,
        'entry_price': round(float(price), 2),
        'sl': round(float(sl), 2),
        'initial_sl': round(float(sl), 2),
        't1': round(float(t1), 2),
        't2': round(float(t2), 2),
        'qty': int(qty),
        'remaining_qty': int(qty),
        'booked_profit': 0.0,
        'entry_time': now.strftime('%H:%M:%S'),
        'entry_date': today_str,
        't1_hit': False,
        'grade': grade,
        'score': float(score)
    }
    _save_active_positions(active_positions)
    _save_alerted_entries(alerted_entries_today)
    
    # Execute live paper trade with Rs. 5,000 virtual capital
    try:
        import paper_trading
        paper_trading.record_paper_entry(
            symbol=symbol, price=float(price), qty=int(qty),
            sl=float(sl), t1=float(t1), t2=float(t2),
            setup="15m ORB Breakout"
        )
    except Exception as e:
        print(f"Error logging paper trade: {e}")
    
    # Log the trade for self-tuner (silent)
    try:
        log_trade({

            'symbol': symbol,
            'entry_time': now.strftime('%Y-%m-%d %H:%M:%S'),
            'entry_price': float(price),
            'sl': float(sl), 't1': float(t1), 't2': float(t2),
            'qty': int(qty), 'score': float(score), 'grade': grade,
            'window': get_current_window().get('name', 'UNKNOWN'),
            'sentiment_score': 0,
            'news_label': 'N/A', 'vix_value': 0,
            'params_version': 1
        })
    except Exception:
        pass

def check_active_positions(price_map: dict):
    """
    Real-time tracking of active intraday positions:
    1. Target 2 Hit (Price >= T2): Book remaining profit, close position.
    2. Target 1 Hit (Price >= T1): Book 50% profit, trail SL to entry price (Risk-Free).
    3. Stop-Loss Hit (Price <= SL): Exit trade immediately.
    4. 3:20 PM Square-Off: Auto close MIS trade before market closes.
    """
    global active_positions
    if not active_positions:
        return
        
    now = datetime.now(IST)
    is_square_off_time = (now.hour == 15 and now.minute >= 20) or now.hour > 15
    symbols_to_close = []
    
    for symbol, pos in list(active_positions.items()):
        current_price = price_map.get(symbol)
        
        # If stock price not in price_map, fetch fresh quote directly
        if not current_price or current_price <= 0:
            try:
                from engine import fetch_stock_data_direct
                df = fetch_stock_data_direct(symbol, period="1d", interval="5m")
                if df is not None and not df.empty:
                    current_price = float(df['Close'].iloc[-1])
            except Exception:
                pass
                
        if not current_price or current_price <= 0:
            continue
            
        entry_p = pos['entry_price']
        rem_qty = pos.get('remaining_qty', pos['qty'])
        t1_target = pos['t1']
        t2_target = pos['t2']
        sl_level = pos['sl']
        t1_already_hit = pos.get('t1_hit', False)
        
        # 1. 3:20 PM Square-off before market close
        if is_square_off_time:
            exit_pnl = rem_qty * (current_price - entry_p)
            total_pnl = pos.get('booked_profit', 0.0) + exit_pnl
            pnl_sign = "+" if total_pnl >= 0 else ""
            
            msg = (
                f"⏰ <b>3:20 PM INTRADAY CLOSE — {symbol}</b>\n\n"
                f"💰 Exit Price: <b>₹{current_price:.2f}</b>\n"
                f"💵 Net Trade P&L: <b>{pnl_sign}₹{total_pnl:.2f}</b>\n"
                f"📦 Closed: <b>{rem_qty} shs</b>\n\n"
                f"🏁 Auto-closing MIS before market settlement."
            )
            
            try:
                import paper_trading
                pres = paper_trading.record_paper_exit(symbol, current_price, "3:20 PM Square-off")
                if pres.get("success"):
                    msg += f"\n💼 <b>Virtual Capital:</b> ₹{pres['new_balance']:,.2f} ({pres['net_pnl']:+0.2f} after Groww fees)"
            except Exception:
                pass
                
            _send_telegram_message(msg)

            
            try:
                log_trade({
                    'symbol': symbol,
                    'entry_time': f"{pos.get('entry_date')} {pos.get('entry_time')}",
                    'entry_price': entry_p,
                    'exit_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                    'exit_price': current_price,
                    'qty': pos['qty'],
                    'exit_reason': 'INTRADAY_320_CLOSE',
                    'net_pnl': round(total_pnl, 2),
                    'score': pos.get('score', 0),
                    'grade': pos.get('grade', ''),
                    'window': 'CONTINUATION',
                    'is_winner': total_pnl > 0,
                    'params_version': 1
                })
            except Exception:
                pass
                
            symbols_to_close.append(symbol)
            continue
            
        # 2. Target 2 Hit (Price >= T2)
        if current_price >= t2_target:
            exit_pnl = rem_qty * (current_price - entry_p)
            total_pnl = pos.get('booked_profit', 0.0) + exit_pnl
            
            msg = (
                f"🏆 <b>TARGET 2 HIT — {symbol}</b>\n\n"
                f"💰 Exit Price: <b>₹{current_price:.2f}</b> (Target: ₹{t2_target:.2f})\n"
                f"💵 Total Profit: <b>+₹{total_pnl:.2f}</b>\n"
                f"📦 Closed: <b>{rem_qty} shs</b>\n\n"
                f"✅ All targets reached! Trade CLOSED."
            )
            
            try:
                import paper_trading
                pres = paper_trading.record_paper_exit(symbol, current_price, "TARGET_2_HIT")
                if pres.get("success"):
                    msg += f"\n💼 <b>Virtual Capital:</b> ₹{pres['new_balance']:,.2f} ({pres['net_pnl']:+0.2f} after Groww fees)"
            except Exception:
                pass
                
            _send_telegram_message(msg)
            
            try:
                log_trade({
                    'symbol': symbol,
                    'entry_time': f"{pos.get('entry_date')} {pos.get('entry_time')}",
                    'entry_price': entry_p,
                    'exit_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                    'exit_price': current_price,
                    'qty': pos['qty'],
                    'exit_reason': 'TARGET_2',
                    'net_pnl': round(total_pnl, 2),
                    'score': pos.get('score', 0),
                    'grade': pos.get('grade', ''),
                    'window': get_current_window().get('name', 'UNKNOWN'),
                    'is_winner': True,
                    'params_version': 1
                })
            except Exception:
                pass
                
            symbols_to_close.append(symbol)
            continue
            
        # 3. Target 1 Hit (Price >= T1)
        if current_price >= t1_target and not t1_already_hit:
            total_qty = pos['qty']
            if total_qty <= 1:
                # Single share trade: close at T1
                total_pnl = 1 * (current_price - entry_p)
                msg = (
                    f"🎯 <b>TARGET 1 HIT — {symbol}</b>\n\n"
                    f"💰 Exit Price: <b>₹{current_price:.2f}</b> (Target: ₹{t1_target:.2f})\n"
                    f"💵 Profit: <b>+₹{total_pnl:.2f}</b>\n"
                    f"📦 Closed: <b>1 shs</b>\n\n"
                    f"✅ Target reached! Single share closed."
                )
                
                try:
                    import paper_trading
                    pres = paper_trading.record_paper_exit(symbol, current_price, "TARGET_1_HIT")
                    if pres.get("success"):
                        msg += f"\n💼 <b>Virtual Capital:</b> ₹{pres['new_balance']:,.2f} ({pres['net_pnl']:+0.2f} after Groww fees)"
                except Exception:
                    pass
                    
                _send_telegram_message(msg)
                
                try:
                    log_trade({
                        'symbol': symbol,
                        'entry_time': f"{pos.get('entry_date')} {pos.get('entry_time')}",
                        'entry_price': entry_p,
                        'exit_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                        'exit_price': current_price,
                        'qty': 1,
                        'exit_reason': 'TARGET_1',
                        'net_pnl': round(total_pnl, 2),
                        'score': pos.get('score', 0),
                        'grade': pos.get('grade', ''),
                        'window': get_current_window().get('name', 'UNKNOWN'),
                        'is_winner': True,
                        'params_version': 1
                    })
                except Exception:
                    pass
                    
                symbols_to_close.append(symbol)
                continue
            else:
                # Multi-share: book 50%, trail SL to entry price
                booked_qty = max(1, total_qty // 2)
                remaining_qty = total_qty - booked_qty
                booked_pnl = booked_qty * (current_price - entry_p)
                
                msg = (
                    f"🎯 <b>TARGET 1 HIT — {symbol}</b>\n\n"
                    f"💰 Current: <b>₹{current_price:.2f}</b> (Target: ₹{t1_target:.2f})\n"
                    f"💵 Booked: <b>+₹{booked_pnl:.2f}</b> ({booked_qty} shs)\n"
                    f"🛡️ <b>SL Moved to Cost: ₹{entry_p:.2f}</b> (Risk-Free)\n"
                    f"🎯 Holding {remaining_qty} shs for T2: ₹{t2_target:.2f}"
                )
                
                try:
                    import paper_trading
                    paper_trading.record_paper_exit(symbol, current_price, "TARGET_1_PARTIAL", exit_qty=booked_qty)
                except Exception:
                    pass
                    
                _send_telegram_message(msg)
                
                pos['t1_hit'] = True
                pos['booked_profit'] = round(booked_pnl, 2)
                pos['remaining_qty'] = remaining_qty
                pos['sl'] = entry_p  # Trail SL to cost / break-even
                _save_active_positions(active_positions)
                continue
                
        # 4. Stop-Loss Hit (Price <= SL)
        if current_price <= sl_level:
            exit_pnl = rem_qty * (current_price - entry_p)
            total_pnl = pos.get('booked_profit', 0.0) + exit_pnl
            pnl_sign = "+" if total_pnl >= 0 else ""
            
            if t1_already_hit:
                msg = (
                    f"🛡️ <b>TRAILED SL HIT — {symbol}</b>\n\n"
                    f"💰 Exit Price: <b>₹{current_price:.2f}</b> (Cost: ₹{sl_level:.2f})\n"
                    f"💵 Net Trade P&L: <b>{pnl_sign}₹{total_pnl:.2f}</b>\n"
                    f"📦 Closed: <b>{rem_qty} shs</b>\n\n"
                    f"✅ Remaining half closed at cost. Profit locked!"
                )
            else:
                msg = (
                    f"🛑 <b>STOP-LOSS HIT — {symbol}</b>\n\n"
                    f"💰 Exit Price: <b>₹{current_price:.2f}</b> (SL: ₹{sl_level:.2f})\n"
                    f"📉 Loss: <b>₹{total_pnl:.2f}</b>\n"
                    f"📦 Closed: <b>{rem_qty} shs</b>\n\n"
                    f"⚠️ Strict risk cut. Discipline protects capital."
                )
                
            try:
                import paper_trading
                pres = paper_trading.record_paper_exit(symbol, current_price, "TRAILED_SL" if t1_already_hit else "STOP_LOSS")
                if pres.get("success"):
                    msg += f"\n💼 <b>Virtual Capital:</b> ₹{pres['new_balance']:,.2f} ({pres['net_pnl']:+0.2f} after Groww fees)"
            except Exception:
                pass
                
            _send_telegram_message(msg)
            
            try:
                log_trade({
                    'symbol': symbol,
                    'entry_time': f"{pos.get('entry_date')} {pos.get('entry_time')}",
                    'entry_price': entry_p,
                    'exit_time': now.strftime('%Y-%m-%d %H:%M:%S'),
                    'exit_price': current_price,
                    'qty': pos['qty'],
                    'exit_reason': 'TRAILED_SL' if t1_already_hit else 'STOP_LOSS',
                    'net_pnl': round(total_pnl, 2),
                    'score': pos.get('score', 0),
                    'grade': pos.get('grade', ''),
                    'window': get_current_window().get('name', 'UNKNOWN'),
                    'is_winner': total_pnl > 0,
                    'params_version': 1
                })
            except Exception:
                pass

                
            symbols_to_close.append(symbol)
            continue
            
    if symbols_to_close:
        for sym in symbols_to_close:
            active_positions.pop(sym, None)
        _save_active_positions(active_positions)

# Track sent status announcements to avoid duplicate broadcasts
MORNING_GREETED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'morning_greeted.json')

def _load_morning_greeted() -> str:
    if not os.path.exists(MORNING_GREETED_FILE):
        return ""
    try:
        with open(MORNING_GREETED_FILE, 'r') as f:
            return json.load(f).get('date', '')
    except Exception:
        return ""

def _save_morning_greeted(date_str: str):
    try:
        with open(MORNING_GREETED_FILE, 'w') as f:
            json.dump({'date': date_str}, f)
    except Exception:
        pass

sent_session_updates = set()

async def background_market_scanner():
    """Continuous automated background scanner during market hours (QUIET & DISCIPLINED)."""
    print("[Scanner] Background market scanner started...")
    
    while True:
        try:
            now = datetime.now(IST)
            today_str = now.strftime('%Y-%m-%d')
            
            if is_market_open():
                # 0. Daily Morning Heartbeat Message (Sends once every morning when market opens)
                if _load_morning_greeted() != today_str:
                    _save_morning_greeted(today_str)
                    sent_session_updates.add(f"{today_str}_MORNING")
                    greeting_msg = (
                        "🌅 <b>Good Morning Sandeep!</b>\n\n"
                        "🤖 <b>I am LIVE & monitoring the market.</b>\n"
                        "📈 Strategy: 15m ORB + Volume Shocker\n"
                        "🛡️ Capital: ₹5,000 | 1 Trade Max (Strict Discipline)\n\n"
                        "✨ <i>Hoping for a great and profitable trade today! Have a wonderful day.</i>"
                    )
                    _send_telegram_message(greeting_msg)

                # 1. STRICT DEAD ZONE (12:00 - 14:00 IST): Trading strictly paused!
                if 12 <= now.hour < 14:
                    # In Dead Zone: ONLY check active positions for T1/T2/SL exits.
                    # NEVER scan for or send new BUY signals!
                    if active_positions:
                        check_active_positions({})
                    await asyncio.sleep(60)
                    continue

                # 2. Daily Swing Trading Scan (15:15 IST) — 1 single summary message
                swing_key = f"{today_str}_SWING"
                if swing_key not in sent_session_updates and now.hour == 15 and now.minute >= 15:
                    sent_session_updates.add(swing_key)
                    swing_candidates = scan_swing_candidates(TOTAL_CAPITAL)
                    if swing_candidates:
                        msg = "📊 <b>SWING PICKS (3:15 PM)</b>\n\n"
                        for c in swing_candidates[:3]:
                            msg += f"🔥 <b>{c['symbol']}</b> ({c['type']})\n"
                            msg += f"   Buy: ₹{c['price']:.2f} | SL: ₹{c['sl']:.2f}\n"
                            msg += f"   T1: ₹{c['t1']:.2f} | T2: ₹{c['t2']:.2f}\n"
                            msg += f"   Qty: <b>{c['qty']}</b>\n\n"
                        _send_telegram_message(msg)

                # 2b. Friday Weekly Paper Trading Report (15:35 IST) — Full Weekly P&L Audit
                friday_paper_key = f"{today_str}_FRIDAY_PAPER_REPORT"
                if friday_paper_key not in sent_session_updates and now.weekday() == 4 and now.hour == 15 and now.minute >= 35:
                    sent_session_updates.add(friday_paper_key)
                    try:
                        import paper_trading
                        rep = paper_trading.generate_weekly_paper_report()
                        _send_telegram_message(rep)
                    except Exception as e:
                        print(f"Error sending Friday paper report: {e}")

                # 3. Live scan across watchlist
                result = scan_watchlist(DEFAULT_WATCHLIST, TOTAL_CAPITAL)
                stocks = result.get('stocks', [])
                price_map = {s['symbol']: s['price'] for s in stocks if 'symbol' in s and 'price' in s}
                
                # Check active positions for T1, T2, SL, or 3:20 PM close
                check_active_positions(price_map)
                
                # 4. Only alert new entry if within valid trading window (STRICTLY before 14:45 IST)
                # Never take an intraday MIS entry after 2:45 PM since broker square-off is at 3:15-3:20 PM!
                window = get_current_window()
                can_enter = (
                    window.get('active', False) and
                    window.get('name') != 'DEAD_ZONE' and
                    (now.hour < 14 or (now.hour == 14 and now.minute < 45)) and
                    len(active_positions) < MAX_ACTIVE_POSITIONS and
                    len(alerted_entries_today) < MAX_DAILY_TRADES
                )
                if can_enter:
                    entries = [s for s in stocks if s.get('is_entry')]
                    if entries:
                        _send_telegram_alert(entries)
                    
                await asyncio.sleep(60) # Scan every 1 minute during market hours
            else:
                # Outside market hours: close any lingering positions cleanly
                if active_positions:
                    check_active_positions({})
                await asyncio.sleep(300)
        except Exception as e:
            print(f"Background scanner error: {e}")
            await asyncio.sleep(60)

async def weekly_self_tune():
    """Background task: Run weekly self-tune every Sunday at 8 PM IST."""
    print("Weekly self-tune scheduler started...")
    
    while True:
        try:
            now = datetime.now(IST)
            # Check if it's Sunday (weekday 6) and around 8 PM
            if now.weekday() == 6 and now.hour == 20 and now.minute < 5:
                print("[Self-Tune] Sunday 8 PM — Starting weekly self-tune cycle...")
                
                _send_telegram_message(
                    "🔬 <b>Weekly Self-Tune Starting...</b>\n\n"
                    "The bot is analyzing last week's trades and testing parameter optimizations.\n"
                    "This takes 5-10 minutes. You'll receive the full report when done."
                )
                
                try:
                    # Run the full tune cycle
                    report = run_weekly_tune(capital=TOTAL_CAPITAL)
                    
                    # Send the report to Telegram
                    if report:
                        _send_telegram_message(report)
                    else:
                        _send_telegram_message(
                            "📊 <b>Weekly Self-Tune Complete</b>\n\n"
                            "No trades logged this week. Parameters unchanged.\n"
                            "✅ Bot is ready for Monday with current settings."
                        )
                    
                    print("[Self-Tune] Weekly tune cycle complete.")
                except Exception as e:
                    print(f"[Self-Tune] Error during tune: {e}")
                    _send_telegram_message(
                        f"⚠️ <b>Self-Tune Error</b>\n\n"
                        f"Auto-tune encountered an issue: {str(e)[:100]}\n"
                        f"Current parameters remain unchanged. Bot is safe."
                    )
                
                # Send weekly paper trading audit alongside self-tune
                try:
                    import paper_trading
                    paper_rep = paper_trading.generate_weekly_paper_report()
                    _send_telegram_message(paper_rep)
                except Exception as pe:
                    print(f"[Paper-Trading] Error generating Sunday report: {pe}")
                
                # Sleep 1 hour to prevent re-triggering
                await asyncio.sleep(3600)
            else:
                # Check every 5 minutes
                await asyncio.sleep(300)
        except Exception as e:
            print(f"Self-tune scheduler error: {e}")
            await asyncio.sleep(300)

@app.on_event("startup")
async def startup_event():
    # Start auto-scanner in background
    asyncio.create_task(background_market_scanner())
    # Start weekly self-tune scheduler
    asyncio.create_task(weekly_self_tune())

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Master Trading Plan v2 — Mobile Portal</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: {
            sans: ['Inter', 'sans-serif'],
            mono: ['JetBrains Mono', 'monospace'],
          }
        }
      }
    }
  </script>
  <style>
    body { font-family: 'Inter', sans-serif; -webkit-tap-highlight-color: transparent; }
    .custom-scrollbar::-webkit-scrollbar { width: 4px; height: 4px; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen pb-24" x-data="tradingApp()">
  <header class="sticky top-0 z-40 bg-slate-900/90 backdrop-blur-md border-b border-slate-800 px-4 py-3">
    <div class="flex items-center justify-between">
      <div class="flex items-center space-x-2">
        <div class="w-8 h-8 rounded-lg bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-emerald-400 font-bold text-lg">🏆</div>
        <div>
          <h1 class="text-sm font-bold tracking-tight text-slate-100">MASTER TRADING v2</h1>
          <p class="text-[10px] text-slate-400 font-mono">6-Layer Intraday System</p>
        </div>
      </div>
      <div class="flex items-center space-x-2">
        <span class="px-2.5 py-1 rounded-full text-[11px] font-semibold font-mono tracking-wide"
              :class="{
                'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30': status.window_active,
                'bg-amber-500/20 text-amber-400 border border-amber-500/30': status.window_name === 'DEAD_ZONE',
                'bg-slate-800 text-slate-400 border border-slate-700': !status.window_active && status.window_name !== 'DEAD_ZONE'
              }">
          <span class="inline-block w-1.5 h-1.5 rounded-full mr-1"
                :class="status.window_active ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'"></span>
          <span x-text="status.window_name || 'CLOSED'"></span>
        </span>
      </div>
    </div>
    <div class="grid grid-cols-3 gap-2 mt-3 pt-2.5 border-t border-slate-800/60 text-xs">
      <div class="bg-slate-950/60 p-2 rounded-lg border border-slate-800/80">
        <div class="text-[10px] text-slate-400">Global Score</div>
        <div class="font-bold font-mono mt-0.5" :class="globalData.global_score >= 3 ? 'text-emerald-400' : 'text-amber-400'">
          🌍 <span x-text="globalData.global_score !== undefined ? globalData.global_score + '/5' : '...'"></span>
        </div>
      </div>
      <div class="bg-slate-950/60 p-2 rounded-lg border border-slate-800/80">
        <div class="text-[10px] text-slate-400">FII Activity</div>
        <div class="font-bold font-mono text-slate-200 mt-0.5 truncate text-[11px]" x-text="fiiData.signal || 'Loading...'"></div>
      </div>
      <div class="bg-slate-950/60 p-2 rounded-lg border border-slate-800/80">
        <div class="text-[10px] text-slate-400">Nifty 50</div>
        <div class="font-bold font-mono mt-0.5 text-[11px]" :class="(scanData.nifty_change || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'">
          <span x-text="(scanData.nifty_change >= 0 ? '+' : '') + (scanData.nifty_change || 0).toFixed(2) + '%'"></span>
        </div>
      </div>
    </div>
  </header>
  <main class="p-4 space-y-4 max-w-lg mx-auto">
    <div x-show="activeTab === 'scanner'" x-transition class="space-y-4">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-base font-bold text-slate-100">8-Shield Live Scanner</h2>
          <p class="text-xs text-slate-400">Auto-refresh every 30s</p>
        </div>
        <button @click="fetchScan()" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 active:scale-95 transition text-white text-xs font-semibold rounded-lg flex items-center space-x-1.5 shadow-sm shadow-emerald-900/30">
          <span :class="{'animate-spin': isScanning}">🔄</span>
          <span x-text="isScanning ? 'Scanning...' : 'Scan Now'"></span>
        </button>
      </div>
      <template x-if="entryStocks.length > 0">
        <div class="p-3.5 bg-emerald-500/10 border border-emerald-500/40 rounded-xl space-y-1">
          <div class="flex items-center text-emerald-400 font-bold text-xs">
            <span class="text-base mr-1.5">🔥</span> ACTIONABLE ENTRY DETECTED!
          </div>
          <p class="text-xs text-slate-300">
            <span class="font-semibold text-emerald-300" x-text="entryStocks.map(s => s.symbol).join(', ')"></span>
            passed all 8 Shields with high score!
          </p>
        </div>
      </template>
      <template x-if="positions && positions.length > 0">
        <div class="p-3.5 bg-blue-950/40 border border-blue-500/40 rounded-xl space-y-2">
          <div class="flex items-center justify-between">
            <div class="flex items-center text-blue-400 font-bold text-xs">
              <span class="text-base mr-1.5">⚡</span> ACTIVE POSITIONS (<span x-text="positions.length"></span>)
            </div>
            <span class="text-[10px] text-blue-300 font-mono">Live Target / SL Tracking</span>
          </div>
          <div class="space-y-2">
            <template x-for="p in positions" :key="p.symbol">
              <div class="p-2.5 bg-slate-950/80 rounded-lg border border-blue-900/40 text-xs flex items-center justify-between">
                <div>
                  <div class="font-bold text-slate-100 flex items-center space-x-1.5">
                    <span x-text="p.symbol"></span>
                    <span class="text-[9px] px-1.5 py-0.5 rounded font-mono font-semibold" :class="p.t1_hit ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' : 'bg-blue-500/20 text-blue-400 border border-blue-500/40'" x-text="p.t1_hit ? 'T1 BOOKED' : 'OPEN'"></span>
                  </div>
                  <div class="text-[10px] text-slate-400 font-mono mt-0.5">
                    Entry: ₹<span x-text="p.entry_price"></span> | Qty: <span x-text="p.remaining_qty"></span>/<span x-text="p.qty"></span>
                  </div>
                </div>
                <div class="text-right text-[11px] font-mono">
                  <div class="text-emerald-400 font-bold">T1: ₹<span x-text="p.t1"></span> | T2: ₹<span x-text="p.t2"></span></div>
                  <div class="text-rose-400 font-semibold">SL: ₹<span x-text="p.sl"></span></div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </template>
      <div class="space-y-3">
        <template x-for="stock in scanData.stocks || []" :key="stock.symbol">
          <div class="bg-slate-900 border rounded-xl overflow-hidden transition"
               :class="{
                 'border-emerald-500/50 shadow-md shadow-emerald-950/40': stock.grade === 'ELITE' || stock.grade === 'STRONG',
                 'border-slate-800': stock.grade !== 'ELITE' && stock.grade !== 'STRONG'
               }">
            <div class="p-3.5 border-b border-slate-800/80 flex items-center justify-between">
              <div class="flex items-center space-x-2.5">
                <span class="font-bold text-sm tracking-wide text-slate-100" x-text="stock.symbol"></span>
                <span class="px-2 py-0.5 rounded text-[10px] font-extrabold uppercase font-mono tracking-wider"
                      :class="{
                        'bg-emerald-500 text-slate-950': stock.grade === 'ELITE',
                        'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40': stock.grade === 'STRONG',
                        'bg-amber-500/20 text-amber-400 border border-amber-500/40': stock.grade === 'AVERAGE',
                        'bg-slate-800 text-slate-400': stock.grade === 'SKIP'
                      }"
                      x-text="stock.grade"></span>
              </div>
              <div class="text-right">
                <div class="text-sm font-bold font-mono text-slate-100">₹<span x-text="stock.price.toFixed(2)"></span></div>
                <div class="text-[10px] font-mono" :class="stock.price >= stock.vwap ? 'text-emerald-400' : 'text-rose-400'">
                  VWAP ₹<span x-text="stock.vwap.toFixed(2)"></span>
                </div>
              </div>
            </div>
            <template x-if="stock.is_entry">
              <div class="p-3 bg-emerald-950/30 border-b border-emerald-900/40 grid grid-cols-3 gap-2 text-center text-xs">
                <div class="bg-slate-950/60 p-2 rounded border border-emerald-900/30">
                  <div class="text-[10px] text-slate-400">Stop Loss</div>
                  <div class="font-bold font-mono text-rose-400">₹<span x-text="stock.sl"></span></div>
                </div>
                <div class="bg-slate-950/60 p-2 rounded border border-emerald-900/30">
                  <div class="text-[10px] text-slate-400">Target 1 (60%)</div>
                  <div class="font-bold font-mono text-emerald-400">₹<span x-text="stock.t1"></span></div>
                </div>
                <div class="bg-slate-950/60 p-2 rounded border border-emerald-900/30">
                  <div class="text-[10px] text-slate-400">Quantity</div>
                  <div class="font-bold font-mono text-amber-300"><span x-text="stock.qty"></span> shs</div>
                </div>
              </div>
            </template>
            <div class="p-3.5 space-y-2.5 text-xs">
              <div class="flex items-center justify-between text-slate-300">
                <span>Score: <b class="text-slate-100" x-text="stock.score"></b>/16</span>
                <span>RSI: <b :class="stock.rsi >= 45 && stock.rsi <= 68 ? 'text-emerald-400' : 'text-slate-400'" x-text="stock.rsi.toFixed(1)"></b></span>
                <span>ADX: <b :class="stock.adx >= 20 ? 'text-emerald-400' : 'text-slate-400'" x-text="stock.adx.toFixed(1)"></b></span>
              </div>
              <details class="group">
                <summary class="cursor-pointer text-[11px] text-slate-400 hover:text-slate-200 flex items-center justify-between py-1 select-none">
                  <span>View 8 Shields Breakdown</span>
                  <span class="text-xs transition-transform group-open:rotate-180">▼</span>
                </summary>
                <div class="pt-2 space-y-1.5 text-[11px] border-t border-slate-800/80 mt-1">
                  <template x-for="(shield, sname) in stock.shields" :key="sname">
                    <div class="flex items-start justify-between">
                      <span class="text-slate-400" x-text="sname"></span>
                      <span class="font-mono text-right" :class="shield.passed ? 'text-emerald-400' : 'text-rose-400'" x-text="shield.passed ? '✓ PASS' : '✗ FAIL'"></span>
                    </div>
                  </template>
                </div>
              </details>
            </div>
          </div>
        </template>
      </div>
    </div>
    <div x-show="activeTab === 'global'" x-transition class="space-y-4">
      <div><h2 class="text-base font-bold text-slate-100">Layer 1: Global Market Check</h2><p class="text-xs text-slate-400">Assessed daily at 8:00 AM IST</p></div>
      <div class="p-4 bg-gradient-to-br from-slate-900 to-slate-950 border border-slate-800 rounded-2xl text-center space-y-2">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">Total Global Score</div>
        <div class="text-4xl font-extrabold font-mono text-emerald-400" x-text="globalData.global_score + ' / 5'"></div>
        <p class="text-xs text-slate-300 font-medium" x-text="globalData.description"></p>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-xl divide-y divide-slate-800 text-xs">
        <div class="p-3 flex justify-between items-center"><div><div class="font-semibold text-slate-200">Gift Nifty Gap</div><div class="text-[10px] text-slate-400 font-mono" x-text="globalData.gift_nifty_signal"></div></div><div class="font-mono font-bold" :class="(globalData.gift_nifty_gap || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'"><span x-text="(globalData.gift_nifty_gap >= 0 ? '+' : '') + (globalData.gift_nifty_gap || 0).toFixed(0) + ' pts'"></span></div></div>
        <div class="p-3 flex justify-between items-center"><div><div class="font-semibold text-slate-200">US Markets (Dow / Nasdaq)</div><div class="text-[10px] text-slate-400 font-mono" x-text="globalData.us_signal"></div></div><div class="font-mono text-right font-bold text-slate-300"><div>Dow: <span x-text="(globalData.us_dow_change || 0).toFixed(2) + '%'"></span></div><div>NQ: <span x-text="(globalData.us_nasdaq_change || 0).toFixed(2) + '%'"></span></div></div></div>
        <div class="p-3 flex justify-between items-center"><div><div class="font-semibold text-slate-200">Crude Oil (WTI)</div><div class="text-[10px] text-slate-400 font-mono" x-text="globalData.crude_signal"></div></div><div class="font-mono font-bold" :class="(globalData.crude_change || 0) <= 0 ? 'text-emerald-400' : 'text-rose-400'"><span x-text="(globalData.crude_change || 0).toFixed(2) + '%'"></span></div></div>
        <div class="p-3 flex justify-between items-center"><div><div class="font-semibold text-slate-200">USD / INR</div><div class="text-[10px] text-slate-400 font-mono" x-text="globalData.currency_signal"></div></div><div class="font-mono font-bold text-slate-300"><span x-text="(globalData.usdinr_change || 0).toFixed(2) + '%'"></span></div></div>
        <div class="p-3 flex justify-between items-center"><div><div class="font-semibold text-slate-200">Asian Markets (Nikkei / Hang Seng)</div><div class="text-[10px] text-slate-400 font-mono" x-text="globalData.asia_signal"></div></div><div class="font-mono text-right font-bold text-slate-300"><div>N225: <span x-text="(globalData.asia_nikkei_change || 0).toFixed(2) + '%'"></span></div><div>HSI: <span x-text="(globalData.asia_hangseng_change || 0).toFixed(2) + '%'"></span></div></div></div>
      </div>
    </div>
    <div x-show="activeTab === 'backtest'" x-transition class="space-y-4">
      <div><h2 class="text-base font-bold text-slate-100">Mobile Backtest Runner</h2><p class="text-xs text-slate-400">Run 60-day historical strategy backtest</p></div>
      <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl space-y-3 text-xs">
        <div><label class="text-[11px] text-slate-400 font-semibold">Symbols (comma separated)</label><input type="text" x-model="btSymbols" class="w-full mt-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs focus:outline-none focus:border-emerald-500"></div>
        <div class="flex space-x-2">
          <div class="flex-1"><label class="text-[11px] text-slate-400 font-semibold">Capital (₹)</label><input type="number" x-model="btCapital" class="w-full mt-1 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs focus:outline-none focus:border-emerald-500"></div>
          <div class="flex-1 flex items-end"><button @click="runBacktest()" :disabled="isBacktesting" class="w-full py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-800 text-white font-bold rounded-lg transition active:scale-95 shadow-sm shadow-emerald-950"><span x-text="isBacktesting ? 'Running...' : 'Run Backtest'"></span></button></div>
        </div>
      </div>
      <template x-if="btResults">
        <div class="space-y-3">
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="bg-slate-900 border border-slate-800 p-3 rounded-xl"><div class="text-[10px] text-slate-400">Win Rate</div><div class="text-lg font-bold font-mono text-emerald-400 mt-0.5"><span x-text="btResults.metrics.win_rate.toFixed(1)"></span>%</div><div class="text-[10px] text-slate-400 mt-1"><span x-text="btResults.metrics.winners"></span>W / <span x-text="btResults.metrics.losers"></span>L</div></div>
            <div class="bg-slate-900 border border-slate-800 p-3 rounded-xl"><div class="text-[10px] text-slate-400">Net P&L</div><div class="text-lg font-bold font-mono mt-0.5" :class="btResults.metrics.net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'">₹<span x-text="btResults.metrics.net_pnl.toFixed(2)"></span></div><div class="text-[10px] text-slate-400 mt-1">ROI: <span x-text="btResults.metrics.return_on_capital.toFixed(2)"></span>%</div></div>
            <div class="bg-slate-900 border border-slate-800 p-3 rounded-xl"><div class="text-[10px] text-slate-400">Profit Factor</div><div class="text-base font-bold font-mono text-slate-200 mt-0.5" x-text="btResults.metrics.profit_factor.toFixed(2)"></div></div>
            <div class="bg-slate-900 border border-slate-800 p-3 rounded-xl"><div class="text-[10px] text-slate-400">Max Drawdown</div><div class="text-base font-bold font-mono text-rose-400 mt-0.5">₹<span x-text="btResults.metrics.max_drawdown.toFixed(0)"></span></div></div>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 text-xs space-y-2">
            <div class="font-bold text-slate-200">Executed Trades (<span x-text="btResults.trades.length"></span>)</div>
            <div class="space-y-2 max-h-64 overflow-y-auto custom-scrollbar">
              <template x-for="t in btResults.trades" :key="t.entry_time + t.symbol">
                <div class="p-2 bg-slate-950 rounded border border-slate-800/80 flex items-center justify-between text-[11px]">
                  <div><div class="font-bold text-slate-200" x-text="t.symbol + ' (' + t.window + ')'"></div><div class="text-[10px] text-slate-400 font-mono" x-text="t.entry_time"></div></div>
                  <div class="text-right font-mono font-bold" :class="t.net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'"><div>₹<span x-text="t.net_pnl.toFixed(2)"></span></div><div class="text-[9px] text-slate-400" x-text="t.exit_reason"></div></div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </template>
    </div>
    <div x-show="activeTab === 'settings'" x-transition class="space-y-4">
      <div><h2 class="text-base font-bold text-slate-100">Settings & Mobile Alerts</h2><p class="text-xs text-slate-400">Configure instant Telegram notifications & broker connection</p></div>
      <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-3 text-xs">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-2 text-emerald-400 font-bold">
            <span>🌱</span><span>Groww Broker Integration</span>
          </div>
          <span class="px-2 py-0.5 rounded text-[10px] font-bold"
                :class="growwAccount.connected ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/20 text-rose-400 border border-rose-500/30'">
            <span class="inline-block w-1.5 h-1.5 rounded-full mr-1" :class="growwAccount.connected ? 'bg-emerald-400' : 'bg-rose-400'"></span>
            <span x-text="growwAccount.connected ? 'ACTIVE' : 'DISCONNECTED'"></span>
          </span>
        </div>
        <template x-if="growwAccount.connected">
          <div class="space-y-2 pt-1">
            <div class="grid grid-cols-2 gap-2 text-slate-300 text-[11px]">
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800">
                <span class="text-slate-400">Account UCC:</span> <b class="text-slate-100 font-mono" x-text="growwAccount.ucc"></b>
              </div>
              <div class="bg-slate-950/60 p-2 rounded border border-slate-800">
                <span class="text-slate-400">Clear Cash:</span> <b class="text-emerald-400 font-mono">₹<span x-text="(growwAccount.clear_cash || 0).toFixed(2)"></span></b>
              </div>
            </div>
            <div class="flex justify-between items-center text-[11px] text-slate-400 pt-1">
              <span>Holdings: <b class="text-slate-200" x-text="growwAccount.total_holdings_count || 0"></b> stocks</span>
              <span>Open Positions: <b class="text-slate-200" x-text="growwAccount.open_positions_count || 0"></b></span>
            </div>
            <div class="text-[10px] text-emerald-400/90 font-mono">
              ⚡ Daily 9:00 AM Auto-Refresh: Active (Zero manual login needed)
            </div>
          </div>
        </template>
        <button @click="refreshGroww()" :disabled="isRefreshingGroww" class="w-full py-2 bg-emerald-700/60 hover:bg-emerald-600 active:scale-95 transition text-white font-semibold rounded-lg flex items-center justify-center space-x-1.5 text-xs">
          <span :class="{'animate-spin': isRefreshingGroww}">🔄</span>
          <span x-text="isRefreshingGroww ? 'Refreshing Session...' : 'Refresh Groww Session'"></span>
        </button>
        <div x-show="growwMsg" class="text-center font-mono text-[10px] text-emerald-400" x-text="growwMsg"></div>
      </div>
      <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-3 text-xs">
        <div class="flex items-center space-x-2 text-emerald-400 font-bold"><span>📲</span><span>Instant Telegram Push Notifications</span></div>
        <p class="text-slate-300">Get real-time push notifications on your phone whenever an <b>ELITE</b> or <b>STRONG</b> setup triggers during market hours.</p>
        <button @click="testTelegram()" class="w-full py-2.5 bg-blue-600 hover:bg-blue-500 active:scale-95 transition text-white font-bold rounded-lg flex items-center justify-center space-x-2"><span>🚀 Send Test Alert to Phone</span></button>
        <div x-show="tgMsg" class="text-center font-mono text-[11px] text-emerald-400" x-text="tgMsg"></div>
      </div>
      <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-2 text-xs text-slate-300">
        <div class="font-bold text-slate-100">📱 How to Access on Mobile:</div>
        <ol class="list-decimal list-inside space-y-1 text-slate-400 text-[11px]"><li>Connect your phone to the same WiFi as this PC.</li><li>Open browser on your phone and visit:</li></ol>
        <div class="p-2 bg-slate-950 border border-slate-800 rounded font-mono text-emerald-400 text-center font-bold text-xs select-all" x-text="'http://' + (status.local_ip || 'loading...') + ':8000'"></div>
      </div>
    </div>
  </main>
  <nav class="fixed bottom-0 left-0 right-0 z-50 bg-slate-900/95 backdrop-blur-md border-t border-slate-800 px-3 py-2">
    <div class="max-w-md mx-auto grid grid-cols-4 gap-1 text-center">
      <button @click="activeTab = 'scanner'" class="py-1.5 rounded-lg flex flex-col items-center justify-center transition" :class="activeTab === 'scanner' ? 'text-emerald-400 font-bold' : 'text-slate-400 hover:text-slate-200'"><span class="text-lg">📡</span><span class="text-[10px] mt-0.5">Scanner</span></button>
      <button @click="activeTab = 'global'" class="py-1.5 rounded-lg flex flex-col items-center justify-center transition" :class="activeTab === 'global' ? 'text-emerald-400 font-bold' : 'text-slate-400 hover:text-slate-200'"><span class="text-lg">🌍</span><span class="text-[10px] mt-0.5">Global</span></button>
      <button @click="activeTab = 'backtest'" class="py-1.5 rounded-lg flex flex-col items-center justify-center transition" :class="activeTab === 'backtest' ? 'text-emerald-400 font-bold' : 'text-slate-400 hover:text-slate-200'"><span class="text-lg">📊</span><span class="text-[10px] mt-0.5">Backtest</span></button>
      <button @click="activeTab = 'settings'" class="py-1.5 rounded-lg flex flex-col items-center justify-center transition" :class="activeTab === 'settings' ? 'text-emerald-400 font-bold' : 'text-slate-400 hover:text-slate-200'"><span class="text-lg">⚙️</span><span class="text-[10px] mt-0.5">Settings</span></button>
    </div>
  </nav>
  <script>
    function tradingApp() {
      return {
        activeTab: 'scanner',
        status: {},
        globalData: {},
        fiiData: {},
        scanData: {},
        positions: [],
        growwAccount: {},
        isScanning: false,
        isBacktesting: false,
        isRefreshingGroww: false,
        btSymbols: 'SBIN, RELIANCE, HCLTECH, INFY',
        btCapital: 100000,
        btResults: null,
        tgMsg: '',
        growwMsg: '',
        get entryStocks() { return (this.scanData.stocks || []).filter(s => s.is_entry); },
        async init() {
          await this.fetchStatus();
          await this.fetchGlobal();
          await this.fetchFII();
          await this.fetchPositions();
          await this.fetchGrowwAccount();
          await this.fetchScan();
          setInterval(() => { if (this.activeTab === 'scanner') { this.fetchScan(true); this.fetchPositions(); } }, 30000);
        },
        async fetchStatus() { try { const r = await fetch('/api/status'); this.status = await r.json(); } catch(e) { console.error('Status fetch failed', e); } },
        async fetchGlobal() { try { const r = await fetch('/api/global'); this.globalData = await r.json(); } catch(e) { console.error('Global fetch failed', e); } },
        async fetchFII() { try { const r = await fetch('/api/fii'); this.fiiData = await r.json(); } catch(e) { console.error('FII fetch failed', e); } },
        async fetchPositions() { try { const r = await fetch('/api/positions'); const d = await r.json(); this.positions = d.positions || []; } catch(e) { console.error('Positions fetch failed', e); } },
        async fetchGrowwAccount() { try { const r = await fetch('/api/groww/account'); this.growwAccount = await r.json(); } catch(e) { console.error('Groww account fetch failed', e); } },
        async refreshGroww() { this.isRefreshingGroww = true; this.growwMsg = ''; try { const r = await fetch('/api/groww/refresh', {method: 'POST'}); const d = await r.json(); if(d.success) { this.growwMsg = '✓ Session refreshed!'; await this.fetchGrowwAccount(); } else { this.growwMsg = 'Error: ' + (d.message || 'Failed'); } } catch(e) { this.growwMsg = 'Failed: ' + e; } finally { this.isRefreshingGroww = false; } },
        async fetchScan(silent=false) { if(!silent) this.isScanning=true; try { const r = await fetch('/api/scan'); this.scanData = await r.json(); await this.fetchPositions(); } catch(e) { console.error('Scan fetch failed', e); } finally { if(!silent) this.isScanning=false; } },
        async runBacktest() { this.isBacktesting=true; this.btResults=null; try { const syms=this.btSymbols.split(',').map(s=>s.trim().toUpperCase()).filter(Boolean); const r=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbols:syms,capital:parseFloat(this.btCapital)||100000,period:'60d'})}); this.btResults=await r.json(); } catch(e) { alert('Backtest failed: '+e); } finally { this.isBacktesting=false; } },
        async testTelegram() { this.tgMsg='Sending alert...'; try { const r=await fetch('/api/telegram/test',{method:'POST'}); const d=await r.json(); if(d.success){this.tgMsg='✓ Test alert delivered to your Telegram!';}else{this.tgMsg='⚠️ Telegram not configured yet. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env file.';} } catch(e) { this.tgMsg='Error sending test alert: '+e; } }
      }
    }
  </script>
</body>
</html>'''

@app.get('/health')
@app.head('/health')
async def health_check():
    """Health check endpoint for Render."""
    return {'status': 'ok'}

@app.get('/', response_class=HTMLResponse)
@app.head('/', response_class=HTMLResponse)
async def serve_dashboard():
    """Serve mobile-optimized dashboard."""
    return DASHBOARD_HTML

@app.get('/api/status')
async def get_system_status():
    """Get system and market status."""
    window = get_current_window()
    nifty = check_nifty_regime()
    return {
        'server_time': datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'),
        'is_market_open': is_market_open(),
        'window_name': window['name'],
        'window_active': window['active'],
        'window_number': window['number'],
        'max_trades': MAX_DAILY_TRADES,
        'daily_trades_taken': len(alerted_entries_today),
        'active_positions_count': len(active_positions),
        'nifty_regime': nifty.get('regime', 'UNKNOWN'),
        'nifty_change': nifty.get('today_gain', 0.0),
        'nifty_description': nifty.get('description', ''),
        'nifty_can_long': nifty.get('can_long', False),
        'total_capital': TOTAL_CAPITAL,
        'local_ip': _get_local_ip()
    }

def get_yfinance_change(ticker: str) -> float:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?range=5d&interval=1d"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            res = json.loads(response.read().decode('utf-8'))
            closes = [c for c in res['chart']['result'][0]['indicators']['quote'][0]['close'] if c is not None]
            if len(closes) >= 2:
                return ((closes[-1] - closes[-2]) / closes[-2]) * 100.0
        return 0.0
    except Exception:
        return 0.0

@app.get('/api/global')
async def get_global_check():
    """Get Layer 1 Global Market Check."""
    try:
        # Gift Nifty approximation using ^NSEI
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?range=5d&interval=1d"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=5) as response:
                res = json.loads(response.read().decode('utf-8'))
                closes = [c for c in res['chart']['result'][0]['indicators']['quote'][0]['close'] if c is not None]
                gift_nifty_gap = closes[-1] - closes[-2] if len(closes) >= 2 else 0.0
        except Exception:
            gift_nifty_gap = 0.0

            
        us_dow_change = get_yfinance_change("^DJI")
        us_nasdaq_change = get_yfinance_change("^IXIC")
        crude_change = get_yfinance_change("CL=F")
        usdinr_change = get_yfinance_change("USDINR=X")
        asia_nikkei_change = get_yfinance_change("^N225")
        asia_hangseng_change = get_yfinance_change("^HSI")
        
        score = 0
        
        # Gift Nifty
        if gift_nifty_gap > 10:
            gift_nifty_signal = "GAP_UP"
            score += 1
        elif gift_nifty_gap < -10:
            gift_nifty_signal = "GAP_DOWN"
        else:
            gift_nifty_signal = "FLAT"
            
        # US Markets
        if us_dow_change > 0 and us_nasdaq_change > 0:
            us_signal = "BOTH_GREEN"
            score += 1
        elif us_dow_change < 0 and us_nasdaq_change < 0:
            us_signal = "BOTH_RED"
        else:
            us_signal = "NEUTRAL"
            
        # Crude Oil
        if crude_change <= 0:
            crude_signal = "FLAT_OR_DOWN"
            score += 1
        else:
            crude_signal = "UP"
            
        # USD/INR
        if usdinr_change <= 0.1:
            currency_signal = "STABLE"
            score += 1
        else:
            currency_signal = "WEAK"
            
        # Asia
        if asia_nikkei_change > 0 and asia_hangseng_change > 0:
            asia_signal = "BOTH_GREEN"
            score += 1
        elif asia_nikkei_change < 0 and asia_hangseng_change < 0:
            asia_signal = "BOTH_RED"
        else:
            asia_signal = "NEUTRAL"

        return {
            "global_score": score,
            "can_trade": score >= 2,
            "position_size_multiplier": 1.0 if score >= 3 else 0.5,
            "description": f"Global {score}/5 | Nifty:{gift_nifty_signal} | US:{us_signal} | Crude:{crude_signal} | INR:{currency_signal} | Asia:{asia_signal}",
            "gift_nifty_gap": gift_nifty_gap,
            "gift_nifty_signal": gift_nifty_signal,
            "us_signal": us_signal,
            "us_dow_change": us_dow_change,
            "us_nasdaq_change": us_nasdaq_change,
            "crude_signal": crude_signal,
            "crude_change": crude_change,
            "currency_signal": currency_signal,
            "usdinr_change": usdinr_change,
            "asia_signal": asia_signal,
            "asia_nikkei_change": asia_nikkei_change,
            "asia_hangseng_change": asia_hangseng_change
        }
    except Exception as e:
        return {
            "global_score": 3,
            "can_trade": True,
            "position_size_multiplier": 1.0,
            "description": f"Error fetching data: {str(e)}",
            "gift_nifty_gap": 0,
            "gift_nifty_signal": "FLAT",
            "us_signal": "NEUTRAL",
            "us_dow_change": 0,
            "us_nasdaq_change": 0,
            "crude_signal": "FLAT",
            "crude_change": 0,
            "currency_signal": "NEUTRAL",
            "usdinr_change": 0,
            "asia_signal": "NEUTRAL",
            "asia_nikkei_change": 0,
            "asia_hangseng_change": 0
        }

@app.get('/api/fii')
async def get_fii_dii():
    """Get Layer 2 FII/DII data."""
    fii_net_cr = float(os.environ.get('FII_NET_CR', '0'))
    dii_net_cr = float(os.environ.get('DII_NET_CR', '0'))
    
    if fii_net_cr > 500:
        scenario = 1
        scenario_name = "FII Heavy Buying"
        signal = "BULLISH"
        score_bonus = 2.0
        can_trade = True
    elif fii_net_cr > 0:
        scenario = 2
        scenario_name = "FII Buying"
        signal = "SLIGHTLY_BULLISH"
        score_bonus = 1.0
        can_trade = True
    elif fii_net_cr >= -500:
        scenario = 4
        scenario_name = "FII Selling"
        signal = "SLIGHTLY_BEARISH"
        score_bonus = -1.0
        can_trade = True
    else:
        scenario = 5
        scenario_name = "FII Heavy Selling"
        signal = "BEARISH"
        score_bonus = -2.0
        can_trade = False
        
    fii_action = "BUYING" if fii_net_cr > 0 else "SELLING" if fii_net_cr < 0 else "NEUTRAL"
    dii_action = "BUYING" if dii_net_cr > 0 else "SELLING" if dii_net_cr < 0 else "NEUTRAL"
    
    return {
        "scenario": scenario,
        "scenario_name": scenario_name,
        "signal": signal,
        "score_bonus": score_bonus,
        "fii_net_cr": fii_net_cr,
        "dii_net_cr": dii_net_cr,
        "fii_action": fii_action,
        "dii_action": dii_action,
        "can_trade": can_trade,
        "description": f"FII Rs.{fii_net_cr:+}Cr net {fii_action.lower()} | DII Rs.{dii_net_cr:+}Cr"
    }

@app.get('/api/scan')
async def run_live_scan(symbols: Optional[str] = None):
    """Run real-time 8-shield scan on watchlist."""
    try:
        from groww_manager import ensure_daily_token
        ensure_daily_token()
    except Exception:
        pass

    sym_list = [s.strip().upper() for s in symbols.split(',')] if symbols else DEFAULT_WATCHLIST
    result = scan_watchlist(sym_list, TOTAL_CAPITAL)
    stocks = result.get('stocks', [])
    price_map = {s['symbol']: s['price'] for s in stocks if 'symbol' in s and 'price' in s}
    
    # 1. Evaluate open active positions for T1, T2, SL, or square-off
    check_active_positions(price_map)
    
    # 2. Alert new entries (only if within valid trading window before 14:45 IST)
    entries = [s for s in stocks if s.get('is_entry')]
    if entries and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        now = datetime.now(IST)
        window = get_current_window()
        can_enter = (
            window.get('active', False) and
            window.get('name') != 'DEAD_ZONE' and
            (now.hour < 14 or (now.hour == 14 and now.minute < 45)) and
            len(active_positions) < MAX_ACTIVE_POSITIONS and
            len(alerted_entries_today) < MAX_DAILY_TRADES
        )
        if can_enter:
            _send_telegram_alert(entries)
        
    return result

@app.post('/api/backtest')
async def run_mobile_backtest(req: BacktestRequest):
    """Execute backtest from mobile."""
    symbols = req.symbols or DEFAULT_WATCHLIST[:4]
    return run_backtest(symbols, req.capital or TOTAL_CAPITAL, req.period or '60d')

@app.get('/api/swing')
async def get_swing_signals(capital: Optional[float] = None):
    """Scan 50 top Indian stocks for Daily Swing Trading setups."""
    cap = capital or TOTAL_CAPITAL
    candidates = scan_swing_candidates(cap)
    return {
        'timestamp': datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'),
        'total_scanned': len(SWING_WATCHLIST_50),
        'capital': cap,
        'candidates_count': len(candidates),
        'candidates': candidates
    }

@app.post('/api/telegram/test')
async def trigger_test_telegram():
    """Send test alert to user's Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {'success': False}
    try:
        success = _send_telegram_message('✅ Test Alert from Master Trading v2!\n\nYour Telegram alerts are working correctly.\n🤖 You will receive ELITE & STRONG trade signals during market hours.\n📰 Sentiment Analysis: Active\n🔬 Weekly Self-Tune: Active (Sundays 8 PM)')
        return {'success': success}
    except Exception:
        return {'success': False}

@app.get('/api/test-data')
async def test_data():
    import traceback
    from engine import fetch_stock_data_direct, scan_stock
    
    direct_err = None
    direct_df = None
    try:
        direct_df = fetch_stock_data_direct('SBIN')
    except Exception as e:
        direct_err = str(e)
        
    scan_res = None
    scan_err = None
    try:
        scan_res = scan_stock('SBIN', 5000)
    except Exception as e:
        scan_err = str(e)
        
    return {
        'direct_df_none': direct_df is None,
        'direct_df_rows': len(direct_df) if direct_df is not None else 0,
        'direct_err': direct_err,
        'scan_res_none': scan_res is None,
        'scan_res': scan_res,
        'scan_err': scan_err
    }

@app.get('/api/groww/token')
@app.post('/api/groww/token')
async def update_groww_token(token: Optional[str] = None):
    """Update or check daily Groww access token."""
    from groww_manager import save_groww_token, get_groww_token
    if token and len(token.strip()) > 5:
        success = save_groww_token(token.strip())
        if success:
            _send_telegram_message("✅ <b>Groww API Token Activated!</b>\nBot is now streaming directly from Groww servers.")
        return {'success': success, 'message': 'Token updated successfully' if success else 'Failed to save token'}
    
    current_token = get_groww_token()
    return {
        'has_token': bool(current_token),
        'token_preview': (current_token[:6] + '...' + current_token[-4:]) if current_token else None
    }

@app.get('/api/groww/account')
async def get_groww_account():
    """Get live Groww account status, UCC, cash balance, holdings, and positions."""
    try:
        from groww_manager import get_account_summary, ensure_daily_token
        ensure_daily_token()
        return get_account_summary()
    except Exception as e:
        return {'connected': False, 'error': str(e)}

@app.post('/api/groww/refresh')
async def refresh_groww_session():
    """Force generate a fresh Groww session access token."""
    try:
        from groww_manager import refresh_groww_token
        return refresh_groww_token()
    except Exception as e:
        return {'success': False, 'error': str(e)}

@app.get('/api/sentiment')
async def get_sentiment(symbol: Optional[str] = None):
    """Get live market sentiment analysis (News + VIX + FII/DII)."""
    try:
        result = get_market_sentiment(symbol)
        return result
    except Exception as e:
        return {'error': str(e), 'sentiment_label': 'NEUTRAL', 'can_trade': True}

@app.get('/api/tune-status')
async def get_tune_status():
    """Get current tuned parameters, version, and tune history."""
    try:
        current_params = load_tuned_params()
        history = load_tune_history()
        return {
            'current_params': current_params,
            'history_count': len(history),
            'recent_history': history[-5:] if history else [],
            'next_tune': 'Sunday 8:00 PM IST',
            'safety_locks': {
                'max_risk_per_trade': '₹150 (3% of ₹5,000) — LOCKED',
                'max_trades_per_day': '1 — LOCKED',
                'macd_mandatory': 'True — LOCKED',
                'dead_zone_block': '12:00-14:00 — LOCKED',
                'stop_loss': 'Always ON — LOCKED'
            }
        }
    except Exception as e:
        return {'error': str(e)}

@app.get('/api/trade-log')
async def get_trade_log(days: Optional[int] = 7):
    """Get logged trades for review."""
    try:
        trades = load_trade_log(days=days or 7)
        from self_tune import calculate_performance
        perf = calculate_performance(trades)
        return {
            'trades_count': len(trades),
            'period_days': days,
            'performance': perf,
            'trades': trades[-50:]  # Last 50 trades
        }
    except Exception as e:
        return {'error': str(e), 'trades': []}

@app.post('/api/tune/run')
async def manual_tune_trigger():
    """Manually trigger a self-tune cycle (for testing)."""
    try:
        report = run_weekly_tune(capital=TOTAL_CAPITAL)
        if report:
            _send_telegram_message(report)
        return {'success': True, 'report_sent': bool(report)}
    except Exception as e:
        return {'success': False, 'error': str(e)}

@app.get('/api/positions')
async def get_active_positions():
    """Get currently active tracked intraday positions."""
    return {
        'count': len(active_positions),
        'positions': list(active_positions.values())
    }

@app.post('/api/positions/clear')
async def clear_active_positions():
    """Clear all active tracked positions."""
    global active_positions
    active_positions.clear()
    _save_active_positions(active_positions)
    return {'success': True, 'count': 0}

@app.post('/api/positions/test')
async def test_position_trigger(symbol: str = 'SBIN', entry: float = 800.0, t1: float = 810.0, t2: float = 820.0, sl: float = 790.0, qty: int = 2):
    """Create a mock test position to test target/SL triggers."""
    active_positions[symbol] = {
        'symbol': symbol,
        'entry_price': entry,
        'sl': sl,
        'initial_sl': sl,
        't1': t1,
        't2': t2,
        'qty': qty,
        'remaining_qty': qty,
        'booked_profit': 0.0,
        'entry_time': datetime.now(IST).strftime('%H:%M:%S'),
        'entry_date': datetime.now(IST).strftime('%Y-%m-%d'),
        't1_hit': False,
        'grade': 'ELITE',
        'score': 16.0
    }
    _save_active_positions(active_positions)
    return {'success': True, 'position': active_positions[symbol]}

@app.get('/api/paper')
async def get_paper_portfolio():
    """Get live virtual paper trading account, balance, active trades, and PnL."""
    try:
        import paper_trading
        return paper_trading.get_paper_account()
    except Exception as e:
        return {'error': str(e)}

@app.post('/api/paper/report')
async def trigger_paper_report():
    """Generate and send weekly paper trading audit report to Telegram."""
    try:
        import paper_trading
        report = paper_trading.generate_weekly_paper_report()
        success = _send_telegram_message(report)
        return {'success': success, 'report': report}
    except Exception as e:
        return {'success': False, 'error': str(e)}

@app.post('/api/paper/reset')
async def reset_paper_account(capital: Optional[float] = 5000.0):
    """Reset the paper trading account back to fresh initial capital."""
    try:
        import paper_trading
        account = paper_trading.init_paper_account(initial_capital=capital or 5000.0)
        # Force re-init if file exists
        from paper_trading import ACCOUNT_FILE
        if os.path.exists(ACCOUNT_FILE):
            os.remove(ACCOUNT_FILE)
        account = paper_trading.init_paper_account(initial_capital=capital or 5000.0)
        return {'success': True, 'account': account}
    except Exception as e:
        return {'success': False, 'error': str(e)}

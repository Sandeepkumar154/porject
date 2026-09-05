import os
import socket
import json
import asyncio
import urllib.request
import urllib.parse
import ssl
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import yfinance as yf

from engine import (
    scan_watchlist, scan_stock, run_backtest, get_current_window,
    is_market_open, DEFAULT_WATCHLIST, WINDOWS,
    scan_swing_candidates, SWING_WATCHLIST_50
)

app = FastAPI(title='Master Trading Plan v2 — Improved', version='2.1.0')

# Environment variables
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8649513530:AAHgwOOrmHz9WNrWw-b3OUQtBevM-zSDAXk')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '1221493262')
TOTAL_CAPITAL = float(os.environ.get('TOTAL_CAPITAL', '100000'))

# In-memory tracking of alerted signals to prevent duplicate spam
alerted_entries_today = set()

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
        req = urllib.request.Request(url, data=data)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            return response.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def _send_telegram_alert(entries: list):
    """Format and send trading signal alert."""
    today = datetime.now().strftime('%Y-%m-%d')
    for entry in entries:
        symbol = entry.get('symbol', 'UNKNOWN')
        grade = entry.get('grade', 'NONE')
        price = entry.get('price', 0)
        sl = entry.get('sl', 0)
        t1 = entry.get('t1', 0)
        t2 = entry.get('t2', 0)
        qty = entry.get('qty', 0)
        
        # Deduplication key: symbol + day + hour (max 1 alert per stock per hour)
        hour_slot = datetime.now().strftime('%Y-%m-%d %H')
        alert_key = f"{symbol}_{hour_slot}"
        if alert_key in alerted_entries_today:
            continue
            
        alerted_entries_today.add(alert_key)
        
        text = f"🚨 <b>INTRADAY TRADE SIGNAL: {symbol}</b>\n\n"
        text += f"🏆 Grade: <b>{grade}</b> (High Conviction)\n"
        text += f"📈 <b>Action: BUY @ ₹{price:.2f}</b>\n"
        text += f"🛑 Stop Loss: ₹{sl:.2f}\n"
        text += f"🎯 Target 1 (2:1): ₹{t1:.2f}\n"
        text += f"🎯 Target 2 (3:1): ₹{t2:.2f}\n"
        text += f"📦 Suggested Qty: <b>{qty} shares</b>\n\n"
        text += f"⏰ Time: {datetime.now().strftime('%H:%M:%S IST')}\n"
        text += f"📊 Strategy: <i>8-Shield System v2</i>"
        
        _send_telegram_message(text)

# Track sent status announcements to avoid duplicate broadcasts
sent_session_updates = set()

async def background_market_scanner():
    """Continuous automated background scanner during market hours."""
    print("Background market scanner started...")
    
    # Send startup announcement
    _send_telegram_message(
        "🤖 <b>Groww Trading Bot Active</b>\n\n"
        "✅ 8-Shield Scanner: <b>Online</b>\n"
        "📈 Watchlist: 15 Top NSE Stocks\n"
        "⏰ Automated Scanning: <b>Active (Every 60s)</b>\n\n"
        "<i>You will receive automatic alerts for ELITE & STRONG trade signals, plus session status updates throughout market hours.</i>"
    )
    
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime('%Y-%m-%d')
            current_time = now.time()
            
            # Check market session milestones for Telegram status updates
            # 1. Market Open (09:15 - 09:20)
            if is_market_open():
                open_key = f"{today_str}_OPEN"
                if open_key not in sent_session_updates and now.hour == 9 and now.minute >= 15:
                    sent_session_updates.add(open_key)
                    _send_telegram_message(
                        "🔔 <b>NSE Market Open (09:15 IST)</b>\n\n"
                        "🟢 Bot is actively monitoring the watchlist for high-conviction 8-Shield setups.\n"
                        "🎯 Target Windows: <b>PRIME</b> (09:20-09:45) & <b>MOMENTUM</b> (10:00-12:00)."
                    )

                # 2. Midday Dead Zone (12:00)
                dead_key = f"{today_str}_DEADZONE"
                if dead_key not in sent_session_updates and now.hour == 12:
                    sent_session_updates.add(dead_key)
                    _send_telegram_message(
                        "⏸️ <b>Midday Dead Zone (12:00 - 13:30 IST)</b>\n\n"
                        "Low-volume chop zone active. Bot is filtering out false breakouts to protect capital.\n"
                        "⚡ Next Trading Window: <b>CONTINUATION</b> (14:00 - 15:00)."
                    )

                # 3. Continuation Session (14:00)
                cont_key = f"{today_str}_CONTINUATION"
                if cont_key not in sent_session_updates and now.hour == 14:
                    sent_session_updates.add(cont_key)
                    _send_telegram_message(
                        "⚡ <b>Continuation Window Active (14:00 IST)</b>\n\n"
                        "Scanning for afternoon institutional continuation trends."
                    )

                # 4. Daily Swing Trading Scan (15:15 IST - 15 mins before market close)
                swing_key = f"{today_str}_SWING"
                if swing_key not in sent_session_updates and now.hour == 15 and now.minute >= 15:
                    sent_session_updates.add(swing_key)
                    swing_candidates = scan_swing_candidates(TOTAL_CAPITAL)
                    if swing_candidates:
                        msg = "📊 <b>DAILY SWING TRADING PICKS (3:15 PM)</b>\n\n"
                        msg += "<i>Top Daily Breakout & Dip-Buying Setups to Hold (3-10 Days):</i>\n\n"
                        for c in swing_candidates[:4]:
                            msg += f"🔥 <b>{c['symbol']}</b> ({c['type']})\n"
                            msg += f"   • Price: ₹{c['price']:.2f}\n"
                            msg += f"   • Stop-Loss: ₹{c['sl']:.2f} (-3.5%)\n"
                            msg += f"   • Target 1: ₹{c['t1']:.2f} (+6%)\n"
                            msg += f"   • Target 2: ₹{c['t2']:.2f} (+10%)\n"
                            msg += f"   • Suggested Qty for ₹{int(TOTAL_CAPITAL)}: <b>{c['qty']} shares</b>\n"
                            msg += f"   • Setup: <i>{c['setup']}</i>\n\n"
                        _send_telegram_message(msg)

                # Run live scan across watchlist
                result = scan_watchlist(DEFAULT_WATCHLIST, TOTAL_CAPITAL)
                entries = [s for s in result.get('stocks', []) if s.get('is_entry')]
                if entries:
                    _send_telegram_alert(entries)
                    
                await asyncio.sleep(60) # Scan every 1 minute during market hours
            else:
                # Market Close (15:30)
                close_key = f"{today_str}_CLOSE"
                if close_key not in sent_session_updates and (now.hour == 15 and now.minute >= 30 or now.hour > 15) and now.weekday() < 5:
                    sent_session_updates.add(close_key)
                    _send_telegram_message(
                        "🏁 <b>NSE Market Closed (15:30 IST)</b>\n\n"
                        "All intraday positions auto squared-off.\n"
                        "Bot will resume automatic scanning next trading day at 09:15 AM IST."
                    )
                    
                # Outside market hours, sleep 5 minutes
                await asyncio.sleep(300)
        except Exception as e:
            print(f"Background scanner error: {e}")
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    # Start auto-scanner in background
    asyncio.create_task(background_market_scanner())

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
      <div><h2 class="text-base font-bold text-slate-100">Settings & Mobile Alerts</h2><p class="text-xs text-slate-400">Configure instant Telegram notifications</p></div>
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
        isScanning: false,
        isBacktesting: false,
        btSymbols: 'SBIN, RELIANCE, HCLTECH, INFY',
        btCapital: 100000,
        btResults: null,
        tgMsg: '',
        get entryStocks() { return (this.scanData.stocks || []).filter(s => s.is_entry); },
        async init() {
          await this.fetchStatus();
          await this.fetchGlobal();
          await this.fetchFII();
          await this.fetchScan();
          setInterval(() => { if (this.activeTab === 'scanner') { this.fetchScan(true); } }, 30000);
        },
        async fetchStatus() { try { const r = await fetch('/api/status'); this.status = await r.json(); } catch(e) { console.error('Status fetch failed', e); } },
        async fetchGlobal() { try { const r = await fetch('/api/global'); this.globalData = await r.json(); } catch(e) { console.error('Global fetch failed', e); } },
        async fetchFII() { try { const r = await fetch('/api/fii'); this.fiiData = await r.json(); } catch(e) { console.error('FII fetch failed', e); } },
        async fetchScan(silent=false) { if(!silent) this.isScanning=true; try { const r = await fetch('/api/scan'); this.scanData = await r.json(); } catch(e) { console.error('Scan fetch failed', e); } finally { if(!silent) this.isScanning=false; } },
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
    return {
        'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'is_market_open': is_market_open(),
        'window_name': window['name'],
        'window_active': window['active'],
        'window_number': window['number'],
        'max_trades': window['max_trades'],
        'total_capital': TOTAL_CAPITAL,
        'local_ip': _get_local_ip()
    }

def get_yfinance_change(ticker: str) -> float:
    try:
        data = yf.Ticker(ticker).history(period="5d")
        if len(data) >= 2:
            prev_close = data['Close'].iloc[-2]
            last_close = data['Close'].iloc[-1]
            return ((last_close - prev_close) / prev_close) * 100.0
        return 0.0
    except Exception:
        return 0.0

@app.get('/api/global')
async def get_global_check():
    """Get Layer 1 Global Market Check."""
    try:
        # Gift Nifty approximation using ^NSEI
        try:
            nifty_data = yf.Ticker("^NSEI").history(period="2d")
            gift_nifty_gap = nifty_data['Close'].iloc[-1] - nifty_data['Close'].iloc[-2] if len(nifty_data) >= 2 else 0.0
        except:
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
    sym_list = [s.strip().upper() for s in symbols.split(',')] if symbols else DEFAULT_WATCHLIST
    result = scan_watchlist(sym_list, TOTAL_CAPITAL)
    
    entries = [s for s in result.get('stocks', []) if s.get('is_entry')]
    if entries and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
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
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
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
        success = _send_telegram_message('✅ Test Alert from Master Trading v2!\n\nYour Telegram alerts are working correctly.\n🤖 You will receive ELITE & STRONG trade signals during market hours.')
        return {'success': success}
    except Exception:
        return {'success': False}

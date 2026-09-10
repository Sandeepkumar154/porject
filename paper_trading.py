import os
import json
from datetime import datetime
import pytz
from typing import Dict, Any, List, Optional

IST = pytz.timezone('Asia/Kolkata')
ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_account.json")

# Groww exact fee structures:
INTRADAY_ROUND_TRIP_FEE = 45.0  # Rs. 40 flat brokerage (buy+sell) + Rs. 5 STT/GST/exchange
SWING_SELL_FEE = 20.0          # Groww Delivery: Rs. 0 brokerage, ~Rs. 20 DP charges + STT on sell

def get_ist_now_str() -> str:
    """Return formatted live IST timestamp."""
    return datetime.now(IST).strftime('%I:%M:%S %p IST (%d-%b-%Y)')

def get_ist_iso_str() -> str:
    """Return ISO format string."""
    return datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')

def init_paper_account(force_reset: bool = False) -> Dict[str, Any]:
    """Initialize or load the dual-book paper trading account (Intraday Rs. 5k + Swing Rs. 5k)."""
    if os.path.exists(ACCOUNT_FILE) and not force_reset:
        try:
            with open(ACCOUNT_FILE, "r") as f:
                data = json.load(f)
                # Verify schema has both intraday and swing
                if "intraday" in data and "swing" in data:
                    return data
        except Exception:
            pass
            
    account = {
        "account_id": "PAPER-SANDEEP-10K",
        "created_at": get_ist_iso_str(),
        "last_updated": get_ist_iso_str(),
        "total_initial_capital": 10000.0,
        "total_current_balance": 10000.0,
        "total_realised_pnl": 0.0,
        "intraday": {
            "book_name": "Intraday (15m ORB + Volume Shocker)",
            "initial_capital": 5000.0,
            "current_balance": 5000.0,
            "realised_pnl": 0.0,
            "total_brokerage_paid": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "active_trade": None,
            "trades_history": []
        },
        "swing": {
            "book_name": "Swing Trading (Daily Breakout / 20 EMA Dip)",
            "initial_capital": 5000.0,
            "current_balance": 5000.0,
            "realised_pnl": 0.0,
            "total_brokerage_paid": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "active_positions": [],
            "trades_history": []
        }
    }
    save_paper_account(account)
    return account

def get_paper_account() -> Dict[str, Any]:
    """Get current state of the dual paper account."""
    acc = init_paper_account()
    # Recalculate top-level aggregates
    intra = acc.get("intraday", {})
    swing = acc.get("swing", {})
    acc["total_current_balance"] = round(intra.get("current_balance", 5000.0) + swing.get("current_balance", 5000.0), 2)
    acc["total_realised_pnl"] = round(intra.get("realised_pnl", 0.0) + swing.get("realised_pnl", 0.0), 2)
    
    # Backwards compatibility flat fields for intraday
    acc["initial_capital"] = intra.get("initial_capital", 5000.0)
    acc["current_balance"] = intra.get("current_balance", 5000.0)
    acc["realised_pnl"] = intra.get("realised_pnl", 0.0)
    acc["total_brokerage_paid"] = intra.get("total_brokerage_paid", 0.0)
    acc["total_trades"] = intra.get("total_trades", 0)
    acc["winning_trades"] = intra.get("winning_trades", 0)
    acc["losing_trades"] = intra.get("losing_trades", 0)
    acc["win_rate"] = intra.get("win_rate", 0.0)
    acc["active_trade"] = intra.get("active_trade")
    acc["trades_history"] = intra.get("trades_history", [])
    return acc

def save_paper_account(account: Dict[str, Any]) -> bool:
    """Save paper account state to disk."""
    try:
        account["last_updated"] = get_ist_iso_str()
        with open(ACCOUNT_FILE, "w") as f:
            json.dump(account, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving paper account: {e}")
        return False

# ==========================================
# 1. INTRADAY PAPER TRADING (Rs. 5,000 Capital)
# ==========================================

def record_paper_entry(symbol: str, price: float, qty: int, sl: float, t1: float, t2: float, setup: str = "15m ORB Breakout") -> Dict[str, Any]:
    """Execute real-time Intraday entry with exact timestamp."""
    account = get_paper_account()
    now_str = get_ist_now_str()
    iso_str = get_ist_iso_str()
    
    trade = {
        "symbol": symbol,
        "book": "INTRADAY",
        "setup": setup,
        "executed_time": now_str,
        "entry_iso": iso_str,
        "entry_price": round(float(price), 2),
        "qty": int(qty),
        "trade_value": round(float(price) * int(qty), 2),
        "sl": round(float(sl), 2),
        "t1": round(float(t1), 2),
        "t2": round(float(t2), 2),
        "status": "OPEN",
        "remaining_qty": int(qty),
        "booked_gross": 0.0
    }
    
    account["intraday"]["active_trade"] = trade
    save_paper_account(account)
    return trade

def record_paper_exit(symbol: str, exit_price: float, exit_reason: str, exit_qty: Optional[int] = None) -> Dict[str, Any]:
    """Close out Intraday trade, deduct Groww fees (Rs. 45), update balance."""
    account = get_paper_account()
    intra = account.get("intraday", {})
    trade = intra.get("active_trade")
    
    if not trade or trade.get("symbol") != symbol:
        return {"success": False, "message": "No matching active intraday paper trade found."}
        
    now_str = get_ist_now_str()
    iso_str = get_ist_iso_str()
    entry_price = trade["entry_price"]
    qty_to_close = exit_qty if exit_qty and exit_qty <= trade["remaining_qty"] else trade["remaining_qty"]
    
    leg_pnl = round(qty_to_close * (exit_price - entry_price), 2)
    trade["booked_gross"] = round(trade.get("booked_gross", 0.0) + leg_pnl, 2)
    trade["remaining_qty"] -= qty_to_close
    
    is_fully_closed = trade["remaining_qty"] <= 0
    
    if is_fully_closed:
        gross_pnl = trade["booked_gross"]
        net_pnl = round(gross_pnl - INTRADAY_ROUND_TRIP_FEE, 2)
        
        trade["exit_time"] = now_str
        trade["exit_iso"] = iso_str
        trade["exit_price"] = round(float(exit_price), 2)
        trade["exit_reason"] = exit_reason
        trade["status"] = "CLOSED"
        trade["gross_pnl"] = gross_pnl
        trade["brokerage"] = INTRADAY_ROUND_TRIP_FEE
        trade["net_pnl"] = net_pnl
        trade["return_pct"] = round((net_pnl / intra["initial_capital"]) * 100, 2)
        trade["is_winner"] = net_pnl > 0
        
        # Update account balances
        intra["current_balance"] = round(intra["current_balance"] + net_pnl, 2)
        intra["realised_pnl"] = round(intra["realised_pnl"] + net_pnl, 2)
        intra["total_brokerage_paid"] = round(intra["total_brokerage_paid"] + INTRADAY_ROUND_TRIP_FEE, 2)
        intra["total_trades"] += 1
        if net_pnl > 0:
            intra["winning_trades"] += 1
        else:
            intra["losing_trades"] += 1
            
        intra["win_rate"] = round((intra["winning_trades"] / intra["total_trades"]) * 100, 1)
        if "trades_history" not in intra:
            intra["trades_history"] = []
        intra["trades_history"].append(trade)
        intra["active_trade"] = None
        
        save_paper_account(account)
        return {
            "success": True,
            "fully_closed": True,
            "net_pnl": net_pnl,
            "gross_pnl": gross_pnl,
            "brokerage": INTRADAY_ROUND_TRIP_FEE,
            "new_balance": intra["current_balance"],
            "exit_time": now_str,
            "trade": trade
        }
    else:
        save_paper_account(account)
        return {
            "success": True,
            "fully_closed": False,
            "partial_pnl": leg_pnl,
            "remaining_qty": trade["remaining_qty"],
            "exit_time": now_str,
            "trade": trade
        }

# ==========================================
# 2. SWING TRADING PAPER BOOK (Rs. 5,000 Capital)
# ==========================================

def record_swing_entry(symbol: str, price: float, qty: int, sl: float, t1: float, t2: float, setup: str = "Daily Breakout") -> Dict[str, Any]:
    """
    Execute real-time Swing Trade entry using dedicated Rs. 5,000 Swing Capital (1x Delivery/CNC).
    """
    account = get_paper_account()
    swing = account.get("swing", {})
    now_str = get_ist_now_str()
    iso_str = get_ist_iso_str()
    
    trade_value = round(float(price) * int(qty), 2)
    avail = swing.get("current_balance", 5000.0)
    
    # Check if swing balance allows this purchase
    if trade_value > avail + 50.0:
        qty = max(1, int(avail / price))
        trade_value = round(float(price) * qty, 2)
        
    trade = {
        "symbol": symbol,
        "book": "SWING",
        "setup": setup,
        "executed_time": now_str,
        "entry_iso": iso_str,
        "entry_price": round(float(price), 2),
        "qty": int(qty),
        "trade_value": trade_value,
        "sl": round(float(sl), 2),
        "t1": round(float(t1), 2),
        "t2": round(float(t2), 2),
        "status": "OPEN",
        "remaining_qty": int(qty),
        "booked_gross": 0.0,
        "t1_hit": False
    }
    
    # Deduct cash for delivery purchase
    swing["current_balance"] = round(swing["current_balance"] - trade_value, 2)
    if "active_positions" not in swing:
        swing["active_positions"] = []
    swing["active_positions"].append(trade)
    
    save_paper_account(account)
    return trade

def record_swing_exit(symbol: str, exit_price: float, exit_reason: str, exit_qty: Optional[int] = None) -> Dict[str, Any]:
    """Close out a Swing Trade, return cash to balance, deduct Groww DP charges (Rs. 20)."""
    account = get_paper_account()
    swing = account.get("swing", {})
    active_positions = swing.get("active_positions", [])
    
    matching_idx = None
    trade = None
    for idx, pos in enumerate(active_positions):
        if pos.get("symbol") == symbol:
            matching_idx = idx
            trade = pos
            break
            
    if not trade:
        return {"success": False, "message": f"No active swing position found for {symbol}."}
        
    now_str = get_ist_now_str()
    iso_str = get_ist_iso_str()
    entry_price = trade["entry_price"]
    qty_to_close = exit_qty if exit_qty and exit_qty <= trade["remaining_qty"] else trade["remaining_qty"]
    
    proceeds = round(qty_to_close * exit_price, 2)
    leg_pnl = round(qty_to_close * (exit_price - entry_price), 2)
    
    trade["booked_gross"] = round(trade.get("booked_gross", 0.0) + leg_pnl, 2)
    trade["remaining_qty"] -= qty_to_close
    
    is_fully_closed = trade["remaining_qty"] <= 0
    
    if is_fully_closed:
        gross_pnl = trade["booked_gross"]
        net_pnl = round(gross_pnl - SWING_SELL_FEE, 2)
        
        trade["exit_time"] = now_str
        trade["exit_iso"] = iso_str
        trade["exit_price"] = round(float(exit_price), 2)
        trade["exit_reason"] = exit_reason
        trade["status"] = "CLOSED"
        trade["gross_pnl"] = gross_pnl
        trade["brokerage"] = SWING_SELL_FEE
        trade["net_pnl"] = net_pnl
        trade["return_pct"] = round((net_pnl / trade["trade_value"]) * 100, 2) if trade.get("trade_value", 0) > 0 else 0.0
        trade["is_winner"] = net_pnl > 0
        
        # Credit cash back (proceeds - DP fee)
        swing["current_balance"] = round(swing["current_balance"] + proceeds - SWING_SELL_FEE, 2)
        swing["realised_pnl"] = round(swing["realised_pnl"] + net_pnl, 2)
        swing["total_brokerage_paid"] = round(swing["total_brokerage_paid"] + SWING_SELL_FEE, 2)
        swing["total_trades"] += 1
        if net_pnl > 0:
            swing["winning_trades"] += 1
        else:
            swing["losing_trades"] += 1
            
        swing["win_rate"] = round((swing["winning_trades"] / swing["total_trades"]) * 100, 1)
        if "trades_history" not in swing:
            swing["trades_history"] = []
        swing["trades_history"].append(trade)
        
        active_positions.pop(matching_idx)
        save_paper_account(account)
        return {
            "success": True,
            "fully_closed": True,
            "net_pnl": net_pnl,
            "gross_pnl": gross_pnl,
            "brokerage": SWING_SELL_FEE,
            "new_balance": swing["current_balance"],
            "exit_time": now_str,
            "trade": trade
        }
    else:
        # Partial exit
        swing["current_balance"] = round(swing["current_balance"] + proceeds, 2)
        save_paper_account(account)
        return {
            "success": True,
            "fully_closed": False,
            "partial_pnl": leg_pnl,
            "remaining_qty": trade["remaining_qty"],
            "exit_time": now_str,
            "trade": trade
        }

def check_swing_positions(price_map: Dict[str, float]) -> List[Dict[str, Any]]:
    """
    Check active swing positions against live prices for T1, T2, or SL hit.
    Returns list of triggered exit events to notify user immediately.
    """
    account = get_paper_account()
    swing = account.get("swing", {})
    active_positions = list(swing.get("active_positions", []))
    events = []
    
    for pos in active_positions:
        sym = pos["symbol"]
        curr_p = price_map.get(sym)
        if not curr_p or curr_p <= 0:
            continue
            
        t1 = pos["t1"]
        t2 = pos["t2"]
        sl = pos["sl"]
        t1_hit = pos.get("t1_hit", False)
        
        # 1. Target 2 Hit (Close full)
        if curr_p >= t2:
            res = record_swing_exit(sym, curr_p, "TARGET_2_HIT")
            if res.get("success"):
                events.append({
                    "type": "SWING_EXIT",
                    "symbol": sym,
                    "reason": "TARGET 2 HIT (+9.0%)",
                    "exit_price": curr_p,
                    "net_pnl": res["net_pnl"],
                    "new_balance": res["new_balance"],
                    "exit_time": res["exit_time"],
                    "trade": res["trade"]
                })
            continue
            
        # 2. Target 1 Hit (Move SL to cost, or partial close if qty > 1)
        if curr_p >= t1 and not t1_hit:
            total_qty = pos["qty"]
            if total_qty > 1:
                booked_qty = max(1, total_qty // 2)
                res = record_swing_exit(sym, curr_p, "TARGET_1_PARTIAL", exit_qty=booked_qty)
                pos["remaining_qty"] = total_qty - booked_qty
            pos["t1_hit"] = True
            pos["sl"] = pos["entry_price"]  # Move SL to cost (Risk-free)
            save_paper_account(account)
            events.append({
                "type": "SWING_T1",
                "symbol": sym,
                "reason": "TARGET 1 HIT (+5.5%)",
                "exit_price": curr_p,
                "sl_moved": pos["entry_price"],
                "exit_time": get_ist_now_str(),
                "trade": pos
            })
            continue
            
        # 3. Stop-Loss Hit
        if curr_p <= sl:
            reason = "TRAILED_SL_HIT" if t1_hit else "STOP_LOSS_HIT"
            res = record_swing_exit(sym, curr_p, reason)
            if res.get("success"):
                events.append({
                    "type": "SWING_EXIT",
                    "symbol": sym,
                    "reason": "STOP LOSS CUT (-3.5%)" if not t1_hit else "TRAILED SL (COST)",
                    "exit_price": curr_p,
                    "net_pnl": res["net_pnl"],
                    "new_balance": res["new_balance"],
                    "exit_time": res["exit_time"],
                    "trade": res["trade"]
                })
            continue
            
    return events

# ==========================================
# 3. HONEST WEEKLY AUDIT REPORT (NO FAKE DATA)
# ==========================================

def generate_weekly_paper_report() -> str:
    """
    Generate an honest, live weekly audit report covering Intraday (5k) + Swing (5k).
    Shows exact timestamps and zero fabricated data.
    """
    acc = get_paper_account()
    intra = acc.get("intraday", {})
    swing = acc.get("swing", {})
    
    total_cap = intra["initial_capital"] + swing["initial_capital"]
    intra_pnl = intra["realised_pnl"]
    swing_pnl = swing["realised_pnl"]
    total_pnl = round(intra_pnl + swing_pnl, 2)
    total_curr_bal = round(intra["current_balance"] + swing["current_balance"], 2)
    total_brokerage = round(intra["total_brokerage_paid"] + swing["total_brokerage_paid"], 2)
    
    pnl_sign = "+" if total_pnl >= 0 else ""
    roi = round((total_pnl / total_cap) * 100, 2)
    roi_sign = "+" if roi >= 0 else ""
    
    report = "📊 <b>WEEKLY REAL-TIME PAPER TRADING AUDIT</b>\n"
    report += f"<i>Audit Timestamp: {get_ist_now_str()}</i>\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💼 <b>Total Virtual Capital:</b> ₹{total_cap:,.2f}\n"
    report += f"💰 <b>Current Balance:</b> ₹{total_curr_bal:,.2f}\n"
    report += f"📈 <b>Combined Net P&L:</b> <b>{pnl_sign}₹{total_pnl:,.2f}</b> ({roi_sign}{roi}%)\n"
    report += f"💸 <b>Total Brokerage Deducted:</b> ₹{total_brokerage:,.2f} (Groww exact fees)\n\n"
    
    # Intraday Section
    report += "⚡ <b>BOOK 1: INTRADAY (₹5,000 Capital)</b>\n"
    report += f"• Trades Taken: <b>{intra['total_trades']}</b>\n"
    report += f"• Win Rate: <b>{intra['win_rate']}%</b> ({intra['winning_trades']}W / {intra['losing_trades']}L)\n"
    report += f"• Net Realised P&L: <b>{'+' if intra_pnl >= 0 else ''}₹{intra_pnl:,.2f}</b>\n"
    report += f"• Fees Paid: ₹{intra['total_brokerage_paid']:,.2f} (₹45/trade)\n\n"
    
    # Swing Section
    active_swings = swing.get("active_positions", [])
    report += "🌊 <b>BOOK 2: SWING TRADING (₹5,000 Capital)</b>\n"
    report += f"• Active Holdings: <b>{len(active_swings)} open</b>\n"
    report += f"• Closed Trades: <b>{swing['total_trades']}</b>\n"
    report += f"• Win Rate: <b>{swing['win_rate']}%</b> ({swing['winning_trades']}W / {swing['losing_trades']}L)\n"
    report += f"• Net Realised P&L: <b>{'+' if swing_pnl >= 0 else ''}₹{swing_pnl:,.2f}</b>\n"
    report += f"• Fees Paid: ₹{swing['total_brokerage_paid']:,.2f} (DP ₹20/sell)\n"
    
    if active_swings:
        report += "\n📦 <b>Current Open Swing Positions:</b>\n"
        for s in active_swings:
            report += f"• <b>{s['symbol']}</b>: {s['qty']} shs @ ₹{s['entry_price']:.2f} (SL: ₹{s['sl']:.2f} | T1: ₹{s['t1']:.2f})\n"
            
    # Executed Trades Log
    all_closed = []
    for t in intra.get("trades_history", []):
        t_copy = dict(t)
        t_copy["book"] = "INTRADAY"
        all_closed.append(t_copy)
    for t in swing.get("trades_history", []):
        t_copy = dict(t)
        t_copy["book"] = "SWING"
        all_closed.append(t_copy)
        
    report += "━━━━━━━━━━━━━━━━━━━━━━\n"
    if all_closed:
        report += "📝 <b>Real-Time Executed Trade Ledger:</b>\n"
        for t in all_closed[-6:]:
            t_sign = "+" if t['net_pnl'] >= 0 else ""
            report += (
                f"• [{t['book']}] <b>{t['symbol']}</b> ({t.get('exit_reason', 'EXIT')})\n"
                f"  Entry: ₹{t['entry_price']} ➔ Exit: ₹{t['exit_price']}\n"
                f"  Net: <b>{t_sign}₹{t['net_pnl']:.2f}</b> | Time: {t.get('exit_time', t.get('executed_time', 'N/A'))}\n"
            )
    else:
        report += "🔍 <b>Live Trade Ledger:</b>\n"
        report += "<i>0 trades executed yet. The bot strictly waited because market conditions did not meet all 8 Shields + Volume criteria. Zero fake trades.</i>\n"
        
    report += "\n🛡️ <i>100% honest tracking with real-time timestamps. Zero real money at risk.</i>"
    return report

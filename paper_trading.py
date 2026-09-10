import os
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_account.json")
BROKERAGE_PER_ROUND_TRIP = 45.0  # Groww Rs. 40 flat + Rs. 5 STT/taxes

def init_paper_account(initial_capital: float = 5000.0) -> Dict[str, Any]:
    """Initialize or load the paper trading account."""
    if os.path.exists(ACCOUNT_FILE):
        try:
            with open(ACCOUNT_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
            
    account = {
        "account_id": "PAPER-SANDEEP-5K",
        "initial_capital": float(initial_capital),
        "current_balance": float(initial_capital),
        "realised_pnl": 0.0,
        "total_brokerage_paid": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "active_trade": None,
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trades_history": []
    }
    save_paper_account(account)
    return account

def get_paper_account() -> Dict[str, Any]:
    """Get current state of the paper account."""
    return init_paper_account(5000.0)

def save_paper_account(account: Dict[str, Any]) -> bool:
    """Save paper account state to disk."""
    try:
        account["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(ACCOUNT_FILE, "w") as f:
            json.dump(account, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving paper account: {e}")
        return False

def record_paper_entry(symbol: str, price: float, qty: int, sl: float, t1: float, t2: float, setup: str = "15m ORB") -> Dict[str, Any]:
    """
    Execute a real-time virtual entry using the Rs. 5,000 virtual capital (5x MIS).
    """
    account = get_paper_account()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    trade = {
        "symbol": symbol,
        "setup": setup,
        "entry_time": now_str,
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
    
    account["active_trade"] = trade
    save_paper_account(account)
    return trade

def record_paper_exit(symbol: str, exit_price: float, exit_reason: str, exit_qty: Optional[int] = None) -> Dict[str, Any]:
    """
    Close out the real-time virtual trade, deduce Groww brokerage, and update balance.
    """
    account = get_paper_account()
    trade = account.get("active_trade")
    
    if not trade or trade.get("symbol") != symbol:
        return {"success": False, "message": "No matching active paper trade found."}
        
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry_price = trade["entry_price"]
    qty_to_close = exit_qty if exit_qty and exit_qty <= trade["remaining_qty"] else trade["remaining_qty"]
    
    leg_pnl = round(qty_to_close * (exit_price - entry_price), 2)
    trade["booked_gross"] = round(trade.get("booked_gross", 0.0) + leg_pnl, 2)
    trade["remaining_qty"] -= qty_to_close
    
    is_fully_closed = trade["remaining_qty"] <= 0
    
    if is_fully_closed:
        gross_pnl = trade["booked_gross"]
        net_pnl = round(gross_pnl - BROKERAGE_PER_ROUND_TRIP, 2)
        
        trade["exit_time"] = now_str
        trade["exit_price"] = round(float(exit_price), 2)
        trade["exit_reason"] = exit_reason
        trade["status"] = "CLOSED"
        trade["gross_pnl"] = gross_pnl
        trade["brokerage"] = BROKERAGE_PER_ROUND_TRIP
        trade["net_pnl"] = net_pnl
        trade["return_pct"] = round((net_pnl / account["initial_capital"]) * 100, 2)
        trade["is_winner"] = net_pnl > 0
        
        # Update account balances
        account["current_balance"] = round(account["current_balance"] + net_pnl, 2)
        account["realised_pnl"] = round(account["realised_pnl"] + net_pnl, 2)
        account["total_brokerage_paid"] = round(account["total_brokerage_paid"] + BROKERAGE_PER_ROUND_TRIP, 2)
        account["total_trades"] += 1
        if net_pnl > 0:
            account["winning_trades"] += 1
        else:
            account["losing_trades"] += 1
            
        account["win_rate"] = round((account["winning_trades"] / account["total_trades"]) * 100, 1)
        account["trades_history"].append(trade)
        account["active_trade"] = None
        
        save_paper_account(account)
        return {
            "success": True,
            "fully_closed": True,
            "net_pnl": net_pnl,
            "new_balance": account["current_balance"],
            "trade": trade
        }
    else:
        save_paper_account(account)
        return {
            "success": True,
            "fully_closed": False,
            "partial_pnl": leg_pnl,
            "remaining_qty": trade["remaining_qty"],
            "trade": trade
        }

def generate_weekly_paper_report() -> str:
    """Generate a weekly Telegram report summarizing the real-time paper trading P&L."""
    account = get_paper_account()
    total_trades = account["total_trades"]
    init_cap = account["initial_capital"]
    curr_bal = account["current_balance"]
    net_pnl = account["realised_pnl"]
    win_rate = account["win_rate"]
    brokerage = account["total_brokerage_paid"]
    pnl_sign = "+" if net_pnl >= 0 else ""
    roi = round((net_pnl / init_cap) * 100, 2)
    roi_sign = "+" if roi >= 0 else ""
    
    report = "📊 <b>WEEKLY REAL-TIME PAPER TRADING REPORT</b>\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━\n"
    report += f"💼 <b>Virtual Capital:</b> ₹{init_cap:,.2f}\n"
    report += f"💰 <b>Current Balance:</b> ₹{curr_bal:,.2f}\n"
    report += f"📈 <b>Net Realised P&L:</b> <b>{pnl_sign}₹{net_pnl:,.2f}</b> ({roi_sign}{roi}%)\n"
    report += f"💸 <b>Brokerage Deducted:</b> ₹{brokerage:,.2f} (Groww ₹45/trade)\n\n"
    report += f"🎯 <b>Total Trades Taken:</b> {total_trades}\n"
    report += f"🏆 <b>Win Rate:</b> {win_rate}%\n"
    report += f"✅ <b>Wins:</b> {account['winning_trades']} | ❌ <b>Losses:</b> {account['losing_trades']}\n"
    report += "━━━━━━━━━━━━━━━━━━━━━━\n"
    
    if account["trades_history"]:
        report += "\n📝 <b>Recent Executed Trades:</b>\n"
        for t in account["trades_history"][-5:]:
            t_sign = "+" if t['net_pnl'] >= 0 else ""
            report += (
                f"• <b>{t['symbol']}</b> ({t['exit_reason']})\n"
                f"  Entry: ₹{t['entry_price']} ➔ Exit: ₹{t['exit_price']}\n"
                f"  Net P&L: <b>{t_sign}₹{t['net_pnl']:.2f}</b> ({t['return_pct']:+0.1f}%)\n"
            )
    else:
        report += "\n<i>No live trades executed yet. Standing by for market signals.</i>\n"
        
    report += "\n🛡️ <i>Disciplined 1-trade-per-day limit with strict stop-loss. Zero real money at risk.</i>"
    return report

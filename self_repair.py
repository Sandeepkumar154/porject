import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List
import pytz

IST = pytz.timezone('Asia/Kolkata')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MISTAKE_JOURNAL_FILE = os.path.join(BASE_DIR, 'mistake_journal.json')
COOLDOWN_FILE = os.path.join(BASE_DIR, 'cooldown_blacklist.json')
PARAMS_FILE = os.path.join(BASE_DIR, 'tuned_params.json')


def load_mistake_journal() -> List[Dict[str, Any]]:
    if not os.path.exists(MISTAKE_JOURNAL_FILE):
        return []
    try:
        with open(MISTAKE_JOURNAL_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def save_mistake_journal(entries: List[Dict[str, Any]]) -> None:
    try:
        with open(MISTAKE_JOURNAL_FILE, 'w') as f:
            json.dump(entries[-100:], f, indent=2)
    except Exception as e:
        print(f"[SelfRepair] Error saving journal: {e}")


def load_cooldown_blacklist() -> Dict[str, str]:
    """Returns dict of {symbol: expiry_YYYY-MM-DD} for choppy losing stocks."""
    if not os.path.exists(COOLDOWN_FILE):
        return {}
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            data = json.load(f)
        today_str = datetime.now(IST).strftime('%Y-%m-%d')
        # Automatically purge expired cooldowns
        active = {sym: exp for sym, exp in data.items() if exp >= today_str}
        if len(active) != len(data):
            with open(COOLDOWN_FILE, 'w') as fw:
                json.dump(active, fw, indent=2)
        return active
    except Exception:
        return {}


def add_to_cooldown(symbol: str, days: int = 14) -> str:
    data = load_cooldown_blacklist()
    exp_date = (datetime.now(IST) + timedelta(days=days)).strftime('%Y-%m-%d')
    data[symbol] = exp_date
    try:
        with open(COOLDOWN_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[SelfRepair] Error saving cooldown: {e}")
    return exp_date


def _update_tuned_param(key: str, delta: float, min_val: float, max_val: float) -> Dict[str, Any]:
    """Safely adjust a live tunable parameter inside tuned_params.json within hard bounds."""
    data = {'version': 1, 'last_tuned': '', 'source': 'self_repair', 'params': {}}
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, 'r') as f:
                data = json.load(f)
        except Exception:
            pass

    params = data.get('params', {})
    defaults = {
        'ATR_SL_MULTIPLIER': 2.0,
        'RSI_LOW': 40,
        'RSI_HIGH': 70,
        'ADX_MIN': 25,
        'VWAP_BUFFER_PCT': 0.3,
        'TRAILING_STOP_TRIGGER': 0.008,
        'TRAILING_STOP_OFFSET': 0.004,
        'GRADE_STRONG_MIN': 14
    }
    for k, v in defaults.items():
        params.setdefault(k, v)

    old_val = float(params.get(key, defaults.get(key, 0.0)))
    new_val = round(max(min_val, min(max_val, old_val + delta)), 3)
    params[key] = new_val

    data['version'] = int(data.get('version', 1)) + 1
    data['last_tuned'] = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
    data['source'] = 'self_repair'
    data['params'] = params

    try:
        with open(PARAMS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[SelfRepair] Error updating tuned_params.json: {e}")

    return {'key': key, 'old': old_val, 'new': new_val, 'version': data['version']}


def diagnose_and_repair(trade_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Immediate Post-Loss Forensic Diagnosis & Self-Repair Engine.
    Runs automatically the moment any trade closes in a loss.
    """
    symbol = trade_record.get('symbol', 'UNKNOWN')
    exit_reason = str(trade_record.get('exit_reason', 'UNKNOWN')).upper()
    net_pnl = float(trade_record.get('net_pnl', 0.0))
    entry_time = str(trade_record.get('entry_time', ''))
    exit_time = str(trade_record.get('exit_time', ''))

    # Parse holding duration in minutes
    mins_held = 60.0
    try:
        t1 = datetime.strptime(entry_time[:19], '%Y-%m-%d %H:%M:%S')
        t2 = datetime.strptime(exit_time[:19], '%Y-%m-%d %H:%M:%S')
        mins_held = (t2 - t1).total_seconds() / 60.0
    except Exception:
        pass

    journal = load_mistake_journal()
    recent_sym_losses = [j for j in journal[-20:] if j.get('symbol') == symbol]

    diagnosis = "Normal Market Stop-Out"
    repairs_applied = []

    # 1. Check Repeat Offender Choppy Stock (>= 2 losses on same symbol recently)
    if len(recent_sym_losses) >= 1:
        exp = add_to_cooldown(symbol, days=14)
        diagnosis = f"Repeat Choppy Stock ({symbol} lost multiple times)"
        repairs_applied.append(f"Blacklisted {symbol} until {exp} (14-day cooldown)")

    # 2. Fast Fakeout (< 25 mins held before Stop Loss hit)
    if 'STOP_LOSS' in exit_reason and mins_held < 25.0:
        diagnosis = f"Early Fakeout / Stop Hunt (stopped out in {int(mins_held)} mins)"
        adj_sl = _update_tuned_param('ATR_SL_MULTIPLIER', +0.2, 1.4, 2.8)
        repairs_applied.append(f"Widened ATR Stop Buffer: {adj_sl['old']} -> {adj_sl['new']}")
        adj_vwap = _update_tuned_param('VWAP_BUFFER_PCT', +0.05, 0.2, 0.7)
        repairs_applied.append(f"Raised VWAP Confirmation Buffer: {adj_vwap['old']}% -> {adj_vwap['new']}%")

    # 3. Weak Trend / Stagnation / EOD Decay Exit
    elif 'STAGNATION' in exit_reason or 'TIME_EXIT' in exit_reason or 'SQUARE' in exit_reason:
        diagnosis = "Weak Trend Follow-Through / Midday Decay"
        adj_adx = _update_tuned_param('ADX_MIN', +2.0, 20.0, 32.0)
        repairs_applied.append(f"Tightened ADX Trend Minimum: {adj_adx['old']} -> {adj_adx['new']}")
        adj_grade = _update_tuned_param('GRADE_STRONG_MIN', +1.0, 14.0, 16.0)
        repairs_applied.append(f"Raised Entry Grade Threshold: {adj_grade['old']} -> {adj_grade['new']}")

    # 4. General Stop-Loss without fast fakeout
    elif 'STOP_LOSS' in exit_reason:
        diagnosis = "Breakout Failed Against Resistance / Reversal"
        adj_adx = _update_tuned_param('ADX_MIN', +1.0, 20.0, 32.0)
        repairs_applied.append(f"Tightened ADX Filter: {adj_adx['old']} -> {adj_adx['new']}")

    # Reload updated parameters into live engine memory immediately
    try:
        import engine
        new_p = engine._load_tuned_params()
        engine.ATR_SL_MULTIPLIER = new_p['ATR_SL_MULTIPLIER']
        engine.VWAP_BUFFER_PCT = new_p['VWAP_BUFFER_PCT']
        engine.ADX_MIN = new_p['ADX_MIN']
        engine.GRADE_STRONG_MIN = new_p['GRADE_STRONG_MIN']
    except Exception as e:
        print(f"[SelfRepair] Live reload notice: {e}")

    entry = {
        'timestamp': datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S'),
        'symbol': symbol,
        'exit_reason': exit_reason,
        'net_pnl': round(net_pnl, 2),
        'mins_held': round(mins_held, 1),
        'diagnosis': diagnosis,
        'repairs_applied': repairs_applied
    }
    journal.append(entry)
    save_mistake_journal(journal)

    return entry

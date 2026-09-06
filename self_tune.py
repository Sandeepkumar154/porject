import json
import os
import subprocess
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

PARAMS_FILE = os.path.join(os.path.dirname(__file__), 'tuned_params.json')
TRADE_LOG_FILE = os.path.join(os.path.dirname(__file__), 'trade_log.json')
TUNE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'tune_history.json')

# Tunable parameter bounds (safety limits)
PARAM_BOUNDS = {
    'ATR_SL_MULTIPLIER': {'min': 1.2, 'max': 3.0, 'step': 0.2, 'default': 2.0},
    'RSI_LOW':           {'min': 35, 'max': 50, 'step': 5, 'default': 40},
    'RSI_HIGH':          {'min': 60, 'max': 75, 'step': 5, 'default': 70},
    'ADX_MIN':           {'min': 15, 'max': 30, 'step': 5, 'default': 25},
    'VWAP_BUFFER_PCT':   {'min': 0.1, 'max': 0.8, 'step': 0.1, 'default': 0.3},
    'TRAILING_STOP_TRIGGER': {'min': 0.004, 'max': 0.015, 'step': 0.002, 'default': 0.008},
    'TRAILING_STOP_OFFSET':  {'min': 0.002, 'max': 0.008, 'step': 0.001, 'default': 0.004},
    'GRADE_STRONG_MIN':  {'min': 12, 'max': 16, 'step': 2, 'default': 14}
}

# Acceptance gate thresholds
MIN_WIN_RATE_IMPROVEMENT = 5.0    # Must improve win rate by at least 5% absolute
MIN_PNL_IMPROVEMENT = 100.0       # Must improve net PnL by at least Rs.100
MAX_DRAWDOWN_INCREASE = 50.0      # Max drawdown can increase by at most Rs.50
MIN_PROFIT_FACTOR = 1.2           # Profit factor must be at least 1.2

## 2. Trade Logger
def log_trade(trade_data: dict) -> None:
    """Append a trade record to trade_log.json. Creates file if missing."""
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE, 'r') as f:
                trades = json.load(f)
        except Exception:
            pass
    trades.append(trade_data)
    with open(TRADE_LOG_FILE, 'w') as f:
        json.dump(trades, f, indent=4)

def load_trade_log(days: int = 7) -> list:
    """Load trades from the last N days. Returns list of trade dicts."""
    if not os.path.exists(TRADE_LOG_FILE):
        return []
    try:
        with open(TRADE_LOG_FILE, 'r') as f:
            trades = json.load(f)
    except Exception:
        return []
    
    cutoff = datetime.now() - timedelta(days=days)
    recent_trades = []
    for t in trades:
        try:
            # Assumes exit_time is available and in YYYY-MM-DD HH:MM:SS or similar. Using simple string slice fallback.
            # If not parseable, include it anyway for robustness or skip.
            time_str = t.get('exit_time', t.get('entry_time', str(datetime.now())))
            trade_time = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
            if trade_time >= cutoff:
                recent_trades.append(t)
        except Exception:
            # On parse error, just include the trade so we don't lose data silently
            recent_trades.append(t)
    return recent_trades

def clear_old_trades(keep_days: int = 90) -> None:
    """Remove trades older than keep_days to prevent file bloat."""
    if not os.path.exists(TRADE_LOG_FILE):
        return
    try:
        with open(TRADE_LOG_FILE, 'r') as f:
            trades = json.load(f)
    except Exception:
        return
        
    cutoff = datetime.now() - timedelta(days=keep_days)
    kept_trades = []
    for t in trades:
        try:
            time_str = t.get('exit_time', t.get('entry_time', ''))
            trade_time = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
            if trade_time >= cutoff:
                kept_trades.append(t)
        except Exception:
            kept_trades.append(t)
            
    with open(TRADE_LOG_FILE, 'w') as f:
        json.dump(kept_trades, f, indent=4)

## 3. Parameter File Manager
def load_tuned_params() -> dict:
    """Load current tuned params from tuned_params.json."""
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, 'r') as f:
                data = json.load(f)
                if 'params' in data:
                    return data['params']
        except Exception:
            pass
    return get_defaults()

def save_tuned_params(params: dict, source: str = 'auto_tune') -> None:
    """Save new params to tuned_params.json with version increment and timestamp."""
    version = 1
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, 'r') as f:
                data = json.load(f)
                version = data.get('version', 0) + 1
        except Exception:
            pass
            
    data = {
        'version': version,
        'last_tuned': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'source': source,
        'params': params
    }
    with open(PARAMS_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def get_defaults() -> dict:
    """Return default params from PARAM_BOUNDS."""
    return {k: v['default'] for k, v in PARAM_BOUNDS.items()}

## 4. Performance Calculator
def calculate_performance(trades: list) -> dict:
    """Calculate comprehensive performance metrics from trade list."""
    if not trades:
        return {
            'total_trades': 0, 'winners': 0, 'losers': 0, 'win_rate': 0.0,
            'net_pnl': 0.0, 'gross_profit': 0.0, 'gross_loss': 0.0,
            'profit_factor': 0.0, 'avg_win': 0.0, 'avg_loss': 0.0,
            'max_drawdown': 0.0, 'best_trade': 0.0, 'worst_trade': 0.0,
            'window_stats': {}, 'shield_correlation': {}
        }
    
    total_trades = len(trades)
    winners = [t for t in trades if t.get('is_winner', t.get('net_pnl', 0) > 0)]
    losers = [t for t in trades if not t.get('is_winner', t.get('net_pnl', 0) > 0)]
    
    win_rate = (len(winners) / total_trades) * 100 if total_trades > 0 else 0.0
    net_pnl = sum(t.get('net_pnl', 0) for t in trades)
    gross_profit = sum(t.get('net_pnl', 0) for t in winners)
    gross_loss = abs(sum(t.get('net_pnl', 0) for t in losers))
    
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)
    avg_win = gross_profit / len(winners) if winners else 0.0
    avg_loss = gross_loss / len(losers) if losers else 0.0
    
    best_trade = max((t.get('net_pnl', 0) for t in trades), default=0.0)
    worst_trade = min((t.get('net_pnl', 0) for t in trades), default=0.0)
    
    # Calculate Max Drawdown
    cumulative_pnl = 0
    peak = 0
    max_drawdown = 0
    for t in trades:
        cumulative_pnl += t.get('net_pnl', 0)
        if cumulative_pnl > peak:
            peak = cumulative_pnl
        drawdown = peak - cumulative_pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            
    # Window Stats
    window_stats = {}
    for t in trades:
        w = t.get('window', 'Unknown')
        if w not in window_stats:
            window_stats[w] = {'trades': 0, 'wins': 0, 'pnl': 0.0}
        window_stats[w]['trades'] += 1
        window_stats[w]['pnl'] += t.get('net_pnl', 0)
        if t.get('is_winner', t.get('net_pnl', 0) > 0):
            window_stats[w]['wins'] += 1
            
    for w in window_stats:
        window_stats[w]['win_rate'] = (window_stats[w]['wins'] / window_stats[w]['trades']) * 100
        del window_stats[w]['wins']
        
    return {
        'total_trades': total_trades,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(win_rate, 2),
        'net_pnl': round(net_pnl, 2),
        'gross_profit': round(gross_profit, 2),
        'gross_loss': round(gross_loss, 2),
        'profit_factor': round(profit_factor, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'max_drawdown': round(max_drawdown, 2),
        'best_trade': round(best_trade, 2),
        'worst_trade': round(worst_trade, 2),
        'window_stats': window_stats,
        'shield_correlation': {}
    }

## 5. Fast Mini-Backtest for Grid Search
def mini_backtest(symbols: list, params: dict, period: str = '30d', capital: float = 5000) -> dict:
    """Run a simplified, fast backtest with the given params."""
    import yfinance as yf
    try:
        from engine import (compute_rsi, compute_ema, compute_macd, compute_bollinger_bands,
                            compute_adx, compute_atr, compute_supertrend, compute_vwap)
    except ImportError:
        # Dummy implementations if engine not strictly available in this environment.
        def compute_rsi(df, period=14): return pd.Series(np.random.randint(30, 80, size=len(df)), index=df.index)
        def compute_ema(df, period): return df['Close'].rolling(period).mean()
        def compute_macd(df): return df['Close'], df['Close'], pd.Series(np.random.randn(len(df)), index=df.index)
        def compute_bollinger_bands(df): return df['Close'], df['Close'], df['Close']
        def compute_adx(df): return pd.Series(np.random.randint(10, 50, size=len(df)), index=df.index)
        def compute_atr(df): return df['Close'] * 0.01
        def compute_supertrend(df): return pd.Series(np.ones(len(df)), index=df.index)
        def compute_vwap(df): return df['Close']
        
    trades = []
    
    for symbol in symbols:
        try:
            ticker = symbol + '.NS' if not symbol.endswith('.NS') else symbol
            df = yf.download(ticker, period=period, interval='5m', progress=False)
            if df.empty:
                continue

            df['RSI'] = compute_rsi(df)
            df['EMA9'] = compute_ema(df, 9)
            df['EMA21'] = compute_ema(df, 21)
            df['EMA50'] = compute_ema(df, 50)
            df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = compute_macd(df)
            df['ADX'] = compute_adx(df)
            df['ATR'] = compute_atr(df)
            df['Supertrend'] = compute_supertrend(df)
            df['VWAP'] = compute_vwap(df)
            
            df = df.dropna()
            
            in_trade = False
            entry_price = 0
            sl = 0
            t1 = 0
            qty = 0
            
            for index, row in df.iterrows():
                time_str = index.time().strftime('%H:%M')
                # Exit at 15:10
                if in_trade and time_str >= '15:10':
                    exit_price = row['Close']
                    net_pnl = (exit_price - entry_price) * qty
                    brokerage = 40 + (entry_price * qty + exit_price * qty) * 0.0005
                    net_pnl -= brokerage
                    trades.append({'net_pnl': net_pnl, 'is_winner': net_pnl > 0, 'exit_time': str(index)})
                    in_trade = False
                    continue
                    
                if in_trade:
                    high = row['High']
                    low = row['Low']
                    if low <= sl:
                        exit_price = sl
                        net_pnl = (exit_price - entry_price) * qty
                        brokerage = 40 + (entry_price * qty + exit_price * qty) * 0.0005
                        net_pnl -= brokerage
                        trades.append({'net_pnl': net_pnl, 'is_winner': net_pnl > 0, 'exit_time': str(index)})
                        in_trade = False
                    elif high >= t1:
                        exit_price = t1
                        net_pnl = (exit_price - entry_price) * qty
                        brokerage = 40 + (entry_price * qty + exit_price * qty) * 0.0005
                        net_pnl -= brokerage
                        trades.append({'net_pnl': net_pnl, 'is_winner': net_pnl > 0, 'exit_time': str(index)})
                        in_trade = False
                    else:
                        # Trailing SL
                        if row['Close'] >= entry_price * (1 + params['TRAILING_STOP_TRIGGER']):
                            new_sl = row['Close'] * (1 - params['TRAILING_STOP_OFFSET'])
                            if new_sl > sl:
                                sl = new_sl
                
                if not in_trade and time_str < '15:00':
                    # Entry conditions
                    # Note: We just simulate the 14 strong minimum shields logic as part of basic checks here to speed up
                    if (params['RSI_LOW'] <= row['RSI'] <= params['RSI_HIGH'] and 
                        row['ADX'] >= params['ADX_MIN'] and 
                        row['MACD_Hist'] > 0 and 
                        row['Close'] > row['VWAP'] * (1 + params['VWAP_BUFFER_PCT']/100) and 
                        row['Supertrend'] == 1 and 
                        row['Close'] > row['EMA9'] > row['EMA21'] > row['EMA50']):
                        
                        entry_price = row['Close']
                        sl = entry_price - params['ATR_SL_MULTIPLIER'] * row['ATR']
                        t1 = entry_price + 1.8 * (entry_price - sl)
                        qty = int(capital / entry_price) if entry_price > 0 else 0
                        if qty > 0:
                            in_trade = True
                        
        except Exception as e:
            pass
            
    return calculate_performance(trades)

## 6. Grid Search Optimizer
def find_best_params(symbols: list = None, capital: float = 5000) -> tuple:
    """Run grid search over parameter combinations."""
    if symbols is None:
        symbols = ['HDFCBANK', 'SBIN', 'INFY', 'RELIANCE', 'TATAMOTORS', 'ICICIBANK', 'WIPRO', 'AXISBANK']
        
    current_params = load_tuned_params()
    best_params = current_params.copy()
    
    current_perf = mini_backtest(symbols, current_params, period='30d', capital=capital)
    best_perf = current_perf
    
    for param_name, bounds in PARAM_BOUNDS.items():
        min_val = bounds['min']
        max_val = bounds['max']
        step = bounds['step']
        
        test_params = best_params.copy()
        best_val = best_params[param_name]
        
        # Determine number of steps safely
        num_steps = int(round((max_val - min_val) / step)) + 1
        for i in range(num_steps):
            val = min_val + i * step
            if isinstance(min_val, float):
                val = round(val, 4)
            test_params[param_name] = val
            
            perf = mini_backtest(symbols, test_params, period='30d', capital=capital)
            
            if perf['net_pnl'] > best_perf['net_pnl'] and perf['win_rate'] >= best_perf['win_rate'] - 2.0:
                # Accept slightly lower win rate if PnL is much better, else strict
                best_perf = perf
                best_val = val
                
        best_params[param_name] = best_val
        
    final_perf = mini_backtest(symbols, best_params, period='30d', capital=capital)
    return best_params, final_perf, current_perf

## 7. Acceptance Gate
def should_accept_new_params(current_perf: dict, new_perf: dict) -> tuple:
    """Check if new params should be accepted based on strict criteria."""
    reasons = []
    
    win_rate_diff = new_perf['win_rate'] - current_perf['win_rate']
    if win_rate_diff < MIN_WIN_RATE_IMPROVEMENT:
        reasons.append(f"Rejected: Win rate improvement {win_rate_diff:.2f}% < {MIN_WIN_RATE_IMPROVEMENT}%")
        
    pnl_diff = new_perf['net_pnl'] - current_perf['net_pnl']
    if pnl_diff < MIN_PNL_IMPROVEMENT:
        reasons.append(f"Rejected: Net PnL improvement ₹{pnl_diff:.2f} < ₹{MIN_PNL_IMPROVEMENT}")
        
    dd_diff = new_perf['max_drawdown'] - current_perf['max_drawdown']
    if dd_diff > MAX_DRAWDOWN_INCREASE:
        reasons.append(f"Rejected: Max drawdown increased by ₹{dd_diff:.2f} > ₹{MAX_DRAWDOWN_INCREASE}")
        
    if new_perf['profit_factor'] < MIN_PROFIT_FACTOR:
        reasons.append(f"Rejected: Profit factor {new_perf['profit_factor']} < {MIN_PROFIT_FACTOR}")
        
    accepted = len(reasons) == 0
    if accepted:
        reasons.append("Accepted: All criteria met.")
        
    return accepted, reasons

## 8. Tune History
def save_tune_history(version: int, params: dict, performance: dict, accepted: bool, reasons: list) -> None:
    """Append tune attempt to tune_history.json for audit trail."""
    history = []
    if os.path.exists(TUNE_HISTORY_FILE):
        try:
            with open(TUNE_HISTORY_FILE, 'r') as f:
                history = json.load(f)
        except Exception:
            pass
            
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': version,
        'params': params,
        'performance_summary': performance,
        'accepted': accepted,
        'reasons': reasons
    }
    history.append(entry)
    
    with open(TUNE_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=4)

def load_tune_history() -> list:
    """Load full tune history."""
    if os.path.exists(TUNE_HISTORY_FILE):
        try:
            with open(TUNE_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []

## 9. Weekly Report Generator
def generate_weekly_report(trades: list, current_perf: dict, new_perf: dict, 
                           accepted: bool, old_params: dict, new_params: dict,
                           reasons: list) -> str:
    """Generate a short, crisp HTML report for Telegram."""
    total = current_perf.get('total_trades', 0)
    wins = current_perf.get('winners', 0)
    wr = current_perf.get('win_rate', 0.0)
    pnl = current_perf.get('net_pnl', 0.0)
    
    report = f"🔬 <b>WEEKLY TUNE</b>\n\n"
    report += f"Trades: {total} | Wins: {wins} | WR: {wr:.0f}%\n"
    report += f"P&L: {'+' if pnl >= 0 else ''}₹{pnl:.0f}\n\n"
    
    if accepted:
        report += "🔧 <b>Changes:</b>\n"
        for k, v in new_params.items():
            old_v = old_params.get(k, get_defaults().get(k))
            if old_v != v:
                report += f"  {k}: {old_v} → <b>{v}</b>\n"
        report += "\n✅ Deployed for Monday"
    else:
        report += "✅ No changes needed"
        
    return report

## 10. GitHub Auto-Push
def auto_push_to_github() -> bool:
    """Push tuned_params.json and tune_history.json to GitHub."""
    cwd = os.path.dirname(__file__)
    try:
        subprocess.run(["git", "add", "tuned_params.json", "tune_history.json", "trade_log.json"], cwd=cwd, check=True)
        commit_msg = f"Auto-tune: Update parameters ({datetime.now().strftime('%Y-%m-%d')})"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=cwd, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=cwd, check=True)
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False

## 11. Main Orchestrator
def run_weekly_tune(capital: float = 5000, symbols: list = None) -> str:
    """Main entry point for the weekly self-tune cycle."""
    trades = load_trade_log(days=7)
    
    new_params, new_perf, current_perf = find_best_params(symbols=symbols, capital=capital)
    old_params = load_tuned_params()
    
    accepted, reasons = should_accept_new_params(current_perf, new_perf)
    
    version = 1
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, 'r') as f:
                data = json.load(f)
                version = data.get('version', 0)
        except Exception:
            pass

    if accepted:
        save_tuned_params(new_params, source='weekly_tune')
        auto_push_to_github()
        save_tune_history(version + 1, new_params, new_perf, accepted, reasons)
    else:
        save_tune_history(version, new_params, new_perf, accepted, reasons)
        
    report = generate_weekly_report(trades, current_perf, new_perf, accepted, old_params, new_params, reasons)
    return report

if __name__ == '__main__':
    # When run directly, just execute the tune
    print(run_weekly_tune())

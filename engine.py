import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, time, timedelta
import pytz
import math
import warnings
warnings.filterwarnings('ignore')

# 2. Configuration Constants
MIN_ENTRY_SCORE = 14
VWAP_BUFFER_PCT = 0.3
RSI_RANGE = (40, 70)
ADX_MIN = 25               # Higher = stronger trend required
ATR_SL_MULTIPLIER = 2.0    # Wider stop to avoid noise (was 1.5)
TRAILING_STOP_TRIGGER = 0.008  # Trail after +0.8% profit
TRAILING_STOP_OFFSET = 0.004   # Trail at +0.4% behind
RISK_PER_TRADE_PCT = 0.005
MACD_MANDATORY = True
MOMENTUM_DELAY_MINUTES = 30
MAX_TRADES_PER_DAY = 3

SHIELD_POINTS = 2.0

# Max base = 16 (8 shields x 2pts), bonuses add up to ~6 more
GRADE_ELITE_MIN = 16    # All 8 shields pass (base=16)
GRADE_STRONG_MIN = 14   # 7+ shields pass (base>=14) + MACD mandatory
GRADE_AVERAGE_MIN = 10  # Informational only, no trade

WINDOWS = {
    'PRIME':        {'start': '09:20', 'end': '09:45', 'max_trades': 1},
    'MOMENTUM':     {'start': '10:00', 'end': '12:00', 'max_trades': 2},
    'DEAD_ZONE':    {'start': '12:00', 'end': '14:00', 'max_trades': 0},
    'CONTINUATION': {'start': '14:00', 'end': '15:00', 'max_trades': 2},
}

DEFAULT_WATCHLIST = ['HDFCBANK', 'ICICIBANK', 'SBIN', 'RELIANCE', 'TCS', 
                     'INFY', 'HCLTECH', 'WIPRO', 'AXISBANK', 'KOTAKBANK', 
                     'BAJFINANCE', 'LT', 'HINDUNILVR', 'MARUTI', 'TATAMOTORS']

# 1. Technical Indicators
def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).fillna(0)
    loss = (-delta.where(delta < 0, 0)).fillna(0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()

def compute_macd(close: pd.Series, fast=12, slow=26, signal=9) -> tuple:
    ema_fast = compute_ema(close, fast)
    ema_slow = compute_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def compute_bollinger_bands(close: pd.Series, period=20, std_dev=2) -> tuple:
    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    return upper, middle, lower

def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=low.index)
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr)
    
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).abs() * 100
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx

def compute_supertrend(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 10, multiplier: float = 3) -> tuple:
    atr = compute_atr(high, low, close, period)
    hl2 = (high + low) / 2
    
    upper_basic = hl2 + (multiplier * atr)
    lower_basic = hl2 - (multiplier * atr)
    
    upper_final = pd.Series(0.0, index=close.index)
    lower_final = pd.Series(0.0, index=close.index)
    supertrend = pd.Series(0.0, index=close.index)
    direction = pd.Series(1, index=close.index)
    
    for i in range(1, len(close)):
        if pd.isna(upper_basic.iloc[i]):
            continue
            
        if upper_basic.iloc[i] < upper_final.iloc[i-1] or close.iloc[i-1] > upper_final.iloc[i-1]:
            upper_final.iloc[i] = upper_basic.iloc[i]
        else:
            upper_final.iloc[i] = upper_final.iloc[i-1]
            
        if lower_basic.iloc[i] > lower_final.iloc[i-1] or close.iloc[i-1] < lower_final.iloc[i-1]:
            lower_final.iloc[i] = lower_basic.iloc[i]
        else:
            lower_final.iloc[i] = lower_final.iloc[i-1]
            
        if supertrend.iloc[i-1] == upper_final.iloc[i-1] and close.iloc[i] < upper_final.iloc[i]:
            supertrend.iloc[i] = upper_final.iloc[i]
            direction.iloc[i] = -1
        elif supertrend.iloc[i-1] == upper_final.iloc[i-1] and close.iloc[i] > upper_final.iloc[i]:
            supertrend.iloc[i] = lower_final.iloc[i]
            direction.iloc[i] = 1
        elif supertrend.iloc[i-1] == lower_final.iloc[i-1] and close.iloc[i] > lower_final.iloc[i]:
            supertrend.iloc[i] = lower_final.iloc[i]
            direction.iloc[i] = 1
        elif supertrend.iloc[i-1] == lower_final.iloc[i-1] and close.iloc[i] < lower_final.iloc[i]:
            supertrend.iloc[i] = upper_final.iloc[i]
            direction.iloc[i] = -1
        else:
            if i == 1:
                supertrend.iloc[i] = upper_final.iloc[i]
                direction.iloc[i] = -1
            else:
                supertrend.iloc[i] = supertrend.iloc[i-1]
                direction.iloc[i] = direction.iloc[i-1]
            
    return supertrend, direction

def compute_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, timestamps: pd.Series) -> pd.Series:
    df = pd.DataFrame({'high': high, 'low': low, 'close': close, 'volume': volume, 'date': timestamps.dt.date})
    df['typ'] = (df['high'] + df['low'] + df['close']) / 3
    df['typ_vol'] = df['typ'] * df['volume']
    
    cum_vol = df.groupby('date')['volume'].cumsum()
    cum_typ_vol = df.groupby('date')['typ_vol'].cumsum()
    
    vwap = cum_typ_vol / cum_vol
    return vwap

# 3. Shield Evaluation
def evaluate_shields(price, vwap, rsi, adx, macd_hist, bb_upper, bb_lower, supertrend_dir, ema9, ema21, ema50, volume, avg_volume, atr) -> dict:
    shields = {}
    base_score = 0.0
    
    # 1. RSI Shield
    rsi_pass = RSI_RANGE[0] <= rsi <= RSI_RANGE[1]
    shields['RSI'] = {'passed': rsi_pass, 'reason': f"RSI={rsi:.1f}", 'points': SHIELD_POINTS if rsi_pass else 0}
    
    # 2. Supertrend Shield
    st_pass = supertrend_dir == 1
    shields['Supertrend'] = {'passed': st_pass, 'reason': "Bullish" if st_pass else "Bearish", 'points': SHIELD_POINTS if st_pass else 0}
    
    # 3. VWAP Shield
    vwap_req = vwap * (1 + VWAP_BUFFER_PCT/100)
    vwap_pass = price > vwap_req
    shields['VWAP'] = {'passed': vwap_pass, 'reason': f"Price > VWAP + {VWAP_BUFFER_PCT}%", 'points': SHIELD_POINTS if vwap_pass else 0}
    
    # 4. MACD Shield
    macd_pass = macd_hist > 0
    shields['MACD'] = {'passed': macd_pass, 'reason': f"Hist={macd_hist:.2f}", 'points': SHIELD_POINTS if macd_pass else 0}
    
    # 5. Bollinger Shield
    bb_pass = bb_lower < price < bb_upper
    bb_reason = f"Price Rs.{price:.2f} within BB range"
    if price >= bb_upper:
        bb_reason = f"Price Rs.{price:.2f} at/above upper BB Rs.{bb_upper:.2f}"
    elif price <= bb_lower:
        bb_reason = f"Price Rs.{price:.2f} at/below lower BB Rs.{bb_lower:.2f}"
    shields['Bollinger'] = {'passed': bb_pass, 'reason': bb_reason, 'points': SHIELD_POINTS if bb_pass else 0}
    
    # 6. ADX Shield
    adx_pass = adx >= ADX_MIN
    shields['ADX'] = {'passed': adx_pass, 'reason': f"ADX={adx:.1f}", 'points': SHIELD_POINTS if adx_pass else 0}
    
    # 7. EMA Ladder Shield
    ema_pass = price > ema9 >= ema21 >= ema50
    shields['EMA Ladder'] = {'passed': ema_pass, 'reason': "Bullish Alignment" if ema_pass else "Misaligned", 'points': SHIELD_POINTS if ema_pass else 0}
    
    # 8. Entry Volume Shield
    vol_pass = volume >= 1.5 * avg_volume
    multiplier = volume / avg_volume if avg_volume else 0
    shields['Volume'] = {'passed': vol_pass, 'reason': f"{multiplier:.1f}x Avg", 'points': SHIELD_POINTS if vol_pass else 0}
    
    for s in shields.values():
        base_score += s['points']
        
    all_passed = all(s['passed'] for s in shields.values())
    mandatory_passed = shields['MACD']['passed']
    
    return {
        'shields': shields,
        'base_score': base_score,
        'all_passed': all_passed,
        'mandatory_passed': mandatory_passed
    }

# 4. Stock Scanner
def scan_stock(symbol: str, capital: float = 100000, for_backtest: bool = False, df: pd.DataFrame = None) -> dict:
    if df is None:
        try:
            df = yf.download(f"{symbol}.NS", period="5d", interval="5m")
            if df.empty:
                return None
        except:
            return None

    if len(df) < 50:
        return None
        
    # Compute indicators
    close = df['Close'].squeeze()
    high = df['High'].squeeze()
    low = df['Low'].squeeze()
    volume = df['Volume'].squeeze()
    
    # Series timestamp
    if isinstance(df.index, pd.DatetimeIndex):
        ts = pd.Series(df.index, index=df.index)
    else:
        ts = pd.Series(pd.to_datetime(df.index), index=df.index)
        
    rsi = compute_rsi(close)
    macd_line, sig_line, macd_hist = compute_macd(close)
    bb_upper, bb_mid, bb_lower = compute_bollinger_bands(close)
    adx = compute_adx(high, low, close)
    atr = compute_atr(high, low, close)
    ema9 = compute_ema(close, 9)
    ema21 = compute_ema(close, 21)
    ema50 = compute_ema(close, 50)
    st_line, st_dir = compute_supertrend(high, low, close)
    vwap = compute_vwap(high, low, close, volume, ts)
    avg_volume = volume.rolling(20).mean()

    # Get latest
    latest = -1
    c_price = close.iloc[latest]
    c_vwap = vwap.iloc[latest]
    c_rsi = rsi.iloc[latest]
    c_adx = adx.iloc[latest]
    c_macd_hist = macd_hist.iloc[latest]
    c_bb_upper = bb_upper.iloc[latest]
    c_bb_lower = bb_lower.iloc[latest]
    c_st_dir = st_dir.iloc[latest]
    c_ema9 = ema9.iloc[latest]
    c_ema21 = ema21.iloc[latest]
    c_ema50 = ema50.iloc[latest]
    c_vol = volume.iloc[latest]
    c_avg_vol = avg_volume.iloc[latest]
    c_atr = atr.iloc[latest]

    shield_res = evaluate_shields(
        c_price, c_vwap, c_rsi, c_adx, c_macd_hist, c_bb_upper, c_bb_lower,
        c_st_dir, c_ema9, c_ema21, c_ema50, c_vol, c_avg_vol, c_atr
    )

    base_score = shield_res['base_score']
    bonus_score = 0
    
    # Bonus points logic
    bonuses = []
    if c_vol >= 3 * c_avg_vol:
        bonus_score += 4
        bonuses.append("Mega Volume")
    elif c_vol >= 2 * c_avg_vol:
        bonus_score += 2
        bonuses.append("High Volume")
        
    if c_rsi >= 60:
        bonus_score += 2
        bonuses.append("Strong Momentum")

    total_score = base_score + bonus_score
    
    passed_shields_count = sum(1 for s in shield_res['shields'].values() if s['passed'])

    # Grading logic
    grade = 'SKIP'
    is_entry = False
    
    if base_score >= GRADE_ELITE_MIN and shield_res['all_passed']:
        grade = 'ELITE'
        is_entry = True
    elif base_score >= GRADE_STRONG_MIN and passed_shields_count >= 6 and shield_res['mandatory_passed']:
        grade = 'STRONG'
        is_entry = True
    elif total_score >= GRADE_AVERAGE_MIN:
        grade = 'AVERAGE'
    
    sl = c_price - (ATR_SL_MULTIPLIER * c_atr)
    sl_risk = c_price - sl
    t1 = c_price + (2.0 * sl_risk)
    t2 = c_price + (3.0 * sl_risk)
    
    risk_amount = capital * RISK_PER_TRADE_PCT
    qty = int(risk_amount / sl_risk) if sl_risk > 0 else 0
    qty = max(0, min(qty, int((capital * 0.1) / c_price))) # Cap position size to 10% of capital

    return {
        'symbol': symbol,
        'score': float(total_score),
        'base_score': float(base_score),
        'bonus_score': float(bonus_score),
        'grade': grade,
        'all_shields_pass': bool(shield_res['all_passed']),
        'is_entry': bool(is_entry),
        'price': float(c_price),
        'vwap': float(c_vwap),
        'rsi': float(c_rsi),
        'adx': float(c_adx),
        'sl': round(float(sl), 2),
        'sl_risk': round(float(sl_risk), 2),
        't1': round(float(t1), 2),
        't2': round(float(t2), 2),
        'qty': int(qty),
        'risk_amount': round(float(risk_amount), 2),
        'risk_pct': float(RISK_PER_TRADE_PCT * 100),
        'shields': {k: {'passed': bool(v['passed']), 'reason': str(v['reason']), 'points': float(v['points'])} for k, v in shield_res['shields'].items()},
        'bonuses': bonuses
    }

def scan_watchlist(symbols: list, capital: float = 100000) -> dict:
    stocks_res = []
    for sym in symbols:
        res = scan_stock(sym, capital)
        if res:
            stocks_res.append(res)
            
    stocks_res.sort(key=lambda x: x['score'], reverse=True)
    
    # Nifty proxy
    nifty = scan_stock('^NSEI', capital) if '^NSEI' in symbols else None
    nifty_dir = "NEUTRAL"
    nifty_change = 0.0
    if nifty:
        nifty_dir = "BULLISH" if nifty['price'] > nifty['vwap'] else "BEARISH"

    # Time and window
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    window = get_current_window()
    
    return {
        'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
        'window': window['name'] if window else "OUTSIDE_HOURS",
        'nifty_change': nifty_change,
        'nifty_direction': nifty_dir,
        'global_score': len([s for s in stocks_res if s['is_entry']]),
        'stocks': stocks_res
    }

# 5. Trading Window Logic
def get_current_window() -> dict:
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist).time()
    
    for name, w in WINDOWS.items():
        st = datetime.strptime(w['start'], '%H:%M').time()
        en = datetime.strptime(w['end'], '%H:%M').time()
        if st <= now <= en:
            return {'name': name, 'active': True, 'number': list(WINDOWS.keys()).index(name) + 1, 'max_trades': w['max_trades']}
    return {'name': 'OUTSIDE', 'active': False, 'number': 0, 'max_trades': 0}

def is_market_open() -> bool:
    ist = pytz.timezone('Asia/Kolkata')
    dt = datetime.now(ist)
    if dt.weekday() > 4:
        return False
    now = dt.time()
    st = time(9, 15)
    en = time(15, 30)
    return st <= now <= en

# 6. Backtest Engine
def run_backtest(symbols: list, capital: float = 100000, period: str = '60d') -> dict:
    all_trades = []
    
    for symbol in symbols:
        try:
            df = yf.download(f"{symbol}.NS", period=period, interval="5m")
            if df.empty or len(df) < 100:
                continue
                
            close = df['Close'].squeeze()
            high = df['High'].squeeze()
            low = df['Low'].squeeze()
            volume = df['Volume'].squeeze()
            
            ts = pd.Series(df.index, index=df.index)
            # convert to IST if UTC
            if ts.dt.tz is not None:
                ts = ts.dt.tz_convert('Asia/Kolkata')
            else:
                ts = ts.dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
                
            rsi = compute_rsi(close)
            macd_line, sig_line, macd_hist = compute_macd(close)
            bb_upper, bb_mid, bb_lower = compute_bollinger_bands(close)
            adx = compute_adx(high, low, close)
            atr = compute_atr(high, low, close)
            ema9 = compute_ema(close, 9)
            ema21 = compute_ema(close, 21)
            ema50 = compute_ema(close, 50)
            st_line, st_dir = compute_supertrend(high, low, close)
            vwap = compute_vwap(high, low, close, volume, ts)
            avg_volume = volume.rolling(20).mean()
            
            in_trade = False
            trade_info = {}
            stock_daily_traded = {}  # date -> True if already traded this stock today
            last_exit_time = None    # cooldown: don't re-enter for 60 min after exit
            
            for i in range(50, len(df)):
                c_time = ts.iloc[i]
                c_date = c_time.date()
                
                c_price = close.iloc[i]
                c_high = high.iloc[i]
                c_low = low.iloc[i]
                
                if in_trade:
                    exit_reason = None
                    exit_price = 0
                    
                    # Update trailing stop
                    entry_price = trade_info['entry_price']
                    current_gain = (c_price - entry_price) / entry_price
                    
                    if current_gain >= TRAILING_STOP_TRIGGER:
                        new_sl = c_price * (1 - TRAILING_STOP_OFFSET)
                        if new_sl > trade_info['sl']:
                            trade_info['sl'] = new_sl
                    
                    # Exit checks (priority order)
                    if c_low <= trade_info['sl']:
                        exit_reason = "STOP_LOSS"
                        exit_price = trade_info['sl']
                    elif c_high >= trade_info['t1']:
                        exit_reason = "TARGET_HIT"
                        exit_price = trade_info['t1']
                    elif adx.iloc[i] < 12:
                        exit_reason = "ADX_DROP"
                        exit_price = c_price
                    elif c_time.time() >= time(15, 10):
                        exit_reason = "TIME_EXIT"
                        exit_price = c_price
                        
                    if exit_reason:
                        pnl = (exit_price - entry_price) * trade_info['qty']
                        turnover = (entry_price + exit_price) * trade_info['qty']
                        cost = 40 + (turnover * 0.0005)
                        net_pnl = pnl - cost
                        
                        all_trades.append({
                            'symbol': symbol,
                            'entry_time': trade_info['entry_time'].strftime("%Y-%m-%d %H:%M"),
                            'entry_price': float(entry_price),
                            'exit_time': c_time.strftime("%Y-%m-%d %H:%M"),
                            'exit_price': float(exit_price),
                            'qty': int(trade_info['qty']),
                            'exit_reason': exit_reason,
                            'net_pnl': round(float(net_pnl), 2),
                            'score': float(trade_info['score']),
                            'grade': trade_info['grade'],
                            'window': trade_info['window'],
                            'is_winner': bool(net_pnl > 0)
                        })
                        in_trade = False
                        last_exit_time = c_time  # Start cooldown
                        
                else:
                    # Per-stock: max 1 trade per stock per day
                    if stock_daily_traded.get(c_date, False):
                        continue
                    
                    # 60-minute cooldown after last exit
                    if last_exit_time is not None:
                        minutes_since_exit = (c_time - last_exit_time).total_seconds() / 60
                        if minutes_since_exit < 60:
                            continue
                        
                    # Find window
                    w_name = 'OUTSIDE'
                    for name, w in WINDOWS.items():
                        st = datetime.strptime(w['start'], '%H:%M').time()
                        en = datetime.strptime(w['end'], '%H:%M').time()
                        if st <= c_time.time() <= en:
                            w_name = name
                            break
                            
                    if w_name == 'OUTSIDE' or w_name == 'DEAD_ZONE':
                        continue
                        
                    if w_name == 'MOMENTUM' and c_time.time() < time(10, 0):
                        continue
                        
                    # Evaluate Entry
                    s_res = evaluate_shields(
                        c_price, vwap.iloc[i], rsi.iloc[i], adx.iloc[i], macd_hist.iloc[i],
                        bb_upper.iloc[i], bb_lower.iloc[i], st_dir.iloc[i], ema9.iloc[i],
                        ema21.iloc[i], ema50.iloc[i], volume.iloc[i], avg_volume.iloc[i], atr.iloc[i]
                    )
                    
                    base_score = s_res['base_score']
                    # Add bonuses (same as scan_stock)
                    bonus_score = 0
                    c_vol_val = volume.iloc[i]
                    c_avg_vol_val = avg_volume.iloc[i]
                    if c_avg_vol_val > 0 and c_vol_val >= 3 * c_avg_vol_val:
                        bonus_score += 4
                    elif c_avg_vol_val > 0 and c_vol_val >= 2 * c_avg_vol_val:
                        bonus_score += 2
                    if rsi.iloc[i] >= 60:
                        bonus_score += 2
                    
                    score = base_score + bonus_score
                    passed_shields = sum(1 for s in s_res['shields'].values() if s['passed'])
                    
                    is_entry = False
                    grade = 'SKIP'
                    
                    if base_score >= GRADE_ELITE_MIN and s_res['all_passed']:
                        grade = 'ELITE'
                        is_entry = True
                    elif base_score >= GRADE_STRONG_MIN and passed_shields >= 6 and s_res['mandatory_passed']:
                        grade = 'STRONG'
                        is_entry = True
                        
                    if is_entry:
                        sl = c_price - (ATR_SL_MULTIPLIER * atr.iloc[i])
                        sl_risk = c_price - sl
                        if sl_risk <= 0:
                            continue
                            
                        risk_amt = capital * RISK_PER_TRADE_PCT
                        qty = int(risk_amt / sl_risk)
                        qty = max(1, min(qty, int((capital * 0.1) / c_price)))
                        
                        t1 = c_price + (2.0 * sl_risk)
                        t2 = c_price + (3.0 * sl_risk)
                        
                        trade_info = {
                            'entry_time': c_time,
                            'entry_price': c_price,
                            'qty': qty,
                            'sl': sl,
                            't1': t1,
                            't2': t2,
                            'score': score,
                            'grade': grade,
                            'window': w_name
                        }
                        in_trade = True
                        stock_daily_traded[c_date] = True  # Max 1 trade per stock per day
                        
        except Exception as e:
            print(f"Error backtesting {symbol}: {e}")
            
    # Compute Metrics
    total_trades = len(all_trades)
    winners = [t for t in all_trades if t['is_winner']]
    losers = [t for t in all_trades if not t['is_winner']]
    
    win_rate = (len(winners) / total_trades * 100) if total_trades > 0 else 0
    gross_pnl = sum(t['net_pnl'] for t in winners) - abs(sum(min(0, t['net_pnl']) for t in all_trades))
    total_costs = sum(40 + ((t['entry_price'] + t['exit_price']) * t['qty'] * 0.0005) for t in all_trades) if all_trades else 0
    net_pnl = sum(t['net_pnl'] for t in all_trades)
    return_on_capital = (net_pnl / capital) * 100 if capital > 0 else 0
    
    avg_win = float(np.mean([t['net_pnl'] for t in winners])) if winners else 0
    avg_loss = float(np.mean([abs(t['net_pnl']) for t in losers])) if losers else 0
    risk_reward = avg_win / avg_loss if avg_loss != 0 else 0
    total_win_pnl = sum(t['net_pnl'] for t in winners)
    total_loss_pnl = abs(sum(t['net_pnl'] for t in losers))
    profit_factor = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else 0
    
    # Sharpe ratio (daily PnL basis)
    daily_pnl = {}
    for t in all_trades:
        d = t['entry_time'][:10]
        daily_pnl[d] = daily_pnl.get(d, 0) + t['net_pnl']
    daily_returns = list(daily_pnl.values())
    if len(daily_returns) > 1:
        sharpe_ratio = float(np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)) if np.std(daily_returns) > 0 else 0
    else:
        sharpe_ratio = 0
    
    # Max drawdown
    equity = [0]
    for t in sorted(all_trades, key=lambda x: x['entry_time']):
        equity.append(equity[-1] + t['net_pnl'])
    peak = 0
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd
    max_drawdown_pct = (max_dd / capital * 100) if capital > 0 else 0
    
    # Consecutive losing days
    max_consec = 0
    consec = 0
    for d in sorted(daily_pnl.keys()):
        if daily_pnl[d] < 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0
    
    winning_days = sum(1 for v in daily_pnl.values() if v > 0)
    losing_days = sum(1 for v in daily_pnl.values() if v <= 0)
    best_day = max(daily_pnl.values()) if daily_pnl else 0
    worst_day = min(daily_pnl.values()) if daily_pnl else 0
    
    # Window stats
    window_stats = {}
    for t in all_trades:
        w = t['window']
        if w not in window_stats:
            window_stats[w] = {'trades': 0, 'wins': 0, 'pnl': 0}
        window_stats[w]['trades'] += 1
        if t['is_winner']:
            window_stats[w]['wins'] += 1
        window_stats[w]['pnl'] += t['net_pnl']
    
    # Grade stats
    grade_stats = {}
    for t in all_trades:
        g = t['grade']
        if g not in grade_stats:
            grade_stats[g] = {'trades': 0, 'wins': 0, 'pnl': 0}
        grade_stats[g]['trades'] += 1
        if t['is_winner']:
            grade_stats[g]['wins'] += 1
        grade_stats[g]['pnl'] += t['net_pnl']
    
    # Exit counts
    exit_counts = {}
    for t in all_trades:
        r = t['exit_reason']
        exit_counts[r] = exit_counts.get(r, 0) + 1
    
    # Stock stats
    stock_stats = {}
    for t in all_trades:
        s = t['symbol']
        if s not in stock_stats:
            stock_stats[s] = {'trades': 0, 'wins': 0, 'pnl': 0, 'scores': []}
        stock_stats[s]['trades'] += 1
        if t['is_winner']:
            stock_stats[s]['wins'] += 1
        stock_stats[s]['pnl'] += t['net_pnl']
        stock_stats[s]['scores'].append(t['score'])
    
    # Monthly stats
    monthly_stats = {}
    for t in all_trades:
        m = t['entry_time'][:7]
        if m not in monthly_stats:
            monthly_stats[m] = {'trades': 0, 'wins': 0, 'pnl': 0}
        monthly_stats[m]['trades'] += 1
        if t['is_winner']:
            monthly_stats[m]['wins'] += 1
        monthly_stats[m]['pnl'] += t['net_pnl']
    
    return {
        'metrics': {
            'total_trades': total_trades,
            'winners': len(winners),
            'losers': len(losers),
            'win_rate': round(win_rate, 2),
            'gross_pnl': round(sum(t['net_pnl'] for t in all_trades), 2),
            'total_costs': round(total_costs, 2),
            'net_pnl': round(net_pnl, 2),
            'return_on_capital': round(return_on_capital, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'risk_reward': round(risk_reward, 2),
            'profit_factor': round(profit_factor, 2),
            'sharpe_ratio': round(sharpe_ratio, 2),
            'max_drawdown': round(max_dd, 2),
            'max_drawdown_pct': round(max_drawdown_pct, 2),
            'max_consec_losing_days': max_consec,
            'total_trading_days': len(daily_pnl),
            'winning_days': winning_days,
            'losing_days': losing_days,
            'best_day': round(best_day, 2),
            'worst_day': round(worst_day, 2),
            'window_stats': window_stats,
            'grade_stats': grade_stats,
            'exit_counts': exit_counts,
            'stock_stats': stock_stats,
            'monthly_stats': monthly_stats
        },
        'trades': all_trades
    }

# 50 High-Volume, High-Momentum Indian Stocks under ₹1,500 (Ideal for ₹5,000 capital)
SWING_WATCHLIST_50 = [
    'TATASTEEL', 'FEDERALBNK', 'IDFCFIRSTB', 'PNB', 'BANKBARODA', 
    'SAIL', 'IOC', 'ONGC', 'COALINDIA', 'GAIL', 
    'NTPC', 'POWERGRID', 'BHEL', 'PFC', 'RECLTD', 
    'NATIONALUM', 'HINDCOPPER', 'IRFC', 'RVNL', 'HUDCO',
    'ITC', 'WIPRO', 'SBIN', 'BEL', 'HAL', 
    'VEDL', 'HINDALCO', 'TATACHEM', 'TATAPOWER', 'ASHOKLEY',
    'EXIDEIND', 'AMBUJACEM', 'DLF', 'JIOFIN',
    'HDFCBANK', 'ICICIBANK', 'AXISBANK', 'KOTAKBANK', 'BHARTIARTL',
    'SUNPHARMA', 'CIPLA', 'APOLLOTYRE', 'TVSMOTOR', 'CUMMINSIND',
    'VOLTAS', 'HAVELLS', 'JSWSTEEL', 'CHOLAFIN', 'INDHOTEL'
]

def scan_swing_candidates(capital: float = 5000) -> list:
    """
    Scans 50 high-momentum Indian stocks for daily Swing Trading setups.
    Returns list of candidate dicts sorted by setup quality.
    """
    tickers = [f"{s}.NS" for s in SWING_WATCHLIST_50]
    try:
        raw = yf.download(tickers, period="1y", interval="1d", group_by='ticker', progress=False)
    except Exception as e:
        print(f"Error downloading swing data: {e}")
        return []
        
    signals = []
    
    for s in SWING_WATCHLIST_50:
        tk = f"{s}.NS"
        if tk not in raw.columns.levels[0]:
            continue
        df = raw[tk].dropna(how='all')
        if len(df) < 100:
            continue
            
        c = df['Close'].dropna()
        h = df['High'].loc[c.index]
        l = df['Low'].loc[c.index]
        v = df['Volume'].loc[c.index]
        
        price = float(c.iloc[-1])
        ema20 = compute_ema(c, 20)
        ema50 = compute_ema(c, 50)
        ema200 = compute_ema(c, 200)
        rsi14 = compute_rsi(c, 14)
        atr14 = compute_atr(df.loc[c.index], 14)
        avg_v = v.rolling(20).mean()
        
        c_ema20 = float(ema20.iloc[-1])
        c_ema50 = float(ema50.iloc[-1])
        c_ema200 = float(ema200.iloc[-1]) if len(c) >= 200 else c_ema50
        c_rsi = float(rsi14.iloc[-1])
        c_vol = float(v.iloc[-1])
        c_avg_vol = float(avg_v.iloc[-1])
        c_atr = float(atr14.iloc[-1])
        
        high_20 = float(h.iloc[-21:-1].max()) if len(h) >= 21 else price
        vol_multiplier = c_vol / c_avg_vol if c_avg_vol > 0 else 1.0
        
        # 1. Breakout setup
        if (price >= high_20 * 0.995 and 
            price > c_ema50 and 
            c_ema50 > c_ema200 and
            52 <= c_rsi <= 72 and 
            vol_multiplier >= 1.2):
            
            sl = round(price * 0.965, 2)
            t1 = round(price * 1.06, 2)
            t2 = round(price * 1.10, 2)
            qty = max(1, int(capital / price))
            
            signals.append({
                'symbol': s,
                'price': round(price, 2),
                'type': 'BREAKOUT',
                'setup': '20-Day High Breakout + Volume Surge',
                'rsi': round(c_rsi, 1),
                'vol_surge': f"{vol_multiplier:.1f}x",
                'sl': sl,
                't1': t1,
                't2': t2,
                'qty': qty,
                'score': round(vol_multiplier * 10 + (price / high_20) * 10, 1)
            })
            
        # 2. Bull Trend Dip Buy
        elif (price > c_ema200 and 
              c_ema50 > c_ema200 and 
              abs(price - c_ema20) / c_ema20 <= 0.02 and 
              42 <= c_rsi <= 55):
              
            sl = round(price * 0.96, 2)
            t1 = round(price * 1.05, 2)
            t2 = round(price * 1.08, 2)
            qty = max(1, int(capital / price))
            
            signals.append({
                'symbol': s,
                'price': round(price, 2),
                'type': 'DIP_BUY',
                'setup': '20 EMA Pullback Bounce (Bull Trend)',
                'rsi': round(c_rsi, 1),
                'vol_surge': f"{vol_multiplier:.1f}x",
                'sl': sl,
                't1': t1,
                't2': t2,
                'qty': qty,
                'score': round(15.0 + (55 - c_rsi), 1)
            })

    signals.sort(key=lambda x: x['score'], reverse=True)
    return signals



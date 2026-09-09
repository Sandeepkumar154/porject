import os
import json
from datetime import datetime, timedelta
import pandas as pd

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "groww_token.json")

def get_groww_token() -> str:
    """Load the daily Groww access token from file or environment."""
    env_token = os.environ.get("GROWW_ACCESS_TOKEN")
    if env_token and len(env_token.strip()) > 10:
        return env_token.strip()
        
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                data = json.load(f)
                token = data.get("access_token", "").strip()
                if token:
                    return token
        except Exception:
            pass
    return None

def save_groww_token(token: str) -> bool:
    """Save the daily Groww access token generated after morning approval."""
    try:
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": token.strip(),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving Groww token: {e}")
        return False

def fetch_groww_candles(symbol: str, interval: str = "5m", days: int = 5) -> pd.DataFrame:
    """
    Fetch live 5m candles from Groww Trade API.
    Returns None if token is expired, missing, or error occurs.
    """
    token = get_groww_token()
    if not token:
        return None
        
    try:
        from growwapi import GrowwAPI
        groww = GrowwAPI(access_token=token)
        
        int_map = {
            "5m": getattr(groww, "CANDLE_INTERVAL_MIN_5", "5m"),
            "15m": getattr(groww, "CANDLE_INTERVAL_MIN_15", "15m"),
            "1d": getattr(groww, "CANDLE_INTERVAL_DAY", "1d")
        }
        ci = int_map.get(interval, int_map["5m"])
        
        clean_sym = symbol.replace(".NS", "")
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        
        candles = groww.get_historical_candles(
            exchange=groww.EXCHANGE_NSE,
            segment=groww.SEGMENT_CASH,
            groww_symbol=f"NSE-{clean_sym}",
            start_time=start_time.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
            candle_interval=ci
        )
        
        if not candles:
            return None
            
        df = pd.DataFrame(candles)
        if df.empty or "close" not in df.columns:
            return None
            
        df.index = pd.to_datetime(df["timestamp"])
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        df.dropna(inplace=True)
        return df if len(df) >= 20 else None
        
    except Exception as e:
        print(f"Groww API fetch notice for {symbol}: {e}")
        return None

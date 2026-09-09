import os
import json
from datetime import datetime, timedelta
import pandas as pd
from typing import Optional, Dict, Any, Tuple

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "groww_token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "groww_credentials.json")

def get_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Groww API Key and API Secret.
    Checks environment variables first, then local credentials file.
    """
    api_key = os.environ.get("GROWW_API_KEY")
    api_secret = os.environ.get("GROWW_SECRET")
    
    if api_key and api_secret:
        return api_key.strip(), api_secret.strip()
        
    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE, "r") as f:
                data = json.load(f)
                k = data.get("api_key", "").strip()
                s = data.get("api_secret", "").strip()
                if k and s:
                    return k, s
        except Exception:
            pass
            
    return None, None

def get_groww_token() -> Optional[str]:
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
    """Save the daily Groww access token."""
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

def refresh_groww_token() -> Dict[str, Any]:
    """
    Generate a fresh Groww access token using api_key and api_secret.
    Runs automatically each morning (at 9:00 AM IST) and on bot startup.
    """
    api_key, api_secret = get_credentials()
    if not api_key or not api_secret:
        return {
            "success": False,
            "message": "Missing Groww API Key or Secret. Set GROWW_API_KEY and GROWW_SECRET in env or credentials file."
        }
        
    try:
        from growwapi import GrowwAPI
        token = GrowwAPI.get_access_token(api_key=api_key, secret=api_secret)
        if token and len(token) > 20:
            save_groww_token(token)
            return {
                "success": True,
                "message": "Groww session token successfully generated!",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        else:
            return {"success": False, "message": "Failed to obtain token from Groww API."}
    except Exception as e:
        return {"success": False, "message": f"Groww auth error: {str(e)}"}

def ensure_daily_token() -> bool:
    """
    Ensures that a fresh Groww session token exists for today.
    If no token exists or token is from a previous date, automatically refreshes it.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                data = json.load(f)
                updated_at = data.get("updated_at", "")
                token = data.get("access_token", "")
                if token and len(token) > 20 and updated_at.startswith(today_str):
                    return True
        except Exception:
            pass
            
    res = refresh_groww_token()
    return res.get("success", False)

_cached_client = None
_cached_token = None

def get_groww_client():
    """Get an authenticated GrowwAPI instance. Refreshes token if needed."""
    global _cached_client, _cached_token
    token = get_groww_token()
    if not token:
        res = refresh_groww_token()
        if res.get("success"):
            token = get_groww_token()
            
    if not token:
        return None
        
    if _cached_client is not None and _cached_token == token:
        return _cached_client

    try:
        from growwapi import GrowwAPI
        _cached_client = GrowwAPI(token)
        _cached_token = token
        return _cached_client
    except Exception as e:
        print(f"Error creating Groww client: {e}")
        return None


def get_account_summary() -> Dict[str, Any]:
    """
    Fetch live account summary from Groww: UCC, margin/cash, holdings, positions.
    """
    client = get_groww_client()
    if not client:
        return {
            "connected": False,
            "error": "Groww client not authenticated. Missing or invalid token."
        }
        
    try:
        profile = client.get_user_profile()
        margin = client.get_available_margin_details()
        holdings_res = client.get_holdings_for_user()
        positions_res = client.get_positions_for_user()
        
        clear_cash = margin.get("clear_cash", 0.0) if margin else 0.0
        holdings_list = holdings_res.get("holdings", []) if holdings_res else []
        positions_list = positions_res.get("positions", []) if positions_res else []
        
        return {
            "connected": True,
            "ucc": profile.get("ucc", "Unknown") if profile else "Unknown",
            "segments": profile.get("active_segments", []) if profile else [],
            "clear_cash": clear_cash,
            "total_holdings_count": len(holdings_list),
            "holdings": holdings_list,
            "open_positions_count": len(positions_list),
            "positions": positions_list,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        # If call failed because token expired, attempt one refresh
        print(f"Groww call failed: {e}. Attempting token refresh...")
        refresh_res = refresh_groww_token()
        if refresh_res.get("success"):
            try:
                new_token = get_groww_token()
                from growwapi import GrowwAPI
                client = GrowwAPI(new_token)
                profile = client.get_user_profile()
                margin = client.get_available_margin_details()
                holdings_res = client.get_holdings_for_user()
                positions_res = client.get_positions_for_user()
                return {
                    "connected": True,
                    "ucc": profile.get("ucc", "Unknown") if profile else "Unknown",
                    "segments": profile.get("active_segments", []) if profile else [],
                    "clear_cash": margin.get("clear_cash", 0.0) if margin else 0.0,
                    "total_holdings_count": len(holdings_res.get("holdings", [])),
                    "holdings": holdings_res.get("holdings", []),
                    "open_positions_count": len(positions_res.get("positions", [])),
                    "positions": positions_res.get("positions", []),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            except Exception as e2:
                return {"connected": False, "error": str(e2)}
        return {"connected": False, "error": str(e)}

def fetch_groww_candles(symbol: str, interval: str = "5m", days: int = 5) -> Optional[pd.DataFrame]:
    """
    Fetch live 5m candles from Groww Trade API.
    Returns None if token is expired, missing, or error occurs.
    """
    client = get_groww_client()
    if not client:
        return None
        
    try:
        int_map = {
            "5m": getattr(client, "CANDLE_INTERVAL_MIN_5", "5m"),
            "15m": getattr(client, "CANDLE_INTERVAL_MIN_15", "15m"),
            "1d": getattr(client, "CANDLE_INTERVAL_DAY", "1d")
        }
        ci = int_map.get(interval, int_map["5m"])
        
        clean_sym = symbol.replace(".NS", "")
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        
        candles = client.get_historical_candles(
            exchange=client.EXCHANGE_NSE,
            segment=client.SEGMENT_CASH,
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
        
    except Exception:
        return None


import os
import json
from datetime import datetime, timedelta
import pandas as pd
from typing import Optional, Dict, Any, Tuple

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "groww_token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "groww_credentials.json")

# ═══════════════════════════════════════════════════════════════════
# TOTP FLOW CREDENTIALS (Approach 2 — No Expiry, Fully Automated)
# ═══════════════════════════════════════════════════════════════════
# TOTP Token: Used as 'api_key' in GrowwAPI.get_access_token()
# TOTP Secret: Used by pyotp to generate 6-digit OTP codes automatically

FALLBACK_TOTP_TOKEN = "eyJraWQiOiJaTUtjVXciLCJhbGciOiJFUzI1NiJ9.eyJleHAiOjI1Nzg1Mzk4NjUsImlhdCI6MTc5MDEzOTg2NSwibmJmIjoxNzkwMTM5ODY1LCJzdWIiOiJ7XCJ0b2tlblJlZklkXCI6XCIxNTZhM2EzNS0xZjNmLTQwYTItYWE3YS05Yzg1MmRlMjNiY2RcIixcInZlbmRvckludGVncmF0aW9uS2V5XCI6XCJlMzFmZjIzYjA4NmI0MDZjODg3NGIyZjZkODQ5NTMxM1wiLFwidXNlckFjY291bnRJZFwiOlwiYTg4ODYzZjgtOThjNS00ZGFmLThhNmQtNTJkNDlmYjMyZTA4XCIsXCJkZXZpY2VJZFwiOlwiMTNiNzM5MWItMmM3NS01NTJmLTgyYTktNzYxOGE3OWIwZWEwXCIsXCJzZXNzaW9uSWRcIjpcIjhhMWQ4M2Q0LTFlODQtNDU5Yi1iOTYzLWY4ODkwMzM3ZTU0NlwiLFwiYWRkaXRpb25hbERhdGFcIjpcIno1NC9NZzltdjE2WXdmb0gvS0EwYk9RZWhjRFFNeTl3bjFtc2RMTWVLUzVSTkczdTlLa2pWZDNoWjU1ZStNZERhWXBOVi9UOUxIRmtQejFFQisybTdRPT1cIixcInJvbGVcIjpcImF1dGgtdG90cFwiLFwic291cmNlSXBBZGRyZXNzXCI6XCIxNTcuNTEuMTQzLjEwMCwxNzIuNjkuMTIyLjE3NSwzNS4yNDEuMjMuMTIzXCIsXCJ0d29GYUV4cGlyeVRzXCI6MjU3ODUzOTg2NTM3MSxcInZlbmRvck5hbWVcIjpcImdyb3d3QXBpXCJ9IiwiaXNzIjoiYXBleC1hdXRoLXByb2QtYXBwIn0.IE3kMbpYV3GvVYW92dqtvgudOrfwKza6zxKyCX0szcnUZBrVemWtlWWFJXpXGiD3QNxOibniXlepi6el2Xt8Rg"

FALLBACK_TOTP_SECRET = "IYNRN6KNHDWCM7GS4IIJZM47MRBMOC5G"


def get_totp_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Get Groww TOTP Token and TOTP Secret for Approach 2 (automated, no-expiry) login.
    Priority: Environment variables → credentials file → hardcoded fallbacks.
    """
    totp_token = os.environ.get("GROWW_TOTP_TOKEN", "").strip()
    totp_secret = os.environ.get("GROWW_TOTP_SECRET", "").strip()

    if totp_token and totp_secret and len(totp_token) > 20 and len(totp_secret) > 10:
        return totp_token, totp_secret

    if os.path.exists(CREDENTIALS_FILE):
        try:
            with open(CREDENTIALS_FILE, "r") as f:
                data = json.load(f)
                t = data.get("totp_token", "").strip()
                s = data.get("totp_secret", "").strip()
                if t and s and len(t) > 20 and len(s) > 10:
                    return t, s
        except Exception:
            pass

    return FALLBACK_TOTP_TOKEN, FALLBACK_TOTP_SECRET


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
                updated = data.get("updated_at", "")
                # Only return token if it was generated today (tokens expire at 6 AM)
                today_str = datetime.now().strftime("%Y-%m-%d")
                if token and updated.startswith(today_str):
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
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "auth_method": "TOTP_FLOW_v2"
            }, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving Groww token: {e}")
        return False


def refresh_groww_token() -> Dict[str, Any]:
    """
    Generate a fresh Groww access token using TOTP Flow (Approach 2).
    Uses pyotp to auto-generate the 6-digit OTP from the TOTP Secret.
    Runs automatically each morning (at 8:50 AM IST) and on bot startup.
    NO manual approval needed — this flow has no expiry.
    """
    totp_token, totp_secret = get_totp_credentials()
    if not totp_token or not totp_secret:
        return {
            "success": False,
            "message": "Missing TOTP Token or TOTP Secret. Set GROWW_TOTP_TOKEN and GROWW_TOTP_SECRET."
        }

    try:
        import pyotp
        from growwapi import GrowwAPI

        # Generate the 6-digit TOTP code automatically
        totp_gen = pyotp.TOTP(totp_secret)
        otp_code = totp_gen.now()

        print(f"[Groww TOTP] Generated OTP: {otp_code[:2]}**** at {datetime.now().strftime('%H:%M:%S')}")

        # Authenticate using TOTP flow (Approach 2 from Groww docs)
        access_token = GrowwAPI.get_access_token(api_key=totp_token, totp=otp_code)

        if access_token and len(access_token) > 20:
            save_groww_token(access_token)
            return {
                "success": True,
                "message": "Groww TOTP login successful! Session token generated automatically.",
                "auth_method": "TOTP_FLOW_v2",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        else:
            return {"success": False, "message": "TOTP auth returned empty token. Check credentials."}
    except ImportError as ie:
        return {"success": False, "message": f"Missing library: {str(ie)}. Run: pip install pyotp growwapi"}
    except Exception as e:
        return {"success": False, "message": f"Groww TOTP auth error: {str(e)}"}


def ensure_daily_token() -> bool:
    """
    Ensures that a fresh Groww session token exists for today.
    If no token exists or token is from a previous day, automatically refreshes via TOTP.
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

    print(f"[Groww] No valid token for {today_str}. Generating via TOTP...")
    res = refresh_groww_token()
    success = res.get("success", False)
    print(f"[Groww] Token refresh result: {res.get('message')}")
    return success


_cached_client = None
_cached_token = None

def get_groww_client():
    """Get an authenticated GrowwAPI instance. Refreshes token via TOTP if needed."""
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
            "auth_method": "TOTP_FLOW_v2",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        # If call failed because token expired, attempt one TOTP refresh
        print(f"Groww call failed: {e}. Attempting TOTP refresh...")
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
                    "auth_method": "TOTP_FLOW_v2",
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            except Exception as e2:
                return {"connected": False, "error": str(e2)}
        return {"connected": False, "error": str(e)}


def fetch_groww_candles(symbol: str, interval: str = "5m", days: int = 5) -> Optional[pd.DataFrame]:
    """
    Fetch live candles from Groww Trade API.
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

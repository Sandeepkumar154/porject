import urllib.request
import urllib.parse
import json
import re
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pandas as pd

# Sentiment word lists
BULLISH_WORDS = [
    'surge', 'surges', 'surging', 'rally', 'rallies', 'rallying',
    'soar', 'soars', 'soaring', 'jump', 'jumps', 'jumping',
    'gain', 'gains', 'rise', 'rises', 'rising', 'climbs',
    'upgrade', 'upgrades', 'upgraded', 'outperform', 'overweight',
    'buy', 'bullish', 'breakout', 'record high', 'record-high',
    'profit', 'profits', 'beats', 'strong', 'boom', 'booming',
    'recovery', 'recovers', 'positive', 'upside', 'growth',
    'expansion', 'dividend', 'bonus', 'buyback', 'optimistic',
    'target raised', 'price target', 'accumulate', 'recommend'
]

BEARISH_WORDS = [
    'crash', 'crashes', 'crashing', 'fall', 'falls', 'falling',
    'drop', 'drops', 'dropping', 'plunge', 'plunges', 'plunging',
    'sink', 'sinks', 'sinking', 'tumble', 'tumbles', 'tumbling',
    'decline', 'declines', 'declining', 'slump', 'slumps',
    'downgrade', 'downgrades', 'downgraded', 'underperform', 'underweight',
    'sell', 'bearish', 'breakdown', 'record low', 'sell-off',
    'loss', 'losses', 'misses', 'weak', 'bust', 'recession',
    'warning', 'warns', 'negative', 'downside', 'shrink',
    'contraction', 'debt', 'default', 'fraud', 'scam',
    'target cut', 'reduce', 'avoid', 'penalty', 'fine'
]

def fetch_news_sentiment(symbol: str) -> dict:
    """Fetch latest news for a stock from Google News RSS and calculate sentiment."""
    timestamp = datetime.now().isoformat()
    default_result = {
        'symbol': symbol,
        'headlines': [],
        'bullish_count': 0,
        'bearish_count': 0,
        'sentiment_score': 0.0,
        'sentiment_label': 'NEUTRAL',
        'key_headline': '',
        'timestamp': timestamp
    }

    query = f"{symbol} NSE stock"
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            
            headlines = []
            for item in root.findall('.//item/title'):
                if item.text:
                    headlines.append(item.text)
            
            headlines = headlines[:10]  # up to 10 recent
            
            bullish_count = 0
            bearish_count = 0
            key_headline = ""
            max_sentiment_abs = -1

            for hl in headlines:
                hl_lower = hl.lower()
                hl_bull = sum(1 for word in BULLISH_WORDS if re.search(rf'\b{re.escape(word)}\b', hl_lower))
                hl_bear = sum(1 for word in BEARISH_WORDS if re.search(rf'\b{re.escape(word)}\b', hl_lower))
                
                bullish_count += hl_bull
                bearish_count += hl_bear
                
                hl_net = hl_bull - hl_bear
                if abs(hl_net) > max_sentiment_abs:
                    max_sentiment_abs = abs(hl_net)
                    key_headline = hl

            total_words = bullish_count + bearish_count
            sentiment_score = 0.0
            if total_words > 0:
                sentiment_score = (bullish_count - bearish_count) / total_words

            if sentiment_score > 0.3:
                label = 'BULLISH'
            elif sentiment_score < -0.3:
                label = 'BEARISH'
            else:
                label = 'NEUTRAL'
                
            return {
                'symbol': symbol,
                'headlines': headlines,
                'bullish_count': bullish_count,
                'bearish_count': bearish_count,
                'sentiment_score': round(sentiment_score, 2),
                'sentiment_label': label,
                'key_headline': key_headline,
                'timestamp': timestamp
            }
            
    except Exception as e:
        return default_result


def fetch_india_vix() -> dict:
    """Fetch India VIX from Yahoo Finance (^INDIAVIX)."""
    timestamp = datetime.now().isoformat()
    default_result = {
        'vix_value': 15.0,
        'vix_change': 0.0,
        'fear_level': 'NEUTRAL',
        'can_trade': True,
        'position_multiplier': 1.0,
        'description': 'Default/Fallback VIX',
        'timestamp': timestamp
    }
    
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EINDIAVIX?interval=1d&range=5d"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            result = data.get('chart', {}).get('result', [])
            if not result:
                return default_result
                
            close_prices = result[0].get('indicators', {}).get('quote', [{}])[0].get('close', [])
            close_prices = [p for p in close_prices if p is not None]
            
            if len(close_prices) < 2:
                if len(close_prices) == 1:
                    vix_value = close_prices[0]
                    vix_change = 0.0
                else:
                    return default_result
            else:
                vix_value = close_prices[-1]
                prev_vix = close_prices[-2]
                if prev_vix == 0 or prev_vix is None:
                    vix_change = 0.0
                else:
                    vix_change = ((vix_value - prev_vix) / prev_vix) * 100
                
            vix_value = round(vix_value, 2)
            vix_change = round(vix_change, 2)
            
            if vix_value < 12:
                fear_level = 'EXTREME_GREED'
                mult = 1.0
                can_trade = True
            elif 12 <= vix_value < 15:
                fear_level = 'GREED'
                mult = 1.0
                can_trade = True
            elif 15 <= vix_value < 18:
                fear_level = 'NEUTRAL'
                mult = 1.0
                can_trade = True
            elif 18 <= vix_value < 22:
                fear_level = 'FEAR'
                mult = 0.5
                can_trade = True
            elif 22 <= vix_value <= 28:
                fear_level = 'EXTREME_FEAR'
                mult = 0.0
                can_trade = False
            else:
                fear_level = 'PANIC'
                mult = 0.0
                can_trade = False
                
            return {
                'vix_value': vix_value,
                'vix_change': vix_change,
                'fear_level': fear_level,
                'can_trade': can_trade,
                'position_multiplier': mult,
                'description': f"VIX is {vix_value} ({fear_level})",
                'timestamp': timestamp
            }
    except Exception as e:
        return default_result


def fetch_fii_dii_flows() -> dict:
    """Fetch latest FII and DII institutional flow sentiment from financial feeds."""
    timestamp = datetime.now().isoformat()
    default_res = {
        'available': True,
        'fii_signal': 'NEUTRAL',
        'dii_signal': 'BULLISH',
        'combined_signal': 'NEUTRAL',
        'summary': 'Institutional Flows Balanced',
        'timestamp': timestamp
    }
    try:
        url = "https://news.google.com/rss/search?q=FII+DII+net+buyers+sellers+NSE+crore&hl=en-IN&gl=IN&ceid=IN:en"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            headlines = [item.text.lower() for item in root.findall('.//item/title') if item.text][:8]
            buy_hits = sum(1 for h in headlines if any(w in h for w in ['fii buy', 'fiis buy', 'net buyers', 'inflow', 'fpi inflow', 'dii buy']))
            sell_hits = sum(1 for h in headlines if any(w in h for w in ['fii sell', 'fiis sell', 'net sellers', 'outflow', 'fpi outflow', 'offload']))
            if buy_hits > sell_hits + 1:
                sig = 'BULLISH'
            elif sell_hits > buy_hits + 1:
                sig = 'BEARISH'
            else:
                sig = 'NEUTRAL'
            return {
                'available': True,
                'buy_mentions': buy_hits,
                'sell_mentions': sell_hits,
                'combined_signal': sig,
                'headline_sample': headlines[0] if headlines else '',
                'timestamp': timestamp
            }
    except Exception:
        return default_res


def get_market_sentiment(symbol: str = None) -> dict:
    """Get combined market sentiment from all 3 sources."""
    timestamp = datetime.now().isoformat()
    
    news = fetch_news_sentiment(symbol) if symbol else None
    vix = fetch_india_vix()
    fii_dii = fetch_fii_dii_flows()
    
    score = 0
    
    if news:
        if news['sentiment_label'] == 'BULLISH':
            score += 1
        elif news['sentiment_label'] == 'BEARISH':
            score -= 1
            
    if vix['fear_level'] in ('EXTREME_GREED', 'GREED', 'NEUTRAL'):
        score += 1
    elif vix['fear_level'] in ('FEAR', 'EXTREME_FEAR', 'PANIC'):
        score -= 1
        
    if fii_dii['combined_signal'] in ('SUPER_BULLISH', 'BULLISH'):
        score += 1
    elif fii_dii['combined_signal'] in ('BEARISH', 'SUPER_BEARISH'):
        score -= 1
        
    if score >= 2:
        label = 'STRONG_BULLISH'
    elif score == 1:
        label = 'BULLISH'
    elif score == 0:
        label = 'NEUTRAL'
    elif score == -1:
        label = 'BEARISH'
    else:
        label = 'STRONG_BEARISH'
        
    summary_parts = []
    summary_parts.append(f"Sentiment {score}/3")
    if news:
        summary_parts.append(f"News:{news['sentiment_label']}")
    summary_parts.append(f"VIX:{vix['vix_value']}({vix['fear_level']})")
    summary_parts.append(f"FII:{fii_dii['combined_signal']}")
    
    return {
        'combined_score': score,
        'sentiment_label': label,
        'sentiment_passed': score >= 0,
        'can_trade': vix['can_trade'],
        'position_multiplier': vix['position_multiplier'],
        'news': news or {},
        'vix': vix,
        'fii_dii': fii_dii,
        'summary': " | ".join(summary_parts),
        'timestamp': timestamp
    }


def get_watchlist_sentiment(symbols: list) -> dict:
    """Get sentiment for entire watchlist."""
    timestamp = datetime.now().isoformat()
    
    vix = fetch_india_vix()
    fii_dii = fetch_fii_dii_flows()
    market_sentiment = {
        'vix': vix,
        'fii_dii': fii_dii
    }
    
    stock_sentiments = {}
    for sym in symbols:
        stock_sentiments[sym] = fetch_news_sentiment(sym)
        
    return {
        'market_sentiment': market_sentiment,
        'stock_sentiments': stock_sentiments,
        'can_trade': vix['can_trade'],
        'timestamp': timestamp
    }

import asyncio
import os
import signal
import sys
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
import aiohttp
import asyncio

import MetaTrader5 as mt5
import orjson
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json

# Config via environment variables
API_TOKEN = os.environ.get("API_TOKEN", "")
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
MT5_TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", "")

# News analysis configuration
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "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")
JBLANKED_API_URL = os.environ.get("JBLANKED_API_URL", "https://www.jblanked.com/news/api/forex-factory/calendar/today/")
JBLANKED_API_KEY = os.environ.get("JBLANKED_API_KEY", "32FEvnEZ")
NEWS_UPDATE_INTERVAL_HOURS = int(os.environ.get("NEWS_UPDATE_INTERVAL_HOURS", "24"))
NEWS_CACHE_MAX_ITEMS = int(os.environ.get("NEWS_CACHE_MAX_ITEMS", "100"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    kwargs = {}
    if MT5_TERMINAL_PATH:
        kwargs["path"] = MT5_TERMINAL_PATH
    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        print(f"MT5 initialize failed: {err}", file=sys.stderr, flush=True)
        raise RuntimeError(f"MT5 initialize failed: {err}")
    v = mt5.version()
    print(f"MT5 initialized. Version: {v}", flush=True)
    
    # Initialize news cache and start scheduler
    print("📰 Initializing news analysis system...")
    print(f"🔑 Perplexity API Key: {PERPLEXITY_API_KEY[:10]}...")
    print(f"🔑 Jblanked API Key: {JBLANKED_API_KEY}")
    news_task = asyncio.create_task(news_scheduler())
    print("📰 News scheduler started")
    
    yield
    
    # Shutdown
    news_task.cancel()
    try:
        await news_task
    except asyncio.CancelledError:
        pass
    mt5.shutdown()

app = FastAPI(title="MT5 Market Data Stream", version="2.0.0", lifespan=lifespan)

# Always add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],  # Allow all origins in development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Timeframe(str, Enum):
    M1 = "1M"
    M5 = "5M"
    M15 = "15M"
    M30 = "30M"
    H1 = "1H"
    H4 = "4H"
    D1 = "1D"
    W1 = "1W"

# MT5 timeframe mapping
MT5_TIMEFRAMES = {
    Timeframe.M1: mt5.TIMEFRAME_M1,
    Timeframe.M5: mt5.TIMEFRAME_M5,
    Timeframe.M15: mt5.TIMEFRAME_M15,
    Timeframe.M30: mt5.TIMEFRAME_M30,
    Timeframe.H1: mt5.TIMEFRAME_H1,
    Timeframe.H4: mt5.TIMEFRAME_H4,
    Timeframe.D1: mt5.TIMEFRAME_D1,
    Timeframe.W1: mt5.TIMEFRAME_W1,
}

class Tick(BaseModel):
    symbol: str
    time: int              # epoch ms
    time_iso: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    flags: Optional[int] = None

class OHLC(BaseModel):
    symbol: str
    timeframe: str
    time: int              # epoch ms
    time_iso: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None

class SubscriptionInfo(BaseModel):
    symbol: str
    timeframe: Timeframe
    subscription_time: datetime
    data_types: List[str]  # ["ticks", "ohlc"]

class NewsItem(BaseModel):
    headline: str
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None
    currency: Optional[str] = None
    impact: Optional[str] = None
    time: Optional[str] = None

class NewsAnalysis(BaseModel):
    headline: str
    forecast: Optional[str] = None
    previous: Optional[str] = None
    actual: Optional[str] = None
    currency: Optional[str] = None
    time: Optional[str] = None
    analysis: Dict[str, str]  # AI analysis results
    analyzed_at: datetime

def _to_tick(symbol: str, info) -> Optional[Tick]:
    if info is None:
        return None
    ts_ms = getattr(info, "time_msc", 0) or int(getattr(info, "time", 0)) * 1000
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return Tick(
        symbol=symbol,
        time=ts_ms,
        time_iso=dt.isoformat(),
        bid=getattr(info, "bid", None),
        ask=getattr(info, "ask", None),
        last=getattr(info, "last", None),
        volume=getattr(info, "volume_real", None) or getattr(info, "volume", None),
        flags=getattr(info, "flags", None),
    )

def ensure_symbol_selected(symbol: str) -> None:
    # First check if symbol exists at all
    info = mt5.symbol_info(symbol)
    
    if info is None:
        # Symbol doesn't exist - let's check what symbols are available
        all_symbols = mt5.symbols_get()
        if all_symbols:
            # Show first few available symbols for debugging
            sample_symbols = [s.name for s in all_symbols[:10]]
            print(f"Available symbols (first 10): {sample_symbols}")
            
            # Check if there's a case sensitivity issue
            upper_symbol = symbol.upper()
            lower_symbol = symbol.lower()
            
            # Try to find similar symbols
            similar_symbols = []
            for s in all_symbols:
                s_name = s.name
                if upper_symbol in s_name.upper() or lower_symbol in s_name.lower():
                    similar_symbols.append(s_name)
                    if len(similar_symbols) >= 5:  # Limit to 5 suggestions
                        break
            
            error_detail = f"Unknown symbol: '{symbol}'. "
            if similar_symbols:
                error_detail += f"Similar symbols found: {similar_symbols}"
            else:
                error_detail += f"Available symbols (first 10): {sample_symbols}"
            
            raise HTTPException(status_code=404, detail=error_detail)
        else:
            raise HTTPException(status_code=500, detail="No symbols available from MT5 - check connection")
    
    # Symbol exists, now try to select it
    if not mt5.symbol_select(symbol, True):
        if not info.visible:
            # Try to make it visible first
            if not mt5.symbol_select(symbol, True):
                raise HTTPException(status_code=400, detail=f"Failed to select symbol: {symbol} - symbol exists but cannot be selected")
        else:
            raise HTTPException(status_code=400, detail=f"Failed to select symbol: {symbol} - unknown error")

# Global OHLC cache: {symbol: {timeframe: deque([OHLC_bars])}}
global_ohlc_cache: Dict[str, Dict[str, deque]] = {}

# Global news cache
global_news_cache: List[NewsAnalysis] = []
news_cache_metadata: Dict[str, any] = {
    "last_updated": None,
    "next_update_time": None,
    "is_updating": False
}

def require_api_token_header(x_api_key: Optional[str] = None):
    # For REST: expect header "X-API-Key"
    if API_TOKEN and x_api_key != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

def _to_ohlc(symbol: str, timeframe: str, rate_data) -> Optional[OHLC]:
    """Convert MT5 rate data to OHLC model"""
    if rate_data is None:
        return None
    
    # MT5 returns numpy structured arrays, access by index: (time, open, high, low, close, tick_volume, spread, real_volume)
    try:
        ts_ms = int(rate_data[0]) * 1000  # time is at index 0
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        
        return OHLC(
            symbol=symbol,
            timeframe=timeframe,
            time=ts_ms,
            time_iso=dt.isoformat(),
            open=float(rate_data[1]),    # open is at index 1
            high=float(rate_data[2]),    # high is at index 2
            low=float(rate_data[3]),     # low is at index 3
            close=float(rate_data[4]),   # close is at index 4
            volume=float(rate_data[5])   # tick_volume is at index 5
        )
    except (IndexError, ValueError, TypeError) as e:
        print(f"Error converting rate data to OHLC: {e}")
        print(f"Rate data type: {type(rate_data)}")
        print(f"Rate data: {rate_data}")
        return None

def get_ohlc_data(symbol: str, timeframe: Timeframe, count: int = 100) -> List[OHLC]:
    """Get OHLC data from MT5"""
    print(f"📊 Fetching OHLC: {symbol} {timeframe.value} x{count}")
    
    ensure_symbol_selected(symbol)
    
    mt5_timeframe = MT5_TIMEFRAMES.get(timeframe)
    if mt5_timeframe is None:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
    
    # Get rates from MT5
    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
    
    if rates is None or len(rates) == 0:
        print(f"⚠️ No rates from MT5 for {symbol}")
        return []
    
    print(f"📊 Got {len(rates)} rates from MT5")
    
    # Convert to OHLC objects
    ohlc_data = []
    for rate in rates:
        ohlc = _to_ohlc(symbol, timeframe.value, rate)
        if ohlc:
            ohlc_data.append(ohlc)
    
    print(f"📊 Converted to {len(ohlc_data)} OHLC bars")
    return ohlc_data

def get_current_ohlc(symbol: str, timeframe: Timeframe) -> Optional[OHLC]:
    """Get current (most recent) OHLC bar"""
    data = get_ohlc_data(symbol, timeframe, 1)
    return data[0] if data else None

def calculate_next_update_time(subscription_time: datetime, timeframe: Timeframe) -> datetime:
    """Calculate when the next OHLC update should be sent"""
    if timeframe == Timeframe.M1:
        # Next minute boundary
        next_update = subscription_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
    elif timeframe == Timeframe.M5:
        # Next 5-minute boundary
        current_minute = subscription_time.minute
        next_minute = ((current_minute // 5) + 1) * 5
        if next_minute >= 60:
            next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_update = subscription_time.replace(minute=next_minute, second=0, microsecond=0)
    elif timeframe == Timeframe.M15:
        # Next 15-minute boundary
        current_minute = subscription_time.minute
        next_minute = ((current_minute // 15) + 1) * 15
        if next_minute >= 60:
            next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_update = subscription_time.replace(minute=next_minute, second=0, microsecond=0)
    elif timeframe == Timeframe.M30:
        # Next 30-minute boundary
        current_minute = subscription_time.minute
        next_minute = ((current_minute // 30) + 1) * 30
        if next_minute >= 60:
            next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_update = subscription_time.replace(minute=next_minute, second=0, microsecond=0)
    elif timeframe == Timeframe.H1:
        # Next hour boundary
        next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif timeframe == Timeframe.H4:
        # Next 4-hour boundary
        current_hour = subscription_time.hour
        next_hour = ((current_hour // 4) + 1) * 4
        if next_hour >= 24:
            next_update = subscription_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            next_update = subscription_time.replace(hour=next_hour, minute=0, second=0, microsecond=0)
    elif timeframe == Timeframe.D1:
        # Next day boundary (midnight UTC)
        next_update = subscription_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    elif timeframe == Timeframe.W1:
        # Next Monday midnight UTC
        days_ahead = 7 - subscription_time.weekday()
        if days_ahead == 7:  # Already Monday
            days_ahead = 7
        next_update = subscription_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    else:
        # Default to 1 minute
        next_update = subscription_time + timedelta(minutes=1)
    
    return next_update

def update_ohlc_cache(symbol: str, timeframe: Timeframe, max_bars: int = 100):
    """Update the global OHLC cache for a symbol/timeframe"""
    global global_ohlc_cache
    
    if symbol not in global_ohlc_cache:
        global_ohlc_cache[symbol] = {}
    
    if timeframe.value not in global_ohlc_cache[symbol]:
        global_ohlc_cache[symbol][timeframe.value] = deque(maxlen=max_bars)
    
    # Get current OHLC data
    current_ohlc = get_current_ohlc(symbol, timeframe)
    if current_ohlc is None:
        return
    
    cache = global_ohlc_cache[symbol][timeframe.value]
    
    # If cache is empty or this is a new time period, add the bar
    if not cache or cache[-1].time != current_ohlc.time:
        cache.append(current_ohlc)
    else:
        # Update the current bar (same time period)
        cache[-1] = current_ohlc

def get_cached_ohlc(symbol: str, timeframe: Timeframe, count: int = 100) -> List[OHLC]:
    """Get OHLC data from cache, fetch from MT5 if not cached"""
    global global_ohlc_cache
    
    if symbol not in global_ohlc_cache:
        global_ohlc_cache[symbol] = {}
    
    if timeframe.value not in global_ohlc_cache[symbol]:
        # Not cached, fetch from MT5
        print(f"📡 Cache miss - fetching from MT5: {symbol} {timeframe.value}")
        ohlc_data = get_ohlc_data(symbol, timeframe, count)
        global_ohlc_cache[symbol][timeframe.value] = deque(ohlc_data, maxlen=count)
        return ohlc_data
    
    # Return cached data
    cached_data = list(global_ohlc_cache[symbol][timeframe.value])
    print(f"📦 Cache hit: {symbol} {timeframe.value} ({len(cached_data)} bars)")
    return cached_data

async def fetch_jblanked_news() -> List[NewsItem]:
    """Fetch news data from Jblanked API"""
    try:
        print("📰 Fetching news from Jblanked API...")
        headers = {
            "Authorization": f"Api-Key {JBLANKED_API_KEY}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(JBLANKED_API_URL, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"📰 Raw API response type: {type(data)}")
                    print(f"📰 Raw API response: {data}")
                    news_items = []
                    
                    # Handle different response formats
                    if isinstance(data, list):
                        # API returns list directly
                        items = data
                    elif isinstance(data, dict) and 'data' in data:
                        # API returns object with data field
                        items = data['data']
                    else:
                        # Try to find any array in the response
                        items = []
                        if isinstance(data, dict):
                            for key, value in data.items():
                                if isinstance(value, list):
                                    items = value
                                    break
                    
                    print(f"📰 Processing {len(items) if items else 0} items from response")
                    
                    # Process the API response
                    for item in items:
                        if isinstance(item, dict):
                            print(f"📰 Processing item: {item}")
                            print(f"📰 Available fields: {list(item.keys())}")
                            
                            # Extract fields with debugging
                            headline = item.get('Name', '') or item.get('title', '') or item.get('headline', '') or item.get('name', '')
                            forecast = item.get('Forecast', '') or item.get('forecast', '') or item.get('expected', '')
                            previous = item.get('Previous', '') or item.get('previous', '') or item.get('prev', '')
                            actual = item.get('Actual', '') or item.get('actual', '') or item.get('result', '')
                            currency = item.get('Currency', '') or item.get('currency', '') or item.get('ccy', '') or item.get('country', '')
                            impact = item.get('Strength', '') or item.get('impact', '') or item.get('importance', '')
                            time = item.get('Date', '') or item.get('time', '') or item.get('date', '') or item.get('timestamp', '')
                            
                            # Additional context fields from Jblanked API
                            outcome = item.get('Outcome', '')
                            quality = item.get('Quality', '')
                            
                            # Enhanced headline with context if available
                            if outcome and headline:
                                headline = f"{headline} ({outcome})"
                            if quality and headline:
                                headline = f"{headline} - {quality}"
                            
                            # Convert numeric values to strings for Pydantic validation
                            if isinstance(forecast, (int, float)):
                                forecast = str(forecast)
                            if isinstance(previous, (int, float)):
                                previous = str(previous)
                            if isinstance(actual, (int, float)):
                                actual = str(actual)
                            
                            # Handle empty strings - convert to None if empty
                            if headline == '':
                                headline = None
                            if forecast == '':
                                forecast = None
                            if previous == '':
                                previous = None
                            if actual == '':
                                actual = None
                            if currency == '':
                                currency = None
                            if impact == '':
                                impact = None
                            if time == '':
                                time = None
                            
                            print(f"📰 Mapped fields:")
                            print(f"   Headline: '{headline}'")
                            print(f"   Forecast: '{forecast}'")
                            print(f"   Previous: '{previous}'")
                            print(f"   Actual: '{actual}'")
                            print(f"   Currency: '{currency}'")
                            print(f"   Time: '{time}'")
                            
                            news_item = NewsItem(
                                headline=headline,
                                forecast=forecast,
                                previous=previous,
                                actual=actual,
                                currency=currency,
                                impact=impact,
                                time=time
                            )
                            news_items.append(news_item)
                    
                    print(f"📰 Fetched {len(news_items)} news items from Jblanked API")
                    return news_items
                else:
                    print(f"❌ Jblanked API error: {response.status}")
                    text = await response.text()
                    print(f"   Response: {text}")
                    return []
    except Exception as e:
        print(f"❌ Error fetching Jblanked API news: {e}")
        import traceback
        traceback.print_exc()
        return []

async def analyze_news_with_perplexity(news_item: NewsItem) -> Optional[NewsAnalysis]:
    prompt = (
        "Analyze the following economic news for Forex trading impact.\n"
        f"News: {news_item.headline}\n"
        f"Forecast: {news_item.forecast or 'N/A'}\n"
        f"Previous: {news_item.previous or 'N/A'}\n"
        f"Actual: {news_item.actual or 'N/A'}\n"
        "Provide:\n"
        "1. Expected effect (Bullish, Bearish, Neutral).\n"
        "2. Which currencies are most impacted.\n"
        "3. Suggested currency pairs to monitor."
    )

    # 🔒 Required auth header & stable endpoint
    url = "https://api.perplexity.ai/chat/completions"  # official endpoint
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",  # the only supported auth
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "fx-news-analyzer/1.0"
    }

    # ✅ Use a model that is available via the Chat Completions API
    # You can use "sonar", "sonar-pro", "sonar-reasoning" or "sonar-deep-research".
    # If you hit plan/permission issues, fall back to "sonar".
    payload = {
        "model": "sonar",  # try "sonar-deep-research" if your account has access
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.1
    }

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    backoff = [0.5, 1.5, 3.0]  # simple retry for transient errors

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt, delay in enumerate([0] + backoff):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with session.post(url, headers=headers, json=payload) as resp:
                    text = await resp.text()
                    if resp.status == 200:
                        data = json.loads(text)
                        analysis_text = data["choices"][0]["message"]["content"]
                        effect = "Neutral"
                        lt = analysis_text.lower()
                        if "bullish" in lt:
                            effect = "Bullish"
                        elif "bearish" in lt:
                            effect = "Bearish"

                        analysis = {
                            "effect": effect,
                            "currencies_impacted": "Multiple",
                            "currency_pairs": "Major pairs",
                            "full_analysis": analysis_text
                        }
                        return NewsAnalysis(
                            headline=news_item.headline,
                            forecast=news_item.forecast,
                            previous=news_item.previous,
                            actual=news_item.actual,
                            currency=news_item.currency,
                            time=news_item.time,
                            analysis=analysis,
                            analyzed_at=datetime.now(timezone.utc)
                        )
                    elif resp.status in (429, 500, 502, 503, 504) and attempt < len(backoff):
                        # retry on transient errors
                        continue
                    else:
                        # useful server message for debugging
                        raise RuntimeError(f"Perplexity API {resp.status}: {text}")
            except asyncio.TimeoutError:
                if attempt >= len(backoff):
                    raise
                continue

async def update_news_cache():
    """Update the global news cache with fresh data"""
    global global_news_cache, news_cache_metadata
    
    if news_cache_metadata["is_updating"]:
        print("📰 News update already in progress, skipping...")
        return
    
    try:
        news_cache_metadata["is_updating"] = True
        print("📰 Starting news cache update...")
        
        # Fetch news from Jblanked API
        news_items = await fetch_jblanked_news()
        if not news_items:
            print("⚠️ No news items fetched, keeping existing cache")
            return
        
        # Analyze each news item
        analyzed_news = []
        for news_item in news_items:
            try:
                # Add timeout for each news analysis
                analysis = await asyncio.wait_for(
                    analyze_news_with_perplexity(news_item), 
                    timeout=60.0  # 60 seconds per news item
                )
                if analysis:
                    analyzed_news.append(analysis)
                # Small delay to respect API rate limits
                await asyncio.sleep(0.1)
            except asyncio.TimeoutError:
                print(f"⏰ Timeout analyzing news: {news_item.headline[:50]}...")
                continue
            except Exception as e:
                print(f"❌ Error analyzing news: {news_item.headline[:50]}... Error: {e}")
                continue
        
        if analyzed_news:
            global_news_cache = analyzed_news[:NEWS_CACHE_MAX_ITEMS]
            news_cache_metadata["last_updated"] = datetime.now(timezone.utc)
            news_cache_metadata["next_update_time"] = datetime.now(timezone.utc) + timedelta(hours=NEWS_UPDATE_INTERVAL_HOURS)
            
            print(f"✅ News cache updated: {len(global_news_cache)} items")
            print(f"⏰ Next update scheduled for: {news_cache_metadata['next_update_time']}")
        else:
            print("⚠️ No news analyzed successfully, storing raw news data as fallback")
            # Fallback: store raw news data without AI analysis
            raw_news = []
            for news_item in news_items:
                raw_analysis = NewsAnalysis(
                    headline=news_item.headline,
                    forecast=news_item.forecast,
                    previous=news_item.previous,
                    actual=news_item.actual,
                    currency=news_item.currency,
                    time=news_item.time,
                    analysis={
                        "effect": "Unknown",
                        "currencies_impacted": "Unknown",
                        "currency_pairs": "Unknown",
                        "full_analysis": "AI analysis failed - raw data only"
                    },
                    analyzed_at=datetime.now(timezone.utc)
                )
                raw_news.append(raw_analysis)
            
            global_news_cache = raw_news[:NEWS_CACHE_MAX_ITEMS]
            news_cache_metadata["last_updated"] = datetime.now(timezone.utc)
            news_cache_metadata["next_update_time"] = datetime.now(timezone.utc) + timedelta(hours=NEWS_UPDATE_INTERVAL_HOURS)
            
            print(f"✅ News cache updated with raw data: {len(global_news_cache)} items")
            print(f"⏰ Next update scheduled for: {news_cache_metadata['next_update_time']}")
            print("💡 Tip: Check Perplexity API key and run test_perplexity_auth.py to debug authentication")
            
    except Exception as e:
        print(f"❌ Error updating news cache: {e}")
        import traceback
        traceback.print_exc()
    finally:
        news_cache_metadata["is_updating"] = False

async def news_scheduler():
    """Background task to schedule news updates every 24 hours"""
    while True:
        try:
            current_time = datetime.now(timezone.utc)
            
            # Check if we need to update
            if (news_cache_metadata["next_update_time"] is None or 
                current_time >= news_cache_metadata["next_update_time"]):
                await update_news_cache()
            
            # Wait for 1 hour before checking again
            await asyncio.sleep(3600)  # 1 hour
            
        except Exception as e:
            print(f"❌ Error in news scheduler: {e}")
            await asyncio.sleep(3600)  # Wait 1 hour before retrying

@app.get("/health")
def health():
    v = mt5.version()
    return {"status": "ok", "mt5_version": v}

@app.get("/test-ws")
def test_websocket():
    return {"message": "WebSocket endpoint available at /ws/market"}

@app.get("/api/ohlc/{symbol}")
def get_ohlc(symbol: str, timeframe: str = Query("1M"), count: int = Query(100, le=500), x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Get OHLC data for a symbol and timeframe"""
    try:
        tf = Timeframe(timeframe)
        sym = symbol.upper()
        ohlc_data = get_ohlc_data(sym, tf, count)
        return {
            "symbol": sym,
            "timeframe": timeframe,
            "count": len(ohlc_data),
            "data": [ohlc.model_dump() for ohlc in ohlc_data]
        }
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tick/{symbol}")
def get_tick(symbol: str, x_api_key: Optional[str] = Depends(require_api_token_header)):
    sym = symbol.upper()
    ensure_symbol_selected(sym)
    info = mt5.symbol_info_tick(sym)
    tick = _to_tick(sym, info)
    if tick is None:
        raise HTTPException(status_code=404, detail="No tick available")
    return tick.model_dump()

@app.get("/api/symbols")
def search_symbols(q: str = Query(..., min_length=1), x_api_key: Optional[str] = Depends(require_api_token_header)):
    q_upper = q.upper()
    res = []
    for s in mt5.symbols_get():
        name = getattr(s, "name", "")
        if q_upper in name.upper():
            res.append({"name": name, "path": getattr(s, "path", "")})
            if len(res) >= 50:
                break
    return {"results": res}

@app.get("/api/news/analysis")
def get_news_analysis(x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Get all cached news analysis data"""
    global global_news_cache, news_cache_metadata
    
    return {
        "news_count": len(global_news_cache),
        "last_updated": news_cache_metadata["last_updated"],
        "next_update": news_cache_metadata["next_update_time"],
        "is_updating": news_cache_metadata["is_updating"],
        "data": [news.model_dump() for news in global_news_cache]
    }

@app.post("/api/news/refresh")
async def refresh_news_manual(x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Manually trigger news refresh (for testing)"""
    await update_news_cache()
    return {"message": "News refresh triggered", "status": "success"}

class WSClient:
    def __init__(self, websocket: WebSocket, token: str):
        self.websocket = websocket
        self.token = token
        # Legacy tick subscriptions
        self.symbols: Set[str] = set()
        self._last_sent_ts: Dict[str, int] = {}
        # New subscription model
        self.subscriptions: Dict[str, SubscriptionInfo] = {}  # symbol -> subscription info
        self.next_ohlc_updates: Dict[str, datetime] = {}  # symbol -> next update time
        self._task: Optional[asyncio.Task] = None
        self._ohlc_task: Optional[asyncio.Task] = None
        self._send_interval_s: float = 0.10  # 10 Hz for ticks

    async def start(self):
        # WebSocket is already accepted in the main handler
        # Start both tick and OHLC background tasks
        self._task = asyncio.create_task(self._tick_loop())
        self._ohlc_task = asyncio.create_task(self._ohlc_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ohlc_task:
            self._ohlc_task.cancel()
            try:
                await self._ohlc_task
            except asyncio.CancelledError:
                pass

    async def _tick_loop(self):
        """Handle real-time tick streaming"""
        try:
            while True:
                await self._send_tick_updates()
                await asyncio.sleep(self._send_interval_s)
        except asyncio.CancelledError:
            return
    
    async def _ohlc_loop(self):
        """Handle scheduled OHLC updates"""
        try:
            while True:
                await self._send_scheduled_ohlc_updates()
                await asyncio.sleep(1.0)  # Check every second for due updates
        except asyncio.CancelledError:
            return

    async def _send_tick_updates(self):
        """Send real-time tick updates for subscribed symbols"""
        tick_symbols = set()
        
        # Collect symbols that need tick updates
        for symbol, sub_info in self.subscriptions.items():
            if "ticks" in sub_info.data_types:
                tick_symbols.add(symbol)
        
        # Also include legacy tick subscriptions
        tick_symbols.update(self.symbols)
        
        if not tick_symbols:
            return
            
        updates: List[dict] = []
        for sym in list(tick_symbols):
            try:
                ensure_symbol_selected(sym)
                info = mt5.symbol_info_tick(sym)
                if info is None:
                    continue
                ts_ms = getattr(info, "time_msc", 0) or int(getattr(info, "time", 0)) * 1000
                if self._last_sent_ts.get(sym) == ts_ms:
                    continue
                tick = _to_tick(sym, info)
                if tick:
                    updates.append(tick.model_dump())
                    self._last_sent_ts[sym] = ts_ms
                    
                    # Update OHLC cache for this symbol if we have OHLC subscriptions
                    if sym in self.subscriptions and "ohlc" in self.subscriptions[sym].data_types:
                        update_ohlc_cache(sym, self.subscriptions[sym].timeframe)
                        
            except HTTPException:
                # symbol disappeared or invalid; drop it
                if sym in self.symbols:
                    self.symbols.discard(sym)
                if sym in self.subscriptions:
                    del self.subscriptions[sym]
                    
        if updates:
            await self.websocket.send_bytes(orjson.dumps({"type": "ticks", "data": updates}))
    
    async def _send_scheduled_ohlc_updates(self):
        """Send OHLC updates when timeframe periods complete"""
        current_time = datetime.now(timezone.utc)
        
        for symbol, next_update_time in list(self.next_ohlc_updates.items()):
            if current_time >= next_update_time:
                try:
                    sub_info = self.subscriptions.get(symbol)
                    if sub_info and "ohlc" in sub_info.data_types:
                        # Get current OHLC data from cache
                        cached_data = get_cached_ohlc(symbol, sub_info.timeframe, 1)
                        if cached_data:
                            current_ohlc = cached_data[-1]
                            await self.websocket.send_json({
                                "type": "ohlc_update",
                                "data": current_ohlc.model_dump()
                            })
                        
                        # Schedule next update
                        self.next_ohlc_updates[symbol] = calculate_next_update_time(
                            current_time, sub_info.timeframe
                        )
                        
                except Exception as e:
                    print(f"❌ Error sending OHLC update for {symbol}: {e}")
                    # Remove problematic subscription
                    if symbol in self.subscriptions:
                        del self.subscriptions[symbol]
                    if symbol in self.next_ohlc_updates:
                        del self.next_ohlc_updates[symbol]

    async def handle_message(self, message: dict):
        action = message.get("action")
        
        if action == "subscribe":
            # New subscription format
            symbol = message.get("symbol", "")
            timeframe = message.get("timeframe", "1M")
            data_types = message.get("data_types", ["ticks", "ohlc"])
            
            if not symbol:
                await self.websocket.send_json({"type": "error", "error": "symbol_required"})
                return
            
            try:
                # Validate timeframe
                tf = Timeframe(timeframe)
                ensure_symbol_selected(symbol)
                
                # Create subscription info
                sub_info = SubscriptionInfo(
                    symbol=symbol,
                    timeframe=tf,
                    subscription_time=datetime.now(timezone.utc),
                    data_types=data_types
                )
                
                self.subscriptions[symbol] = sub_info
                
                # Send initial OHLC data if requested
                if "ohlc" in data_types:
                    try:
                        print(f"📊 Fetching initial OHLC data for {symbol} ({timeframe})")
                        ohlc_data = get_cached_ohlc(symbol, tf, 100)
                        print(f"📊 Got {len(ohlc_data) if ohlc_data else 0} OHLC bars for {symbol}")
                        
                        if ohlc_data:
                            await self.websocket.send_json({
                                "type": "initial_ohlc",
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "data": [ohlc.model_dump() for ohlc in ohlc_data]
                            })
                            print(f"✅ Sent initial OHLC data for {symbol}: {len(ohlc_data)} bars")
                        else:
                            print(f"⚠️ No OHLC data available for {symbol}")
                        
                        # Schedule next OHLC update
                        self.next_ohlc_updates[symbol] = calculate_next_update_time(
                            sub_info.subscription_time, tf
                        )
                        print(f"⏰ Scheduled next OHLC update for {symbol} at {self.next_ohlc_updates[symbol]}")
                        
                    except Exception as e:
                        print(f"❌ Error getting initial OHLC for {symbol}: {e}")
                        import traceback
                        traceback.print_exc()
                        await self.websocket.send_json({
                            "type": "error", 
                            "error": f"failed_to_get_ohlc: {str(e)}"
                        })
                        return
                
                await self.websocket.send_json({
                    "type": "subscribed",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "data_types": data_types
                })
                
            except ValueError:
                await self.websocket.send_json({"type": "error", "error": f"invalid_timeframe: {timeframe}"})
            except HTTPException as e:
                await self.websocket.send_json({"type": "error", "error": str(e.detail)})
            except Exception as e:
                await self.websocket.send_json({"type": "error", "error": str(e)})
                
        elif action == "subscribe_legacy":
            # Legacy tick-only subscription
            syms = message.get("symbols", [])
            for s in syms:
                ensure_symbol_selected(s)
                self.symbols.add(s)
            await self.websocket.send_json({"type": "subscribed", "symbols": sorted(self.symbols)})
            
        elif action == "unsubscribe":
            symbol = message.get("symbol", "")
            if symbol:
                if symbol in self.subscriptions:
                    del self.subscriptions[symbol]
                if symbol in self.next_ohlc_updates:
                    del self.next_ohlc_updates[symbol]
                self.symbols.discard(symbol)
                await self.websocket.send_json({"type": "unsubscribed", "symbol": symbol})
            else:
                # Legacy unsubscribe
                syms = message.get("symbols", [])
                for s in syms:
                    self.symbols.discard(s)
                    if s in self.subscriptions:
                        del self.subscriptions[s]
                    if s in self.next_ohlc_updates:
                        del self.next_ohlc_updates[s]
                await self.websocket.send_json({"type": "unsubscribed", "symbols": sorted(syms)})
                
        elif action == "ping":
            await self.websocket.send_json({"type": "pong"})
        else:
            await self.websocket.send_json({"type": "error", "error": "unknown_action"})

# Keep legacy endpoint for backward compatibility
@app.websocket("/ws/ticks")
async def ws_ticks_legacy(websocket: WebSocket):
    """Legacy WebSocket endpoint for tick-only streaming"""
    print("🔌 Legacy WebSocket connection attempt received")
    client = None
    
    try:
        await websocket.accept()
        await websocket.send_json({"type": "connected", "message": "Legacy tick WebSocket connected"})
        
        client = WSClient(websocket, "")
        await client.start()
        
        while True:
            data = await websocket.receive_text()
            try:
                message = orjson.loads(data)
                # Force legacy behavior
                if message.get("action") == "subscribe":
                    message["action"] = "subscribe_legacy"
                await client.handle_message(message)
            except Exception as parse_error:
                await websocket.send_json({"type": "error", "error": str(parse_error)})
                
    except WebSocketDisconnect:
        print("🔌 Legacy WebSocket disconnected")
    except Exception as e:
        print(f"❌ Legacy WebSocket error: {e}")
    finally:
        if client:
            await client.stop()

@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    print("🔌 WebSocket connection attempt received")
    client = None
    
    try:
        await websocket.accept()
        print("✅ WebSocket connection accepted")
        
        # Send a welcome message
        await websocket.send_json({
            "type": "connected", 
            "message": "WebSocket connected successfully",
            "supported_timeframes": [tf.value for tf in Timeframe],
            "supported_data_types": ["ticks", "ohlc"]
        })
        print("📤 Sent welcome message")
        
        # Create WSClient for real MT5 data
        client = WSClient(websocket, "")
        await client.start()
        print("📊 WSClient started with MT5 integration")
        
        # Handle incoming messages
        while True:
            data = await websocket.receive_text()
            print(f"📥 Received: {data}")
            
            try:
                message = orjson.loads(data)
                print(f"📋 Parsed message: {message}")
                await client.handle_message(message)
                
            except Exception as parse_error:
                print(f"❌ Error parsing message: {parse_error}")
                await websocket.send_json({"type": "error", "error": str(parse_error)})
                
    except WebSocketDisconnect:
        print("🔌 WebSocket disconnected normally")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🧹 Cleaning up WSClient...")
        if client:
            await client.stop()

def _install_sigterm_handler(loop: asyncio.AbstractEventLoop):
    def _handler():
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()
    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handler)
    except NotImplementedError:
        # Windows without Proactor might not support signal handlers; ignore.
        pass

if __name__ == "__main__":
    # Run with: python server.py
    import uvicorn
    print("🚀 Starting MT5 Market Data Server...")
    print("📊 Available endpoints:")
    print("   - WebSocket (new): ws://localhost:8000/ws/market")
    print("   - WebSocket (legacy): ws://localhost:8000/ws/ticks")
    print("   - REST OHLC: GET /api/ohlc/{symbol}?timeframe=1M&count=100")
    print("   - News Analysis: GET /api/news/analysis")
    print("   - News Refresh: POST /api/news/refresh")
    print("   - Health check: GET /health")
    print("")
    print("📋 Supported timeframes: 1M, 5M, 15M, 30M, 1H, 4H, 1D, 1W")
    print("📋 Supported data types: ticks, ohlc")
    print("📰 News analysis: Auto-updates every 24 hours via Jblanked API + Perplexity AI (sonar-deep-research)")
    print("🔧 Test news: python test_news_simple.py")
    print("🔑 Test Perplexity auth: python test_perplexity_auth.py")
    print("")
    
    _install_sigterm_handler(asyncio.get_event_loop())
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False, server_header=False, date_header=False)

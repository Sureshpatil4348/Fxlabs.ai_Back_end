import asyncio
import os
import signal
import sys
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

import MetaTrader5 as mt5
import orjson
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    API_TOKEN,
    ALLOWED_ORIGINS,
    MT5_TERMINAL_PATH,
)
import app.news as news
from app.models import Timeframe, Tick, OHLC, SubscriptionInfo, NewsItem, NewsAnalysis
from app.mt5_utils import (
    MT5_TIMEFRAMES,
    ensure_symbol_selected,
    _to_tick,
    get_ohlc_data,
    get_current_ohlc,
    calculate_next_update_time,
    update_ohlc_cache,
    get_cached_ohlc,
)

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
    news_task = asyncio.create_task(news.news_scheduler())    
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
"""Models are defined in app.models"""

"""OHLC model is imported from app.models"""

"""SubscriptionInfo model is imported from app.models"""

"""NewsItem model is imported from app.models"""

"""NewsAnalysis model is imported from app.models"""

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

def get_ohlc_data(symbol: str, timeframe: Timeframe, count: int = 250) -> List[OHLC]:
    """Get OHLC data from MT5"""
    
    ensure_symbol_selected(symbol)
    
    mt5_timeframe = MT5_TIMEFRAMES.get(timeframe)
    if mt5_timeframe is None:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
    
    # Get rates from MT5
    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
    
    if rates is None or len(rates) == 0:
        print(f"⚠️ No rates from MT5 for {symbol}")
        return []
    
    
    # Convert to OHLC objects
    ohlc_data = []
    for rate in rates:
        ohlc = _to_ohlc(symbol, timeframe.value, rate)
        if ohlc:
            ohlc_data.append(ohlc)
    
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
    return cached_data


@app.get("/health")
def health():
    v = mt5.version()
    return {"status": "ok", "mt5_version": v}

@app.get("/test-ws")
def test_websocket():
    return {"message": "WebSocket endpoint available at /ws/market"}

@app.get("/api/ohlc/{symbol}")
def get_ohlc(symbol: str, timeframe: str = Query("1M"), count: int = Query(250, le=500), x_api_key: Optional[str] = Depends(require_api_token_header)):
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
    return {
        "news_count": len(news.global_news_cache),
        "last_updated": news.news_cache_metadata["last_updated"],
        "next_update": news.news_cache_metadata["next_update_time"],
        "is_updating": news.news_cache_metadata["is_updating"],
        "data": [item.model_dump() for item in news.global_news_cache]
    }

@app.post("/api/news/refresh")
async def refresh_news_manual(x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Manually trigger news refresh (for testing)"""
    await news.update_news_cache()
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
                        ohlc_data = get_cached_ohlc(symbol, tf, 250)
                        
                        if ohlc_data:
                            await self.websocket.send_json({
                                "type": "initial_ohlc",
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "data": [ohlc.model_dump() for ohlc in ohlc_data]
                            })
                        else:
                            print(f"⚠️ No OHLC data available for {symbol}")
                        
                        # Schedule next OHLC update
                        self.next_ohlc_updates[symbol] = calculate_next_update_time(
                            sub_info.subscription_time, tf
                        )
                        
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
    client = None
    
    try:
        await websocket.accept()
        
        # Send a welcome message
        await websocket.send_json({
            "type": "connected", 
            "message": "WebSocket connected successfully",
            "supported_timeframes": [tf.value for tf in Timeframe],
            "supported_data_types": ["ticks", "ohlc"]
        })
        
        # Create WSClient for real MT5 data
        client = WSClient(websocket, "")
        await client.start()
        
        # Handle incoming messages
        while True:
            data = await websocket.receive_text()
            
            try:
                message = orjson.loads(data)
                await client.handle_message(message)
                
            except Exception as parse_error:
                print(f"❌ Error parsing message: {parse_error}")
                await websocket.send_json({"type": "error", "error": str(parse_error)})
                
    except WebSocketDisconnect:
        print("Websocket Disconnected")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        import traceback
        traceback.print_exc()
    finally:
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
    
    _install_sigterm_handler(asyncio.get_event_loop())
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False, server_header=False, date_header=False)

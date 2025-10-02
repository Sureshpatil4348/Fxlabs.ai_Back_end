import asyncio
import os
import signal
import sys
from collections import deque, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any
import re

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    print("Warning: MetaTrader5 not available on this platform. Some features may be limited.")
    mt5 = None
    MT5_AVAILABLE = False
import orjson
import logging
from app.logging_config import configure_logging
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    API_TOKEN,
    ALLOWED_ORIGINS,
    MT5_TERMINAL_PATH,
)
import app.news as news
from app.alert_cache import alert_cache
from app.rsi_tracker_alert_service import rsi_tracker_alert_service
from app.rsi_correlation_tracker_alert_service import rsi_correlation_tracker_alert_service
from app.heatmap_tracker_alert_service import heatmap_tracker_alert_service
from app.heatmap_indicator_tracker_alert_service import heatmap_indicator_tracker_alert_service
from app.email_service import email_service
from app.daily_mail_service import daily_mail_scheduler
from app.models import (
    Timeframe,
    Tick,
    OHLC,
    SubscriptionInfo,
    NewsItem,
    NewsAnalysis,
    PriceBasis,
    OHLCSchema,
)
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

# Ensure logging has timestamps across the app
configure_logging()

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
    
    # Initialize news cache and start scheduler (loads FS cache on start)
    news_task = asyncio.create_task(news.news_scheduler())
    # Start news reminder 1-minute scheduler
    news_reminder_task = asyncio.create_task(news.news_reminder_scheduler())
    # Start daily morning brief scheduler (09:00 IST)
    daily_task = asyncio.create_task(daily_mail_scheduler())

    # Start minute alerts scheduler (fetch + evaluate RSI Tracker)
    global _minute_scheduler_task, _minute_scheduler_running
    _minute_scheduler_running = True
    _minute_scheduler_task = asyncio.create_task(_minute_alerts_scheduler())
    
    yield
    
    # Shutdown
    news_task.cancel()
    news_reminder_task.cancel()
    daily_task.cancel()
    if _minute_scheduler_task:
        _minute_scheduler_task.cancel()
    try:
        await news_task
    except asyncio.CancelledError:
        pass
    try:
        await news_reminder_task
    except asyncio.CancelledError:
        pass
    try:
        await daily_task
    except asyncio.CancelledError:
        pass
    if _minute_scheduler_task:
        try:
            await _minute_scheduler_task
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

# OHLC caching is centralized in app.mt5_utils (get_cached_ohlc/update_ohlc_cache)

# Global news cache
global_news_cache: List[NewsAnalysis] = []
news_cache_metadata: Dict[str, any] = {
    "last_updated": None,
    "next_update_time": None,
    "is_updating": False
}

# Minute-based alert scheduler
ENABLE_TICK_TRIGGERED_ALERTS = False  # Tick-driven checks disabled
_minute_scheduler_task: Optional[asyncio.Task] = None
_minute_scheduler_running: bool = False

# Rate limiting for test emails
test_email_rate_limits: Dict[str, List[datetime]] = defaultdict(list)
TEST_EMAIL_RATE_LIMIT = 5  # Max 5 test emails per hour per API key
TEST_EMAIL_RATE_WINDOW = timedelta(hours=1)

# Allowed domains for test emails (configurable via environment)
ALLOWED_EMAIL_DOMAINS = os.environ.get("ALLOWED_EMAIL_DOMAINS", "gmail.com,yahoo.com,outlook.com,hotmail.com").split(",")
ALLOWED_EMAIL_DOMAINS = [domain.strip().lower() for domain in ALLOWED_EMAIL_DOMAINS if domain.strip()]

def require_api_token_header(x_api_key: Optional[str] = None):
    # For REST: expect header "X-API-Key"
    if API_TOKEN and x_api_key != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

"""Unsubscribe feature removed per spec: no unsubscribe token validation."""

def check_test_email_rate_limit(api_key: str) -> bool:
    """Check if API key has exceeded test email rate limit"""
    now = datetime.now(timezone.utc)
    cutoff_time = now - TEST_EMAIL_RATE_WINDOW
    
    # Clean old entries
    test_email_rate_limits[api_key] = [
        timestamp for timestamp in test_email_rate_limits[api_key] 
        if timestamp > cutoff_time
    ]
    
    # Check if under limit
    if len(test_email_rate_limits[api_key]) >= TEST_EMAIL_RATE_LIMIT:
        return False
    
    # Record this request
    test_email_rate_limits[api_key].append(now)
    return True

def validate_test_email_recipient(email: str) -> bool:
    """Validate that email recipient is allowed"""
    if not email or not isinstance(email, str):
        return False
    
    # Basic email format validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return False
    
    # Extract domain
    domain = email.split('@')[1].lower()
    
    # Check if domain is allowed
    return domain in ALLOWED_EMAIL_DOMAINS

    # (helpers removed; using app.mt5_utils for OHLC conversion, fetch, caching, and scheduling)


async def _get_user_tracked_symbols(user_email: str) -> Set[str]:
    """Return the set of unique symbols tracked by a user across all active alerts.

    This inspects the in-memory alert_cache and supports both simple pair lists
    (e.g., ["EURUSD", "GBPUSD"]) and correlation-style lists of lists
    (e.g., [["EURUSD", "GBPUSD"], ["USDJPY", "GBPUSD"]]). It also tolerates
    legacy cache objects where correlation pairs might be stored under
    "correlation_pairs".
    """
    symbols: Set[str] = set()
    try:
        all_alerts = await alert_cache.get_all_alerts()
    except Exception:
        all_alerts = {}

    for _uid, alerts in all_alerts.items():
        for alert in alerts:
            if alert.get("user_email") != user_email:
                continue
            if not alert.get("is_active", True):
                continue

            pairs = alert.get("pairs", [])
            if isinstance(pairs, list):
                if all(isinstance(p, str) for p in pairs):
                    symbols.update(pairs)
                elif all(isinstance(p, list) for p in pairs):
                    for combo in pairs:
                        for sym in combo:
                            if isinstance(sym, str):
                                symbols.add(sym)

            corr_pairs = alert.get("correlation_pairs")
            if isinstance(corr_pairs, list) and all(isinstance(cp, list) for cp in corr_pairs):
                for combo in corr_pairs:
                    for sym in combo:
                        if isinstance(sym, str):
                            symbols.add(sym)

    return symbols


async def _minute_alerts_scheduler():
    """Every 5 minutes: refresh alerts and evaluate all alert types."""
    try:
        logger = logging.getLogger(__name__)
        while _minute_scheduler_running:
            try:
                await alert_cache._refresh_cache()
            except Exception:
                pass
            try:
                rsi_trig = await rsi_tracker_alert_service.check_rsi_tracker_alerts()
                logger.info("🔎 rsi_tracker_eval | triggers: %d", len(rsi_trig))
            except Exception as e:
                print(f"❌ RSI Tracker evaluation error: {e}")
            try:
                corr_trig = await rsi_correlation_tracker_alert_service.check_rsi_correlation_tracker_alerts()
                logger.info("🔎 rsi_correlation_eval | triggers: %d", len(corr_trig))
            except Exception as e:
                print(f"❌ RSI Correlation Tracker evaluation error: {e}")
            try:
                heatmap_trig = await heatmap_tracker_alert_service.check_heatmap_tracker_alerts()
                logger.info("🔎 heatmap_tracker_eval | triggers: %d", len(heatmap_trig))
            except Exception as e:
                print(f"❌ Heatmap Tracker evaluation error: {e}")
            try:
                indicator_trig = await heatmap_indicator_tracker_alert_service.check_heatmap_indicator_tracker_alerts()
                logger.info("🔎 indicator_tracker_eval | triggers: %d", len(indicator_trig))
            except Exception as e:
                print(f"❌ Indicator Tracker evaluation error: {e}")
            await asyncio.sleep(300)
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"❌ Minute scheduler error: {e}")

@app.get("/health")
def health():
    v = mt5.version()
    return {"status": "ok", "mt5_version": v}

# Unsubscribe endpoints removed per spec

@app.get("/test-ws")
def test_websocket():
    return {"message": "WebSocket endpoint available at /ws/market"}

@app.get("/api/ohlc/{symbol}")
def get_ohlc(symbol: str, timeframe: str = Query("5M"), count: int = Query(250, le=500), x_api_key: Optional[str] = Depends(require_api_token_header)):
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

@app.get("/api/alerts/cache")
async def get_alert_cache(x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Get all cached alert configurations"""
    try:
        all_alerts = await alert_cache.get_all_alerts()
        total_alerts = sum(len(alerts) for alerts in all_alerts.values())
        return {
            "user_count": len(all_alerts),
            "total_alerts": total_alerts,
            "last_refresh": alert_cache._last_refresh.isoformat() if alert_cache._last_refresh else None,
            "is_refreshing": alert_cache._is_refreshing,
            "alerts": all_alerts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/alerts/by-category")
async def get_alerts_by_category(x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Get all alerts grouped by category (type)"""
    try:
        grouped = await alert_cache.get_alerts_by_category()
        total = sum(len(v) for v in grouped.values())
        return {
            "total_alerts": total,
            "last_refresh": alert_cache._last_refresh.isoformat() if alert_cache._last_refresh else None,
            "is_refreshing": alert_cache._is_refreshing,
            "categories": grouped,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/alerts/user/{user_id}")
async def get_user_alerts(user_id: str, x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Get cached alert configurations for a specific user"""
    try:
        user_alerts = await alert_cache.get_user_alerts(user_id)
        return {
            "user_id": user_id,
            "alert_count": len(user_alerts),
            "last_refresh": alert_cache._last_refresh.isoformat() if alert_cache._last_refresh else None,
            "alerts": user_alerts
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/alerts/refresh")
async def refresh_alerts_manual(x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Manually trigger alert cache refresh (for testing)"""
    await alert_cache._refresh_cache()
    return {"message": "Alert cache refresh triggered", "status": "success"}

"""Heatmap/RSI/Correlation endpoints removed: using single RSI Tracker alert path."""

async def _check_alerts_safely(tick_data: Dict[str, Any]) -> None:
    """Safely check all alert types without blocking the main loop"""
    try:
        # Check heatmap alerts
        await heatmap_alert_service.check_heatmap_alerts(tick_data)
    except Exception as e:
        print(f"❌ Error checking heatmap alerts: {e}")
    
    try:
        # Check RSI alerts
        await rsi_alert_service.check_rsi_alerts(tick_data)
    except Exception as e:
        print(f"❌ Error checking RSI alerts: {e}")
    
    try:
        # Check RSI correlation alerts
        await rsi_correlation_alert_service.check_rsi_correlation_alerts(tick_data)
    except Exception as e:
        print(f"❌ Error checking RSI correlation alerts: {e}")

class WSClient:
    def __init__(self, websocket: WebSocket, token: str):
        self.websocket = websocket
        self.token = token
        # Legacy tick subscriptions
        self.symbols: Set[str] = set()
        self._last_sent_ts: Dict[str, int] = {}
        # New subscription model (supports multiple timeframes per symbol)
        # subscriptions[symbol][timeframe] -> SubscriptionInfo
        self.subscriptions: Dict[str, Dict[Timeframe, SubscriptionInfo]] = {}
        # next_ohlc_updates[symbol][timeframe] -> next boundary datetime
        self.next_ohlc_updates: Dict[str, Dict[Timeframe, datetime]] = {}
        self._task: Optional[asyncio.Task] = None
        self._ohlc_task: Optional[asyncio.Task] = None
        self._send_interval_s: float = 0.10  # 10 Hz for ticks

    def _is_connected(self) -> bool:
        try:
            return getattr(self.websocket, "client_state", None) == WebSocketState.CONNECTED
        except Exception:
            return False

    async def _try_send_bytes(self, data: bytes) -> bool:
        if not self._is_connected():
            return False
        try:
            await self.websocket.send_bytes(data)
            return True
        except Exception:
            return False

    async def _try_send_json(self, obj: dict) -> bool:
        if not self._is_connected():
            return False
        try:
            await self.websocket.send_json(obj)
            return True
        except Exception:
            return False

    @staticmethod
    def _map_basis_only(ohlc_dict: Dict[str, Any], basis: PriceBasis) -> Dict[str, Any]:
        """Return an OHLC dict shaped per basis_only schema: canonical keys reflect requested basis; parallel fields removed."""
        mapped = dict(ohlc_dict)
        # Choose source fields based on basis
        if basis == PriceBasis.BID:
            open_v = mapped.get("openBid", mapped.get("open"))
            high_v = mapped.get("highBid", mapped.get("high"))
            low_v = mapped.get("lowBid", mapped.get("low"))
            close_v = mapped.get("closeBid", mapped.get("close"))
        elif basis == PriceBasis.ASK:
            open_v = mapped.get("openAsk", mapped.get("open"))
            high_v = mapped.get("highAsk", mapped.get("high"))
            low_v = mapped.get("lowAsk", mapped.get("low"))
            close_v = mapped.get("closeAsk", mapped.get("close"))
        else:
            open_v = mapped.get("open")
            high_v = mapped.get("high")
            low_v = mapped.get("low")
            close_v = mapped.get("close")

        mapped["open"], mapped["high"], mapped["low"], mapped["close"] = open_v, high_v, low_v, close_v
        # Remove parallel fields for basis-only schema
        for k in ("openBid","highBid","lowBid","closeBid","openAsk","highAsk","lowAsk","closeAsk"):
            if k in mapped:
                del mapped[k]
        return mapped

    def _format_ohlc_for_subscription(self, ohlc: OHLC, symbol: str, timeframe: Timeframe) -> Dict[str, Any]:
        """Shape an OHLC payload according to the subscriber's selected schema and basis for this symbol×timeframe."""
        sub_map = self.subscriptions.get(symbol)
        ohlc_dict = ohlc.model_dump()
        if not sub_map:
            return ohlc_dict
        sub = sub_map.get(timeframe)
        if not sub:
            return ohlc_dict
        if sub.ohlc_schema == OHLCSchema.BASIS_ONLY:
            return self._map_basis_only(ohlc_dict, sub.price_basis)
        return ohlc_dict

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
            except Exception:
                # Task may already have ended due to disconnect
                pass
        if self._ohlc_task:
            self._ohlc_task.cancel()
            try:
                await self._ohlc_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def _tick_loop(self):
        """Handle real-time tick streaming"""
        try:
            while True:
                if not self._is_connected():
                    return
                await self._send_tick_updates()
                await asyncio.sleep(self._send_interval_s)
        except asyncio.CancelledError:
            return
        except Exception:
            # Any exception here should end the loop quietly (likely disconnect)
            return
    
    async def _ohlc_loop(self):
        """Handle scheduled OHLC updates"""
        try:
            while True:
                if not self._is_connected():
                    return
                await self._send_scheduled_ohlc_updates()
                await asyncio.sleep(1.0)  # Check every second for due updates
        except asyncio.CancelledError:
            return
        except Exception:
            # Likely disconnect while sending
            return

    async def _send_tick_updates(self):
        """Send real-time tick updates for subscribed symbols"""
        tick_symbols = set()
        
        # Collect symbols that need tick updates (any timeframe requesting ticks)
        for symbol, tf_map in self.subscriptions.items():
            for sub_info in tf_map.values():
                if "ticks" in sub_info.data_types:
                    tick_symbols.add(symbol)
                    break
        
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
                    
                    # Update OHLC caches for all subscribed timeframes requesting OHLC for this symbol
                    if sym in self.subscriptions:
                        for tf, si in self.subscriptions[sym].items():
                            if "ohlc" in si.data_types:
                                update_ohlc_cache(sym, tf)
                        
            except HTTPException:
                # symbol disappeared or invalid; drop it
                if sym in self.symbols:
                    self.symbols.discard(sym)
                if sym in self.subscriptions:
                    del self.subscriptions[sym]
                    
        if updates:
            sent = await self._try_send_bytes(orjson.dumps({"type": "ticks", "data": updates}))
            if not sent:
                # Stop trying if client is gone
                return
            
            # Check for alerts on tick updates (non-blocking background task)
            # Only check alerts if there are active alerts to avoid unnecessary processing
            try:
                all_alerts = await alert_cache.get_all_alerts()
                total_alerts = sum(len(alerts) for alerts in all_alerts.values())
                
                if ENABLE_TICK_TRIGGERED_ALERTS and total_alerts > 0:
                    # Provide tick_data in a dict keyed by symbol as expected by alert services
                    tick_data_map = {}
                    for td in updates:
                        sym = td.get("symbol")
                        if not sym:
                            continue
                        tick_data_map[sym] = {
                            "bid": td.get("bid"),
                            "ask": td.get("ask"),
                            "time": td.get("time"),
                            "volume": td.get("volume"),
                        }
                    tick_data = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "symbols": list(tick_symbols),
                        "tick_data": tick_data_map
                    }
                    # Create background task to check alerts without blocking the tick loop
                    asyncio.create_task(_check_alerts_safely(tick_data))
            except Exception as e:
                # If alert cache check fails, still try to check alerts to be safe
                tick_data_map = {}
                for td in updates:
                    sym = td.get("symbol")
                    if not sym:
                        continue
                    tick_data_map[sym] = {
                        "bid": td.get("bid"),
                        "ask": td.get("ask"),
                        "time": td.get("time"),
                        "volume": td.get("volume"),
                    }
                if ENABLE_TICK_TRIGGERED_ALERTS:
                    tick_data = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "symbols": list(tick_symbols),
                        "tick_data": tick_data_map
                    }
                    asyncio.create_task(_check_alerts_safely(tick_data))
    
    async def _send_scheduled_ohlc_updates(self):
        """Send OHLC updates when timeframe periods complete"""
        current_time = datetime.now(timezone.utc)
        
        for symbol, tf_map in list(self.next_ohlc_updates.items()):
            for tf, next_update_time in list(tf_map.items()):
                if current_time >= next_update_time:
                    try:
                        sub_info = self.subscriptions.get(symbol, {}).get(tf)
                        if sub_info and "ohlc" in sub_info.data_types:
                            # Refresh cache from MT5 at boundary to include zero-tick flat minutes
                            update_ohlc_cache(symbol, tf)
                            # Get current OHLC data from cache (now authoritative and closed)
                            cached_data = get_cached_ohlc(symbol, tf, 1)
                            if cached_data:
                                current_ohlc = cached_data[-1]
                                # Guarantee closed flag for boundary emissions
                                try:
                                    current_ohlc.is_closed = True
                                except Exception:
                                    pass
                                ok = await self._try_send_json({
                                    "type": "ohlc_update",
                                    "data": self._format_ohlc_for_subscription(current_ohlc, symbol, tf)
                                })
                                if not ok:
                                    return
                            # Schedule next update for this symbol×timeframe
                            self.next_ohlc_updates.setdefault(symbol, {})[tf] = calculate_next_update_time(
                                current_time, tf
                            )
                    except Exception as e:
                        print(f"❌ Error sending OHLC update for {symbol} {tf.value}: {e}")
                        # Remove problematic symbol×timeframe subscription
                        try:
                            if symbol in self.subscriptions and tf in self.subscriptions[symbol]:
                                del self.subscriptions[symbol][tf]
                                if not self.subscriptions[symbol]:
                                    del self.subscriptions[symbol]
                            if symbol in self.next_ohlc_updates and tf in self.next_ohlc_updates[symbol]:
                                del self.next_ohlc_updates[symbol][tf]
                                if not self.next_ohlc_updates[symbol]:
                                    del self.next_ohlc_updates[symbol]
                        except Exception:
                            pass

    async def handle_message(self, message: dict):
        action = message.get("action")
        
        if action == "subscribe":
            # New subscription format
            symbol = message.get("symbol", "")
            timeframe = message.get("timeframe", "5M")
            data_types = message.get("data_types", ["ticks", "ohlc"])
            price_basis_str = message.get("price_basis", "last")
            ohlc_schema_str = message.get("ohlc_schema", "parallel")
            
            if not symbol:
                await self._try_send_json({"type": "error", "error": "symbol_required"})
                return
            
            try:
                # Validate timeframe (accept both "1M" style and "M1" alias)
                try:
                    tf = Timeframe(timeframe)
                except ValueError:
                    try:
                        tf = Timeframe[timeframe]
                    except Exception:
                        raise ValueError(f"Invalid timeframe: {timeframe}")
                ensure_symbol_selected(symbol)
                
                # Parse enums with safe defaults
                try:
                    pb = PriceBasis(price_basis_str)
                except Exception:
                    pb = PriceBasis.LAST
                try:
                    schema = OHLCSchema(ohlc_schema_str)
                except Exception:
                    schema = OHLCSchema.PARALLEL

                # Create subscription info
                sub_info = SubscriptionInfo(
                    symbol=symbol,
                    timeframe=tf,
                    subscription_time=datetime.now(timezone.utc),
                    data_types=data_types,
                    price_basis=pb,
                    ohlc_schema=schema,
                )
                
                # Persist subscription (allow multiple TFs per symbol)
                if symbol not in self.subscriptions:
                    self.subscriptions[symbol] = {}
                self.subscriptions[symbol][tf] = sub_info
                
                # Send initial OHLC data if requested
                if "ohlc" in data_types:
                    try:
                        ohlc_data = get_cached_ohlc(symbol, tf, 250)
                        
                        if ohlc_data:
                            ok = await self._try_send_json({
                                "type": "initial_ohlc",
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "data": [self._format_ohlc_for_subscription(ohlc, symbol, tf) for ohlc in ohlc_data]
                            })
                            if not ok:
                                return
                        else:
                            # Only log at debug level to reduce noise
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.debug(f"⚠️ No OHLC data available for {symbol}")
                        
                        # Schedule next OHLC update for this symbol×timeframe
                        self.next_ohlc_updates.setdefault(symbol, {})[tf] = calculate_next_update_time(
                            sub_info.subscription_time, tf
                        )
                        
                    except Exception as e:
                        print(f"❌ Error getting initial OHLC for {symbol}: {e}")
                        import traceback
                        traceback.print_exc()
                        await self._try_send_json({
                            "type": "error",
                            "error": f"failed_to_get_ohlc: {str(e)}"
                        })
                        return
                
                ok = await self._try_send_json({
                    "type": "subscribed",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "data_types": data_types,
                    "price_basis": sub_info.price_basis.value,
                    "ohlc_schema": sub_info.ohlc_schema.value,
                })
                if not ok:
                    return
                
            except ValueError:
                await self._try_send_json({"type": "error", "error": f"invalid_timeframe: {timeframe}"})
            except HTTPException as e:
                await self._try_send_json({"type": "error", "error": str(e.detail)})
            except Exception as e:
                await self._try_send_json({"type": "error", "error": str(e)})
                
        elif action == "subscribe_legacy":
            # Legacy tick-only subscription
            syms = message.get("symbols", [])
            for s in syms:
                ensure_symbol_selected(s)
                self.symbols.add(s)
            await self._try_send_json({"type": "subscribed", "symbols": sorted(self.symbols)})
            
        elif action == "unsubscribe":
            symbol = message.get("symbol", "")
            timeframe_str = message.get("timeframe")
            if symbol:
                if timeframe_str:
                    # Unsubscribe only this symbol×timeframe
                    try:
                        try:
                            tf = Timeframe(timeframe_str)
                        except ValueError:
                            tf = Timeframe[timeframe_str]
                    except Exception:
                        await self._try_send_json({"type": "error", "error": f"invalid_timeframe: {timeframe_str}"})
                        return
                    if symbol in self.subscriptions and tf in self.subscriptions[symbol]:
                        del self.subscriptions[symbol][tf]
                        if not self.subscriptions[symbol]:
                            del self.subscriptions[symbol]
                    if symbol in self.next_ohlc_updates and tf in self.next_ohlc_updates[symbol]:
                        del self.next_ohlc_updates[symbol][tf]
                        if not self.next_ohlc_updates[symbol]:
                            del self.next_ohlc_updates[symbol]
                    self.symbols.discard(symbol)
                    await self._try_send_json({"type": "unsubscribed", "symbol": symbol, "timeframe": tf.value})
                else:
                    # Unsubscribe all timeframes for this symbol
                    if symbol in self.subscriptions:
                        del self.subscriptions[symbol]
                    if symbol in self.next_ohlc_updates:
                        del self.next_ohlc_updates[symbol]
                    self.symbols.discard(symbol)
                    await self._try_send_json({"type": "unsubscribed", "symbol": symbol})
            else:
                # Legacy unsubscribe
                syms = message.get("symbols", [])
                for s in syms:
                    self.symbols.discard(s)
                    if s in self.subscriptions:
                        del self.subscriptions[s]
                    if s in self.next_ohlc_updates:
                        del self.next_ohlc_updates[s]
                await self._try_send_json({"type": "unsubscribed", "symbols": sorted(syms)})
                
        elif action == "ping":
            await self._try_send_json({"type": "pong"})
        else:
            await self._try_send_json({"type": "error", "error": "unknown_action"})

# Keep legacy endpoint for backward compatibility
@app.websocket("/ws/ticks")
async def ws_ticks_legacy(websocket: WebSocket):
    """Legacy WebSocket endpoint for tick-only streaming"""
    client = None
    try:
        await websocket.accept()
        try:
            await websocket.send_json({"type": "connected", "message": "Legacy tick WebSocket connected"})
        except Exception:
            pass

        client = WSClient(websocket, "")
        await client.start()

        while True:
            # If client already disconnected, exit gracefully
            if getattr(websocket, "client_state", None) != WebSocketState.CONNECTED:
                break
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # Starlette raises RuntimeError when not accepted/connected; treat as normal disconnect
                break

            try:
                message = orjson.loads(data)
                # Force legacy behavior
                if message.get("action") == "subscribe":
                    message["action"] = "subscribe_legacy"
                await client.handle_message(message)
            except Exception as parse_error:
                try:
                    await websocket.send_json({"type": "error", "error": str(parse_error)})
                except Exception:
                    break

    except WebSocketDisconnect:
        print("🔌 Legacy WebSocket disconnected")
    except Exception as e:
        # Treat generic runtime receive/accept errors as disconnects to avoid scary traces
        if "accept" in str(e).lower() and "websocket" in str(e).lower():
            print("🔌 Legacy WebSocket disconnected (accept state)")
        else:
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
        try:
            await websocket.send_json({
            "type": "connected", 
            "message": "WebSocket connected successfully",
            "supported_timeframes": [tf.value for tf in Timeframe],
            "supported_data_types": ["ticks", "ohlc"],
            "supported_price_bases": ["last", "bid", "ask"],
            "ohlc_schema": "parallel"
        })
        except Exception:
            # Client may already have disconnected
            pass
        
        # Create WSClient for real MT5 data
        client = WSClient(websocket, "")
        await client.start()
        
        # Handle incoming messages
        while True:
            # Exit if client not connected
            if getattr(websocket, "client_state", None) != WebSocketState.CONNECTED:
                break
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # Starlette raises RuntimeError when not accepted/connected; treat as normal disconnect
                break
            
            try:
                message = orjson.loads(data)
                await client.handle_message(message)
                
            except Exception as parse_error:
                print(f"❌ Error parsing message: {parse_error}")
                try:
                    await websocket.send_json({"type": "error", "error": str(parse_error)})
                except Exception:
                    break
                
    except WebSocketDisconnect:
        print("Websocket Disconnected")
    except Exception as e:
        # Treat generic runtime receive/accept errors as disconnects to avoid scary traces
        if "accept" in str(e).lower() and "websocket" in str(e).lower():
            print("🔌 WebSocket disconnected (accept state)")
        else:
            print(f"❌ WebSocket error: {e}")
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
    print("   - Alert Cache: GET /api/alerts/cache")
    print("   - Alerts by Category: GET /api/alerts/by-category")
    print("   - User Alerts: GET /api/alerts/user/{user_id}")
    print("   - Alert Refresh: POST /api/alerts/refresh")
    print("   - Alerts Cache: GET /api/alerts/cache")
    print("   - Refresh Alerts: POST /api/alerts/refresh")
    print("   - Health check: GET /health")
    
    _install_sigterm_handler(asyncio.get_event_loop())
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False, server_header=False, date_header=False)

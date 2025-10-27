import asyncio
import os
import signal
import sys
from collections import deque, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any
import re
import time
import random

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
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Header, APIRouter
from starlette.websockets import WebSocketState
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import (
    API_TOKEN,
    ALLOWED_ORIGINS,
    MT5_TERMINAL_PATH,
    LIVE_RSI_DEBUGGING,
    DEBUG_API_TOKEN,
)
import app.news as news
from app.alert_cache import alert_cache
from app.rsi_tracker_alert_service import rsi_tracker_alert_service
from app.heatmap_tracker_alert_service import heatmap_tracker_alert_service
from app.heatmap_indicator_tracker_alert_service import heatmap_indicator_tracker_alert_service
from app.currency_strength_alert_service import currency_strength_alert_service
from app.email_service import email_service
from app.daily_mail_service import daily_mail_scheduler
from app.models import (
    Timeframe,
    Tick,
    OHLC,
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
    get_daily_change_pct_bid,
    get_daily_change_bid,
    canonicalize_symbol,
)
from app.rsi_utils import calculate_rsi_series, closed_closes
from app.indicator_cache import indicator_cache
from app.price_cache import price_cache
from app.indicators import rsi_latest as ind_rsi_latest, ema_latest as ind_ema_latest, macd_latest as ind_macd_latest, utbot_latest as ind_utbot_latest, ichimoku_latest as ind_ichimoku_latest
from app.quantum import compute_quantum_for_symbol
from app.currency_strength import compute_currency_strength_for_timeframe
from app.currency_strength_cache import currency_strength_cache
from app.constants import RSI_SUPPORTED_SYMBOLS
from app.trending_pairs import trending_pairs_cache, trending_pairs_scheduler, refresh_trending_pairs

# Async wrappers for blocking MT5 calls to avoid event-loop stalls (especially on Windows)
async def _get_ohlc_data_async(symbol: str, timeframe: Timeframe, count: int = 300):
    return await asyncio.to_thread(get_ohlc_data, symbol, timeframe, count)

async def _update_ohlc_cache_async(symbol: str, timeframe: Timeframe):
    return await asyncio.to_thread(update_ohlc_cache, symbol, timeframe)

async def _ensure_symbol_selected_async(symbol: str):
    return await asyncio.to_thread(ensure_symbol_selected, symbol)

async def _symbol_info_tick_async(symbol: str):
    try:
        return await asyncio.to_thread(mt5.symbol_info_tick, symbol)
    except Exception:
        return None

# Windows event-loop policy: prefer Selector policy for better WebSocket compatibility
try:
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]
except Exception:
    pass

async def _daily_change_pct_bid_async(symbol: str):
    try:
        return await asyncio.to_thread(get_daily_change_pct_bid, symbol)
    except Exception:
        return None

async def _daily_change_bid_async(symbol: str):
    try:
        return await asyncio.to_thread(get_daily_change_bid, symbol)
    except Exception:
        return None
# One-time warmup to backfill indicator cache at startup
async def _warm_populate_indicator_cache() -> None:
    try:
        logger = logging.getLogger("obs.indicator")
        baseline_tfs: List[Timeframe] = _rollout_timeframes()
        try:
            symbols_for_rollout: List[str] = list(ALLOWED_WS_SYMBOLS)
        except Exception:
            symbols_for_rollout = [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS]
        total_pairs = len(symbols_for_rollout) * len(baseline_tfs)
        logger.info("🚀 indicator_warmup | pairs=%d", total_pairs)
        processed = 0
        for sym in symbols_for_rollout:
            for tf in baseline_tfs:
                try:
                    bars = await _get_ohlc_data_async(sym, tf, 300)
                    if not bars:
                        continue
                    closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
                    if not closed_bars:
                        continue
                    last_closed = closed_bars[-1]
                    closes = [b.close for b in closed_bars]
                    highs = [b.high for b in closed_bars]
                    lows = [b.low for b in closed_bars]
                    highs = [b.high for b in closed_bars]
                    lows = [b.low for b in closed_bars]
                    rsi_val = ind_rsi_latest(closes, 14) if len(closes) >= 15 else None
                    if rsi_val is not None:
                        await indicator_cache.update_rsi(sym, tf.value, 14, float(rsi_val), ts_ms=last_closed.time)
                    for p in (21, 50, 200):
                        if len(closes) >= p:
                            ema_val = ind_ema_latest(closes, p)
                            if ema_val is not None:
                                await indicator_cache.update_ema(sym, tf.value, int(p), float(ema_val), ts_ms=last_closed.time)
                    macd_trip = ind_macd_latest(closes, 12, 26, 9)
                    # Additional indicators for logging/WS snapshot (not cached)
                    utbot_vals = None
                    ich_vals = None
                    try:
                        utbot_vals = ind_utbot_latest(highs, lows, closes, 50, 10, 3.0)
                    except Exception:
                        utbot_vals = None
                    try:
                        ich_vals = ind_ichimoku_latest(highs, lows, closes, 9, 26, 52, 26)
                    except Exception:
                        ich_vals = None
                    # Additional indicators
                    utbot_vals = None
                    ich_vals = None
                    try:
                        utbot_vals = ind_utbot_latest(highs, lows, closes, 50, 10, 3.0)
                    except Exception:
                        utbot_vals = None
                    try:
                        ich_vals = ind_ichimoku_latest(highs, lows, closes, 9, 26, 52, 26)
                    except Exception:
                        ich_vals = None
                    if macd_trip is not None:
                        macd_v, sig_v, hist_v = macd_trip
                        await indicator_cache.update_macd(sym, tf.value, 12, 26, 9, float(macd_v), float(sig_v), float(hist_v), ts_ms=last_closed.time)
                    processed += 1
                except Exception:
                    continue
        logger.info("✅ indicator_warmup_done | processed=%d", processed)
    except Exception as e:
        print(f"❌ Indicator warmup error: {e}")

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

    # Warm populate indicator cache on startup (best-effort, non-blocking)
    try:
        asyncio.create_task(_warm_populate_indicator_cache())
    except Exception:
        pass

    # Warm populate currency strength cache (best-effort, non-blocking)
    async def _warm_populate_currency_strength_cache() -> None:
        try:
            # Only supported timeframes (>=5M and WS-allowed)
            baseline_tfs: List[Timeframe] = [Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1]
            try:
                allowed_tfs = ALLOWED_WS_TIMEFRAMES
            except Exception:
                allowed_tfs = set(Timeframe)
            baseline_tfs = [tf for tf in baseline_tfs if tf in allowed_tfs]
            try:
                symbols_for_rollout: List[str] = list(ALLOWED_WS_SYMBOLS)
            except Exception:
                symbols_for_rollout = [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS]
            for tf in baseline_tfs:
                try:
                    res = await compute_currency_strength_for_timeframe(tf, symbols_for_rollout)
                    if res is None:
                        continue
                    ts_ms, values = res
                    await currency_strength_cache.update(tf.value, values, ts_ms=ts_ms)
                except Exception:
                    continue
        except Exception:
            pass

    try:
        asyncio.create_task(_warm_populate_currency_strength_cache())
    except Exception:
        pass

    # Start minute alerts scheduler (fetch + evaluate RSI Tracker) — boundary-aligned
    global _minute_scheduler_task, _minute_scheduler_running
    _minute_scheduler_running = True
    _minute_scheduler_task = asyncio.create_task(_minute_alerts_scheduler())
    
    # Start 10s closed-bar indicator poller (always computes across all allowed symbols/timeframes)
    global _indicator_scheduler_task
    _indicator_scheduler_task = asyncio.create_task(_indicator_scheduler())
    # Start WS metrics reporter (dual-run soak)
    global _ws_metrics_task
    _ws_metrics_task = asyncio.create_task(_ws_metrics_reporter())

    # Start Trending Pairs scheduler (startup + hourly)
    async def _broadcast_trending(snapshot: Dict[str, Any]) -> None:
        # Push to all connected clients as a concise event
        async with _connected_clients_lock:
            clients = list(_connected_clients)
        if not clients:
            return
        msg = {"type": "trending_pairs", "data": snapshot}
        for c in clients:
            try:
                if getattr(c, "v2_broadcast", False):
                    await c._try_send_json(msg)
            except Exception:
                continue

    try:
        try:
            symbols_for_trending: List[str] = list(ALLOWED_WS_SYMBOLS)
        except Exception:
            symbols_for_trending = [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS]
        global _trending_task
        _trending_task = asyncio.create_task(
            trending_pairs_scheduler(symbols_for_trending, threshold_pct=0.05, broadcast=_broadcast_trending)
        )
    except Exception:
        pass
    
    yield
    
    # Shutdown
    news_task.cancel()
    news_reminder_task.cancel()
    daily_task.cancel()
    if _minute_scheduler_task:
        _minute_scheduler_task.cancel()
    if _indicator_scheduler_task:
        _indicator_scheduler_task.cancel()
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
    if _indicator_scheduler_task:
        try:
            await _indicator_scheduler_task
        except asyncio.CancelledError:
            pass
    if _trending_task:
        try:
            _trending_task.cancel()
            await _trending_task
        except asyncio.CancelledError:
            pass
    if _ws_metrics_task:
        try:
            _ws_metrics_task.cancel()
            await _ws_metrics_task
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

"""SubscriptionInfo model removed; v2 is broadcast-only"""

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

# Minute-based alert scheduler (boundary aligned)
ENABLE_TICK_TRIGGERED_ALERTS = False  # Tick-driven checks disabled
_minute_scheduler_task: Optional[asyncio.Task] = None
_minute_scheduler_running: bool = False

# Indicator scheduler state
_indicator_scheduler_task: Optional[asyncio.Task] = None
# 
# Track connected WS clients to discover subscribed symbol×timeframe sets for indicators
_connected_clients = set()
_connected_clients_lock = asyncio.Lock()
# Last processed closed-bar timestamp per (symbol:tf)
_indicator_last_bar: Dict[str, int] = {}

 

# D1 reference cache for daily % change computations (per symbol, keyed by UTC day)
_d1_ref_cache: Dict[str, Tuple[str, float]] = {}

# WebSocket metrics (v2 only): in-memory counters per endpoint label
_ws_metrics: Dict[str, Dict[str, int]] = {
    "v2": defaultdict(int),
}
_ws_metrics_task: Optional[asyncio.Task] = None
_trending_task: Optional[asyncio.Task] = None

def _metrics_inc(label: str, key: str, by: int = 1) -> None:
    try:
        if label not in _ws_metrics:
            return
        _ws_metrics[label][key] = _ws_metrics[label].get(key, 0) + int(by)
    except Exception:
        # Observability must never break runtime
        pass

# Rate limiting for test emails
test_email_rate_limits: Dict[str, List[datetime]] = defaultdict(list)
TEST_EMAIL_RATE_LIMIT = 5  # Max 5 test emails per hour per API key
TEST_EMAIL_RATE_WINDOW = timedelta(hours=1)

# Allowed domains for test emails (configurable via environment)
ALLOWED_EMAIL_DOMAINS = os.environ.get("ALLOWED_EMAIL_DOMAINS", "gmail.com,yahoo.com,outlook.com,hotmail.com").split(",")
ALLOWED_EMAIL_DOMAINS = [domain.strip().lower() for domain in ALLOWED_EMAIL_DOMAINS if domain.strip()]

# WebSocket security and shaping caps (environment configurable)
# Allowed symbols: defaults to RSI_SUPPORTED_SYMBOLS; override via WS_ALLOWED_SYMBOLS (comma-separated, broker-suffixed)
_ws_allowed_symbols_env = [
    canonicalize_symbol(s) for s in os.environ.get("WS_ALLOWED_SYMBOLS", "").split(",") if s.strip()
]
ALLOWED_WS_SYMBOLS: Set[str] = set(_ws_allowed_symbols_env or [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS])
WS_MAX_SYMBOLS: int = int(os.environ.get("WS_MAX_SYMBOLS", "10"))
WS_MAX_SUBSCRIPTIONS: int = int(os.environ.get("WS_MAX_SUBSCRIPTIONS", "32"))
WS_MAX_TFS_PER_SYMBOL: int = int(os.environ.get("WS_MAX_TFS_PER_SYMBOL", "7"))

# Allowed timeframes for WS (defaults to all model-defined timeframes)
_ws_allowed_tfs_env = [
    t.strip().upper() for t in os.environ.get("WS_ALLOWED_TIMEFRAMES", "").split(",") if t.strip()
]
if _ws_allowed_tfs_env:
    env_tf_values: Set[str] = set(_ws_allowed_tfs_env)
    ALLOWED_WS_TIMEFRAMES: Set[Timeframe] = set()
    for tf in Timeframe:
        # accept both enum value (e.g., "5M") and name (e.g., "M5")
        if (tf.value.upper() in env_tf_values) or (tf.name.upper() in env_tf_values):
            ALLOWED_WS_TIMEFRAMES.add(tf)
else:
    ALLOWED_WS_TIMEFRAMES = set(Timeframe)

# Indicator rollout (gradual enablement) — defaults: 10 symbols × 3 timeframes (M1,M5,M15)
_rollout_symbols_env = [
    s.strip().upper() for s in os.environ.get("INDICATOR_ROLLOUT_SYMBOLS", "").split(",") if s.strip()
]
INDICATOR_ROLLOUT_MAX_SYMBOLS: int = int(os.environ.get("INDICATOR_ROLLOUT_MAX_SYMBOLS", "10"))
_rollout_tfs_env = [
    t.strip().upper() for t in os.environ.get("INDICATOR_ROLLOUT_TFS", "M1,M5,M15").split(",") if t.strip()
]

def _rollout_timeframes() -> List[Timeframe]:
    # Always use full baseline timeframes; no rollout/env control
    return [
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
        Timeframe.W1,
    ]

def _rollout_symbols() -> List[str]:
    # If explicitly provided, honor env list (filtered to supported symbols); else use default supported list
    base = [canonicalize_symbol(s) for s in (_rollout_symbols_env or RSI_SUPPORTED_SYMBOLS)]
    # Filter to allowlist to protect MT5 IPC
    filtered = [s for s in base if s in set(RSI_SUPPORTED_SYMBOLS)]
    if not filtered:
        filtered = [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS]
    return filtered

def require_api_token_header(x_api_key: Optional[str] = None):
    # For REST: expect header "X-API-Key"
    if API_TOKEN and x_api_key != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

def require_debug_bearer_token(authorization: Optional[str] = Header(default=None)) -> str:
    """Require Authorization: Bearer <DEBUG_API_TOKEN> for all /api/debug/* endpoints.

    Returns provided token on success; raises 401 with explicit reason otherwise.
    """
    expected = (DEBUG_API_TOKEN or "").strip()
    logger = logging.getLogger("auth.debug")

    def _mask_token(s: str) -> str:
        try:
            if not s:
                return "<empty>"
            n = len(s)
            head = s[:10]
            tail = s[-10:] if n > 20 else s[-(n - 10):] if n > 10 else ""
            return f"{head}...{tail} (len={n})"
        except Exception:
            return "<unprintable>"
    if not expected:
        logger.warning(
            "auth.debug unauthorized | reason=debug_token_not_configured"
        )
        raise HTTPException(status_code=401, detail={"error": "debug_token_not_configured"})

    if authorization is None:
        logger.warning(
            "auth.debug unauthorized | reason=missing_authorization_header | expected_mask=%s",
            _mask_token(expected),
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error": "missing_authorization_header",
                "hint": "Provide Authorization: Bearer <DEBUG_API_TOKEN>",
            },
        )
    if not isinstance(authorization, str):
        logger.warning(
            "auth.debug unauthorized | reason=invalid_authorization_type | expected_mask=%s",
            _mask_token(expected),
        )
        raise HTTPException(status_code=401, detail={"error": "invalid_authorization_type"})

    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2:
        logger.warning(
            "auth.debug unauthorized | reason=invalid_authorization_format | header_preview=%s | expected_mask=%s",
            authorization[:24] + ("…" if len(authorization) > 24 else ""),
            _mask_token(expected),
        )
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_authorization_format",
                "hint": "Expected: 'Authorization: Bearer <token>'",
            },
        )
    scheme, token = parts[0], parts[1].strip()
    if scheme.lower() != "bearer":
        logger.warning(
            "auth.debug unauthorized | reason=invalid_authorization_scheme | scheme=%s | expected=Bearer | expected_mask=%s",
            scheme,
            _mask_token(expected),
        )
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_authorization_scheme", "expected": "Bearer"},
        )
    if not token:
        logger.warning(
            "auth.debug unauthorized | reason=missing_token | expected_mask=%s",
            _mask_token(expected),
        )
        raise HTTPException(status_code=401, detail={"error": "missing_token"})
    if token != expected:
        logger.warning(
            "auth.debug unauthorized | reason=invalid_token | expected_mask=%s | received_mask=%s",
            _mask_token(expected),
            _mask_token(token),
        )
        raise HTTPException(status_code=401, detail={"error": "invalid_token"})
    return token

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


def _ws_is_authorized(websocket: WebSocket) -> bool:
    """Mirror REST auth policy for WebSockets using X-API-Key header (optional).

    If API_TOKEN is set, require header X-API-Key to match; otherwise allow.
    """
    try:
        # Starlette headers are case-insensitive
        x_api_key = websocket.headers.get("x-api-key")
        if not x_api_key:
            x_api_key = websocket.headers.get("X-API-Key")
        if API_TOKEN and (x_api_key or "") != API_TOKEN:
            return False
        return True
    except Exception:
        # Fail closed when API_TOKEN is set
        return False if API_TOKEN else True

def validate_test_email_recipient(email: str) -> bool:
    """Validate basic email format only; allow all domains."""
    if not email or not isinstance(email, str):
        return False
    
    # Basic email format validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return False

    # All domains allowed (no allowlist)
    return True

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
    """Align evaluations to timeframe boundaries so closed-bar RSI is processed immediately after candle close.

    Strategy: sleep until the next 5-minute boundary (covers 5M/15M/30M/1H/4H/1D/W1 boundaries),
    then run all evaluators once. Services gate on last closed bar timestamps, so redundant calls are cheap.
    """
    try:
        logger = logging.getLogger(__name__)
        # Lazy import to avoid cycles
        from app.models import Timeframe as TF
        from app.mt5_utils import calculate_next_update_time

        # Compute the very next 5-minute boundary from now
        next_run = calculate_next_update_time(datetime.now(timezone.utc), TF.M5)
        while _minute_scheduler_running:
            # Sleep until boundary with a short floor to avoid tight-looping
            now = datetime.now(timezone.utc)
            delay = max((next_run - now).total_seconds(), 0.05)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

            # Re-check time and run evaluations
            try:
                await alert_cache._refresh_cache()
            except Exception:
                pass
            try:
                rsi_trig = await rsi_tracker_alert_service.check_rsi_tracker_alerts()
                logger.info("🔎 rsi_tracker_eval | triggers: %d", len(rsi_trig))
            except Exception as e:
                print(f"❌ RSI Tracker evaluation error: {e}")
            # RSI Correlation tracker removed
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
            try:
                curstr_trig = await currency_strength_alert_service.check_currency_strength_alerts()
                logger.info("🔎 curstr_tracker_eval | triggers: %d", len(curstr_trig))
            except Exception as e:
                print(f"❌ Currency Strength evaluation error: {e}")

            # Schedule next 5-minute boundary from current time
            next_run = calculate_next_update_time(datetime.now(timezone.utc), TF.M5)
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"❌ Minute scheduler error: {e}")


async def _indicator_scheduler() -> None:
    """Every ~10 seconds detect newly closed bars for subscribed symbol×timeframe sets,
    compute closed-bar indicators, store them in the indicator cache, and broadcast updates.

    Indicators computed (latest closed bar only):
    - RSI(14)
    - EMA(21/50/200)
    - MACD(12,26,9)
    """
    try:
        poll_interval_s = 10.0
        logger = logging.getLogger("obs.indicator")
        while True:
            started_at = datetime.now(timezone.utc)
            cpu_t0 = time.process_time()
            poll_time_ms = int(started_at.timestamp() * 1000)
            # Snapshot clients safely
            async with _connected_clients_lock:
                clients = list(_connected_clients)

            # Build the unique set of (symbol, timeframe) to compute indicators across ALL allowed symbols/timeframes
            pairs: Set[Tuple[str, Timeframe]] = set()
            baseline_tfs: List[Timeframe] = _rollout_timeframes()
            try:
                symbols_for_rollout: List[str] = list(ALLOWED_WS_SYMBOLS)
            except Exception:
                symbols_for_rollout = [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS]
            for sym in symbols_for_rollout:
                for tf in baseline_tfs:
                    pairs.add((sym, tf))

            processed = 0
            error_count = 0
            # Track which timeframes had new closed-bar updates per indicator kind in this poll cycle
            tfs_rsi_updated: Set[str] = set()
            tfs_ema_updated: Set[str] = set()
            tfs_macd_updated: Set[str] = set()
            tfs_cs_updated: Set[str] = set()
            # Ensure quantum is computed once per symbol per cycle
            quantum_done: Set[str] = set()
            for symbol, tf in pairs:
                try:
                    # Fetch recent OHLC and gate on last closed bar (offloaded)
                    bars = await _get_ohlc_data_async(symbol, tf, 300)
                    if not bars:
                        continue
                    closed_bars = [b for b in bars if getattr(b, "is_closed", None) is not False]
                    if not closed_bars:
                        continue
                    last_closed = closed_bars[-1]
                    key = f"{symbol}:{tf.value}"
                    if _indicator_last_bar.get(key) == last_closed.time:
                        continue

                    closes = [b.close for b in closed_bars]
                    highs = [b.high for b in closed_bars]
                    lows = [b.low for b in closed_bars]
                    # Compute indicators for latest closed bar
                    rsi_val = ind_rsi_latest(closes, 14) if len(closes) >= 15 else None
                    ema_vals: Dict[int, Optional[float]] = {}
                    for p in (21, 50, 200):
                        ema_vals[p] = ind_ema_latest(closes, p) if len(closes) >= p else None
                    macd_trip = ind_macd_latest(closes, 12, 26, 9)
                    # Additional indicators for logging/WS snapshot (not cached)
                    utbot_vals = None
                    ich_vals = None
                    try:
                        utbot_vals = ind_utbot_latest(highs, lows, closes, 50, 10, 3.0)
                    except Exception:
                        utbot_vals = None
                    try:
                        ich_vals = ind_ichimoku_latest(highs, lows, closes, 9, 26, 52, 26)
                    except Exception:
                        ich_vals = None

                    # Store to indicator cache (async-safe)
                    if rsi_val is not None:
                        await indicator_cache.update_rsi(symbol, tf.value, 14, float(rsi_val), ts_ms=last_closed.time)
                        tfs_rsi_updated.add(tf.value)
                    for period, v in ema_vals.items():
                        if v is not None:
                            await indicator_cache.update_ema(symbol, tf.value, int(period), float(v), ts_ms=last_closed.time)
                            tfs_ema_updated.add(tf.value)
                    if macd_trip is not None:
                        macd_v, sig_v, hist_v = macd_trip
                        await indicator_cache.update_macd(
                            symbol,
                            tf.value,
                            12,
                            26,
                            9,
                            float(macd_v),
                            float(sig_v),
                            float(hist_v),
                            ts_ms=last_closed.time,
                        )
                        tfs_macd_updated.add(tf.value)

                    _indicator_last_bar[key] = last_closed.time
                    processed += 1

                    # Structured per-update metrics (DEBUG level): latency and values snapshot
                    try:
                        latency_ms = max(poll_time_ms - int(last_closed.time), 0)
                        item_log = {
                            "event": "indicator_item",
                            "sym": symbol,
                            "tf": tf.value,
                            "bar_time": int(last_closed.time),
                            "latency_ms": int(latency_ms),
                            "rsi14": (float(rsi_val) if rsi_val is not None else None),
                            "ema": {k: (float(v) if v is not None else None) for k, v in ema_vals.items()},
                            "macd": (
                                {
                                    "macd": float(macd_trip[0]),
                                    "signal": float(macd_trip[1]),
                                    "hist": float(macd_trip[2]),
                                }
                                if macd_trip
                                else None
                            ),
                            "utbot": (
                                {
                                    "baseline": float(utbot_vals[0]),
                                    "stop": float(utbot_vals[1]),
                                    "direction": int(utbot_vals[2]),
                                    "flip": int(utbot_vals[3]),
                                }
                                if utbot_vals
                                else None
                            ),
                            "ichimoku": (ich_vals if ich_vals else None),
                        }
                        # JSON logs optional; emit at DEBUG to avoid INFO spam
                        logger.debug(orjson.dumps(item_log).decode("utf-8"))
                    except Exception:
                        # Never allow observability to break scheduling
                        pass

                    # Live RSI debug log for 5M closed bars using cache-aligned numbers
                    try:
                        if (
                            LIVE_RSI_DEBUGGING
                            and symbol == "BTCUSDm"
                            and tf == Timeframe.M5
                            and rsi_val is not None
                        ):
                            time_iso = getattr(last_closed, "time_iso", "") or ""
                            if "T" in time_iso:
                                parts = time_iso.split("T", 1)
                                date_part = parts[0]
                                time_part = parts[1]
                            else:
                                date_part = time_iso
                                time_part = ""
                            time_part = time_part.replace("+00:00", "Z")
                            volume_str = f"{last_closed.volume:.2f}" if getattr(last_closed, "volume", None) is not None else "-"
                            tick_volume_str = f"{last_closed.tick_volume:.0f}" if getattr(last_closed, "tick_volume", None) is not None else "-"
                            spread_str = f"{last_closed.spread:.0f}" if getattr(last_closed, "spread", None) is not None else "-"
                            logger = logging.getLogger(__name__)
                            logger.info(
                                "🧭 liveRSI %s 5M RSIclosed(14)=%.2f | date=%s time=%s open=%.5f high=%.5f low=%.5f close=%.5f volume=%s tick_volume=%s spread=%s",
                                symbol,
                                float(rsi_val),
                                date_part,
                                time_part,
                                float(getattr(last_closed, "open", 0.0)),
                                float(getattr(last_closed, "high", 0.0)),
                                float(getattr(last_closed, "low", 0.0)),
                                float(getattr(last_closed, "close", 0.0)),
                                volume_str,
                                tick_volume_str,
                                spread_str,
                            )
                    except Exception:
                        # Debug-only logging must never break the scheduler
                        pass

                    # Broadcast to interested clients (best-effort)
                    if clients:
                        snapshot = {
                            "bar_time": last_closed.time,
                            "indicators": {
                                "rsi": {14: rsi_val} if rsi_val is not None else {},
                            },
                        }
                        msg = {
                            "type": "indicator_update",
                            "symbol": symbol,
                            "timeframe": tf.value,
                            "data": snapshot,
                        }
                        for c in clients:
                            try:
                                if getattr(c, "v2_broadcast", False):
                                    ok = await c._try_send_json(msg)
                                    try:
                                        label = getattr(c, "conn_label", "v2" if getattr(c, "v2_broadcast", False) else "v1")
                                        if ok:
                                            _metrics_inc(label, "ok_indicator_update", 1)
                                        else:
                                            _metrics_inc(label, "fail_indicator_update", 1)
                                    except Exception:
                                        pass
                                    continue
                                sub = getattr(c, "subscriptions", {}).get(symbol, {}).get(tf)
                                if sub and "indicators" in getattr(sub, "data_types", []):
                                    ok = await c._try_send_json(msg)
                                    try:
                                        label = getattr(c, "conn_label", "v2" if getattr(c, "v2_broadcast", False) else "v1")
                                        if ok:
                                            _metrics_inc(label, "ok_indicator_update", 1)
                                        else:
                                            _metrics_inc(label, "fail_indicator_update", 1)
                                    except Exception:
                                        pass
                            except Exception:
                                # Never let a single client block the scheduler
                                continue

                    # Compute and cache currency strength for this timeframe once per poll cycle per timeframe (closed-bar only)
                    try:
                        # Enforce minimum timeframe of 5M and WS-allowed timeframes for currency strength
                        try:
                            allowed_tfs = ALLOWED_WS_TIMEFRAMES
                        except Exception:
                            allowed_tfs = set(Timeframe)
                        if tf == Timeframe.M1 or tf not in allowed_tfs:
                            pass
                        elif tf.value not in tfs_cs_updated:
                            # Use the same rollout symbols set for strength calculation
                            symbols_for_strength: List[str] = list(ALLOWED_WS_SYMBOLS)
                            cs_res = await compute_currency_strength_for_timeframe(tf, symbols_for_strength)
                            if cs_res is not None:
                                cs_ts, cs_values = cs_res
                                # Fetch previous snapshot timestamp (if any) to detect new closed bar
                                try:
                                    prev_latest = await currency_strength_cache.latest(tf.value)
                                    prev_ts = int(prev_latest[0]) if prev_latest else None
                                except Exception:
                                    prev_ts = None
                                await currency_strength_cache.update(tf.value, cs_values, ts_ms=cs_ts)
                                tfs_cs_updated.add(tf.value)
                                # Broadcast currency strength snapshot
                                if clients:
                                    cs_msg = {
                                        "type": "currency_strength_update",
                                        "timeframe": tf.value,
                                        "data": {
                                            "bar_time": cs_ts,
                                            "strength": cs_values,
                                        },
                                    }
                                    for c in clients:
                                        try:
                                            if getattr(c, "v2_broadcast", False):
                                                await c._try_send_json(cs_msg)
                                                continue
                                        except Exception:
                                            continue
                                # Log values on server when pushing for closed candles (new bar only)
                                try:
                                    if prev_ts is None or (isinstance(cs_ts, int) and cs_ts > int(prev_ts)):
                                        logger = logging.getLogger("obs.curstr")
                                        values_json = orjson.dumps({k: float(v) for k, v in cs_values.items()}).decode("utf-8")
                                        logger.info(
                                            "📊 currency_strength_update | tf=%s bar_time=%d values=%s",
                                            tf.value,
                                            int(cs_ts) if isinstance(cs_ts, int) else 0,
                                            values_json,
                                        )
                                except Exception:
                                    # Observability must not break scheduling
                                    pass
                    except Exception:
                        # Strength calculation must never break the indicator scheduler loop
                        pass
                    # Also compute and broadcast quantum analysis snapshot once per symbol per cycle
                    if symbol not in quantum_done:
                        quantum_done.add(symbol)
                        q = await compute_quantum_for_symbol(symbol)
                        q_msg = {
                            "type": "quantum_update",
                            "symbol": symbol,
                            "data": q,
                        }
                        for c in clients:
                            try:
                                if getattr(c, "v2_broadcast", False):
                                    await c._try_send_json(q_msg)
                                    continue
                            except Exception:
                                continue

                    # Correlation updates removed per product decision
                except Exception as e:
                    error_count += 1
                    print(f"❌ Indicator scheduler error for {symbol} {tf.value}: {e}")
                    continue

                # Yield occasionally to keep event loop responsive (Windows handshake stability)
                if (processed % 10) == 0:
                    try:
                        await asyncio.sleep(0)
                    except Exception:
                        pass

            ended_at = datetime.now(timezone.utc)
            cpu_t1 = time.process_time()
            try:
                elapsed_ms = int((ended_at - started_at).total_seconds() * 1000)
                cpu_ms = int((cpu_t1 - cpu_t0) * 1000)
                # Human-friendly summary
                logging.getLogger(__name__).info(
                    "🧮 indicator_poll | pairs=%d processed=%d errors=%d duration_ms=%d cpu_ms=%d",
                    len(pairs),
                    processed,
                    error_count,
                    elapsed_ms,
                    cpu_ms,
                )
                # Structured cycle metrics (DEBUG)
                cycle_log = {
                    "event": "indicator_poll",
                    "pairs_total": len(pairs),
                    "processed": processed,
                    "errors": error_count,
                    "duration_ms": elapsed_ms,
                    "cpu_ms": cpu_ms,
                }
                logger.debug(orjson.dumps(cycle_log).decode("utf-8"))
            except Exception:
                pass

            # Event-driven alert evaluation: run relevant alerts immediately when indicators update
            try:
                # Only use in-memory cache; do not refresh here
                if tfs_rsi_updated:
                    # RSI-dependent alerts
                    asyncio.create_task(rsi_tracker_alert_service.check_rsi_tracker_alerts())
                if tfs_ema_updated or tfs_macd_updated or tfs_rsi_updated:
                    # Custom indicator tracker depends on EMA/MACD/RSI
                    asyncio.create_task(heatmap_indicator_tracker_alert_service.check_heatmap_indicator_tracker_alerts())
                    # Heatmap tracker aggregates across indicators; safe to evaluate when any updated
                    asyncio.create_task(heatmap_tracker_alert_service.check_heatmap_tracker_alerts())
            except Exception:
                # Never allow alert evaluation to break the scheduler
                pass

            try:
                await asyncio.sleep(poll_interval_s)
            except asyncio.CancelledError:
                raise
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"❌ Indicator scheduler fatal error: {e}")


async def _ws_metrics_reporter() -> None:
    """Periodically log WebSocket send counters (v2 only). Resets counters after each report."""
    try:
        interval_s = int(os.environ.get("WS_METRICS_INTERVAL_S", "30"))
        logger = logging.getLogger("obs.ws")
        while True:
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                raise

            # Snapshot and reset counters atomically enough for our purposes
            try:
                # Compute active connections (v2 only)
                active: Dict[str, int] = {"v2": 0}
                async with _connected_clients_lock:
                    for c in list(_connected_clients):
                        label = getattr(c, "conn_label", "v2")
                        if label == "v2":
                            active["v2"] += 1

                snap = {lbl: dict(counts) for lbl, counts in _ws_metrics.items()}
                # Human-friendly rollup
                def fmt(label: str) -> str:
                    d = snap.get(label, {})
                    ticks_ok = int(d.get("ok_ticks", 0))
                    ticks_fail = int(d.get("fail_ticks", 0))
                    ticks_items = int(d.get("ticks_items", 0))
                    ind_ok = int(d.get("ok_indicator_update", 0))
                    ind_fail = int(d.get("fail_indicator_update", 0))
                    opened = int(d.get("connections_opened", 0))
                    closed = int(d.get("connections_closed", 0))
                    err_rate_ticks = (ticks_fail / max(ticks_ok + ticks_fail, 1))
                    err_rate_ind = (ind_fail / max(ind_ok + ind_fail, 1))
                    return (
                        f"conns={active.get(label, 0)} opened={opened} closed={closed} "
                        f"ticks_msgs={ticks_ok+ticks_fail} items={ticks_items} err={err_rate_ticks:.3f} "
                        f"indicator_msgs={ind_ok+ind_fail} err={err_rate_ind:.3f}"
                    )

                logger.info(
                    "📈 ws_metrics | window_s=%d | v2: %s",
                    interval_s,
                    fmt("v2"),
                )

                # Structured JSON snapshot at DEBUG
                try:
                    payload = {
                        "event": "ws_metrics",
                        "window_s": interval_s,
                        "active": active,
                        "counts": snap,
                    }
                    logger.debug(orjson.dumps(payload).decode("utf-8"))
                except Exception:
                    pass

                # Reset counters for the next window
                for k in list(_ws_metrics.keys()):
                    _ws_metrics[k] = defaultdict(int)

            except Exception:
                # Never let metrics reporting crash the server
                pass
    except asyncio.CancelledError:
        return
    except Exception as e:
        print(f"❌ WS metrics reporter error: {e}")


# liveRSI boundary debugger removed — logs now emitted from indicator scheduler for 5M

@app.get("/health")
def health():
    v = mt5.version()
    return {"status": "ok", "mt5_version": v}

# Unsubscribe endpoints removed per spec

@app.get("/test-ws")
def test_websocket():
    return {"message": "WebSocket endpoint available at /market-v2"}

@app.get("/trending-pairs")
async def get_trending_pairs(x_api_key: Optional[str] = Depends(require_api_token_header)):
    """Return the current cached trending pairs snapshot.

    Trending rule: abs(daily_change_pct) >= 0.05 (hardcoded threshold for now).
    """
    snap = await trending_pairs_cache.get_snapshot()
    return snap

@app.get("/api/indicator")
async def get_indicator(
    indicator: str = Query(..., description="Indicator name: rsi|quantum|currency_strength"),
    timeframe: str = Query(
        ...,
        description="Timeframe: one of 1M,5M,15M,30M,1H,4H,1D,1W (currency_strength requires >=5M)",
    ),
    pairs: Optional[List[str]] = Query(None, description="Repeatable symbol param (e.g., pairs=EURUSDm&pairs=BTCUSDm) or CSV"),
    symbols: Optional[List[str]] = Query(None, description="Alias for pairs (repeatable or CSV)"),
    x_api_key: Optional[str] = Depends(require_api_token_header),
):
    """Return the latest closed-bar value for a single indicator across 1 to 32 pairs.

    - indicator: rsi|quantum|currency_strength
      - rsi: fixed period 14
      - quantum: returns per-timeframe and overall Buy/Sell% with signals
      - currency_strength: 8-currency strength map (USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD) using ROC
    - timeframe: must be WS-supported
    - pairs/symbols: 1 to 32 symbols. Defaults to all WS-allowed symbols if omitted (capped to 32).
    """
    # Validate timeframe
    try:
        tf = Timeframe(timeframe)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    # Enforce WS-allowed timeframes when available
    try:
        if 'ALLOWED_WS_TIMEFRAMES' in globals():
            if tf not in ALLOWED_WS_TIMEFRAMES:
                raise HTTPException(status_code=403, detail="forbidden_timeframe")
    except Exception:
        pass

    # Normalize symbols
    requested: List[str] = []
    for src in (pairs or []):
        if src:
            requested.extend([s for s in re.split(r"[,\s]+", src) if s])
    for src in (symbols or []):
        if src:
            requested.extend([s for s in re.split(r"[,\s]+", src) if s])

    if not requested:
        try:
            candidates: List[str] = sorted(list(ALLOWED_WS_SYMBOLS))
        except Exception:
            candidates = sorted([canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS])
    else:
        # Canonicalize
        canonical: List[str] = []
        for s in requested:
            try:
                canonical.append(canonicalize_symbol(s))
            except Exception:
                continue
        # Filter to allowed when available
        try:
            allowed_ws: Set[str] = set(ALLOWED_WS_SYMBOLS)
        except Exception:
            allowed_ws = set([canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS])
        candidates = [s for s in canonical if s in allowed_ws]

    # Enforce pair count limits: 1..32
    if len(candidates) == 0:
        raise HTTPException(status_code=400, detail="no_symbols")
    if len(candidates) > 32:
        candidates = candidates[:32]

    indicator_key = indicator.strip().lower()
    if indicator_key not in {"rsi", "quantum", "currency_strength"}:
        raise HTTPException(status_code=400, detail="unsupported_indicator")

    if indicator_key == "currency_strength":
        # Enforce minimum timeframe 5M for currency strength
        if tf == Timeframe.M1:
            raise HTTPException(status_code=400, detail="min_timeframe_5M")
        # Return cached snapshot; compute on-demand if missing
        cs_latest = await currency_strength_cache.latest(tf.value)
        if not cs_latest:
            try:
                sym_rollout: List[str] = sorted(list(ALLOWED_WS_SYMBOLS))
            except Exception:
                sym_rollout = sorted([canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS])
            res = await compute_currency_strength_for_timeframe(tf, sym_rollout)
            if res is not None:
                ts_ms, values = res
                await currency_strength_cache.update(tf.value, values, ts_ms=ts_ms)
                cs_latest = (ts_ms, values)
        ts_ms = None if not cs_latest else int(cs_latest[0])
        values = None if not cs_latest else cs_latest[1]
        return {
            "indicator": indicator_key,
            "timeframe": tf.value,
            "ts": ts_ms,
            "currencies": values,
        }
    else:
        results: List[Dict[str, Any]] = []
        for sym in candidates:
            try:
                await _ensure_symbol_selected_async(sym)
            except Exception:
                pass

            if indicator_key == "rsi":
                latest = await indicator_cache.get_latest_rsi(sym, tf.value, 14)
                value = None if not latest else float(latest[1])
                ts_ms = None if not latest else int(latest[0])
                results.append({"symbol": sym, "timeframe": tf.value, "ts": ts_ms, "value": value})
            else:  # quantum
                # Compute per-timeframe and overall quantum Buy/Sell% (closed-bar parity)
                try:
                    q = await compute_quantum_for_symbol(sym)
                except Exception:
                    q = None
                results.append({
                    "symbol": sym,
                    "timeframe": tf.value,
                    "ts": None,
                    "quantum": q if q else None,
                })

        return {
            "indicator": indicator_key,
            "timeframe": tf.value,
            "count": len(results),
            "pairs": results,
        }


 


@app.get("/api/pricing")
async def get_pricing(
    pairs: Optional[List[str]] = Query(None, description="Repeatable symbol param (e.g., pairs=EURUSDm&pairs=BTCUSDm) or CSV"),
    symbols: Optional[List[str]] = Query(None, description="Alias for pairs (repeatable or CSV)"),
    x_api_key: Optional[str] = Depends(require_api_token_header),
):
    """Return latest cached price and daily_change_pct for 1 to 32 pairs.

    - Sources the latest value from in-memory price cache, falling back to MT5 tick on miss.
    - Response arrays are ordered by the canonicalized symbol list.
    """
    # Normalize symbols
    requested: List[str] = []
    for src in (pairs or []):
        if src:
            requested.extend([s for s in re.split(r"[,\s]+", src) if s])
    for src in (symbols or []):
        if src:
            requested.extend([s for s in re.split(r"[,\s]+", src) if s])

    if not requested:
        try:
            candidates: List[str] = sorted(list(ALLOWED_WS_SYMBOLS))
        except Exception:
            candidates = sorted([canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS])
    else:
        # Canonicalize and cap to 32
        candidates = []
        for s in requested:
            cs = canonicalize_symbol(s)
            if cs not in candidates:
                candidates.append(cs)
        candidates = candidates[:32]
        # Filter to allowed when available
        try:
            allowed_ws: Set[str] = set(ALLOWED_WS_SYMBOLS)
        except Exception:
            allowed_ws = set([canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS])
        candidates = [s for s in candidates if s in allowed_ws]

    results: List[Dict[str, Any]] = []
    for sym in candidates:
        try:
            # Prefer cache
            snap = await price_cache.get_latest(sym)
            if not snap:
                # Fallback to live MT5 tick (offloaded)
                await _ensure_symbol_selected_async(sym)
                info = await _symbol_info_tick_async(sym)
                if info is not None:
                    ts_ms = getattr(info, "time_msc", 0) or int(getattr(info, "time", 0)) * 1000
                    dt_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()
                    bid = getattr(info, "bid", None)
                    ask = getattr(info, "ask", None)
                    dcp = await _daily_change_pct_bid_async(sym)
                    snap = {
                        "symbol": sym,
                        "time": ts_ms,
                        "time_iso": dt_iso,
                        "bid": float(bid) if bid is not None else None,
                        "ask": float(ask) if ask is not None else None,
                        "daily_change_pct": float(dcp) if dcp is not None else None,
                    }
                    try:
                        await price_cache.update(sym, time_ms=ts_ms, time_iso=dt_iso, bid=bid, ask=ask, daily_change_pct=dcp)
                    except Exception:
                        pass
            if snap:
                results.append(snap)
            else:
                results.append({"symbol": sym, "time": None, "time_iso": None, "bid": None, "ask": None, "daily_change_pct": None})
        except Exception:
            results.append({"symbol": sym, "time": None, "time_iso": None, "bid": None, "ask": None, "daily_change_pct": None})

    return {
        "count": len(results),
        "pairs": results,
    }


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
    def _prune_empty_fields(d: dict) -> dict:
        # Remove empty valued fields from client response per requirements
        for k in ("actual", "previous", "forecast", "revision"):
            try:
                v = d.get(k, None)
                if v is None:
                    d.pop(k, None)
                elif isinstance(v, str) and not v.strip():
                    d.pop(k, None)
            except Exception:
                continue
        return d

    data = []
    for item in news.global_news_cache:
        try:
            obj = item.model_dump()
            data.append(_prune_empty_fields(obj))
        except Exception:
            # Fallback to minimal safe shape
            data.append({
                "uuid": getattr(item, "uuid", None),
                "headline": getattr(item, "headline", None),
                "currency": getattr(item, "currency", None),
                "time": getattr(item, "time", None),
                "analysis": getattr(item, "analysis", {}),
            })

    return {
        "news_count": len(news.global_news_cache),
        "last_updated": news.news_cache_metadata["last_updated"],
        "next_update": news.news_cache_metadata["next_update_time"],
        "is_updating": news.news_cache_metadata["is_updating"],
        "data": data,
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

@app.get("/api/ohlc")
async def get_ohlc(
    symbol: str = Query(..., description="Symbol, e.g., EURUSDm"),
    timeframe: str = Query(..., description="One of 1M,5M,15M,30M,1H,4H,1D,1W"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(100, ge=1, le=1000, description="Bars per page (max 1000)"),
    x_api_key: Optional[str] = Depends(require_api_token_header),
):
    """Return OHLC bars for a single symbol and timeframe with simple pagination.

    Pagination is newest-first by page, but bars are returned in ascending time within the page.
    Page 1 → most recent `per_page` bars; Page 2 → the previous `per_page`, and so on.
    """
    # Normalize and gate symbol to allowed set when available
    sym = canonicalize_symbol(symbol)
    try:
        allowed_ws: Set[str] = set(ALLOWED_WS_SYMBOLS)
    except Exception:
        allowed_ws = set()
    if allowed_ws and sym not in allowed_ws:
        raise HTTPException(status_code=403, detail="forbidden_symbol")

    # Parse timeframe
    try:
        tf = Timeframe(timeframe)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid timeframe: {timeframe}")

    # Fetch last (page * per_page) bars, then slice to the requested window
    total = page * per_page
    bars = await _get_ohlc_data_async(sym, tf, total)
    if not bars:
        return {"symbol": sym, "timeframe": tf.value, "page": page, "per_page": per_page, "count": 0, "bars": []}

    end_idx = len(bars) - (page - 1) * per_page
    start_idx = max(0, end_idx - per_page)
    page_bars = bars[start_idx:end_idx]

    # Serialize minimally (pydantic models are JSON serializable, but we return explicit fields for stability)
    data = [
        {
            "symbol": b.symbol,
            "timeframe": b.timeframe,
            "time": b.time,
            "time_iso": b.time_iso,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "tick_volume": b.tick_volume,
            "spread": b.spread,
            "openBid": b.openBid,
            "highBid": b.highBid,
            "lowBid": b.lowBid,
            "closeBid": b.closeBid,
            "openAsk": b.openAsk,
            "highAsk": b.highAsk,
            "lowAsk": b.lowAsk,
            "closeAsk": b.closeAsk,
            "is_closed": b.is_closed,
        }
        for b in page_bars
    ]

    return {
        "symbol": sym,
        "timeframe": tf.value,
        "page": page,
        "per_page": per_page,
        "count": len(data),
        "bars": data,
    }

# Debug email sender — secured with Authorization: Bearer <API_TOKEN>
# Router for all debug endpoints (shared bearer token)
debug_router = APIRouter(prefix="/api/debug", dependencies=[Depends(require_debug_bearer_token)])

@debug_router.post("/email/send")
async def debug_send_email(
    mail_type: str = Query(..., alias="type", description="Email type: rsi|heatmap|heatmap_tracker|custom_indicator|rsi_correlation|news_reminder|daily_brief|currency_strength|test"),
    to: str = Query(..., description="Recipient email address"),
    bearer_token: str = Depends(require_debug_bearer_token),
):
    # Validate recipient domain for safety
    if not validate_test_email_recipient(to):
        raise HTTPException(status_code=400, detail="invalid_recipient")

    # Rate limit per bearer token (5/hour) to avoid abuse
    rate_key = bearer_token or "anon"
    if not check_test_email_rate_limit(rate_key):
        raise HTTPException(status_code=429, detail="rate_limited")

    # Normalize mail type and define allowed set
    t = (mail_type or "").strip().lower()
    # Map common aliases
    aliases = {
        "quantum": "heatmap_tracker",
        "tracker": "heatmap_tracker",
        "quantum_tracker": "heatmap_tracker",
        "correlation": "rsi_correlation",
        "cs": "currency_strength",
    }
    t = aliases.get(t, t)
    allowed = {
        "rsi",
        "heatmap",
        "heatmap_tracker",
        "custom_indicator",
        "rsi_correlation",
        "news_reminder",
        "daily_brief",
        "currency_strength",
        "test",
    }
    if t not in allowed:
        raise HTTPException(status_code=400, detail={"error": "unknown_type", "allowed": sorted(list(allowed))})

    # Sample data factories
    def sample_symbols(n: int = 2) -> List[str]:
        try:
            pool = list(ALLOWED_WS_SYMBOLS)
            if not pool:
                raise Exception()
        except Exception:
            pool = [canonicalize_symbol(s) for s in RSI_SUPPORTED_SYMBOLS]
        random.shuffle(pool)
        return pool[: max(1, n)]

    def sample_tf() -> str:
        try:
            return random.choice([tf.value for tf in _rollout_timeframes()])
        except Exception:
            return "5M"

    def sample_indicators(k: int = 3) -> List[str]:
        base = ["EMA21", "EMA50", "EMA200", "MACD", "RSI", "UTBOT", "ICHIMOKU"]
        random.shuffle(base)
        return base[:k]

    # Dispatch per email type with random payloads
    ok = False
    detail: Dict[str, Any] = {}
    try:
        if t == "test":
            ok = await email_service.send_test_email(to)
        elif t == "rsi":
            pairs = []
            for sym in sample_symbols(random.randint(1, 2)):
                pairs.append({
                    "symbol": sym,
                    "timeframe": sample_tf(),
                    "rsi_value": round(random.uniform(10, 90), 1),
                    "current_price": round(random.uniform(0.5, 30000.0), 5),
                    "trigger_condition": random.choice(["overbought", "oversold"]),
                })
            cfg = {
                "rsi_overbought_threshold": 70,
                "rsi_oversold_threshold": 30,
            }
            ok = await email_service.send_rsi_alert(to, f"RSI Debug #{random.randint(1000,9999)}", pairs, cfg)
            detail = {"pairs": len(pairs)}
        elif t == "heatmap":
            pairs = []
            for sym in sample_symbols(random.randint(1, 3)):
                pairs.append({
                    "symbol": sym,
                    "strength": round(random.uniform(0, 100), 1),
                    "signal": random.choice(["BUY", "SELL", "NEUTRAL"]),
                    "timeframe": sample_tf(),
                })
            cfg = {
                "trading_style": random.choice(["scalper", "swingtrader"]),
                "buy_threshold_min": 70,
                "buy_threshold_max": 100,
                "sell_threshold_min": 0,
                "sell_threshold_max": 30,
                "selected_indicators": sample_indicators(3),
            }
            ok = await email_service.send_heatmap_alert(to, f"Heatmap Debug #{random.randint(1000,9999)}", pairs, cfg)
            detail = {"pairs": len(pairs)}
        elif t == "heatmap_tracker":
            pairs = []
            for sym in sample_symbols(random.randint(1, 3)):
                cond = random.choice(["BUY", "SELL"])
                buy_pct = round(random.uniform(60, 95), 2)
                sell_pct = round(100 - buy_pct + random.uniform(-5, 5), 2)
                pairs.append({
                    "symbol": sym,
                    "trigger_condition": cond,
                    "buy_percent": buy_pct,
                    "sell_percent": sell_pct,
                    "final_score": round(random.uniform(10, 90), 2),
                    "timeframe": random.choice(["style-weighted", sample_tf()]),
                })
            cfg = {
                "selected_indicators": sample_indicators(4),
                "buy_threshold": 70,
                "sell_threshold": 30,
            }
            ok = await email_service.send_heatmap_tracker_alert(to, f"Quantum Debug #{random.randint(1000,9999)}", pairs, cfg)
            detail = {"pairs": len(pairs)}
        elif t == "custom_indicator":
            pairs = []
            for sym in sample_symbols(random.randint(1, 3)):
                cond = random.choice(["BUY", "SELL"])
                prob = round(random.uniform(55, 92), 2)
                pairs.append({
                    "symbol": sym,
                    "timeframe": sample_tf(),
                    "trigger_condition": cond,
                    "buy_percent": prob if cond == "BUY" else None,
                    "sell_percent": prob if cond == "SELL" else None,
                })
            cfg = {"selected_indicators": sample_indicators(3)}
            ok = await email_service.send_custom_indicator_alert(to, f"Indicator Debug #{random.randint(1000,9999)}", pairs, cfg)
            detail = {"pairs": len(pairs)}
        elif t == "rsi_correlation":
            pairs = []
            for _ in range(random.randint(1, 2)):
                s1, s2 = sample_symbols(2)
                pairs.append({
                    "symbol1": s1,
                    "symbol2": s2,
                    "timeframe": sample_tf(),
                    "correlation_value": round(random.uniform(-1, 1), 2),
                    "trigger_condition": random.choice(["strong_positive", "strong_negative", "weak_correlation", "correlation_break"]),
                })
            cfg = {
                "strong_correlation_threshold": 0.70,
                "moderate_correlation_threshold": 0.30,
                "weak_correlation_threshold": 0.15,
            }
            ok = await email_service.send_rsi_correlation_alert(to, f"RSI Corr Debug #{random.randint(1000,9999)}", "real_correlation", pairs, cfg)
            detail = {"pairs": len(pairs)}
        elif t == "news_reminder":
            ok = await email_service.send_news_reminder(
                to,
                event_title="CPI YoY",
                event_time_local=f"{datetime.now().strftime('%Y-%m-%d %H:%M')} IST",
                currency=random.choice(["USD", "EUR", "GBP", "JPY"]),
                impact=random.choice(["High", "Medium"]),
                previous=f"{round(random.uniform(1.0, 6.0), 1)}%",
                forecast=f"{round(random.uniform(1.0, 6.0), 1)}%",
                expected=f"{round(random.uniform(1.0, 6.0), 1)}%",
                bias=random.choice(["bullish", "bearish", "neutral"]),
            )
        elif t == "daily_brief":
            payload = {
                "date_local": datetime.now().strftime("%Y-%m-%d"),
                "time_label": "IST",
                "tz_name": "Asia/Kolkata",
                "core_signals": [
                    {"pair": s, "signal": random.choice(["BUY", "SELL"]), "probability": round(random.uniform(55, 90), 1), "tf": sample_tf(), "badge_bg": "#19235d"}
                    for s in sample_symbols(3)
                ],
                "rsi_oversold": [{"pair": s, "rsi": round(random.uniform(10, 25), 1)} for s in sample_symbols(2)],
                "rsi_overbought": [{"pair": s, "rsi": round(random.uniform(75, 90), 1)} for s in sample_symbols(2)],
                "news": [
                    {"title": "CPI YoY", "time_local": "09:00 IST", "currency": "USD", "expected": "2.1%", "forecast": "2.2%", "bias": random.choice(["bullish", "bearish"])},
                ],
            }
            ok = await email_service.send_daily_brief(to, payload)
        elif t == "currency_strength":
            timeframe = random.choice(["5M", "15M", "30M", "1H", "4H", "1D", "1W"])
            strong = random.choice(["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"])
            weak = random.choice([c for c in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"] if c != strong])
            ti = [
                {"signal": "strongest", "symbol": strong, "strength": round(random.uniform(20, 80), 2)},
                {"signal": "weakest", "symbol": weak, "strength": round(random.uniform(-80, -20), 2)},
            ]
            all_vals = {c: round(random.uniform(-100, 100), 2) for c in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]}
            prev = {"strongest": {"symbol": random.choice(list(all_vals.keys()))}, "weakest": {"symbol": random.choice(list(all_vals.keys()))}}
            ok = await email_service.send_currency_strength_alert(to, f"Currency Strength Debug #{random.randint(1000,9999)}", timeframe, ti, prev_winners=prev, all_values=all_vals)
            detail = {"timeframe": timeframe}
        else:
            raise HTTPException(status_code=400, detail="unhandled_type")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"type": t, "to": to, "sent": bool(ok), "detail": detail}

# Include the debug router so that all /api/debug/* endpoints share the same bearer protection
app.include_router(debug_router)

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
    
    # RSI correlation alerts removed per product decision

class WSClient:
    def __init__(self, websocket: WebSocket, token: str, supported_data_types: Optional[Set[str]] = None, *, v2_broadcast: bool = False):
        self.websocket = websocket
        self.token = token
        self._last_sent_ts: Dict[str, int] = {}
        # Per-client subscription model removed for v2 broadcast-only
        # subscriptions retained as generic dict for OHLC boundary scheduling state
        self.subscriptions: Dict[str, Dict[Timeframe, Any]] = {}
        # next_ohlc_updates[symbol][timeframe] -> next boundary datetime
        self.next_ohlc_updates: Dict[str, Dict[Timeframe, datetime]] = {}
        self._task: Optional[asyncio.Task] = None
        self._ohlc_task: Optional[asyncio.Task] = None
        self._send_interval_s: float = 1.0  # 1 Hz for ticks (1000ms)
        # Supported data types for this connection (endpoint specific)
        self.supported_data_types: Set[str] = set(supported_data_types or {"ticks", "ohlc"})
        # v2 broadcast mode: server pushes all symbols/timeframes without explicit subscribe
        self.v2_broadcast: bool = bool(v2_broadcast)
        # Security/cap tracking
        self._subscription_count: int = 0

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
        # Only start OHLC loop when this connection supports OHLC streaming
        if "ohlc" in self.supported_data_types:
            self._ohlc_task = asyncio.create_task(self._ohlc_loop())
        # If v2 broadcast, pre-schedule OHLC boundary updates for baseline symbols/timeframes
        if self.v2_broadcast and ("ohlc" in self.supported_data_types):
            try:
                now = datetime.now(timezone.utc)
                baseline_tfs: List[Timeframe] = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1]
                for sym in RSI_SUPPORTED_SYMBOLS:
                    try:
                        await _ensure_symbol_selected_async(sym)
                    except Exception:
                        continue
                    for tf in baseline_tfs:
                        self.next_ohlc_updates.setdefault(sym, {})[tf] = calculate_next_update_time(now, tf)
            except Exception:
                # Never block connection start
                pass

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
                await asyncio.sleep(1.0)  # 1000ms resolution for boundary checks
        except asyncio.CancelledError:
            return
        except Exception:
            # Likely disconnect while sending
            return

    async def _send_tick_updates(self):
        """Send real-time tick updates for subscribed symbols"""
        tick_symbols = set()
        
        # Per-client tick subscriptions removed; rely on v2 broadcast baseline
        
        # v2 broadcast: include rollout baseline symbols (gradual enablement)
        if self.v2_broadcast:
            try:
                # Stream all allowed symbols (default: full RSI_SUPPORTED_SYMBOLS)
                # WS_ALLOWED_SYMBOLS env var can narrow this allowlist.
                tick_symbols.update(ALLOWED_WS_SYMBOLS)
            except Exception:
                # Fallback to full supported list
                tick_symbols.update([s.upper() for s in RSI_SUPPORTED_SYMBOLS])
        
        if not tick_symbols:
            return
            
        updates: List[dict] = []
        iter_count = 0
        for sym in list(tick_symbols):
            try:
                await _ensure_symbol_selected_async(sym)
                info = await _symbol_info_tick_async(sym)
                if info is None:
                    continue
                ts_ms = getattr(info, "time_msc", 0) or int(getattr(info, "time", 0)) * 1000
                if self._last_sent_ts.get(sym) == ts_ms:
                    continue
                tick = _to_tick(sym, info)
                if tick:
                    # Inject daily_change_pct using cached D1 reference to avoid heavy calls per tick
                    dcp_val: Optional[float] = None
                    dc_val: Optional[float] = None
                    try:
                        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        cache_key = _d1_ref_cache.get(sym)
                        if not cache_key or cache_key[0] != today_key:
                            # Refresh reference once per day per symbol
                            ref = await _daily_change_pct_bid_async(sym)
                            # get_daily_change_pct_bid computes using current bid; extract ref by reversing would be noisy
                            # For per-tick efficiency, we will compute dcp directly each time with helper, no separate ref exposure
                            dcp_val = ref
                            dc_val = await _daily_change_bid_async(sym)
                            _d1_ref_cache[sym] = (today_key, ref if ref is not None else float('nan'))
                        else:
                            # Recompute with helper to honor spec with latest bid; fallback to cached ref-derived value
                            dcp_val = await _daily_change_pct_bid_async(sym)
                            dc_val = await _daily_change_bid_async(sym)
                    except Exception:
                        dcp_val = None
                        dc_val = None
                    tick_dict = tick.model_dump()
                    if dcp_val is not None and dcp_val == dcp_val:  # not NaN
                        tick_dict["daily_change_pct"] = float(dcp_val)
                    if dc_val is not None and dc_val == dc_val:  # not NaN
                        tick_dict["daily_change"] = float(dc_val)

                    # Store full tick data for alert processing
                    full_tick_data = tick_dict.copy()
                    updates.append(full_tick_data)
                    self._last_sent_ts[sym] = ts_ms

                    # Update OHLC caches for baseline timeframes in v2 broadcast mode only (no live OHLC streaming in v2)
                    if self.v2_broadcast and ("ohlc" in self.supported_data_types):
                        try:
                            baseline_tfs: List[Timeframe] = [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1]
                            for tf in baseline_tfs:
                                await _update_ohlc_cache_async(sym, tf)
                        except Exception:
                            pass
                    # Update latest price cache for REST pricing endpoint
                    try:
                        await price_cache.update(
                            sym,
                            time_ms=ts_ms,
                            time_iso=tick_dict.get("time_iso"),
                            bid=tick_dict.get("bid"),
                            ask=tick_dict.get("ask"),
                            daily_change_pct=tick_dict.get("daily_change_pct"),
                        )
                    except Exception:
                        pass
                    # Immediately send this tick to clients (per-tick emission)
                    try:
                        bid_only_tick = {
                            "symbol": full_tick_data["symbol"],
                            "time": full_tick_data["time"],
                            "time_iso": full_tick_data.get("time_iso"),
                            "bid": full_tick_data.get("bid"),
                            "daily_change_pct": full_tick_data.get("daily_change_pct"),
                            "daily_change": full_tick_data.get("daily_change"),
                        }
                        sent = await self._try_send_bytes(orjson.dumps({"type": "tick", "data": bid_only_tick}))
                        # Metrics: per-endpoint counters for tick messages and items
                        try:
                            label = getattr(self, "conn_label", "v2")
                            _metrics_inc(label, "ticks_items", by=1)
                            if sent:
                                _metrics_inc(label, "ok_ticks", 1)
                            else:
                                _metrics_inc(label, "fail_ticks", 1)
                        except Exception:
                            pass
                        if not sent:
                            return
                    except Exception:
                        # Do not crash the loop on send issues; treat as disconnect
                        return
            except HTTPException:
                # symbol disappeared or invalid; drop it
                # Clean internal scheduling state
                if sym in self.subscriptions:
                    del self.subscriptions[sym]
            finally:
                iter_count += 1
                if (iter_count % 10) == 0:
                    # Yield to keep event loop responsive for new connections
                    try:
                        await asyncio.sleep(0)
                    except Exception:
                        pass
        if updates:
            # Check for alerts on tick updates (non-blocking background task)
            # Only check alerts if there are active alerts to avoid unnecessary processing
            try:
                all_alerts = await alert_cache.get_all_alerts()
                total_alerts = sum(len(alerts) for alerts in all_alerts.values())

                if ENABLE_TICK_TRIGGERED_ALERTS and total_alerts > 0:
                    # Provide tick_data in a dict keyed by symbol as expected by alert services
                    # Use full tick data for alert processing (includes bid, ask, volume, etc.)
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
            except Exception:
                # If alert cache check fails, still try to check alerts to be safe
                if ENABLE_TICK_TRIGGERED_ALERTS:
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
                    asyncio.create_task(_check_alerts_safely(tick_data))
    
    async def _send_scheduled_ohlc_updates(self):
        """Send OHLC updates when timeframe periods complete"""
        current_time = datetime.now(timezone.utc)
        
        for symbol, tf_map in list(self.next_ohlc_updates.items()):
            for tf, next_update_time in list(tf_map.items()):
                if current_time >= next_update_time:
                    try:
                        # Per-client OHLC subscriptions removed; emit updates only in v2 broadcast mode when enabled
                        if self.v2_broadcast and ("ohlc" in self.supported_data_types):
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
                        # Remove problematic scheduling state
                        try:
                            if symbol in self.next_ohlc_updates and tf in self.next_ohlc_updates[symbol]:
                                del self.next_ohlc_updates[symbol][tf]
                                if not self.next_ohlc_updates[symbol]:
                                    del self.next_ohlc_updates[symbol]
                        except Exception:
                            pass

    async def handle_message(self, message: dict):
        action = message.get("action")
        # Subscriptions removed in v2 broadcast-only; keep ping/pong for keepalive
        if action == "ping":
            await self._try_send_json({"type": "pong"})
        elif action in ("subscribe", "unsubscribe"):
            await self._try_send_json({"type": "info", "message": "v2 broadcast-only: subscribe/unsubscribe ignored"})
        else:
            await self._try_send_json({"type": "error", "error": "unknown_action"})

"""Legacy (/ws/ticks) and v1 (/ws/market) WebSocket endpoints have been removed after cutover. Use /market-v2."""

@app.websocket("/market-v2")
async def ws_market_v2(websocket: WebSocket):
    """Versioned Market Data WebSocket (v2)

    Serves tick and indicator streams and advertises capabilities via greeting.
    """
    client = None
    try:
        # Optional auth: mirror REST policy
        if not _ws_is_authorized(websocket):
            try:
                await websocket.close(code=1008)
            finally:
                return
        await websocket.accept()
        # Send a capabilities greeting for v2
        try:
            await websocket.send_json({
                "type": "connected",
                "message": "WebSocket connected successfully",
                "supported_timeframes": [tf.value for tf in Timeframe],
                # v2: ticks + indicators only; OHLC is not streamed to frontend
                "supported_data_types": ["ticks", "indicators"],
                "supported_price_bases": ["last", "bid", "ask"],
                "indicators": {
                    "rsi": {"method": "wilder", "applied_price": "close", "periods": [14]}
                },
            })
        except Exception:
            # Client may already have disconnected
            pass

        # Reuse the same WSClient implementation, enable broadcast-all for v2
        client = WSClient(websocket, "", supported_data_types={"ticks", "indicators"}, v2_broadcast=True)
        # Tag connection for metrics
        try:
            setattr(client, "conn_label", "v2")
            _metrics_inc("v2", "connections_opened", 1)
        except Exception:
            pass
        await client.start()
        async with _connected_clients_lock:
            _connected_clients.add(client)

        # Main receive loop
        while True:
            if getattr(websocket, "client_state", None) != WebSocketState.CONNECTED:
                break
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                break

            try:
                message = orjson.loads(data)
                await client.handle_message(message)
            except Exception as parse_error:
                try:
                    await websocket.send_json({"type": "error", "error": str(parse_error)})
                except Exception:
                    break
    except WebSocketDisconnect:
        print("Websocket v2 Disconnected")
    except Exception as e:
        if "accept" in str(e).lower() and "websocket" in str(e).lower():
            print("🔌 WebSocket v2 disconnected (accept state)")
        else:
            print(f"❌ WebSocket v2 error: {e}")
    finally:
        if client:
            await client.stop()
        try:
            async with _connected_clients_lock:
                _connected_clients.discard(client)
        except Exception:
            pass
        try:
            _metrics_inc("v2", "connections_closed", 1)
        except Exception:
            pass

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
    print("   - WebSocket (v2): ws://localhost:8000/market-v2")
    print("   - REST Indicator: GET /api/indicator?indicator=rsi&timeframe=5M&pairs=EURUSDm")
    print("   - News Analysis: GET /api/news/analysis")
    print("   - News Refresh: POST /api/news/refresh")
    print("   - Alert Cache: GET /api/alerts/cache")
    print("   - Alerts by Category: GET /api/alerts/by-category")
    print("   - User Alerts: GET /api/alerts/user/{user_id}")
    print("   - Alert Refresh: POST /api/alerts/refresh")
    print("   - Alerts Cache: GET /api/alerts/cache")
    print("   - Refresh Alerts: POST /api/alerts/refresh")
    print("   - Trending Pairs: GET /trending-pairs")
    print("   - Health check: GET /health")
    
    _install_sigterm_handler(asyncio.get_event_loop())
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False, server_header=False, date_header=False)

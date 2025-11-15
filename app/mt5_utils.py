from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import logging

import MetaTrader5 as mt5
from fastapi import HTTPException

from .models import Timeframe, OHLC, Tick
 


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

logger = logging.getLogger(__name__)
_live_rsi_last_logged: Dict[str, int] = {}


def canonicalize_symbol(symbol: str) -> str:
    """Return canonical MT5 symbol form.

    Rules:
    - Trim whitespace
    - Uppercase core instrument letters
    - Force trailing broker suffix 'm' to lowercase when present
    - If no suffix, return fully uppercased symbol
    """
    try:
        s = str(symbol).strip()
        if not s:
            return s
        if s[-1] in ("m", "M"):
            return s[:-1].upper() + "m"
        return s.upper()
    except Exception:
        return str(symbol)


def ensure_symbol_selected(symbol: str) -> None:
    symbol = canonicalize_symbol(symbol)
    info = mt5.symbol_info(symbol)
    if info is None:
        all_symbols = mt5.symbols_get()
        if all_symbols:
            sample_symbols = [s.name for s in all_symbols[:10]]
            upper_symbol = symbol.upper()
            lower_symbol = symbol.lower()
            similar_symbols = []
            for s in all_symbols:
                s_name = s.name
                if upper_symbol in s_name.upper() or lower_symbol in s_name.lower():
                    similar_symbols.append(s_name)
                    if len(similar_symbols) >= 5:
                        break
            error_detail = f"Unknown symbol: '{symbol}'. "
            if similar_symbols:
                error_detail += f"Similar symbols found: {similar_symbols}"
            else:
                error_detail += f"Available symbols (first 10): {sample_symbols}"
            raise HTTPException(status_code=404, detail=error_detail)
        else:
            raise HTTPException(status_code=500, detail="No symbols available from MT5 - check connection")

    if not mt5.symbol_select(symbol, True):
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                raise HTTPException(status_code=400, detail=f"Failed to select symbol: {symbol} - symbol exists but cannot be selected")
        else:
            raise HTTPException(status_code=400, detail=f"Failed to select symbol: {symbol} - unknown error")


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


def get_current_tick(symbol: str) -> Optional[Tick]:
    """Return the current tick for a symbol as a Tick model, or None if unavailable."""
    symbol = canonicalize_symbol(symbol)
    ensure_symbol_selected(symbol)
    info = mt5.symbol_info_tick(symbol)
    return _to_tick(symbol, info)


def _to_ohlc(symbol: str, timeframe: str, rate_data) -> Optional[OHLC]:
    if rate_data is None:
        return None
    try:
        def _rate_val(key: str, idx: int) -> Optional[float]:
            try:
                return float(getattr(rate_data, key))  # type: ignore[arg-type]
            except Exception:
                pass
            try:
                return float(rate_data[key])  # type: ignore[index]
            except Exception:
                pass
            try:
                return float(rate_data[idx])
            except Exception:
                return None
        ts_ms = int(rate_data[0]) * 1000
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        # Determine closed status at conversion time to support strict closed-bar consumers
        tf_seconds_map = {
            "1M": 60,
            "5M": 300,
            "15M": 900,
            "30M": 1800,
            "1H": 3600,
            "4H": 14400,
            "1D": 86400,
            "1W": 604800,
        }
        tf_secs = tf_seconds_map.get(timeframe, 60)
        bar_end_ms = ts_ms + (tf_secs * 1000)
        is_closed = int(datetime.now(timezone.utc).timestamp() * 1000) >= bar_end_ms
        # Derive bid/ask parallel fields using spread when available
        # Prefer structured field `spread` if present; otherwise, fallback to current tick
        spread_points = _rate_val("spread", 6)
        point = None
        try:
            sym_info = mt5.symbol_info(symbol)
            point = getattr(sym_info, "point", None) if sym_info else None
        except Exception:
            point = None
        if (not spread_points or spread_points == 0) and point:
            try:
                tinfo = mt5.symbol_info_tick(symbol)
                if tinfo and getattr(tinfo, "bid", None) is not None and getattr(tinfo, "ask", None) is not None:
                    spread_points = (float(getattr(tinfo, "ask")) - float(getattr(tinfo, "bid"))) / float(point)
            except Exception:
                pass
        half_spread = (spread_points * point / 2.0) if (spread_points and point) else 0.0
        open_bid = float(rate_data[1]) - half_spread if half_spread else None
        high_bid = float(rate_data[2]) - half_spread if half_spread else None
        low_bid = float(rate_data[3]) - half_spread if half_spread else None
        close_bid = float(rate_data[4]) - half_spread if half_spread else None
        open_ask = float(rate_data[1]) + half_spread if half_spread else None
        high_ask = float(rate_data[2]) + half_spread if half_spread else None
        low_ask = float(rate_data[3]) + half_spread if half_spread else None
        close_ask = float(rate_data[4]) + half_spread if half_spread else None

        return OHLC(
            symbol=symbol,
            timeframe=timeframe,
            time=ts_ms,
            time_iso=dt.isoformat(),
            open=float(rate_data[1]),
            high=float(rate_data[2]),
            low=float(rate_data[3]),
            close=float(rate_data[4]),
            volume=_rate_val("real_volume", 7) or _rate_val("tick_volume", 5),
            tick_volume=_rate_val("tick_volume", 5),
            spread=_rate_val("spread", 6),
            openBid=open_bid,
            highBid=high_bid,
            lowBid=low_bid,
            closeBid=close_bid,
            openAsk=open_ask,
            highAsk=high_ask,
            lowAsk=low_ask,
            closeAsk=close_ask,
            is_closed=is_closed,
        )
    except (IndexError, ValueError, TypeError) as e:
        print(f"Error converting rate data to OHLC: {e}")
        print(f"Rate data type: {type(rate_data)}")
        print(f"Rate data: {rate_data}")
        return None


def get_ohlc_data(symbol: str, timeframe: Timeframe, count: int = 250) -> List[OHLC]:
    symbol = canonicalize_symbol(symbol)
    ensure_symbol_selected(symbol)
    mt5_timeframe = MT5_TIMEFRAMES.get(timeframe)
    if mt5_timeframe is None:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
    if rates is None or len(rates) == 0:
        logger.debug(f"⚠️ No rates from MT5 for {symbol}")
        return []
    ohlc_data = []
    for rate in rates:
        ohlc = _to_ohlc(symbol, timeframe.value, rate)
        if ohlc:
            ohlc_data.append(ohlc)
    return ohlc_data


def get_current_ohlc(symbol: str, timeframe: Timeframe) -> Optional[OHLC]:
    symbol = canonicalize_symbol(symbol)
    data = get_ohlc_data(symbol, timeframe, 1)
    return data[0] if data else None


def _get_d1_reference_bid(symbol: str) -> Optional[float]:
    """Return the D1 reference price on Bid basis for daily change calculation.

    Strategy per spec:
    - Fetch last 2 D1 bars.
    - If the latest D1 bar is for today (UTC-based check), use its open (Bid).
    - Otherwise, use the previous D1 bar's close (Bid) to handle session transitions.
    """
    try:
        bars = get_ohlc_data(symbol, Timeframe.D1, 2)
        if not bars:
            return None
        latest = bars[-1]
        prev = bars[-2] if len(bars) > 1 else None

        now_date = datetime.now(timezone.utc).date()
        try:
            latest_dt = datetime.fromisoformat(latest.time_iso)
        except Exception:
            latest_dt = datetime.fromtimestamp(latest.time / 1000.0, tz=timezone.utc)
        latest_date = latest_dt.date()

        # Prefer Bid-parallel fields when available
        if latest_date == now_date:
            ref = latest.openBid if latest.openBid is not None else latest.open
            return float(ref) if ref is not None else None
        # Fallback to previous D1 close on Bid basis
        if prev is not None:
            ref = prev.closeBid if prev.closeBid is not None else prev.close
            return float(ref) if ref is not None else None
        # If only one bar present, fallback to its open
        ref = latest.openBid if latest.openBid is not None else latest.open
        return float(ref) if ref is not None else None
    except Exception:
        return None


def get_daily_change_pct_bid(symbol: str) -> Optional[float]:
    """Compute daily percentage change on Bid basis.

    daily_change_pct = 100 * (bid_now - D1_reference) / D1_reference
    where D1_reference is today's D1 open (bid) if today; else previous D1 close (bid).
    """
    try:
        symbol = canonicalize_symbol(symbol)
        ensure_symbol_selected(symbol)
        tick = get_current_tick(symbol)
        if tick is None or tick.bid is None:
            return None
        ref = _get_d1_reference_bid(symbol)
        if ref is None or ref == 0.0:
            return None
        return 100.0 * (float(tick.bid) - float(ref)) / float(ref)
    except Exception:
        return None

def get_daily_change_bid(symbol: str) -> Optional[float]:
    """Compute absolute daily change on Bid basis.

    daily_change = bid_now - D1_reference
    where D1_reference is today's D1 open (bid) if today; else previous D1 close (bid).
    """
    try:
        symbol = canonicalize_symbol(symbol)
        ensure_symbol_selected(symbol)
        tick = get_current_tick(symbol)
        if tick is None or tick.bid is None:
            return None
        ref = _get_d1_reference_bid(symbol)
        if ref is None:
            return None
        return float(tick.bid) - float(ref)
    except Exception:
        return None
 


def calculate_next_update_time(subscription_time: datetime, timeframe: Timeframe) -> datetime:
    if timeframe == Timeframe.M1:
        next_update = subscription_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
    elif timeframe == Timeframe.M5:
        current_minute = subscription_time.minute
        next_minute = ((current_minute // 5) + 1) * 5
        if next_minute >= 60:
            next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_update = subscription_time.replace(minute=next_minute, second=0, microsecond=0)
    elif timeframe == Timeframe.M15:
        current_minute = subscription_time.minute
        next_minute = ((current_minute // 15) + 1) * 15
        if next_minute >= 60:
            next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_update = subscription_time.replace(minute=next_minute, second=0, microsecond=0)
    elif timeframe == Timeframe.M30:
        current_minute = subscription_time.minute
        next_minute = ((current_minute // 30) + 1) * 30
        if next_minute >= 60:
            next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_update = subscription_time.replace(minute=next_minute, second=0, microsecond=0)
    elif timeframe == Timeframe.H1:
        next_update = subscription_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif timeframe == Timeframe.H4:
        current_hour = subscription_time.hour
        next_hour = ((current_hour // 4) + 1) * 4
        if next_hour >= 24:
            next_update = subscription_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            next_update = subscription_time.replace(hour=next_hour, minute=0, second=0, microsecond=0)
    elif timeframe == Timeframe.D1:
        next_update = subscription_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    elif timeframe == Timeframe.W1:
        days_ahead = 7 - subscription_time.weekday()
        if days_ahead == 7:
            days_ahead = 7
        next_update = subscription_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    else:
        # Default to 1 minute for safety
        next_update = subscription_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return next_update


# Caches
global_ohlc_cache: Dict[str, Dict[str, deque]] = {}


def update_ohlc_cache(symbol: str, timeframe: Timeframe, max_bars: int = 250):
    global global_ohlc_cache
    symbol = canonicalize_symbol(symbol)
    if symbol not in global_ohlc_cache:
        global_ohlc_cache[symbol] = {}
    if timeframe.value not in global_ohlc_cache[symbol]:
        global_ohlc_cache[symbol][timeframe.value] = deque(maxlen=max_bars)
    current_ohlc = get_current_ohlc(symbol, timeframe)
    if current_ohlc is None:
        return
    cache = global_ohlc_cache[symbol][timeframe.value]
    if not cache or cache[-1].time != current_ohlc.time:
        cache.append(current_ohlc)
    else:
        cache[-1] = current_ohlc


def get_cached_ohlc(symbol: str, timeframe: Timeframe, count: int = 250) -> List[OHLC]:
    global global_ohlc_cache
    symbol = canonicalize_symbol(symbol)
    if symbol not in global_ohlc_cache:
        global_ohlc_cache[symbol] = {}
    if timeframe.value not in global_ohlc_cache[symbol]:
        # Only log cache miss at debug level to reduce noise
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"📡 Cache miss - fetching from MT5: {symbol} {timeframe.value}")
        ohlc_data = get_ohlc_data(symbol, timeframe, count)
        global_ohlc_cache[symbol][timeframe.value] = deque(ohlc_data, maxlen=count)
        return ohlc_data
    return list(global_ohlc_cache[symbol][timeframe.value])


def get_ohlc_data_range(symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> List[OHLC]:
    """Fetch OHLC bars within a time range using MT5 copy_rates_range.

    - Returns bars whose open time lies within [start, end].
    - Depth is constrained by broker history and MT5 terminal settings (Max bars in history).
    - Results are converted to internal OHLC models.
    """
    symbol = canonicalize_symbol(symbol)
    ensure_symbol_selected(symbol)
    mt5_timeframe = MT5_TIMEFRAMES.get(timeframe)
    if mt5_timeframe is None:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
    try:
        rates = mt5.copy_rates_range(symbol, mt5_timeframe, start, end)
    except Exception as e:
        logger.debug(f"⚠️ copy_rates_range error for {symbol} {timeframe.value}: {e}")
        rates = None
    if rates is None or len(rates) == 0:
        logger.debug(
            f"⚠️ No rates from MT5 for range {symbol} {timeframe.value} {start.isoformat()} .. {end.isoformat()}"
        )
        return []
    ohlc_data: List[OHLC] = []
    for rate in rates:
        ohlc = _to_ohlc(symbol, timeframe.value, rate)
        if ohlc:
            ohlc_data.append(ohlc)
    return ohlc_data

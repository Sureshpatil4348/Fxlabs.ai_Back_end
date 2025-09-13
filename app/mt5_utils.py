from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

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


def ensure_symbol_selected(symbol: str) -> None:
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


def _to_ohlc(symbol: str, timeframe: str, rate_data) -> Optional[OHLC]:
    if rate_data is None:
        return None
    try:
        ts_ms = int(rate_data[0]) * 1000
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return OHLC(
            symbol=symbol,
            timeframe=timeframe,
            time=ts_ms,
            time_iso=dt.isoformat(),
            open=float(rate_data[1]),
            high=float(rate_data[2]),
            low=float(rate_data[3]),
            close=float(rate_data[4]),
            volume=float(rate_data[5])
        )
    except (IndexError, ValueError, TypeError) as e:
        print(f"Error converting rate data to OHLC: {e}")
        print(f"Rate data type: {type(rate_data)}")
        print(f"Rate data: {rate_data}")
        return None


def get_ohlc_data(symbol: str, timeframe: Timeframe, count: int = 250) -> List[OHLC]:
    ensure_symbol_selected(symbol)
    mt5_timeframe = MT5_TIMEFRAMES.get(timeframe)
    if mt5_timeframe is None:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
    rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
    if rates is None or len(rates) == 0:
        print(f"⚠️ No rates from MT5 for {symbol}")
        return []
    ohlc_data = []
    for rate in rates:
        ohlc = _to_ohlc(symbol, timeframe.value, rate)
        if ohlc:
            ohlc_data.append(ohlc)
    return ohlc_data


def get_current_ohlc(symbol: str, timeframe: Timeframe) -> Optional[OHLC]:
    data = get_ohlc_data(symbol, timeframe, 1)
    return data[0] if data else None


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
        next_update = subscription_time + timedelta(minutes=1)
    return next_update


# Caches
global_ohlc_cache: Dict[str, Dict[str, deque]] = {}


def update_ohlc_cache(symbol: str, timeframe: Timeframe, max_bars: int = 250):
    global global_ohlc_cache
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
    if symbol not in global_ohlc_cache:
        global_ohlc_cache[symbol] = {}
    if timeframe.value not in global_ohlc_cache[symbol]:
        print(f"📡 Cache miss - fetching from MT5: {symbol} {timeframe.value}")
        ohlc_data = get_ohlc_data(symbol, timeframe, count)
        global_ohlc_cache[symbol][timeframe.value] = deque(ohlc_data, maxlen=count)
        return ohlc_data
    return list(global_ohlc_cache[symbol][timeframe.value])



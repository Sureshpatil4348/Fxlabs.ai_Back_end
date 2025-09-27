import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # type: ignore

from .alert_logging import log_debug, log_info, log_error
from .constants import RSI_SUPPORTED_SYMBOLS
from .email_service import email_service
from .models import Timeframe
from .mt5_utils import get_ohlc_data
from .heatmap_tracker_alert_service import heatmap_tracker_alert_service
from . import news as news_mod


logger = logging.getLogger(__name__)


def _unsuffix_symbol(symbol: str) -> str:
    s = symbol.strip()
    return s[:-1] if s.endswith("m") else s


def _pair_display(symbol: str) -> str:
    raw = _unsuffix_symbol(symbol)
    if len(raw) >= 6:
        return f"{raw[:3]}/{raw[3:6]}"
    return raw


def _rsi_latest_from_closes(closes: List[float], period: int = 14) -> Optional[float]:
    n = len(closes)
    if n < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        rsi_first = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_first = 100 - (100 / (1 + rs))
    rsi_val = rsi_first
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_val = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


async def _collect_core_signals() -> List[Dict[str, Any]]:
    """Collect All-in-One (Quantum Analysis) signals for EURUSD, XAUUSD, BTCUSD using dayTrader style."""
    pairs = [
        ("EURUSDm", "EUR/USD"),
        ("XAUUSDm", "XAU/USD"),
        ("BTCUSDm", "BTC/USD"),
    ]
    results: List[Dict[str, Any]] = []
    for sym, disp in pairs:
        try:
            buy_pct, sell_pct, _score = await heatmap_tracker_alert_service._compute_buy_sell_percent(sym, "dayTrader")
            signal = "BUY" if buy_pct >= sell_pct else "SELL"
            probability = round(float(buy_pct if signal == "BUY" else sell_pct), 2)
            badge_bg = "#0CCC7C" if signal == "BUY" else "#E5494D"
            results.append({
                "pair": disp,
                "signal": signal,
                "probability": probability,
                "tf": "dayTrader",
                "badge_bg": badge_bg,
            })
        except Exception as e:
            log_error(logger, "daily_core_signal_error", symbol=sym, error=str(e))
    return results


async def _collect_rsi_h4(period: int = 14) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (oversold_list, overbought_list) for RSI on H4 across supported symbols with thresholds 30/70."""
    oversold: List[Dict[str, Any]] = []
    overbought: List[Dict[str, Any]] = []
    for sym in RSI_SUPPORTED_SYMBOLS:
        try:
            ohlc = get_ohlc_data(sym, Timeframe.H4, period + 20)
            closes = [b.close for b in ohlc]
            rsi_val = _rsi_latest_from_closes(closes, period)
            if rsi_val is None:
                continue
            rsi_rounded = round(float(rsi_val), 2)
            entry = {"pair": _pair_display(sym), "rsi": rsi_rounded}
            if rsi_rounded <= 30.0:
                oversold.append(entry)
            elif rsi_rounded >= 70.0:
                overbought.append(entry)
        except Exception:
            continue
    # Sort for readability
    oversold.sort(key=lambda x: x.get("pair", ""))
    overbought.sort(key=lambda x: x.get("pair", ""))
    return oversold, overbought


def _ist_now() -> datetime:
    if ZoneInfo is None:
        return datetime.now(timezone.utc)
    return datetime.now(ZoneInfo("Asia/Kolkata"))


def _format_date_ist(d: Optional[datetime] = None) -> str:
    dt = d or _ist_now()
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return dt.date().isoformat()


def _collect_today_news_compact() -> List[Dict[str, Any]]:
    """Select today's (IST) high/medium impact news from cache with compact fields."""
    items: List[Dict[str, Any]] = []
    try:
        if not news_mod.global_news_cache:
            news_mod.load_news_cache_from_disk()
    except Exception:
        pass
    try:
        today_ist = _ist_now().date()
        for item in list(news_mod.global_news_cache):
            try:
                # Impact filter
                impact = (item.analysis.get("impact") if item.analysis else "").strip().lower()
                if impact not in ("high", "medium"):
                    continue
                # Date filter in IST
                time_local = news_mod._format_event_time_local(item.time)  # e.g., "YYYY-mm-dd HH:MM IST"
                date_str = (time_local or "").split(" ")[0]
                if not date_str:
                    continue
                try:
                    dt_ist_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if dt_ist_date != today_ist:
                    continue
                title = (item.headline or "").strip()
                bias = news_mod._derive_bias(item.analysis.get("effect") if item.analysis else None)
                items.append({
                    "title": title,
                    "time_local": time_local,
                    "expected": "-",  # not available pre-release
                    "forecast": item.forecast or "-",
                    "bias": bias,
                })
            except Exception:
                continue
        return items
    except Exception as e:
        log_error(logger, "daily_news_collect_error", error=str(e))
        return []


async def _build_daily_payload() -> Dict[str, Any]:
    date_local = _format_date_ist()
    core_signals = await _collect_core_signals()
    rsi_os, rsi_ob = await _collect_rsi_h4()
    news_today = _collect_today_news_compact()
    payload = {
        "date_local_IST": date_local,
        "core_signals": core_signals,
        "rsi_oversold": rsi_os,
        "rsi_overbought": rsi_ob,
        "news": news_today,
    }
    return payload


def _next_9am_ist_utc(now_utc: Optional[datetime] = None) -> datetime:
    now_utc = now_utc or datetime.now(timezone.utc)
    if ZoneInfo is None:
        # Fallback: treat UTC as IST (approx) → schedule in 24h cycles
        base = now_utc
        target = base.replace(hour=3, minute=30, second=0, microsecond=0)  # 9:00 IST is 03:30 UTC (fixed, no DST)
        if base >= target:
            target = target + timedelta(days=1)
        return target
    ist = now_utc.astimezone(ZoneInfo("Asia/Kolkata"))
    target_ist = ist.replace(hour=9, minute=0, second=0, microsecond=0)
    if ist >= target_ist:
        target_ist = target_ist + timedelta(days=1)
    return target_ist.astimezone(timezone.utc)


async def _send_daily_to_all_users(payload: Dict[str, Any]) -> None:
    try:
        emails = await news_mod._fetch_all_user_emails()
    except Exception as e:
        log_error(logger, "daily_fetch_users_error", error=str(e))
        emails = []
    if not emails:
        log_error(logger, "daily_no_users")
        return
    log_info(logger, "daily_send_batch", users=len(emails))
    tasks = []
    for em in emails:
        tasks.append(email_service.send_daily_brief(user_email=em, payload=payload))
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        log_error(logger, "daily_send_batch_error", error=str(e))


async def daily_mail_scheduler() -> None:
    """Scheduler that sends a Daily Morning Brief at 09:00 IST every day."""
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_9am_ist_utc(now)
            sleep_s = max(1.0, (next_run - now).total_seconds())
            log_info(logger, "daily_sleep_until", next_run_utc=next_run.isoformat(), seconds=int(sleep_s))
            await asyncio.sleep(sleep_s)

            # Build and send
            log_info(logger, "daily_build_start")
            payload = await _build_daily_payload()
            log_info(
                logger,
                "daily_build_done",
                core=len(payload.get("core_signals", [])),
                os=len(payload.get("rsi_oversold", [])),
                ob=len(payload.get("rsi_overbought", [])),
                news=len(payload.get("news", [])),
            )
            await _send_daily_to_all_users(payload)
            log_info(logger, "daily_completed")
        except asyncio.CancelledError:
            return
        except Exception as e:
            log_error(logger, "daily_scheduler_error", error=str(e))
            await asyncio.sleep(60)



import asyncio
import logging
import aiohttp
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
from .rsi_utils import calculate_rsi_latest, closed_closes
from . import news as news_mod
from .config import SUPABASE_URL, SUPABASE_SERVICE_KEY, DAILY_TZ_NAME, DAILY_SEND_LOCAL_TIME


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
    # Enforce RSI(14)
    return calculate_rsi_latest(closes, 14)


async def _collect_core_signals() -> List[Dict[str, Any]]:
    """Collect All-in-One (Quantum Analysis) signals for EURUSD, XAUUSD, BTCUSD using scalper style."""
    pairs = [
        ("EURUSDm", "EUR/USD"),
        ("XAUUSDm", "XAU/USD"),
        ("BTCUSDm", "BTC/USD"),
    ]
    results: List[Dict[str, Any]] = []
    for sym, disp in pairs:
        try:
            buy_pct, sell_pct, _score = await heatmap_tracker_alert_service._compute_buy_sell_percent(sym, "scalper")
            signal = "BUY" if buy_pct >= sell_pct else "SELL"
            probability = round(float(buy_pct if signal == "BUY" else sell_pct), 2)
            badge_bg = "#0CCC7C" if signal == "BUY" else "#E5494D"
            results.append({
                "pair": disp,
                "signal": signal,
                "probability": probability,
                "tf": "Intraday",
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
            ohlc = get_ohlc_data(sym, Timeframe.H4, 14 + 20)
            closes = closed_closes(ohlc)
            rsi_val = _rsi_latest_from_closes(closes, 14)
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


def _get_local_tz():
    """Return tzinfo for configured DAILY_TZ_NAME; fallback to best-effort.

    Prefers ZoneInfo when available. If DAILY_TZ_NAME is Asia/Kolkata and ZoneInfo is
    unavailable, fall back to fixed +05:30 offset named IST. Otherwise, fallback to UTC.
    """
    tz_name = (DAILY_TZ_NAME or "").strip() or "Asia/Kolkata"
    try:
        if ZoneInfo is not None:
            return ZoneInfo(tz_name)
    except Exception:
        pass
    if tz_name == "Asia/Kolkata":
        try:
            return timezone(timedelta(hours=5, minutes=30), name="IST")
        except Exception:
            return timezone.utc
    return timezone.utc


def _local_now() -> datetime:
    return datetime.now(_get_local_tz())


def _format_date_local(d: Optional[datetime] = None) -> str:
    dt = d or _local_now()
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return dt.date().isoformat()


def _tz_display_label() -> str:
    tz_name = (DAILY_TZ_NAME or "").strip() or "Asia/Kolkata"
    return "IST" if tz_name == "Asia/Kolkata" else tz_name


def _parse_send_hms() -> Tuple[int, int, int]:
    s = (DAILY_SEND_LOCAL_TIME or "").strip()
    if not s:
        return (9, 0, 0)
    try:
        # Support HH:MM[:SS]
        parts = s.split(":")
        h = int(parts[0]) if len(parts) > 0 else 9
        m = int(parts[1]) if len(parts) > 1 else 0
        sec = int(parts[2]) if len(parts) > 2 else 0
        h = max(0, min(23, h))
        m = max(0, min(59, m))
        sec = max(0, min(59, sec))
        return (h, m, sec)
    except Exception:
        return (9, 0, 0)


def _send_time_label() -> str:
    h, m, s = _parse_send_hms()
    label = _tz_display_label()
    if s:
        return f"{label} {h:02d}:{m:02d}:{s:02d}"
    return f"{label} {h:02d}:{m:02d}"


def _collect_today_news_compact() -> List[Dict[str, Any]]:
    """Select today's (IST) high impact news from cache with compact fields."""
    items: List[Dict[str, Any]] = []
    try:
        if not news_mod.global_news_cache:
            news_mod.load_news_cache_from_disk()
    except Exception:
        pass
    try:
        today_local = _local_now().date()
        for item in list(news_mod.global_news_cache):
            try:
                # Impact filter - only high impact
                impact = (item.analysis.get("impact") if item.analysis else "").strip().lower()
                if impact != "high":
                    continue
                # Date filter in IST
                time_local = news_mod._format_event_time_local(item.time, tz_name=(DAILY_TZ_NAME or "Asia/Kolkata"))  # e.g., "YYYY-mm-dd HH:MM <LABEL>"
                date_str = (time_local or "").split(" ")[0]
                if not date_str:
                    continue
                try:
                    dt_ist_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                if dt_ist_date != today_local:
                    continue
                title = (item.headline or "").strip()
                bias = news_mod._derive_bias(item.analysis.get("effect") if item.analysis else None)
                items.append({
                    "title": title,
                    "time_local": time_local,
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
    date_local = _format_date_local()
    core_signals = await _collect_core_signals()
    rsi_os, rsi_ob = await _collect_rsi_h4()
    news_today = _collect_today_news_compact()
    payload = {
        "date_local": date_local,
        "time_label": _send_time_label(),
        "tz_name": (DAILY_TZ_NAME or "Asia/Kolkata"),
        "core_signals": core_signals,
        "rsi_oversold": rsi_os,
        "rsi_overbought": rsi_ob,
        "news": news_today,
    }
    return payload


def _next_send_local_utc(now_utc: Optional[datetime] = None) -> datetime:
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = _get_local_tz()
    h, m, s = _parse_send_hms()
    local_now = now_utc.astimezone(tz)
    target_local = local_now.replace(hour=h, minute=m, second=s, microsecond=0)
    if local_now >= target_local:
        target_local = target_local + timedelta(days=1)
    return target_local.astimezone(timezone.utc)


async def _fetch_all_user_emails_from_auth() -> List[str]:
    """Fetch all user emails from Supabase Auth admin API.

    Uses service role key to list users. Paginates until no results.
    """
    try:
        supabase_url = SUPABASE_URL
        supabase_key = SUPABASE_SERVICE_KEY
        if not supabase_url or not supabase_key:
            log_error(logger, "daily_auth_users_fetch_skipped", reason="missing_supabase_credentials")
            return []

        base = supabase_url.rstrip("/")
        url = f"{base}/auth/v1/admin/users"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(connect=3, sock_read=10, total=20)
        emails: List[str] = []
        page = 1
        per_page = 1000

        log_info(logger, "daily_auth_fetch_start", page=page, per_page=per_page)

        def _extract_emails_from_user(u: dict) -> List[str]:
            results: List[str] = []
            try:
                primary = (u.get("email") or "").strip()
                if primary:
                    results.append(primary)
            except Exception:
                pass
            try:
                meta = u.get("user_metadata") or {}
                if isinstance(meta, dict):
                    for k in ("email", "email_address", "preferred_email"):
                        v = (meta.get(k) or "").strip()
                        if v:
                            results.append(v)
            except Exception:
                pass
            try:
                identities = u.get("identities") or []
                if isinstance(identities, list):
                    for ident in identities:
                        try:
                            v = (ident.get("email") or "").strip()
                            if v:
                                results.append(v)
                            id_data = ident.get("identity_data") or {}
                            if isinstance(id_data, dict):
                                v2 = (id_data.get("email") or id_data.get("preferred_username") or "").strip()
                                if v2 and "@" in v2:
                                    results.append(v2)
                        except Exception:
                            continue
            except Exception:
                pass
            return [e for e in results if isinstance(e, str) and "@" in e]

        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                params = {"page": page, "per_page": per_page, "aud": "authenticated"}
                try:
                    async with session.get(url, headers=headers, params=params) as resp:
                        if resp.status != 200:
                            txt = await resp.text()
                            log_error(
                                logger,
                                "daily_auth_users_fetch_failed",
                                status=resp.status,
                                page=page,
                                body=(txt[:200] if isinstance(txt, str) else ""),
                            )
                            break
                        data = await resp.json()
                        users = data if isinstance(data, list) else data.get("users", []) if isinstance(data, dict) else []
                        log_info(logger, "daily_auth_fetch_page", page=page, users=len(users))
                        if not users:
                            break
                        page_emails: List[str] = []
                        for u in users:
                            try:
                                page_emails.extend(_extract_emails_from_user(u))
                            except Exception:
                                continue
                        page_emails = sorted({e for e in page_emails})
                        if page_emails:
                            try:
                                log_debug(
                                    logger,
                                    "daily_auth_fetch_page_emails",
                                    page=page,
                                    count=len(page_emails),
                                    emails_csv=",".join(page_emails),
                                )
                            except Exception:
                                pass
                            emails.extend(page_emails)
                        if len(users) < per_page:
                            break
                        page += 1
                except Exception as e:
                    log_error(logger, "daily_auth_users_fetch_error", page=page, error=str(e))
                    break
        final_emails = sorted({e for e in emails if isinstance(e, str) and e})
        try:
            log_info(
                logger,
                "daily_auth_fetch_done",
                users_total=len(final_emails),
                emails_csv=",".join(final_emails),
            )
        except Exception:
            pass
        return final_emails
    except Exception as e:
        log_error(logger, "daily_auth_users_fetch_unexpected", error=str(e))
        return []


async def _send_daily_to_all_users(payload: Dict[str, Any]) -> None:
    try:
        # For daily mails, use Supabase Auth users as the source of truth
        emails = await _fetch_all_user_emails_from_auth()
    except Exception as e:
        log_error(logger, "daily_fetch_users_error", error=str(e))
        emails = []
    if not emails:
        log_error(logger, "daily_no_users")
        return
    try:
        emails_csv = ",".join([e for e in emails if isinstance(e, str)])
    except Exception:
        emails_csv = ""
    try:
        log_info(logger, "daily_auth_emails", users=len(emails), emails_csv=emails_csv)
    except Exception:
        pass
    log_info(logger, "daily_send_batch", users=len(emails), emails_csv=emails_csv)
    tasks = []
    for em in emails:
        tasks.append(email_service.send_daily_brief(user_email=em, payload=payload))
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        log_error(logger, "daily_send_batch_error", error=str(e))


async def daily_mail_scheduler() -> None:
    """Scheduler that sends a Daily Morning Brief at configured local time daily.
    
    Prevents duplicate sends by tracking the last sent date per server instance.
    Each tenant (FXLabs/HexTech) runs in its own process with independent tracking,
    allowing both tenants to send their own daily emails without interference.
    """
    last_sent_date: Optional[str] = None  # Track last sent date in YYYY-MM-DD format (per instance)
    
    # Get tenant info for logging clarity
    from .tenancy import get_tenant_config
    try:
        tenant_name = get_tenant_config().name
    except Exception:
        tenant_name = "Unknown"
    
    while True:
        try:
            now = datetime.now(timezone.utc)
            next_run = _next_send_local_utc(now)
            sleep_s = max(1.0, (next_run - now).total_seconds())
            log_info(logger, "daily_sleep_until", tenant=tenant_name, next_run_utc=next_run.isoformat(), seconds=int(sleep_s))
            await asyncio.sleep(sleep_s)

            # Check current local date to prevent duplicate sends on the same day
            current_local_date = _format_date_local()
            if last_sent_date == current_local_date:
                log_info(logger, "daily_already_sent_today", tenant=tenant_name, date=current_local_date)
                # Skip sending and wait until next day
                # Sleep for 1 hour to avoid tight loop, will recalculate next run time
                await asyncio.sleep(3600)
                continue

            # Build and send
            log_info(logger, "daily_build_start", tenant=tenant_name, date=current_local_date)
            payload = await _build_daily_payload()
            log_info(
                logger,
                "daily_build_done",
                tenant=tenant_name,
                core=len(payload.get("core_signals", [])),
                os=len(payload.get("rsi_oversold", [])),
                ob=len(payload.get("rsi_overbought", [])),
                news=len(payload.get("news", [])),
            )
            await _send_daily_to_all_users(payload)
            
            # Mark as sent for today (per instance)
            last_sent_date = current_local_date
            log_info(logger, "daily_completed", tenant=tenant_name, date=current_local_date)
            
            # Add cooldown period after sending (4 hours) to prevent rapid re-triggering
            # This ensures we don't accidentally send twice even if there's a timing issue
            await asyncio.sleep(14400)  # 4 hours
            
        except asyncio.CancelledError:
            return
        except Exception as e:
            log_error(logger, "daily_scheduler_error", tenant=tenant_name, error=str(e))
            await asyncio.sleep(60)

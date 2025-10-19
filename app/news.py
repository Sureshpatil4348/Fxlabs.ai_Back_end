import asyncio
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

import aiohttp
import logging
import builtins

from .config import (
    JBLANKED_API_KEY,
    JBLANKED_API_URL,
    ASOASIS_API_FOREX_NEWS_ENDPOINT,
    ASOASIS_API_FOREX_NEWS_CLIENT_ID,
    ASOASIS_API_FOREX_NEWS_CLIENT_SECRET,
    NEWS_CACHE_MAX_ITEMS,
    NEWS_UPDATE_INTERVAL_HOURS,
    PERPLEXITY_API_KEY,
    NEWS_CACHE_FILE,
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
)
from .models import NewsAnalysis, NewsItem
from .email_service import email_service
from .alert_logging import log_debug, log_info, log_error
from .config import NEWS_VERBOSE_LOGS


def _get_field(item: dict, keys: List[str]):
    """Return the first present value among keys, allowing numeric 0/0.0 but skipping None/empty strings."""
    for key in keys:
        if key in item:
            val = item.get(key)
            if val is None:
                continue
            if isinstance(val, str) and val.strip() == "":
                continue
            return val
    return ""


def _to_utc_iso8601(time_value) -> Optional[str]:
    """Convert various upstream time formats (UTC+3 by default) to UTC ISO 8601 (Z).

    Behavior:
    - If aware (has tzinfo), convert to UTC.
    - If naive (no tz), assume UTC+3 and convert to UTC.
    - If numeric, treat as Unix epoch (seconds or ms) in UTC.
    Returns ISO string with 'Z' suffix, or None if parsing fails/empty.
    """
    try:
        if time_value is None:
            return None
        # Handle numeric timestamps (epoch seconds or milliseconds)
        if isinstance(time_value, (int, float)):
            ts = float(time_value)
            # Heuristic: > 10^12 implies milliseconds
            if ts > 1_000_000_000_000:
                dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
            else:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

        if isinstance(time_value, str):
            t = time_value.strip()
            if not t:
                return None

            # Try ISO 8601 first
            iso_candidate = t.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso_candidate)
                if dt.tzinfo is None:
                    # Assume UTC+3 for naive times
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=3)))
                dt_utc = dt.astimezone(timezone.utc)
                return dt_utc.isoformat().replace("+00:00", "Z")
            except Exception:
                pass

            # Common fallback patterns (assume UTC+3 if naive)
            patterns = [
                "%Y.%m.%d %H:%M:%S",  # e.g., 2025.09.17 21:00:00 (Jblanked weekly)
                "%Y.%m.%d %H:%M",    # e.g., 2025.09.17 21:00
                "%Y.%m.%d",          # e.g., 2025.09.17
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%Y-%m-%d",
            ]
            for fmt in patterns:
                try:
                    dt = datetime.strptime(t, fmt)
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=3)))
                    dt_utc = dt.astimezone(timezone.utc)
                    return dt_utc.isoformat().replace("+00:00", "Z")
                except Exception:
                    continue

            # Last resort: return None if not parseable
            return None

        return None
    except Exception:
        return None


global_news_cache: List[NewsAnalysis] = []
news_cache_metadata: Dict[str, any] = {
    "last_updated": None,
    "next_update_time": None,
    "is_updating": False,
}

# Local logger for this module
logger = logging.getLogger(__name__)


def _vprint(*args, **kwargs) -> None:
    if NEWS_VERBOSE_LOGS:
        try:
            builtins.print(*args, **kwargs)
        except Exception:
            pass

# Gate all module-level prints behind NEWS_VERBOSE_LOGS
print = _vprint  # type: ignore


def _ensure_parent_dir(file_path: str) -> None:
    try:
        parent = os.path.dirname(file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except Exception:
        pass


def _serialize_datetime(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        # Ensure UTC ISO with Z
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _parse_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def load_news_cache_from_disk() -> None:
    """Load news cache and metadata from filesystem if available.

    JSON shape:
    {
      "metadata": {"last_updated": iso|None, "next_update_time": iso|None},
      "data": [NewsAnalysis-like dict]
    }
    """
    global global_news_cache, news_cache_metadata
    try:
        if not NEWS_CACHE_FILE:
            return
        if not os.path.exists(NEWS_CACHE_FILE):
            return
        with open(NEWS_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        items = payload.get("data", []) if isinstance(payload, dict) else []
        loaded: List[NewsAnalysis] = []
        for obj in items:
            try:
                # Ensure analyzed_at is parsed
                analyzed_at = obj.get("analyzed_at")
                if isinstance(analyzed_at, str):
                    obj["analyzed_at"] = _parse_datetime(analyzed_at) or datetime.now(timezone.utc)
                loaded.append(NewsAnalysis(**obj))
            except Exception:
                continue

        # Sort and trim
        loaded.sort(key=lambda x: _iso_to_dt(x.time), reverse=True)
        if len(loaded) > NEWS_CACHE_MAX_ITEMS:
            loaded = loaded[:NEWS_CACHE_MAX_ITEMS]

        global_news_cache = loaded

        meta = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        lu = _parse_datetime(meta.get("last_updated")) if isinstance(meta, dict) else None
        nu = _parse_datetime(meta.get("next_update_time")) if isinstance(meta, dict) else None
        news_cache_metadata["last_updated"] = lu
        news_cache_metadata["next_update_time"] = nu
        news_cache_metadata["is_updating"] = False
        print(f"🗂️ [cache] Loaded {len(global_news_cache)} news items from disk: {NEWS_CACHE_FILE}")
    except Exception as e:
        print(f"❌ [cache] Failed to load news cache: {e}")


def _save_news_cache_to_disk() -> None:
    """Persist current news cache and metadata to filesystem (atomic write)."""
    try:
        if not NEWS_CACHE_FILE:
            return
        _ensure_parent_dir(NEWS_CACHE_FILE)
        tmp_path = f"{NEWS_CACHE_FILE}.tmp"

        data_list: List[dict] = []
        for item in global_news_cache:
            try:
                obj = item.model_dump()
                # Convert analyzed_at datetime to ISO string
                aa = obj.get("analyzed_at")
                if isinstance(aa, datetime):
                    obj["analyzed_at"] = _serialize_datetime(aa)
                data_list.append(obj)
            except Exception:
                continue

        payload = {
            "metadata": {
                "last_updated": _serialize_datetime(news_cache_metadata.get("last_updated")),
                "next_update_time": _serialize_datetime(news_cache_metadata.get("next_update_time")),
            },
            "data": data_list,
        }

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, NEWS_CACHE_FILE)
        print(f"💾 [cache] Saved {len(global_news_cache)} items to disk: {NEWS_CACHE_FILE}")
    except Exception as e:
        print(f"❌ [cache] Failed to save news cache: {e}")


def _split_headline(headline: Optional[str]) -> tuple:
    """Return (base, outcome, quality) from an augmented headline.

    Format: base (+ optional " (Outcome)") (+ optional " - Quality").
    """
    if not headline:
        return "", None, None
    base = headline.strip()
    quality = None
    outcome = None
    if " - " in base:
        base, quality = base.rsplit(" - ", 1)
        base = base.strip()
        quality = quality.strip() if quality is not None else None
    if base.endswith(")") and "(" in base:
        open_idx = base.rfind("(")
        close_idx = base.rfind(")")
        if open_idx != -1 and close_idx == len(base) - 1 and open_idx < close_idx:
            outcome = base[open_idx + 1:close_idx].strip()
            base = base[:open_idx].rstrip()
    return base, outcome, quality


def _make_dedup_key_from_item(item: NewsItem) -> Optional[tuple]:
    # Prefer upstream-provided stable uuid when available
    if getattr(item, "uuid", None):
        uid = str(item.uuid).strip()
        if uid:
            return ("uuid", uid)
    currency = (item.currency or "").strip()
    time_iso = (item.time or "").strip()
    base, _, _ = _split_headline(item.headline)
    if not currency or not time_iso or not base:
        return None
    return (currency, time_iso, base)


def _make_dedup_key_from_analysis(analysis: NewsAnalysis) -> Optional[tuple]:
    # Prefer upstream-provided stable uuid when available
    if getattr(analysis, "uuid", None):
        uid = str(analysis.uuid).strip()
        if uid:
            return ("uuid", uid)
    currency = (analysis.currency or "").strip()
    time_iso = (analysis.time or "").strip()
    base, _, _ = _split_headline(analysis.headline)
    if not currency or not time_iso or not base:
        return None
    return (currency, time_iso, base)


def _iso_to_dt(time_iso: Optional[str]) -> datetime:
    if not time_iso:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(time_iso.replace("Z", "+00:00"))
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


async def fetch_asoasis_news() -> List[NewsItem]:
    """Fetch today's Forex calendar from ASOasis API and normalize to NewsItem.

    - Endpoint: ASOASIS_API_FOREX_NEWS_ENDPOINT (defaults to Asia/Kolkata timezone)
    - Headers: client-id, client-secret (from env)
    - Filters: Only return items with impact == "high" (case-insensitive) for further processing.
    - Dedup: Consumers should prefer `uuid` for dedup.
    """
    try:
        print("📰 [fetch] Starting ASOasis fetch...")
        print(f"📰 [fetch] URL: {ASOASIS_API_FOREX_NEWS_ENDPOINT}")
        headers = {
            "client-id": ASOASIS_API_FOREX_NEWS_CLIENT_ID,
            "client-secret": ASOASIS_API_FOREX_NEWS_CLIENT_SECRET,
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(ASOASIS_API_FOREX_NEWS_ENDPOINT, headers=headers) as response:
                print(f"📰 [fetch] HTTP status: {response.status}")
                if response.status != 200:
                    print(f"❌ ASOasis API error: {response.status}")
                    try:
                        text = await response.text()
                        print(f"   Response: {text[:500]}")
                    except Exception:
                        pass
                    return []
                data = await response.json()
                # Expected shape: { count: N, items: [...], timezone: 'Asia/Kolkata' }
                items = []
                if isinstance(data, dict):
                    items = data.get("items") or []
                elif isinstance(data, list):
                    items = data
                print(f"📰 [parse] items count={len(items)}")

                news_items: List[NewsItem] = []
                for raw in items:
                    if not isinstance(raw, dict):
                        continue
                    # Map fields
                    uid = str(raw.get("id") or "").strip() or None
                    name = raw.get("name") or raw.get("title") or raw.get("headline") or ""
                    headline = str(name).strip()
                    forecast = raw.get("forecast")
                    previous = raw.get("previous")
                    actual = raw.get("actual")
                    revision = raw.get("revision")
                    currency = raw.get("currency") or raw.get("ccy") or raw.get("country")
                    impact = raw.get("impact")
                    time_value = raw.get("time") or raw.get("timestamp") or raw.get("date")

                    # Normalize strings
                    if isinstance(forecast, (int, float)):
                        forecast = str(forecast)
                    if isinstance(previous, (int, float)):
                        previous = str(previous)
                    if isinstance(actual, (int, float)):
                        actual = str(actual)
                    if isinstance(revision, (int, float)):
                        revision = str(revision)

                    # Convert blanks to None
                    def _nz(v: Optional[str]) -> Optional[str]:
                        try:
                            if v is None:
                                return None
                            s = str(v).strip()
                            return s if s else None
                        except Exception:
                            return None

                    forecast = _nz(forecast)
                    previous = _nz(previous)
                    actual = _nz(actual)
                    revision = _nz(revision)
                    currency = _nz(currency)
                    impact = _nz(impact)

                    # Normalize time (epoch ms/seconds or ISO) -> UTC ISO Z
                    time_iso = _to_utc_iso8601(time_value)

                    # Filter: only high impact for downstream processing
                    imp = (impact or "").strip().lower()
                    if imp != "high":
                        continue

                    print(
                        f"📰 [item] uuid='{(uid or '')[:8]}' headline='{headline[:60]}' time_raw='{str(time_value)[:32]}' -> time_utc='{(time_iso or 'None')[:32]}' impact='{imp}'"
                    )

                    news_items.append(
                        NewsItem(
                            uuid=uid,
                            headline=headline or None,
                            forecast=forecast,
                            previous=previous,
                            actual=actual,
                            revision=revision,
                            currency=currency,
                            impact=impact,
                            time=time_iso,
                        )
                    )

                print(f"📰 [fetch] Parsed high-impact items: {len(news_items)}")
                return news_items
    except Exception as e:
        print(f"❌ Error fetching ASOasis API news: {e}")
        import traceback
        traceback.print_exc()
        return []


async def analyze_news_with_perplexity(news_item: NewsItem) -> Optional[NewsAnalysis]:
    print(f"🔎 [analyze] Start analysis for: '{(news_item.headline or '')[:60]}'")
    # Ask Perplexity to respond with a strict JSON payload to avoid ambiguous wording
    prompt = (
        "You are a Forex macro event classifier used BEFORE an economic release. Output exactly:\n"
        "{\n"
        "  \"effect\": \"bullish|bearish|neutral\",\n"
        "  \"explanation\": \"<max 2 sentences>\"\n"
        "}\n"
        "Constraints:\n"
        "- Lowercase enums only.\n"
        "- No extra fields or text.\n"
        "- Do NOT include any field named 'impact' in your response.\n\n"
        "INPUT\n"
        f"Currency: {news_item.currency}\n"
        f"News: {news_item.headline}\n"
        f"Time: {news_item.time or 'N/A'}\n"
        f"Forecast: {news_item.forecast or 'N/A'}\n"
        f"Previous: {news_item.previous or 'N/A'}\n"
        f"Source impact hint: {news_item.impact or 'N/A'}\n\n"
        "A) CONTEXT (impact is provided by API; do not output it)\n"
        "1) Consider standard taxonomy only to reason about magnitude in the explanation; DO NOT output an 'impact' field.\n"
        "2) Taxonomy by EVENT FAMILY (based on what historically moves FX):\n"
        "   TIER-1 (default \"high\"):\n"
        "   - CPI (headline/core), PCE (US), central-bank rate decisions/statements/pressers/minutes, major labor (NFP/Employment Change, Unemployment Rate, Average/Hourly Earnings), GDP “advance/flash”, ISM PMIs (US), Flash PMIs (EZ/UK), Retail Sales (US/UK/CA headline; US control group). \n"
        "   TIER-2 (default \"medium\"):\n"
        "   - PPI, GDP “second/final”, Retail Sales ex-autos (non-US), durable goods (ex-transport), trade balance, housing starts/building permits, consumer/business confidence, final PMIs.\n"
        "   TIER-3 (default \"low\"):\n"
        "   - Regional/small surveys, auctions, secondary indices with limited FX pass-through.\n"
        "3) Currency-bloc adjustment:\n"
        "   - For G10 (USD, EUR, JPY, GBP, AUD, NZD, CAD, CHF, SEK, NOK): keep tiers as above.\n"
        "   - For non-G10/minor economies: downgrade one tier unless the pair is commonly traded against USD/EUR and the event is TIER-1.\n"
        "4) Do NOT upgrade events due to hype or proximity in time. Color codes (e.g., red/orange/yellow) mean high/medium/low respectively; they are a hint, not an override.\n\n"
        "(Background: Central-bank policy and interest rates are primary FX drivers; inflation (CPI/PCE), labor, GDP, ISM/PMIs, and retail sales are consistently among the most market-moving indicators. See standard FX education sources.) \n\n"
        "B) EFFECT (directional bias for the LISTED currency, pre-release)\n"
        "5) You are PRE-RELEASE. Do NOT guess the actual number. Instead, infer *bias* from consensus vs. prior, policy reaction function, and whether the economy is above/below inflation target or growth trend:\n"
        "   - Inflation-sensitive: If forecast > previous (or above target) and the central bank is vigilant/tightening-biased → effect=\"bullish\". If forecast < previous (disinflation) and easing/neutral bias → effect=\"bearish\".\n"
        "   - Labor: Stronger jobs/wages vs forecast trend → \"bullish\"; weaker → \"bearish\".\n"
        "   - Growth (GDP/PMIs/Retail Sales): Stronger vs recent trend/consensus → \"bullish\"; weaker → \"bearish\".\n"
        "   - Central bank meetings: Expected hawkish shift (tightening/upgrade to inflation/growth/rates path) → \"bullish\"; dovish shift → \"bearish\".\n"
        "6) If the consensus and recent trend are genuinely mixed/flat (no clear skew), set effect=\"neutral\".\n"
        "7) Direction is for the listed currency (not the pair). Do NOT flip signs for quote/base logic.\n\n"
        "C) DATA HYGIENE (pre-release)\n"
        "8) You may look up consensus/central-bank stance from reliable calendars or official sources. Do NOT treat previews as actuals.\n"
        "9) EXPLANATION ≤2 sentences: (i) briefly justify magnitude (taxonomy/source hint) without outputting an 'impact' field, (ii) explain directional bias (forecast/trend/policy stance). No filler words.\n"
    )
    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "fx-news-analyzer/1.0",
    }
    payload = {"model": "sonar", "messages": [{"role": "user", "content": prompt}], "max_tokens": 500, "temperature": 0.1}
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    backoff = [0.5, 1.5, 3.0]
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for attempt, delay in enumerate([0] + backoff):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with session.post(url, headers=headers, json=payload) as resp:
                    text = await resp.text()
                    print(f"🔎 [analyze] Attempt {attempt} status={resp.status} body_len={len(text)}")
                    if resp.status == 200:
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError as e:
                            print(f"❌ [analyze] JSON decode error: {e}")
                            print(f"📄 [analyze] Response body: {text[:500]}...")
                            print(f"📊 [analyze] Response status: {resp.status}")
                            continue  # Try next attempt
                        
                        # Validate response structure
                        choices = data.get("choices")
                        if not choices or not isinstance(choices, list) or len(choices) == 0:
                            print(f"❌ [analyze] Invalid choices structure: {choices}")
                            print(f"📄 [analyze] Response body: {text[:500]}...")
                            print(f"📊 [analyze] Response status: {resp.status}")
                            continue  # Try next attempt
                        
                        first_choice = choices[0]
                        if not isinstance(first_choice, dict):
                            print(f"❌ [analyze] Invalid choice structure: {first_choice}")
                            print(f"📄 [analyze] Response body: {text[:500]}...")
                            print(f"📊 [analyze] Response status: {resp.status}")
                            continue  # Try next attempt
                        
                        message = first_choice.get("message")
                        if not message or not isinstance(message, dict):
                            print(f"❌ [analyze] Invalid message structure: {message}")
                            print(f"📄 [analyze] Response body: {text[:500]}...")
                            print(f"📊 [analyze] Response status: {resp.status}")
                            continue  # Try next attempt
                        
                        analysis_text = message.get("content")
                        if not analysis_text or not isinstance(analysis_text, str):
                            print(f"❌ [analyze] Invalid content: {analysis_text}")
                            print(f"📄 [analyze] Response body: {text[:500]}...")
                            print(f"📊 [analyze] Response status: {resp.status}")
                            continue  # Try next attempt

                        def _normalize_effect(token: Optional[str]) -> str:
                            if not token:
                                return "neutral"
                            t = token.strip().lower()
                            if t in ("bullish", "bearish", "neutral"):
                                return t
                            # simple synonyms mapping
                            if t in ("positive", "hawkish"):
                                return "bullish"
                            if t in ("negative", "dovish"):
                                return "bearish"
                            return "neutral"

                        def _normalize_impact(token: Optional[str]) -> Optional[str]:
                            if not token:
                                return None
                            t = token.strip().lower()
                            if t in ("high", "medium", "low"):
                                return t
                            # map common synonyms
                            high_words = {
                                "significant", "strong", "major", "elevated", "substantial", "pronounced",
                                "considerable", "notable", "sizeable", "severe", "marked", "robust",
                                "heightened", "spike", "surge", "high-impact", "very high", "extreme"
                            }
                            medium_words = {
                                "medium", "moderate", "modest", "balanced", "average", "mixed", "temperate",
                                "somewhat", "moderately"
                            }
                            low_words = {
                                "low", "minor", "limited", "negligible", "minimal", "slight", "muted",
                                "weak", "dampened", "low-impact"
                            }
                            if t in high_words:
                                return "high"
                            if t in medium_words:
                                return "medium"
                            if t in low_words:
                                return "low"
                            return None

                        def _extract_json_block(s: str) -> Optional[dict]:
                            # Strip code fences if present
                            content = s.strip()
                            if content.startswith("```"):
                                # take inner fenced block
                                parts = content.split("```")
                                for part in parts:
                                    part = part.strip()
                                    if part.startswith("{") and part.endswith("}"):
                                        try:
                                            return json.loads(part)
                                        except Exception:
                                            pass
                            # Try direct JSON
                            try:
                                return json.loads(content)
                            except Exception:
                                pass
                            # Try to locate a JSON object substring
                            start_idx = content.find("{")
                            end_idx = content.rfind("}")
                            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                                snippet = content[start_idx:end_idx + 1]
                                # Heuristic: ensure it likely has keys we care about
                                if '"effect"' in snippet or '"explanation"' in snippet:
                                    try:
                                        return json.loads(snippet)
                                    except Exception:
                                        pass
                            return None

                        effect = "neutral"
                        impact_value: Optional[str] = None
                        explanation = None

                        parsed = _extract_json_block(analysis_text)
                        got_effect_from_json = False
                        if parsed and isinstance(parsed, dict):
                            eff_raw = parsed.get("effect")
                            if eff_raw is not None:
                                effect = _normalize_effect(eff_raw)
                                got_effect_from_json = True
                            explanation = parsed.get("explanation")

                        # Fallback: only when field missing from JSON or JSON absent
                        lt = analysis_text.lower()
                        if not got_effect_from_json:
                            m_eff = re.search(r"effect\s*[:\-]\s*\"?([a-z]+)\"?", lt)
                            if m_eff:
                                effect = _normalize_effect(m_eff.group(1)) or effect
                            else:
                                if "bullish" in lt:
                                    effect = "bullish"
                                elif "bearish" in lt:
                                    effect = "bearish"

                        # Extract explanation from raw text when JSON parse fails
                        if (not parsed or not isinstance(explanation, str) or not explanation.strip()):
                            try:
                                # Try to pull explanation": "..." from any JSON-like block in the text
                                m_expl = re.search(r'"explanation"\s*:\s*"(.*?)"', analysis_text, flags=re.IGNORECASE | re.DOTALL)
                                if m_expl:
                                    explanation = m_expl.group(1).strip()
                            except Exception:
                                pass

                        # Final fallback: use source impact hint if provided
                        if (impact_value is None or impact_value not in ("high", "medium", "low")) and (news_item.impact or "").strip():
                            im = (news_item.impact or "").strip().lower()
                            if im in ("high", "medium", "low"):
                                impact_value = im

                        if not impact_value:
                            impact_value = "medium"

                        # Choose human-readable explanation text for full_analysis (never embed raw JSON)
                        if parsed and isinstance(explanation, str) and explanation.strip():
                            full_analysis_text = explanation.strip()
                        elif parsed:
                            # Structured JSON but missing explanation -> synthesize a short line
                            full_analysis_text = f"Effect: {effect}."
                        else:
                            # Free text response, prefer extracted explanation; fallback to brief synthesis
                            if isinstance(explanation, str) and explanation.strip():
                                full_analysis_text = explanation.strip()
                            else:
                                full_analysis_text = f"Effect: {effect}."

                        # Always mirror upstream API impact (ignore AI impact for this field)
                        api_impact = (news_item.impact or "").strip().lower()
                        if api_impact not in ("high", "medium", "low"):
                            api_impact = "medium"

                        # Ensure full_analysis reflects the API impact value we expose downstream
                        # no-op: full_analysis_text already sanitized above. Avoid embedding JSON.

                        print(f"🔎 [analyze] Effect derived: {effect} | Impact (api): {api_impact}")
                        analysis = {
                            "effect": effect,
                            "impact": api_impact,
                            "full_analysis": full_analysis_text,
                        }
                        return NewsAnalysis(
                            uuid=news_item.uuid,
                            headline=news_item.headline,
                            forecast=news_item.forecast,
                            previous=news_item.previous,
                            actual=news_item.actual,
                            revision=news_item.revision,
                            currency=news_item.currency,
                            time=news_item.time,
                            analysis=analysis,
                            analyzed_at=datetime.now(timezone.utc),
                        )
                    elif resp.status in (429, 500, 502, 503, 504) and attempt < len(backoff):
                        print(f"🔁 [analyze] Transient error {resp.status}, will retry...")
                        continue
                    else:
                        raise RuntimeError(f"Perplexity API {resp.status}: {text}")
            except asyncio.TimeoutError:
                print("⏰ [analyze] Timeout; considering retry if available...")
                if attempt >= len(backoff):
                    raise
                continue
        
        # If all attempts failed, return a safe default analysis
        print("❌ [analyze] All attempts failed, returning safe default analysis")
        # Safe default: preserve upstream API impact when available
        api_impact = (news_item.impact or "").strip().lower()
        if api_impact not in ("high", "medium", "low"):
            api_impact = "medium"
        return NewsAnalysis(
            uuid=news_item.uuid,
            headline=news_item.headline,
            forecast=news_item.forecast,
            previous=news_item.previous,
            actual=news_item.actual,
            revision=news_item.revision,
            currency=news_item.currency,
            time=news_item.time,
            analysis={
                "effect": "neutral",
                "impact": api_impact,
                "full_analysis": "Analysis unavailable due to API response format issues.",
            },
            analyzed_at=datetime.now(timezone.utc),
        )


async def update_news_cache():
    global global_news_cache, news_cache_metadata
    if news_cache_metadata["is_updating"]:
        print("🗞️ [update] News update already in progress, skipping...")
        return
    try:
        print("🗞️ [update] Starting news cache update...")
        news_cache_metadata["is_updating"] = True
        news_items = await fetch_asoasis_news()
        print(f"🗞️ [update] Fetched items: {len(news_items)}")
        if not news_items:
            print("⚠️ [update] No news items fetched, keeping existing cache")
            return
        # Build map of existing items for dedup
        existing_map: Dict[tuple, int] = {}
        for i, existing in enumerate(global_news_cache):
            k = _make_dedup_key_from_analysis(existing)
            if k is not None:
                existing_map[k] = i

        updated_cache = list(global_news_cache)

        for idx, news_item in enumerate(news_items, start=1):
            key = _make_dedup_key_from_item(news_item)
            if key is None:
                print(f"⚠️ [update] Skipping item {idx}: insufficient data for dedup (currency/time/base) -> '{(news_item.headline or '')[:60]}'")
                continue

            if key in existing_map:
                cached = updated_cache[existing_map[key]]
                cached_base, cached_outcome, cached_quality = _split_headline(cached.headline)
                item_base, item_outcome, item_quality = _split_headline(news_item.headline)
                changed = False
                if (cached.actual or None) != (news_item.actual or None):
                    changed = True
                if (cached_outcome or None) != (item_outcome or None):
                    changed = True
                if (cached_quality or None) != (item_quality or None):
                    changed = True
                if changed:
                    try:
                        print(f"♻️ [update] Refreshing analysis for existing item: '{item_base[:60]}' @ {news_item.time}")
                        analysis = await asyncio.wait_for(analyze_news_with_perplexity(news_item), timeout=60.0)
                        if analysis:
                            updated_cache[existing_map[key]] = analysis
                            print("✅ [update] Refreshed analysis")
                        else:
                            updated_cache[existing_map[key]] = NewsAnalysis(
                                uuid=news_item.uuid,
                                headline=news_item.headline,
                                forecast=news_item.forecast,
                                previous=news_item.previous,
                                actual=news_item.actual,
                                revision=news_item.revision,
                                currency=news_item.currency,
                                time=news_item.time,
                                analysis={
                                    "effect": "unknown",
                                    "impact": ((news_item.impact or "").strip().lower() if (news_item.impact or "").strip().lower() in ("high", "medium", "low") else "medium"),
                                    "full_analysis": "AI analysis failed - raw data only",
                                },
                                analyzed_at=datetime.now(timezone.utc),
                            )
                            print("⚠️ [update] Analysis returned None, stored raw entry")
                    except asyncio.TimeoutError:
                        print(f"⏰ [update] Timeout refreshing analysis: {news_item.headline[:50]}...")
                    except Exception as e:
                        print(f"❌ [update] Error refreshing analysis: {e}")
                else:
                    print(f"➡️ [update] Duplicate unchanged, keeping cached analysis: '{item_base[:60]}' @ {news_item.time}")
            else:
                try:
                    print(f"🆕 [update] Analyzing NEW item {idx}/{len(news_items)}")
                    analysis = await asyncio.wait_for(analyze_news_with_perplexity(news_item), timeout=60.0)
                    if analysis:
                        updated_cache.append(analysis)
                        existing_map[key] = len(updated_cache) - 1
                        print("✅ [update] Added analyzed item")
                    else:
                        updated_cache.append(NewsAnalysis(
                            uuid=news_item.uuid,
                            headline=news_item.headline,
                            forecast=news_item.forecast,
                            previous=news_item.previous,
                            actual=news_item.actual,
                            revision=news_item.revision,
                            currency=news_item.currency,
                            time=news_item.time,
                            analysis={
                                "effect": "unknown",
                                "impact": ((news_item.impact or "").strip().lower() if (news_item.impact or "").strip().lower() in ("high", "medium", "low") else "medium"),
                                "full_analysis": "AI analysis failed - raw data only",
                            },
                            analyzed_at=datetime.now(timezone.utc),
                        ))
                        existing_map[key] = len(updated_cache) - 1
                        print("⚠️ [update] Analysis None, stored raw entry")
                    await asyncio.sleep(0.1)
                except asyncio.TimeoutError:
                    print(f"⏰ [update] Timeout analyzing new item: {news_item.headline[:50]}...")
                    updated_cache.append(NewsAnalysis(
                        uuid=news_item.uuid,
                        headline=news_item.headline,
                        forecast=news_item.forecast,
                        previous=news_item.previous,
                        actual=news_item.actual,
                        revision=news_item.revision,
                        currency=news_item.currency,
                        time=news_item.time,
                        analysis={
                            "effect": "unknown",
                            "impact": ((news_item.impact or "").strip().lower() if (news_item.impact or "").strip().lower() in ("high", "medium", "low") else "medium"),
                            "full_analysis": "AI analysis failed - raw data only",
                        },
                        analyzed_at=datetime.now(timezone.utc),
                    ))
                    existing_map[key] = len(updated_cache) - 1
                    print("⚠️ [update] Stored raw entry on timeout")
                except Exception as e:
                    print(f"❌ [update] Error analyzing new item: {e}")
                    updated_cache.append(NewsAnalysis(
                        uuid=news_item.uuid,
                        headline=news_item.headline,
                        forecast=news_item.forecast,
                        previous=news_item.previous,
                        actual=news_item.actual,
                        revision=news_item.revision,
                        currency=news_item.currency,
                        time=news_item.time,
                        analysis={
                            "effect": "unknown",
                            "impact": ((news_item.impact or "").strip().lower() if (news_item.impact or "").strip().lower() in ("high", "medium", "low") else "medium"),
                            "full_analysis": "AI analysis failed - raw data only",
                        },
                        analyzed_at=datetime.now(timezone.utc),
                    ))
                    existing_map[key] = len(updated_cache) - 1
                    print("⚠️ [update] Stored raw entry on error")

        # Sort cache by time desc and trim
        updated_cache.sort(key=lambda x: _iso_to_dt(x.time), reverse=True)
        if len(updated_cache) > NEWS_CACHE_MAX_ITEMS:
            removed = len(updated_cache) - NEWS_CACHE_MAX_ITEMS
            print(f"🧹 [update] Trimming cache by removing {removed} oldest items")
            updated_cache = updated_cache[:NEWS_CACHE_MAX_ITEMS]

        global_news_cache = updated_cache
        news_cache_metadata["last_updated"] = datetime.now(timezone.utc)
        news_cache_metadata["next_update_time"] = datetime.now(timezone.utc) + timedelta(hours=NEWS_UPDATE_INTERVAL_HOURS)
        # Persist to disk after successful update
        _save_news_cache_to_disk()
        print(f"✅ [update] Cache size now: {len(global_news_cache)} (max {NEWS_CACHE_MAX_ITEMS})")
        print(f"⏰ [update] Next update scheduled for: {news_cache_metadata['next_update_time']}")
    except Exception as e:
        print(f"❌ [update] Error updating news cache: {e}")
        import traceback
        traceback.print_exc()
    finally:
        news_cache_metadata["is_updating"] = False
        print("🗞️ [update] Update flag reset (is_updating=False)")


async def news_scheduler():
    # Load cache from disk on scheduler start
    load_news_cache_from_disk()
    while True:
        try:
            current_time = datetime.now(timezone.utc)
            print(
                f"⏰ [scheduler] tick at {current_time.isoformat()} | next_update={news_cache_metadata['next_update_time']} | is_updating={news_cache_metadata['is_updating']}"
            )
            if news_cache_metadata["next_update_time"] is None or current_time >= news_cache_metadata["next_update_time"]:
                print("⏰ [scheduler] Triggering update_news_cache()")
                await update_news_cache()
            await asyncio.sleep(1800)
        except Exception as e:
            print(f"❌ [scheduler] Error in news scheduler: {e}")
            await asyncio.sleep(1800)


# ----------------------- News Reminder (5-minute) -----------------------

async def _fetch_all_user_emails_from_auth() -> List[str]:
    """Fetch all user emails from Supabase Auth admin API with verbose logs.

    Uses service role key to list users. Paginates until no results.
    """
    try:
        supabase_url = SUPABASE_URL
        supabase_key = SUPABASE_SERVICE_KEY
        if not supabase_url or not supabase_key:
            log_error(logger, "news_auth_users_fetch_skipped", reason="missing_supabase_credentials")
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

        log_info(logger, "news_auth_fetch_start", page=page, per_page=per_page)

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
                                "news_auth_users_fetch_failed",
                                status=resp.status,
                                page=page,
                                body=(txt[:200] if isinstance(txt, str) else ""),
                            )
                            break
                        data = await resp.json()
                        users = data if isinstance(data, list) else data.get("users", []) if isinstance(data, dict) else []
                        log_info(logger, "news_auth_fetch_page", page=page, users=len(users))
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
                                    "news_auth_fetch_page_emails",
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
                    log_error(logger, "news_auth_users_fetch_error", page=page, error=str(e))
                    break
        final_emails = sorted({e for e in emails if isinstance(e, str) and e})
        try:
            log_info(
                logger,
                "news_auth_fetch_done",
                users_total=len(final_emails),
                emails_csv=",".join(final_emails),
            )
        except Exception:
            pass
        return final_emails
    except Exception as e:
        log_error(logger, "news_auth_users_fetch_unexpected", error=str(e))
        return []

async def _fetch_all_user_emails() -> List[str]:
    """Fetch user emails from Auth; fallback to alert tables if empty."""
    try:
        auth_emails = await _fetch_all_user_emails_from_auth()
        if auth_emails:
            return auth_emails
        log_info(logger, "news_users_fetch_fallback_alert_tables")
    except Exception as e:
        log_error(logger, "news_auth_users_fetch_wrapper_error", error=str(e))
    try:
        # Use centralized configuration
        supabase_url = SUPABASE_URL
        supabase_key = SUPABASE_SERVICE_KEY
        if not supabase_url or not supabase_key:
            log_error(logger, "news_users_fetch_skipped", reason="missing_supabase_credentials")
            return []

        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(connect=3, sock_read=7, total=10)
        tables = [
            "rsi_tracker_alerts",
            "heatmap_tracker_alerts",
            "heatmap_indicator_tracker_alerts",
        ]
        emails: Set[str] = set()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for tbl in tables:
                try:
                    url = f"{supabase_url}/rest/v1/{tbl}"
                    params = {"select": "user_email", "is_active": "eq.true"}
                    async with session.get(url, headers=headers, params=params) as resp:
                        if resp.status == 200:
                            rows = await resp.json()
                            for r in rows:
                                em = (r.get("user_email") or "").strip()
                                if em:
                                    emails.add(em)
                        else:
                            txt = await resp.text()
                            log_error(
                                logger,
                                "news_users_fetch_table_failed",
                                table=tbl,
                                status=resp.status,
                                body=(txt[:200] if isinstance(txt, str) else ""),
                            )
                except Exception as e:
                    log_error(logger, "news_users_fetch_table_error", table=tbl, error=str(e))
        return sorted(emails)
    except Exception as e:
        log_error(logger, "news_users_fetch_error", error=str(e))
        return []


def _format_event_time_local(time_iso: Optional[str], tz_name: str = "Asia/Kolkata") -> str:
    try:
        if not time_iso:
            return ""
        # robust tz handling with fallback for IST
        try:
            from zoneinfo import ZoneInfo
            local_tz = ZoneInfo(tz_name)
        except Exception:
            if tz_name == "Asia/Kolkata":
                try:
                    local_tz = timezone(timedelta(hours=5, minutes=30))
                except Exception:
                    local_tz = timezone.utc
            else:
                local_tz = timezone.utc
        dt_utc = datetime.fromisoformat(time_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        dt_local = dt_utc.astimezone(local_tz)
        label = "IST" if tz_name == "Asia/Kolkata" else tz_name
        return dt_local.strftime(f"%Y-%m-%d %H:%M {label}")
    except Exception:
        try:
            dt = _iso_to_dt(time_iso)
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return ""


def _derive_bias(effect: Optional[str]) -> str:
    t = (effect or "").strip().lower()
    if t == "bullish":
        return "Bullish"
    if t == "bearish":
        return "Bearish"
    if t:
        return t.title()
    return "-"


async def check_and_send_news_reminders() -> None:
    """Check high‑impact news within next 5 minutes and email all users once per event.

    - Scans in-memory `global_news_cache` for events with UTC time within (now, now+5m].
    - Skips items already marked `reminder_sent`.
    - Fetches all user emails from Supabase alert tables (union) and sends reminder.
    - Marks the news item `reminder_sent=True` and persists cache to disk.
    """
    try:
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(minutes=5)
        due_items: List[NewsAnalysis] = []
        for item in list(global_news_cache):
            try:
                if getattr(item, "reminder_sent", False):
                    continue
                event_dt = _iso_to_dt(item.time)
                if now < event_dt <= window_end:
                    # Only consider high‑impact items
                    try:
                        impact_token = (item.analysis or {}).get("impact")
                        impact_norm = str(impact_token).strip().lower() if impact_token is not None else ""
                    except Exception:
                        impact_norm = ""
                    if impact_norm == "high":
                        due_items.append(item)
            except Exception:
                continue

        if not due_items:
            return

        log_info(logger, "news_reminder_due_items", count=len(due_items))

        # Fetch all target users
        emails = await _fetch_all_user_emails()
        if not emails:
            log_error(logger, "news_reminder_no_users")
            # Still mark as sent to avoid spinning forever
            for it in due_items:
                try:
                    setattr(it, "reminder_sent", True)
                except Exception:
                    pass
            _save_news_cache_to_disk()
            return

        # Send reminders per item to all users
        try:
            emails_csv = ",".join([e for e in emails if isinstance(e, str)])
        except Exception:
            emails_csv = ""
        try:
            log_info(logger, "news_auth_emails", users=len(emails), emails_csv=emails_csv)
        except Exception:
            pass
        log_info(logger, "news_reminder_recipients", users=len(emails), emails_csv=emails_csv)
        for item in due_items:
                title = (item.headline or "News Event").strip()
                event_time_local = _format_event_time_local(item.time)
                impact = (item.analysis.get("impact") if item.analysis else None) or "medium"
                previous = item.previous or "-"
                forecast = item.forecast or "-"
                expected = "-"  # Not available pre-release
                bias = _derive_bias(item.analysis.get("effect") if item.analysis else None)

            # Fire-and-forget per-user to avoid blocking; await join for this batch
            tasks = []
            for em in emails:
                tasks.append(
                    email_service.send_news_reminder(
                        user_email=em,
                        event_title=f"[{(item.currency or '-').strip()}] {title}",
                        event_time_local=event_time_local,
                        currency=(item.currency or "-") if hasattr(item, 'currency') else "-",
                        impact=str(impact).title(),
                        previous=str(previous),
                        forecast=str(forecast),
                        expected=str(expected),
                        bias=str(bias),
                    )
                )
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                log_error(logger, "news_reminder_send_batch_error", error=str(e))

            # Mark this item as reminded regardless of individual send failures
            try:
                setattr(item, "reminder_sent", True)
            except Exception:
                pass

        # Persist to disk after flag updates
        _save_news_cache_to_disk()

        log_info(logger, "news_reminder_completed", items=len(due_items), users=len(emails))
    except Exception as e:
        log_error(logger, "news_reminder_error", error=str(e))


async def news_reminder_scheduler():
    """Run every minute to dispatch 5-minute news reminders."""
    # Ensure cache is loaded at least once (no-op if file missing)
    try:
        if not global_news_cache:
            load_news_cache_from_disk()
    except Exception:
        pass
    while True:
        try:
            await check_and_send_news_reminders()
        except Exception as e:
            log_error(logger, "news_reminder_scheduler_error", error=str(e))
        await asyncio.sleep(60)

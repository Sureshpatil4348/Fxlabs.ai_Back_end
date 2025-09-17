import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import aiohttp

from .config import (
    JBLANKED_API_KEY,
    JBLANKED_API_URL,
    NEWS_CACHE_MAX_ITEMS,
    NEWS_UPDATE_INTERVAL_HOURS,
    PERPLEXITY_API_KEY,
)
from .models import NewsAnalysis, NewsItem


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
    currency = (item.currency or "").strip()
    time_iso = (item.time or "").strip()
    base, _, _ = _split_headline(item.headline)
    if not currency or not time_iso or not base:
        return None
    return (currency, time_iso, base)


def _make_dedup_key_from_analysis(analysis: NewsAnalysis) -> Optional[tuple]:
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


async def fetch_jblanked_news() -> List[NewsItem]:
    try:
        print("📰 [fetch] Starting Jblanked fetch...")
        print(f"📰 [fetch] URL: {JBLANKED_API_URL}")
        headers = {"Authorization": f"Api-Key {JBLANKED_API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.get(JBLANKED_API_URL, headers=headers) as response:
                print(f"📰 [fetch] HTTP status: {response.status}")
                if response.status == 200:
                    data = await response.json()
                    print(f"📰 [fetch] JSON type: {type(data).__name__}")
                    news_items = []
                    if isinstance(data, list):
                        print(f"📰 [parse] Top-level list detected. count={len(data)}")
                        items = data
                    elif isinstance(data, dict) and 'data' in data:
                        try:
                            count = len(data.get('data', []))
                        except Exception:
                            count = 'unknown'
                        print(f"📰 [parse] Dict with 'data' key detected. count={count}")
                        items = data['data']
                    else:
                        items = []
                        if isinstance(data, dict):
                            print(f"📰 [parse] Unknown dict structure. Keys={list(data.keys())[:10]}")
                            for _, value in data.items():
                                if isinstance(value, list):
                                    items = value
                                    break
                        print(f"📰 [parse] First list found count={len(items)}")
                    for item in items:
                        if isinstance(item, dict):
                            headline_before = _get_field(item, ['Name', 'title', 'headline', 'name'])
                            headline = _get_field(item, ['Name', 'title', 'headline', 'name'])
                            forecast = _get_field(item, ['Forecast', 'forecast', 'expected'])
                            previous = _get_field(item, ['Previous', 'previous', 'prev'])
                            actual = _get_field(item, ['Actual', 'actual', 'result'])
                            currency = _get_field(item, ['Currency', 'currency', 'ccy', 'country'])
                            impact = _get_field(item, ['Strength', 'impact', 'importance'])
                            time_value = _get_field(item, ['TimeUTC', 'datetime', 'dateTime', 'timestamp', 'Date', 'date', 'Time', 'time'])
                            outcome = item.get('Outcome', '')
                            quality = item.get('Quality', '')
                            if outcome and headline:
                                headline = f"{headline} ({outcome})"
                            if quality and headline:
                                headline = f"{headline} - {quality}"
                            if isinstance(forecast, (int, float)):
                                forecast = str(forecast)
                            if isinstance(previous, (int, float)):
                                previous = str(previous)
                            if isinstance(actual, (int, float)):
                                actual = str(actual)
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
                            # Normalize time to UTC ISO (assume upstream UTC+3 if naive)
                            if time_value == '':
                                time_iso = None
                            else:
                                time_iso = _to_utc_iso8601(time_value)
                            print(
                                f"📰 [item] headline='{(headline or headline_before or '')[:60]}' "
                                f"time_raw='{str(time_value)[:32]}' -> time_utc='{(time_iso or 'None')[:32]}'"
                            )
                            news_item = NewsItem(
                                headline=headline,
                                forecast=forecast,
                                previous=previous,
                                actual=actual,
                                currency=currency,
                                impact=impact,
                                time=time_iso,
                            )
                            news_items.append(news_item)
                    print(f"📰 [fetch] Parsed news items: {len(news_items)}")
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
    print(f"🔎 [analyze] Start analysis for: '{(news_item.headline or '')[:60]}'")
    # Ask Perplexity to respond with a strict JSON payload to avoid ambiguous wording
    prompt = (
        "Analyze the following economic news for Forex trading impact.\n"
        f"News: {news_item.headline}\n"
        f"Forecast: {news_item.forecast or 'N/A'}\n"
        f"Previous: {news_item.previous or 'N/A'}\n"
        f"Actual: {news_item.actual or 'N/A'}\n"
        f"Source impact hint: {(news_item.impact or 'N/A')}\n\n"
        "Respond ONLY with a JSON object using this exact schema (no extra text):\n"
        "{\n"
        "  \"effect\": \"bullish|bearish|neutral\",\n"
        "  \"impact\": \"high|medium|low\",\n"
        "  \"explanation\": \"<max 2 sentences>\"\n"
        "}\n"
        "Rules: values for effect/impact must be lowercase and one of the allowed options."
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
                        data = json.loads(text)
                        analysis_text = data["choices"][0]["message"]["content"]

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
                                # Heuristic: ensure it likely has keys
                                if '"impact"' in snippet or '"effect"' in snippet:
                                    try:
                                        return json.loads(snippet)
                                    except Exception:
                                        pass
                            return None

                        effect = "neutral"
                        impact_value: Optional[str] = None
                        explanation = None

                        parsed = _extract_json_block(analysis_text)
                        if parsed and isinstance(parsed, dict):
                            effect = _normalize_effect(parsed.get("effect"))
                            impact_value = _normalize_impact(parsed.get("impact"))
                            explanation = parsed.get("explanation")

                        if impact_value is None or effect == "neutral":
                            # Fallback: regex extraction from free text
                            lt = analysis_text.lower()
                            # Effect
                            m_eff = re.search(r"effect\s*[:\-]\s*\"?([a-z]+)\"?", lt)
                            if m_eff:
                                effect = _normalize_effect(m_eff.group(1)) or effect
                            else:
                                if "bullish" in lt:
                                    effect = "bullish"
                                elif "bearish" in lt:
                                    effect = "bearish"

                            # Impact
                            m_imp = re.search(r"impact\s*[:\-]\s*\"?([a-z ]+)\"?", lt)
                            if m_imp:
                                impact_value = _normalize_impact(m_imp.group(1)) or impact_value
                            if impact_value is None:
                                # synonym sweep
                                if any(w in lt for w in [
                                    "high impact", "significant", "strong impact", "highly volatile",
                                    "highly impactful", "very high", "major", "substantial"
                                ]):
                                    impact_value = "high"
                                elif any(w in lt for w in [
                                    "medium impact", "moderate", "modest", "moderately"
                                ]):
                                    impact_value = "medium"
                                elif any(w in lt for w in [
                                    "low impact", "minor", "limited", "low volatility", "negligible", "minimal", "slight"
                                ]):
                                    impact_value = "low"

                        # Final fallback: use source impact hint if provided
                        if (impact_value is None or impact_value not in ("high", "medium", "low")) and (news_item.impact or "").strip():
                            im = (news_item.impact or "").strip().lower()
                            if im in ("high", "medium", "low"):
                                impact_value = im

                        if not impact_value:
                            impact_value = "medium"

                        print(f"🔎 [analyze] Effect derived: {effect} | Impact: {impact_value}")
                        analysis = {
                            "effect": effect,
                            "impact": impact_value,
                            "full_analysis": analysis_text,
                        }
                        return NewsAnalysis(
                            headline=news_item.headline,
                            forecast=news_item.forecast,
                            previous=news_item.previous,
                            actual=news_item.actual,
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


async def update_news_cache():
    global global_news_cache, news_cache_metadata
    if news_cache_metadata["is_updating"]:
        print("🗞️ [update] News update already in progress, skipping...")
        return
    try:
        print("🗞️ [update] Starting news cache update...")
        news_cache_metadata["is_updating"] = True
        news_items = await fetch_jblanked_news()
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
                                headline=news_item.headline,
                                forecast=news_item.forecast,
                                previous=news_item.previous,
                                actual=news_item.actual,
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
                            headline=news_item.headline,
                            forecast=news_item.forecast,
                            previous=news_item.previous,
                            actual=news_item.actual,
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
                        headline=news_item.headline,
                        forecast=news_item.forecast,
                        previous=news_item.previous,
                        actual=news_item.actual,
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
                        headline=news_item.headline,
                        forecast=news_item.forecast,
                        previous=news_item.previous,
                        actual=news_item.actual,
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
    while True:
        try:
            current_time = datetime.now(timezone.utc)
            print(
                f"⏰ [scheduler] tick at {current_time.isoformat()} | next_update={news_cache_metadata['next_update_time']} | is_updating={news_cache_metadata['is_updating']}"
            )
            if news_cache_metadata["next_update_time"] is None or current_time >= news_cache_metadata["next_update_time"]:
                print("⏰ [scheduler] Triggering update_news_cache()")
                await update_news_cache()
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"❌ [scheduler] Error in news scheduler: {e}")
            await asyncio.sleep(3600)



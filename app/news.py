import asyncio
import json
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


global_news_cache: List[NewsAnalysis] = []
news_cache_metadata: Dict[str, any] = {
    "last_updated": None,
    "next_update_time": None,
    "is_updating": False,
}


async def fetch_jblanked_news() -> List[NewsItem]:
    try:
        headers = {"Authorization": f"Api-Key {JBLANKED_API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.get(JBLANKED_API_URL, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    news_items = []
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict) and 'data' in data:
                        items = data['data']
                    else:
                        items = []
                        if isinstance(data, dict):
                            for _, value in data.items():
                                if isinstance(value, list):
                                    items = value
                                    break
                    for item in items:
                        if isinstance(item, dict):
                            headline = item.get('Name', '') or item.get('title', '') or item.get('headline', '') or item.get('name', '')
                            forecast = item.get('Forecast', '') or item.get('forecast', '') or item.get('expected', '')
                            previous = item.get('Previous', '') or item.get('previous', '') or item.get('prev', '')
                            actual = item.get('Actual', '') or item.get('actual', '') or item.get('result', '')
                            currency = item.get('Currency', '') or item.get('currency', '') or item.get('ccy', '') or item.get('country', '')
                            impact = item.get('Strength', '') or item.get('impact', '') or item.get('importance', '')
                            time = item.get('Date', '') or item.get('time', '') or item.get('date', '') or item.get('timestamp', '')
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
                            if time == '':
                                time = None
                            news_item = NewsItem(
                                headline=headline,
                                forecast=forecast,
                                previous=previous,
                                actual=actual,
                                currency=currency,
                                impact=impact,
                                time=time,
                            )
                            news_items.append(news_item)
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
                        continue
                    else:
                        raise RuntimeError(f"Perplexity API {resp.status}: {text}")
            except asyncio.TimeoutError:
                if attempt >= len(backoff):
                    raise
                continue


async def update_news_cache():
    global global_news_cache, news_cache_metadata
    if news_cache_metadata["is_updating"]:
        print("📰 News update already in progress, skipping...")
        return
    try:
        news_cache_metadata["is_updating"] = True
        news_items = await fetch_jblanked_news()
        if not news_items:
            print("⚠️ No news items fetched, keeping existing cache")
            return
        analyzed_news = []
        for news_item in news_items:
            try:
                analysis = await asyncio.wait_for(analyze_news_with_perplexity(news_item), timeout=60.0)
                if analysis:
                    analyzed_news.append(analysis)
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
            print(f"⏰ Next update scheduled for: {news_cache_metadata['next_update_time']}")
        else:
            print("⚠️ No news analyzed successfully, storing raw news data as fallback")
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
                        "full_analysis": "AI analysis failed - raw data only",
                    },
                    analyzed_at=datetime.now(timezone.utc),
                )
                raw_news.append(raw_analysis)
            global_news_cache = raw_news[:NEWS_CACHE_MAX_ITEMS]
            news_cache_metadata["last_updated"] = datetime.now(timezone.utc)
            news_cache_metadata["next_update_time"] = datetime.now(timezone.utc) + timedelta(hours=NEWS_UPDATE_INTERVAL_HOURS)
            print(f"✅ News cache updated with raw data: {len(global_news_cache)} items")
            print(f"⏰ Next update scheduled for: {news_cache_metadata['next_update_time']}")
    except Exception as e:
        print(f"❌ Error updating news cache: {e}")
        import traceback
        traceback.print_exc()
    finally:
        news_cache_metadata["is_updating"] = False


async def news_scheduler():
    while True:
        try:
            current_time = datetime.now(timezone.utc)
            if news_cache_metadata["next_update_time"] is None or current_time >= news_cache_metadata["next_update_time"]:
                await update_news_cache()
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"❌ Error in news scheduler: {e}")
            await asyncio.sleep(3600)



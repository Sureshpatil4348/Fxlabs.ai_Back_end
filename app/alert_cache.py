import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import aiohttp
import json
import logging

from .logging_config import configure_logging
from .alert_logging import log_debug, log_info, log_warning, log_error

class AlertCache:
    """Simple in-memory cache for user alert configurations"""
    
    def __init__(self):
        # Cache storage: {user_id: [alert_configs]}
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._last_refresh: Optional[datetime] = None
        self._refresh_interval = timedelta(minutes=5)  # align with 5-minute alert scheduler
        self._is_refreshing = False
        
        # Supabase configuration (tenant-aware from app.config)
        from .config import SUPABASE_URL, SUPABASE_SERVICE_KEY
        self.supabase_url = SUPABASE_URL
        self.supabase_service_key = SUPABASE_SERVICE_KEY
        
        # HTTP timeout configuration for network requests
        self.timeout = aiohttp.ClientTimeout(
            connect=3,      # 3 seconds to establish connection
            sock_read=7,    # 7 seconds to read data from socket
            total=10        # 10 seconds total timeout for entire request
        )
        
        if not self.supabase_url or not self.supabase_service_key:
            print("⚠️ Supabase credentials not found. Alert caching will be disabled.")
    
    async def get_user_alerts(self, user_id: str) -> List[Dict[str, Any]]:
        """Get cached alerts for a user"""
        # Check if cache needs refresh
        if self._should_refresh():
            await self._refresh_cache()
        
        return self._cache.get(user_id, [])
    
    async def get_all_alerts(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all cached alerts for all users"""
        # Check if cache needs refresh
        if self._should_refresh():
            await self._refresh_cache()
        
        return self._cache.copy()
    
    def _should_refresh(self) -> bool:
        """Check if cache should be refreshed"""
        if self._last_refresh is None:
            return True
        
        if self._is_refreshing:
            return False
            
        return datetime.now(timezone.utc) - self._last_refresh >= self._refresh_interval
    
    async def _refresh_cache(self):
        """Refresh the entire cache from Supabase"""
        if self._is_refreshing:
            return
        
        if not self.supabase_url or not self.supabase_service_key:
            return
        
        try:
            self._is_refreshing = True
            print("🔄 Refreshing alert cache...")
            configure_logging()
            logger = logging.getLogger(__name__)
            log_info(
                logger,
                "alert_cache_refresh_start",
            )
            
            # Fetch all active alerts from Supabase
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            # Fetch RSI Tracker alerts (single-alert model)
            rsi_tracker_alerts = await self._fetch_rsi_tracker_alerts(headers)
            # Fetch RSI Correlation Tracker alerts (single-alert model)
            rsi_corr_tracker_alerts = await self._fetch_rsi_correlation_tracker_alerts(headers)
            # Fetch Heatmap/Quantum Tracker alerts (single-alert model)
            heatmap_tracker_alerts = await self._fetch_heatmap_tracker_alerts(headers)
            # Fetch Heatmap Custom Indicator Tracker alerts (single-alert model)
            heatmap_indicator_tracker_alerts = await self._fetch_heatmap_indicator_tracker_alerts(headers)
            
            # Group alerts by user_id
            new_cache = {}
            
            # Process RSI tracker alerts (single alert per user)
            for alert in rsi_tracker_alerts:
                user_id = alert.get("user_id")
                if user_id:
                    if user_id not in new_cache:
                        new_cache[user_id] = []
                    new_cache[user_id].append({
                        "type": "rsi_tracker",
                        "id": alert.get("id"),
                        "alert_name": alert.get("alert_name", "RSI Tracker Alert"),
                        "user_id": alert.get("user_id"),
                        "user_email": alert.get("user_email"),
                        "is_active": alert.get("is_active", True),
                        "timeframe": alert.get("timeframe", "1H"),
                        "rsi_period": alert.get("rsi_period", 14),
                        "rsi_overbought": alert.get("rsi_overbought", alert.get("rsi_overbought_threshold", 70)),
                        "rsi_oversold": alert.get("rsi_oversold", alert.get("rsi_oversold_threshold", 30)),
                        "notification_methods": alert.get("notification_methods", ["email"]),
                        "created_at": alert.get("created_at"),
                        "updated_at": alert.get("updated_at"),
                    })

            # Process RSI correlation tracker alerts (single alert per user)
            for alert in rsi_corr_tracker_alerts:
                user_id = alert.get("user_id")
                if user_id:
                    if user_id not in new_cache:
                        new_cache[user_id] = []
                    new_cache[user_id].append({
                        "type": "rsi_correlation_tracker",
                        "id": alert.get("id"),
                        "alert_name": alert.get("alert_name", "RSI Correlation Tracker Alert"),
                        "user_id": alert.get("user_id"),
                        "user_email": alert.get("user_email"),
                        "is_active": alert.get("is_active", True),
                        "timeframe": alert.get("timeframe", "1H"),
                        "mode": alert.get("mode", "rsi_threshold"),
                        "rsi_period": alert.get("rsi_period", 14),
                        "rsi_overbought": alert.get("rsi_overbought", 70),
                        "rsi_oversold": alert.get("rsi_oversold", 30),
                        "correlation_window": alert.get("correlation_window", 50),
                        "notification_methods": alert.get("notification_methods", ["email"]),
                        "created_at": alert.get("created_at"),
                        "updated_at": alert.get("updated_at"),
                    })

            # Process Heatmap/Quantum tracker alerts (single alert per user)
            for alert in heatmap_tracker_alerts:
                user_id = alert.get("user_id")
                if user_id:
                    if user_id not in new_cache:
                        new_cache[user_id] = []
                    new_cache[user_id].append({
                        "type": "heatmap_tracker",
                        "id": alert.get("id"),
                        "alert_name": alert.get("alert_name", "Heatmap Tracker Alert"),
                        "user_id": alert.get("user_id"),
                        "user_email": alert.get("user_email"),
                        "is_active": alert.get("is_active", True),
                        "pairs": alert.get("pairs", []),
                        "trading_style": alert.get("trading_style", "scalper"),
                        "buy_threshold": alert.get("buy_threshold", 70),
                        "sell_threshold": alert.get("sell_threshold", 30),
                        "notification_methods": alert.get("notification_methods", ["email"]),
                        "created_at": alert.get("created_at"),
                        "updated_at": alert.get("updated_at"),
                    })

            # Process Heatmap Custom Indicator tracker alerts (single alert per user)
            for alert in heatmap_indicator_tracker_alerts:
                user_id = alert.get("user_id")
                if user_id:
                    if user_id not in new_cache:
                        new_cache[user_id] = []
                    new_cache[user_id].append({
                        "type": "heatmap_indicator_tracker",
                        "id": alert.get("id"),
                        "alert_name": alert.get("alert_name", "Indicator Tracker Alert"),
                        "user_id": alert.get("user_id"),
                        "user_email": alert.get("user_email"),
                        "is_active": alert.get("is_active", True),
                        "pairs": alert.get("pairs", []),
                        "timeframe": alert.get("timeframe", "1H"),
                        "indicator": alert.get("indicator", "ema21"),
                        "notification_methods": alert.get("notification_methods", ["email"]),
                        "created_at": alert.get("created_at"),
                        "updated_at": alert.get("updated_at"),
                    })
            
            # Update cache
            self._cache = new_cache
            self._last_refresh = datetime.now(timezone.utc)
            
            total_alerts = sum(len(alerts) for alerts in new_cache.values())
            print(f"✅ Alert cache refreshed: {len(new_cache)} users, {total_alerts} total alerts")
            log_info(
                logger,
                "alert_cache_refreshed",
                users=len(new_cache),
                total_alerts=total_alerts,
            )
            
        except Exception as e:
            print(f"❌ Error refreshing alert cache: {e}")
            import traceback
            traceback.print_exc()
            try:
                configure_logging()
                logger = logging.getLogger(__name__)
                log_error(
                    logger,
                    "alert_cache_refresh_error",
                    error=str(e),
                )
            except Exception:
                pass
        finally:
            self._is_refreshing = False
    
    async def _fetch_rsi_tracker_alerts(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch RSI Tracker alerts from Supabase"""
        try:
            url = f"{self.supabase_url}/rest/v1/rsi_tracker_alerts"
            params = {
                "select": "*",
                "is_active": "eq.true",
            }
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Failed to fetch RSI tracker alerts: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Error fetching RSI tracker alerts: {e}")
            return []

    async def _fetch_rsi_correlation_tracker_alerts(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch RSI Correlation Tracker alerts from Supabase"""
        try:
            url = f"{self.supabase_url}/rest/v1/rsi_correlation_tracker_alerts"
            params = {
                "select": "*",
                "is_active": "eq.true",
            }
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Failed to fetch RSI correlation tracker alerts: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Error fetching RSI correlation tracker alerts: {e}")
            return []

    async def _fetch_heatmap_tracker_alerts(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch Heatmap/Quantum tracker alerts from Supabase"""
        try:
            url = f"{self.supabase_url}/rest/v1/heatmap_tracker_alerts"
            params = {
                "select": "*",
                "is_active": "eq.true",
            }
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Failed to fetch heatmap tracker alerts: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Error fetching heatmap tracker alerts: {e}")
            return []

    async def _fetch_heatmap_indicator_tracker_alerts(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch Heatmap Custom Indicator tracker alerts from Supabase"""
        try:
            url = f"{self.supabase_url}/rest/v1/heatmap_indicator_tracker_alerts"
            params = {
                "select": "*",
                "is_active": "eq.true",
            }
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Failed to fetch heatmap indicator tracker alerts: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Error fetching heatmap indicator tracker alerts: {e}")
            return []
    
    async def start_refresh_scheduler(self):
        """Start background task to refresh cache every 5 minutes"""
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                if self._should_refresh():
                    await self._refresh_cache()
            except Exception as e:
                print(f"❌ Error in alert cache scheduler: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying

# Global alert cache instance
alert_cache = AlertCache()

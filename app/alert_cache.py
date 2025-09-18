import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
import aiohttp
import json

class AlertCache:
    """Simple in-memory cache for user alert configurations"""
    
    def __init__(self):
        # Cache storage: {user_id: [alert_configs]}
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._last_refresh: Optional[datetime] = None
        self._refresh_interval = timedelta(minutes=5)  # 5 minutes
        self._is_refreshing = False
        
        # Supabase configuration
        self.supabase_url = os.environ.get("SUPABASE_URL", "https://hyajwhtkwldrmlhfiuwg.supabase.co")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh5YWp3aHRrd2xkcm1saGZpdXdnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NjI5NjUzNCwiZXhwIjoyMDcxODcyNTM0fQ.UDqYHY5Io0o-fQTswCYQmMdC6UCPQI2gf3aTb9o09SE")
        
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
            
            # Fetch all active alerts from Supabase
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            # Fetch heatmap alerts
            heatmap_alerts = await self._fetch_heatmap_alerts(headers)
            
            # Fetch RSI alerts
            rsi_alerts = await self._fetch_rsi_alerts(headers)
            
            # Fetch RSI correlation alerts
            rsi_correlation_alerts = await self._fetch_rsi_correlation_alerts(headers)
            
            # Group alerts by user_id
            new_cache = {}
            
            # Process heatmap alerts
            for alert in heatmap_alerts:
                user_id = alert.get("user_id")
                if user_id:
                    if user_id not in new_cache:
                        new_cache[user_id] = []
                    new_cache[user_id].append({
                        "type": "heatmap",
                        "id": alert.get("id"),
                        "alert_name": alert.get("alert_name"),
                        "user_id": alert.get("user_id"),
                        "user_email": alert.get("user_email"),  # Add user_email for email notifications
                        "is_active": alert.get("is_active", True),
                        "pairs": alert.get("pairs", []),
                        "timeframes": alert.get("timeframes", []),
                        "selected_indicators": alert.get("selected_indicators", []),
                        "trading_style": alert.get("trading_style", "dayTrader"),
                        "buy_threshold_min": alert.get("buy_threshold_min", 70),
                        "buy_threshold_max": alert.get("buy_threshold_max", 100),
                        "sell_threshold_min": alert.get("sell_threshold_min", 0),
                        "sell_threshold_max": alert.get("sell_threshold_max", 30),
                        "notification_methods": alert.get("notification_methods", ["browser"]),
                        "alert_frequency": alert.get("alert_frequency", "once"),
                        "trigger_on_crossing": alert.get("trigger_on_crossing", True),
                        "created_at": alert.get("created_at"),
                        "updated_at": alert.get("updated_at")
                    })
            
            # Process RSI alerts
            for alert in rsi_alerts:
                user_id = alert.get("user_id")
                if user_id:
                    if user_id not in new_cache:
                        new_cache[user_id] = []
                    new_cache[user_id].append({
                        "type": "rsi",
                        "id": alert.get("id"),
                        "alert_name": alert.get("alert_name"),
                        "user_id": alert.get("user_id"),
                        "user_email": alert.get("user_email"),  # Add user_email for email notifications
                        "is_active": alert.get("is_active", True),
                        "pairs": alert.get("pairs", []),
                        "timeframes": alert.get("timeframes", []),
                        "rsi_period": alert.get("rsi_period", 14),
                        "rsi_overbought_threshold": alert.get("rsi_overbought_threshold", 70),
                        "rsi_oversold_threshold": alert.get("rsi_oversold_threshold", 30),
                        "alert_conditions": alert.get("alert_conditions", []),
                        "rfi_strong_threshold": alert.get("rfi_strong_threshold", 0.80),
                        "rfi_moderate_threshold": alert.get("rfi_moderate_threshold", 0.60),
                        "notification_methods": alert.get("notification_methods", ["browser"]),
                        "alert_frequency": alert.get("alert_frequency", "once"),
                        "trigger_on_crossing": alert.get("trigger_on_crossing", True),
                        "created_at": alert.get("created_at"),
                        "updated_at": alert.get("updated_at")
                    })
            
            # Process RSI correlation alerts
            for alert in rsi_correlation_alerts:
                user_id = alert.get("user_id")
                if user_id:
                    if user_id not in new_cache:
                        new_cache[user_id] = []
                    new_cache[user_id].append({
                        "type": "rsi_correlation",
                        "id": alert.get("id"),
                        "alert_name": alert.get("alert_name"),
                        "user_id": alert.get("user_id"),
                        "user_email": alert.get("user_email"),  # Add user_email for email notifications
                        "is_active": alert.get("is_active", True),
                        "pairs": alert.get("pairs", []),
                        "timeframes": alert.get("timeframes", []),
                        "rsi_period": alert.get("rsi_period", 14),
                        "correlation_threshold": alert.get("correlation_threshold", 0.7),
                        "notification_methods": alert.get("notification_methods", ["browser"]),
                        "alert_frequency": alert.get("alert_frequency", "once"),
                        "trigger_on_crossing": alert.get("trigger_on_crossing", True),
                        "created_at": alert.get("created_at"),
                        "updated_at": alert.get("updated_at")
                    })
            
            # Update cache
            self._cache = new_cache
            self._last_refresh = datetime.now(timezone.utc)
            
            total_alerts = sum(len(alerts) for alerts in new_cache.values())
            print(f"✅ Alert cache refreshed: {len(new_cache)} users, {total_alerts} total alerts")
            
        except Exception as e:
            print(f"❌ Error refreshing alert cache: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._is_refreshing = False
    
    async def _fetch_heatmap_alerts(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch heatmap alerts from Supabase"""
        try:
            url = f"{self.supabase_url}/rest/v1/heatmap_alerts"
            params = {
                "select": "*",
                "is_active": "eq.true"
            }
            
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Failed to fetch heatmap alerts: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Error fetching heatmap alerts: {e}")
            return []
    
    async def _fetch_rsi_alerts(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch RSI alerts from Supabase"""
        try:
            url = f"{self.supabase_url}/rest/v1/rsi_alerts"
            params = {
                "select": "*",
                "is_active": "eq.true"
            }
            
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Failed to fetch RSI alerts: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Error fetching RSI alerts: {e}")
            return []
    
    async def _fetch_rsi_correlation_alerts(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        """Fetch RSI correlation alerts from Supabase"""
        try:
            url = f"{self.supabase_url}/rest/v1/rsi_correlation_alerts"
            params = {
                "select": "*",
                "is_active": "eq.true"
            }
            
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"❌ Failed to fetch RSI correlation alerts: {response.status}")
                        return []
        except Exception as e:
            print(f"❌ Error fetching RSI correlation alerts: {e}")
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

import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
import aiohttp
import json
import logging

from .email_service import email_service
from .alert_cache import alert_cache

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RSIAlertService:
    """
    RSI Alert Service for monitoring RSI conditions and sending notifications
    """
    
    def __init__(self):
        self.supabase_url = os.environ.get("SUPABASE_URL", "https://hyajwhtkwldrmlhfiuwg.supabase.co")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY")
        self.last_triggered_alerts: Dict[str, datetime] = {}  # Track last trigger time per alert
        self.default_cooldown_seconds = 300  # 5 minutes default cooldown
    
    def _should_trigger_alert(self, alert_id: str, cooldown_seconds: int = None) -> bool:
        """Check if alert should be triggered based on cooldown period"""
        if cooldown_seconds is None:
            cooldown_seconds = self.default_cooldown_seconds
        
        if alert_id not in self.last_triggered_alerts:
            return True
        
        last_triggered = self.last_triggered_alerts[alert_id]
        cooldown_duration = timedelta(seconds=cooldown_seconds)
        
        return datetime.now(timezone.utc) - last_triggered >= cooldown_duration
        
    async def check_rsi_alerts(self, tick_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check all RSI alerts against current tick data"""
        
        try:
            # Get all active RSI alerts from cache
            all_alerts = await alert_cache.get_all_alerts()
            
            triggered_alerts = []
            
            for user_id, user_alerts in all_alerts.items():
                for alert in user_alerts:
                    if alert.get("type") == "rsi" and alert.get("is_active", True):
                        alert_id = alert.get("id")
                        if not alert_id:
                            continue
                        
                        # Check if this alert should be triggered based on cooldown
                        if not self._should_trigger_alert(alert_id):
                            continue
                        
                        # Check if this alert should be triggered
                        trigger_result = await self._check_single_rsi_alert(alert, tick_data)
                        
                        if trigger_result:
                            triggered_alerts.append(trigger_result)
                            
                            # Update last triggered time
                            self.last_triggered_alerts[alert_id] = datetime.now(timezone.utc)
                            
                            # Send email notification if configured
                            if "email" in alert.get("notification_methods", []):
                                await self._send_rsi_alert_notification(trigger_result)
            
            return triggered_alerts
            
        except Exception as e:
            logger.error(f"❌ Error checking RSI alerts: {e}")
            return []
    
    async def _check_single_rsi_alert(self, alert: Dict[str, Any], tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check a single RSI alert against current market data"""
        
        try:
            alert_id = alert.get("id")
            alert_name = alert.get("alert_name")
            pairs = alert.get("pairs", [])
            timeframes = alert.get("timeframes", ["1H"])
            alert_conditions = alert.get("alert_conditions", [])
            
            # RSI settings
            rsi_period = alert.get("rsi_period", 14)
            rsi_overbought = alert.get("rsi_overbought_threshold", 70)
            rsi_oversold = alert.get("rsi_oversold_threshold", 30)
            
            # RFI settings
            rfi_strong_threshold = alert.get("rfi_strong_threshold", 0.80)
            rfi_moderate_threshold = alert.get("rfi_moderate_threshold", 0.60)
            
            triggered_pairs = []
            
            # Check each pair and timeframe
            for symbol in pairs:
                for timeframe in timeframes:
                    # Get market data for this symbol/timeframe
                    market_data = self._get_market_data_for_symbol(symbol, timeframe, tick_data)
                    
                    if not market_data:
                        continue
                    
                    # Calculate RSI
                    rsi_value = await self._calculate_rsi(market_data, rsi_period)
                    
                    if rsi_value is None:
                        continue
                    
                    # Calculate RFI score
                    rfi_score = await self._calculate_rfi_score(market_data, rsi_value)
                    
                    # Check alert conditions
                    trigger_condition = self._check_rsi_conditions(
                        rsi_value, rfi_score, alert_conditions,
                        rsi_overbought, rsi_oversold,
                        rfi_strong_threshold, rfi_moderate_threshold
                    )
                    
                    if trigger_condition:
                        triggered_pairs.append({
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "rsi_value": round(rsi_value, 2),
                            "rfi_score": round(rfi_score, 2) if rfi_score else None,
                            "trigger_condition": trigger_condition,
                            "current_price": market_data.get("close", 0),
                            "price_change_percent": self._calculate_price_change_percent(market_data),
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
            
            if triggered_pairs:
                return {
                    "alert_id": alert_id,
                    "alert_name": alert_name,
                    "user_id": alert.get("user_id"),
                    "user_email": alert.get("user_email"),
                    "triggered_pairs": triggered_pairs,
                    "alert_config": alert,
                    "triggered_at": datetime.now(timezone.utc).isoformat()
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking single RSI alert: {e}")
            return None
    
    def _get_market_data_for_symbol(self, symbol: str, timeframe: str, tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get market data for a specific symbol and timeframe"""
        
        try:
            # This is a simplified implementation
            # In a real system, you'd get actual market data from your data source
            
            tick_symbols = tick_data.get("symbols", [])
            tick_market_data = tick_data.get("tick_data", {})
            
            if symbol in tick_symbols and symbol in tick_market_data:
                return tick_market_data[symbol]
            
            # Fallback: simulate market data
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "open": 1.1000,
                "high": 1.1010,
                "low": 1.0990,
                "close": 1.1005,
                "volume": 1000,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting market data for {symbol}: {e}")
            return None
    
    async def _calculate_rsi(self, market_data: Dict[str, Any], period: int = 14) -> Optional[float]:
        """Calculate RSI for the given market data"""
        
        try:
            # This is a simplified RSI calculation
            # In a real system, you'd calculate actual RSI using historical data
            
            close_price = market_data.get("close", 1.1000)
            
            # Simulate RSI calculation based on price movement
            # This is just for demonstration - real RSI needs historical data
            price_factor = (close_price - 1.1000) * 1000
            rsi_value = 50 + price_factor * 2  # Simulate RSI between 0-100
            
            # Ensure RSI is within valid range
            rsi_value = max(0, min(100, rsi_value))
            
            return rsi_value
            
        except Exception as e:
            logger.error(f"❌ Error calculating RSI: {e}")
            return None
    
    async def _calculate_rfi_score(self, market_data: Dict[str, Any], rsi_value: float) -> Optional[float]:
        """Calculate RFI (Relative Flow Index) score"""
        
        try:
            # This is a simplified RFI calculation
            # In a real system, you'd calculate actual RFI using volume and price data
            
            volume = market_data.get("volume", 1000)
            price_change = abs(market_data.get("close", 1.1000) - market_data.get("open", 1.1000))
            
            # Simulate RFI calculation
            volume_factor = min(volume / 1000, 2.0)  # Normalize volume
            price_factor = min(price_change * 1000, 1.0)  # Normalize price change
            
            rfi_score = (volume_factor + price_factor) / 2
            rfi_score = max(0, min(1, rfi_score))  # Ensure 0-1 range
            
            return rfi_score
            
        except Exception as e:
            logger.error(f"❌ Error calculating RFI score: {e}")
            return None
    
    def _check_rsi_conditions(
        self, 
        rsi_value: float, 
        rfi_score: Optional[float],
        alert_conditions: List[str],
        rsi_overbought: int,
        rsi_oversold: int,
        rfi_strong_threshold: float,
        rfi_moderate_threshold: float
    ) -> Optional[str]:
        """Check if any alert conditions are met"""
        
        try:
            for condition in alert_conditions:
                if condition == "overbought" and rsi_value >= rsi_overbought:
                    return "overbought"
                elif condition == "oversold" and rsi_value <= rsi_oversold:
                    return "oversold"
                elif condition == "rfi_strong" and rfi_score and rfi_score >= rfi_strong_threshold:
                    return "rfi_strong"
                elif condition == "rfi_moderate" and rfi_score and rfi_score >= rfi_moderate_threshold:
                    return "rfi_moderate"
                # Note: crossup/crossdown would need historical RSI data to detect crossings
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking RSI conditions: {e}")
            return None
    
    def _calculate_price_change_percent(self, market_data: Dict[str, Any]) -> float:
        """Calculate price change percentage"""
        
        try:
            open_price = market_data.get("open", 1.1000)
            close_price = market_data.get("close", 1.1000)
            
            if open_price == 0:
                return 0.0
            
            change_percent = ((close_price - open_price) / open_price) * 100
            return round(change_percent, 2)
            
        except Exception as e:
            logger.error(f"❌ Error calculating price change: {e}")
            return 0.0
    
    async def _send_rsi_alert_notification(self, trigger_data: Dict[str, Any]):
        """Send RSI alert notification via email"""
        
        try:
            user_email = trigger_data.get("user_email")
            alert_name = trigger_data.get("alert_name")
            triggered_pairs = trigger_data.get("triggered_pairs", [])
            alert_config = trigger_data.get("alert_config", {})
            
            if not user_email:
                logger.warning("No user email found for RSI alert notification")
                return
            
            # Send email notification
            success = await email_service.send_rsi_alert(
                user_email=user_email,
                alert_name=alert_name,
                triggered_pairs=triggered_pairs,
                alert_config=alert_config
            )
            
            if success:
                logger.info(f"✅ RSI alert email sent to {user_email}")
            else:
                logger.warning(f"⚠️ Failed to send RSI alert email to {user_email}")
            
            # Log the trigger in database
            await self._log_rsi_alert_trigger(trigger_data)
            
        except Exception as e:
            logger.error(f"❌ Error sending RSI alert notification: {e}")
    
    async def _log_rsi_alert_trigger(self, trigger_data: Dict[str, Any]):
        """Log RSI alert trigger to database"""
        
        try:
            if not self.supabase_service_key:
                logger.warning("Supabase service key not configured, skipping trigger logging")
                return
            
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.supabase_url}/rest/v1/rsi_alert_triggers"
            
            # Log each triggered pair
            for pair_data in trigger_data.get("triggered_pairs", []):
                trigger_record = {
                    "alert_id": trigger_data.get("alert_id"),
                    "trigger_condition": pair_data.get("trigger_condition"),
                    "symbol": pair_data.get("symbol"),
                    "timeframe": pair_data.get("timeframe"),
                    "rsi_value": pair_data.get("rsi_value"),
                    "rfi_score": pair_data.get("rfi_score"),
                    "current_price": pair_data.get("current_price"),
                    "price_change_percent": pair_data.get("price_change_percent"),
                    "triggered_at": datetime.now(timezone.utc).isoformat()
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=trigger_record) as response:
                        if response.status not in [200, 201]:
                            logger.error(f"Failed to log RSI trigger: {response.status}")
            
        except Exception as e:
            logger.error(f"❌ Error logging RSI alert trigger: {e}")
    
    async def create_rsi_alert(self, alert_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new RSI alert in Supabase"""
        
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.supabase_url}/rest/v1/rsi_alerts"
            
            # Prepare alert data for Supabase
            supabase_data = {
                "alert_name": alert_data.get("alert_name"),
                "user_email": alert_data.get("user_email"),
                "pairs": alert_data.get("pairs", []),
                "timeframes": alert_data.get("timeframes", ["1H"]),
                "rsi_period": alert_data.get("rsi_period", 14),
                "rsi_overbought_threshold": alert_data.get("rsi_overbought_threshold", 70),
                "rsi_oversold_threshold": alert_data.get("rsi_oversold_threshold", 30),
                "alert_conditions": alert_data.get("alert_conditions", []),
                "rfi_strong_threshold": alert_data.get("rfi_strong_threshold", 0.80),
                "rfi_moderate_threshold": alert_data.get("rfi_moderate_threshold", 0.60),
                "notification_methods": alert_data.get("notification_methods", ["email"]),
                "alert_frequency": alert_data.get("alert_frequency", "once"),
                "is_active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=supabase_data) as response:
                    if response.status in [200, 201]:
                        result = await response.json()
                        logger.info(f"✅ RSI alert created: {result.get('id')}")
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to create RSI alert: {response.status} - {error_text}")
                        return None
            
        except Exception as e:
            logger.error(f"❌ Error creating RSI alert: {e}")
            return None

# Create global instance
rsi_alert_service = RSIAlertService()

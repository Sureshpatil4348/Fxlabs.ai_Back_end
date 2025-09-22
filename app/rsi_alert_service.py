import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
import aiohttp
import json
import logging

from .email_service import email_service
from .alert_cache import alert_cache
from .concurrency import pair_locks

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
        # Crossing/confirmation/hysteresis defaults (global rule)
        self.only_new_bars = 3           # Consider crossings that happened within last K closed bars
        self.confirmation_bars = 1       # Require 1 closed bar confirmation after crossing
        self.rearm_overbought = 65       # Hysteresis re-arm for overbought (after falling below 65)
        self.rearm_oversold = 35         # Hysteresis re-arm for oversold (after rising above 35)
        # In-memory hysteresis state: key -> { 'armed_overbought': bool, 'armed_oversold': bool }
        self._hysteresis_map: Dict[str, Dict[str, bool]] = {}
    
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
            total_rsi_alerts = 0
            
            for user_id, user_alerts in all_alerts.items():
                for alert in user_alerts:
                    if alert.get("type") == "rsi" and alert.get("is_active", True):
                        total_rsi_alerts += 1
                        alert_id = alert.get("id")
                        alert_name = alert.get("alert_name", "Unknown")
                        user_email = alert.get("user_email", "Unknown")
                        
                        if not alert_id:
                            logger.warning(f"⚠️ Alert {alert_name} has no ID, skipping")
                            continue
                        
                        # Check if this alert should be triggered based on cooldown
                        if not self._should_trigger_alert(alert_id):
                            continue
                        
                        # Check if this alert should be triggered
                        trigger_result = await self._check_single_rsi_alert(alert, tick_data)
                        
                        if trigger_result:
                            logger.info(f"🚨 ALERT TRIGGERED: {alert_name} (ID: {alert_id}) for user {user_email}")
                            logger.info(f"   Triggered pairs: {len(trigger_result.get('triggered_pairs', []))}")
                            
                            triggered_alerts.append(trigger_result)
                            
                            # Update last triggered time
                            self.last_triggered_alerts[alert_id] = datetime.now(timezone.utc)
                            
                            # Send email notification if configured
                            if "email" in alert.get("notification_methods", []):
                                logger.info(f"📧 Sending email notification for alert {alert_name} to {user_email}")
                                await self._send_rsi_alert_notification(trigger_result)
                            else:
                                logger.info(f"📧 Email notification not configured for alert {alert_name}")
            
            # Only log summary if there are alerts to process or triggers occurred
            if total_rsi_alerts > 0:
                if len(triggered_alerts) > 0:
                    logger.info(f"📊 RSI Alert Check Complete: {total_rsi_alerts} alerts processed, {len(triggered_alerts)} triggered")
                else:
                    # Only log debug level when no triggers to reduce noise
                    logger.debug(f"📊 RSI Alert Check Complete: {total_rsi_alerts} alerts processed, 0 triggered")
            
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
            total_checks = 0
            
            # Check each pair and timeframe
            for symbol in pairs:
                for timeframe in timeframes:
                    total_checks += 1
                    logger.debug(f"🔍 Checking {symbol} {timeframe} (Check {total_checks})")

                    key = f"{symbol}:{timeframe}"
                    async with pair_locks.acquire(key):
                        # Get market data for this symbol/timeframe
                        market_data = self._get_market_data_for_symbol(symbol, timeframe, tick_data)

                        if not market_data:
                            logger.warning(f"⚠️ No market data available for {symbol} {timeframe}")
                            continue

                        # Stale-bar protection
                        if self._is_stale_market(market_data, timeframe):
                            logger.debug(f"⏭️ Stale data skipped for {symbol} {timeframe}")
                            continue

                        logger.debug(f"✅ Market data retrieved for {symbol} {timeframe}: {market_data.get('data_source', 'Unknown')}")

                        # Calculate RSI
                        rsi_value = await self._calculate_rsi(market_data, rsi_period)

                        if rsi_value is None:
                            logger.warning(f"⚠️ Could not calculate RSI for {symbol} {timeframe}")
                            continue

                        logger.debug(f"📊 RSI calculated for {symbol} {timeframe}: {rsi_value:.2f}")

                        # Warm-up: ensure sufficient RSI lookback exists for crossings/confirmation
                        bars_needed = max(5, self.only_new_bars + self.confirmation_bars + 2)
                        rsis = await self._get_recent_rsi_series(symbol, timeframe, rsi_period, bars_needed)
                        if not rsis or len(rsis) < bars_needed:
                            logger.debug(f"⏳ Warm-up insufficient for {symbol} {timeframe} (need ≥{bars_needed} RSI points)")
                            continue

                        # Calculate RFI score
                        rfi_score = await self._calculate_rfi_score(market_data, rsi_value)

                        if rfi_score is not None:
                            logger.debug(f"📊 RFI calculated for {symbol} {timeframe}: {rfi_score:.3f}")

                        # Check alert conditions: prefer RSI crossings with confirmation + Only NEW + hysteresis
                        trigger_condition = None
                        if any(cond in ("overbought", "oversold") for cond in alert_conditions):
                            trigger_condition = await self._detect_rsi_crossing(
                                alert_id=alert_id,
                                symbol=symbol,
                                timeframe=timeframe,
                                period=rsi_period,
                                overbought=rsi_overbought,
                                oversold=rsi_oversold,
                            )

                        # If no RSI crossing, consider RFI-only conditions when present
                        if not trigger_condition and ("rfi_strong" in alert_conditions or "rfi_moderate" in alert_conditions):
                            trigger_condition = self._check_rsi_conditions(
                                rsi_value, rfi_score, alert_conditions,
                                rsi_overbought, rsi_oversold,
                                rfi_strong_threshold, rfi_moderate_threshold
                            )

                        if trigger_condition:
                            logger.info(f"🚨 CONDITION MATCHED: {symbol} {timeframe} - {trigger_condition}")
                            rfi_display = f"{rfi_score:.3f}" if rfi_score is not None else "N/A"
                            logger.info(f"   RSI: {rsi_value:.2f}, RFI: {rfi_display}")

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
                        else:
                            logger.debug(f"ℹ️ No conditions met for {symbol} {timeframe} (RSI: {rsi_value:.2f})")
            
            logger.debug(f"📊 Alert check complete: {total_checks} checks, {len(triggered_pairs)} triggers")
            
            if triggered_pairs:
                logger.info(f"🚨 ALERT TRIGGERED: {alert_name} with {len(triggered_pairs)} triggered pairs")
                for pair in triggered_pairs:
                    logger.info(f"   - {pair['symbol']} {pair['timeframe']}: {pair['trigger_condition']} (RSI: {pair['rsi_value']})")
                
                return {
                    "alert_id": alert_id,
                    "alert_name": alert_name,
                    "user_id": alert.get("user_id"),
                    "user_email": alert.get("user_email"),
                    "triggered_pairs": triggered_pairs,
                    "alert_config": alert,
                    "triggered_at": datetime.now(timezone.utc).isoformat()
                }
            
            logger.debug(f"ℹ️ No triggers for alert '{alert_name}'")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking single RSI alert: {e}")
            return None

    def _tf_seconds(self, timeframe: str) -> int:
        mapping = {
            "1M": 60,
            "5M": 5 * 60,
            "15M": 15 * 60,
            "30M": 30 * 60,
            "1H": 60 * 60,
            "4H": 4 * 60 * 60,
            "1D": 24 * 60 * 60,
            "1W": 7 * 24 * 60 * 60,
        }
        return mapping.get(timeframe, 60)

    def _is_stale_market(self, market_data: Dict[str, Any], timeframe: str) -> bool:
        try:
            ts_iso = market_data.get("timestamp")
            if not ts_iso:
                return False
            dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            return age > 2 * self._tf_seconds(timeframe)
        except Exception:
            return False
    
    def _get_market_data_for_symbol(self, symbol: str, timeframe: str, tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get market data for a specific symbol and timeframe using real MT5 data"""
        
        try:
            # Try to get real MT5 data first
            try:
                from .mt5_utils import get_ohlc_data, get_current_tick
                from .models import Timeframe as MT5Timeframe
                
                # Convert timeframe string to MT5 Timeframe enum
                timeframe_map = {
                    "1M": MT5Timeframe.M1,
                    "5M": MT5Timeframe.M5,
                    "15M": MT5Timeframe.M15,
                    "30M": MT5Timeframe.M30,
                    "1H": MT5Timeframe.H1,
                    "4H": MT5Timeframe.H4,
                    "1D": MT5Timeframe.D1,
                    "1W": MT5Timeframe.W1
                }
                
                mt5_timeframe = timeframe_map.get(timeframe)
                if mt5_timeframe:
                    # Get real OHLC data from MT5
                    ohlc_data = get_ohlc_data(symbol, mt5_timeframe, 50)
                    if ohlc_data and len(ohlc_data) > 0:
                        latest_bar = ohlc_data[-1]
                        tick_data = get_current_tick(symbol)
                        
                        logger.debug(f"✅ Using real MT5 data for {symbol} {timeframe}")
                        return {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "open": latest_bar.open,
                            "high": latest_bar.high,
                            "low": latest_bar.low,
                            "close": latest_bar.close,
                            "volume": latest_bar.volume,
                            "timestamp": latest_bar.time_iso,
                            "bid": tick_data.bid if tick_data else None,
                            "ask": tick_data.ask if tick_data else None,
                            "data_source": "MT5_REAL"
                        }
            except ImportError:
                logger.warning(f"⚠️ MT5 not available, using fallback data for {symbol}")
            except Exception as mt5_error:
                logger.warning(f"⚠️ MT5 error for {symbol}: {mt5_error}, using fallback data")
            
            # Fallback: check tick data first, then simulate
            tick_symbols = tick_data.get("symbols", [])
            tick_market_data = tick_data.get("tick_data", {})
            
            if symbol in tick_symbols and symbol in tick_market_data:
                return tick_market_data[symbol]
            
            # Final fallback: simulate market data
            logger.debug(f"⚠️ Using simulated data for {symbol} - no real data available")
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "open": 1.1000,
                "high": 1.1010,
                "low": 1.0990,
                "close": 1.1005,
                "volume": 1000,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_source": "SIMULATED"
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting market data for {symbol}: {e}")
            return None
    
    async def _calculate_rsi(self, market_data: Dict[str, Any], period: int = 14) -> Optional[float]:
        """Calculate real RSI using historical OHLC data from MT5"""
        
        try:
            # If we have real MT5 data, calculate actual RSI
            if market_data.get("data_source") == "MT5_REAL":
                try:
                    from .mt5_utils import get_ohlc_data
                    from .models import Timeframe as MT5Timeframe
                    
                    symbol = market_data["symbol"]
                    timeframe = market_data["timeframe"]
                    
                    # Convert timeframe string to MT5 Timeframe enum
                    timeframe_map = {
                        "1M": MT5Timeframe.M1,
                        "5M": MT5Timeframe.M5,
                        "15M": MT5Timeframe.M15,
                        "30M": MT5Timeframe.M30,
                        "1H": MT5Timeframe.H1,
                        "4H": MT5Timeframe.H4,
                        "1D": MT5Timeframe.D1,
                        "1W": MT5Timeframe.W1
                    }
                    
                    mt5_timeframe = timeframe_map.get(timeframe)
                    if mt5_timeframe:
                        # Get historical OHLC data for RSI calculation
                        ohlc_data = get_ohlc_data(symbol, mt5_timeframe, period + 10)
                        if ohlc_data and len(ohlc_data) >= period + 1:
                            # Extract close prices
                            closes = [bar.close for bar in ohlc_data]
                            
                            # Calculate real RSI
                            rsi_value = self._calculate_rsi_from_closes(closes, period)
                            if rsi_value is not None:
                                logger.debug(f"✅ Calculated real RSI for {symbol}: {rsi_value:.2f}")
                                return rsi_value
                except Exception as mt5_error:
                    logger.warning(f"⚠️ MT5 RSI calculation failed: {mt5_error}")
            
            # Fallback: simulate RSI calculation
            close_price = market_data.get("close", 1.1000)
            price_factor = (close_price - 1.1000) * 1000
            rsi_value = 50 + price_factor * 2
            rsi_value = max(0, min(100, rsi_value))
            
            logger.debug(f"⚠️ Using simulated RSI for {market_data.get('symbol', 'unknown')}: {rsi_value:.2f}")
            return rsi_value
            
        except Exception as e:
            logger.error(f"❌ Error calculating RSI: {e}")
            return None
    
    def _calculate_rsi_from_closes(self, closes: List[float], period: int = 14) -> Optional[float]:
        """Calculate RSI from a list of close prices"""
        if len(closes) < period + 1:
            return None
        
        gains = 0
        losses = 0
        
        for i in range(1, period + 1):
            change = closes[i] - closes[i - 1]
            if change > 0:
                gains += change
            else:
                losses -= change
        
        avg_gain = gains / period
        avg_loss = losses / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
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
            alert_id = trigger_data.get("alert_id")
            triggered_pairs = trigger_data.get("triggered_pairs", [])
            alert_config = trigger_data.get("alert_config", {})
            
            logger.info(f"📧 Preparing RSI alert email for user: {user_email}")
            logger.info(f"   Alert: {alert_name} (ID: {alert_id})")
            logger.info(f"   Triggered pairs: {len(triggered_pairs)}")
            
            if not user_email:
                logger.warning("⚠️ No user email found for RSI alert notification")
                return
            
            # Log triggered pairs details
            for i, pair in enumerate(triggered_pairs, 1):
                logger.info(f"   Pair {i}: {pair.get('symbol')} {pair.get('timeframe')} - {pair.get('trigger_condition')} (RSI: {pair.get('rsi_value')})")
            
            # Send email notification
            logger.info(f"📤 Sending RSI alert email to {user_email}...")
            success = await email_service.send_rsi_alert(
                user_email=user_email,
                alert_name=alert_name,
                triggered_pairs=triggered_pairs,
                alert_config=alert_config
            )
            
            if success:
                logger.info(f"✅ RSI alert email sent successfully to {user_email}")
                logger.info(f"   Alert: {alert_name} (ID: {alert_id})")
                logger.info(f"   Pairs: {len(triggered_pairs)}")
            else:
                logger.warning(f"⚠️ Failed to send RSI alert email to {user_email}")
                logger.warning(f"   Alert: {alert_name} (ID: {alert_id})")
            
            # Log the trigger in database
            logger.info(f"📝 Logging RSI alert trigger to database...")
            await self._log_rsi_alert_trigger(trigger_data)
            logger.info(f"✅ RSI alert trigger logged to database")
            
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
    
    async def _detect_rsi_crossing(
        self,
        alert_id: str,
        symbol: str,
        timeframe: str,
        period: int,
        overbought: int,
        oversold: int,
    ) -> Optional[str]:
        """Detect RSI threshold crossings with 1-bar confirmation and hysteresis re-arm.

        Returns one of: "overbought_cross", "oversold_cross", or None.
        """
        try:
            rsis = await self._get_recent_rsi_series(symbol, timeframe, period, bars_needed=max(5, self.only_new_bars + self.confirmation_bars + 2))
            if not rsis or len(rsis) < (self.only_new_bars + self.confirmation_bars + 1):
                return None

            # Hysteresis arm/disarm state per alert-symbol-timeframe
            key = f"{alert_id}:{symbol}:{timeframe}"
            st = self._hysteresis_map.setdefault(key, {"armed_overbought": True, "armed_oversold": True})

            latest = rsis[-1]
            # Rearm logic
            if not st["armed_overbought"] and latest <= self.rearm_overbought:
                st["armed_overbought"] = True
            if not st["armed_oversold"] and latest >= self.rearm_oversold:
                st["armed_oversold"] = True

            # Scan last window for crossing with confirmation
            window_start = max(1, len(rsis) - (self.only_new_bars + self.confirmation_bars))
            best_idx = None
            best_type = None
            for i in range(window_start, len(rsis) - self.confirmation_bars):
                prev_val = rsis[i - 1]
                curr_val = rsis[i]

                # Overbought crossing + confirmation
                if st["armed_overbought"] and prev_val < overbought and curr_val >= overbought:
                    confirm_idx = i + self.confirmation_bars
                    if confirm_idx < len(rsis) and rsis[confirm_idx] >= overbought:
                        best_idx, best_type = i, "overbought_cross"

                # Oversold crossing + confirmation
                if st["armed_oversold"] and prev_val > oversold and curr_val <= oversold:
                    confirm_idx = i + self.confirmation_bars
                    if confirm_idx < len(rsis) and rsis[confirm_idx] <= oversold:
                        if best_idx is None or i > best_idx:
                            best_idx, best_type = i, "oversold_cross"

            if best_type:
                if best_type == "overbought_cross":
                    st["armed_overbought"] = False
                else:
                    st["armed_oversold"] = False
                return best_type

            return None
        except Exception as e:
            logger.error(f"❌ Error detecting RSI crossing: {e}")
            return None

    async def _get_recent_rsi_series(self, symbol: str, timeframe: str, period: int, bars_needed: int) -> Optional[List[float]]:
        """Compute recent RSI series using MT5 OHLC data if available."""
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe

            timeframe_map = {
                "1M": MT5Timeframe.M1,
                "5M": MT5Timeframe.M5,
                "15M": MT5Timeframe.M15,
                "30M": MT5Timeframe.M30,
                "1H": MT5Timeframe.H1,
                "4H": MT5Timeframe.H4,
                "1D": MT5Timeframe.D1,
                "1W": MT5Timeframe.W1
            }

            mt5_timeframe = timeframe_map.get(timeframe)
            if not mt5_timeframe:
                return None

            count = max(period + bars_needed + 2, period + 5)
            ohlc_data = get_ohlc_data(symbol, mt5_timeframe, count)
            if not ohlc_data or len(ohlc_data) < period + 1:
                return None
            closes = [bar.close for bar in ohlc_data]
            series = self._calculate_rsi_series(closes, period)
            if not series:
                return None
            return series[-bars_needed:] if len(series) >= bars_needed else series
        except Exception as e:
            logger.debug(f"RSI series unavailable for {symbol} {timeframe}: {e}")
            return None

    def _calculate_rsi_series(self, closes: List[float], period: int) -> List[float]:
        """Return RSI series using Wilder's smoothing for each bar after warmup."""
        n = len(closes)
        if n < period + 1:
            return []
        deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
        gains = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsis: List[float] = []
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100 - (100 / (1 + rs)))

        return rsis
    
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

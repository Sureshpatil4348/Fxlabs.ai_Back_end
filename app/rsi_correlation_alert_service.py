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

class RSICorrelationAlertService:
    """
    RSI Correlation Alert Service for monitoring RSI correlation conditions
    Supports both RSI Threshold mode and Real Correlation mode
    """
    
    def __init__(self):
        self.supabase_url = os.environ.get("SUPABASE_URL", "https://hyajwhtkwldrmlhfiuwg.supabase.co")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY")
        self.last_triggered_alerts: Dict[str, datetime] = {}  # Track last trigger time per alert
    
    def _should_trigger_alert(self, alert: Dict[str, Any]) -> bool:
        """Check if alert should be triggered based on alert_frequency setting"""
        alert_id = alert.get("id")
        if not alert_id:
            return True  # If no ID, allow trigger (shouldn't happen in normal operation)
        
        alert_frequency = alert.get("alert_frequency", "once")
        
        # If never triggered before, allow trigger
        if alert_id not in self.last_triggered_alerts:
            return True
        
        last_triggered = self.last_triggered_alerts[alert_id]
        now = datetime.now(timezone.utc)
        
        # Determine cooldown period based on frequency
        if alert_frequency == "once":
            return False  # Once means never trigger again after first trigger
        elif alert_frequency == "hourly":
            cooldown_duration = timedelta(hours=1)
        elif alert_frequency == "daily":
            cooldown_duration = timedelta(days=1)
        elif alert_frequency == "weekly":
            cooldown_duration = timedelta(weeks=1)
        else:
            # Default to 5 minutes for unknown frequencies
            cooldown_duration = timedelta(minutes=5)
        
        return now - last_triggered >= cooldown_duration
        
    async def check_rsi_correlation_alerts(self, tick_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check all RSI correlation alerts against current tick data"""
        
        try:
            # Get all active RSI correlation alerts from cache
            all_alerts = await alert_cache.get_all_alerts()
            
            triggered_alerts = []
            total_correlation_alerts = 0
            
            for user_id, user_alerts in all_alerts.items():
                for alert in user_alerts:
                    if alert.get("type") == "rsi_correlation" and alert.get("is_active", True):
                        total_correlation_alerts += 1
                        alert_id = alert.get("id")
                        alert_name = alert.get("alert_name", "Unknown")
                        user_email = alert.get("user_email", "Unknown")
                        calculation_mode = alert.get("calculation_mode", "rsi_threshold")
                        
                        if not alert_id:
                            logger.warning(f"⚠️ Alert {alert_name} has no ID, skipping")
                            continue
                        
                        # Check if this alert should be triggered based on frequency
                        if not self._should_trigger_alert(alert):
                            continue
                        
                        # Check if this alert should be triggered
                        trigger_result = await self._check_single_rsi_correlation_alert(alert, tick_data)
                        
                        if trigger_result:
                            logger.info(f"🚨 ALERT TRIGGERED: {alert_name} (ID: {alert_id}) for user {user_email}")
                            logger.info(f"   Triggered pairs: {len(trigger_result.get('triggered_pairs', []))}")
                            
                            triggered_alerts.append(trigger_result)
                            
                            # Update last triggered time immediately after determining trigger
                            self.last_triggered_alerts[alert_id] = datetime.now(timezone.utc)
                            
                            # Send email notification if configured
                            if "email" in alert.get("notification_methods", []):
                                logger.info(f"📧 Sending email notification for alert {alert_name} to {user_email}")
                                await self._send_rsi_correlation_alert_notification(trigger_result)
                            else:
                                logger.info(f"📧 Email notification not configured for alert {alert_name}")
            
            # Only log summary if there are alerts to process or triggers occurred
            if total_correlation_alerts > 0:
                if len(triggered_alerts) > 0:
                    logger.info(f"📊 RSI Correlation Alert Check Complete: {total_correlation_alerts} alerts processed, {len(triggered_alerts)} triggered")
                else:
                    # Only log debug level when no triggers to reduce noise
                    logger.debug(f"📊 RSI Correlation Alert Check Complete: {total_correlation_alerts} alerts processed, 0 triggered")
            
            return triggered_alerts
            
        except Exception as e:
            logger.error(f"❌ Error checking RSI correlation alerts: {e}")
            return []
    
    async def _check_single_rsi_correlation_alert(self, alert: Dict[str, Any], tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check a single RSI correlation alert against current market data"""
        
        try:
            alert_id = alert.get("id")
            alert_name = alert.get("alert_name")
            # Support both field names for backward compatibility
            correlation_pairs = alert.get("pairs", alert.get("correlation_pairs", []))
            timeframes = alert.get("timeframes", ["1H"])
            calculation_mode = alert.get("calculation_mode", "rsi_threshold")
            alert_conditions = alert.get("alert_conditions", [])
            
            triggered_pairs = []
            
            # Check each correlation pair and timeframe
            for pair in correlation_pairs:
                if not isinstance(pair, list) or len(pair) != 2:
                    continue
                    
                symbol1, symbol2 = pair[0], pair[1]
                
                for timeframe in timeframes:
                    # Get market data for both symbols under per-pair locks (stable order to avoid deadlocks)
                    k1 = f"{symbol1}:{timeframe}"
                    k2 = f"{symbol2}:{timeframe}"
                    if k1 == k2:
                        async with pair_locks.acquire(k1):
                            market_data1 = self._get_market_data_for_symbol(symbol1, timeframe, tick_data)
                            market_data2 = self._get_market_data_for_symbol(symbol2, timeframe, tick_data)
                    else:
                        a, b = sorted([k1, k2])
                        async with pair_locks.acquire(a):
                            async with pair_locks.acquire(b):
                                market_data1 = self._get_market_data_for_symbol(symbol1, timeframe, tick_data)
                                market_data2 = self._get_market_data_for_symbol(symbol2, timeframe, tick_data)

                    if not market_data1 or not market_data2:
                        continue

                    # Stale-bar protection for both symbols
                    if self._is_stale_market(market_data1, timeframe) or self._is_stale_market(market_data2, timeframe):
                        continue

                    # Warm-up: ensure sufficient bars for RSI calculations (period + 5)
                    rsi_period = alert.get("rsi_period", 14)
                    if not await self._has_warmup_bars(symbol1, timeframe, rsi_period + 5):
                        continue
                    if not await self._has_warmup_bars(symbol2, timeframe, rsi_period + 5):
                        continue
                    
                    # Process based on calculation mode
                    if calculation_mode == "rsi_threshold":
                        trigger_result = await self._check_rsi_threshold_mode(
                            alert, symbol1, symbol2, timeframe, market_data1, market_data2, alert_conditions
                        )
                    elif calculation_mode == "real_correlation":
                        trigger_result = await self._check_real_correlation_mode(
                            alert, symbol1, symbol2, timeframe, market_data1, market_data2, alert_conditions
                        )
                    else:
                        continue
                    
                    if trigger_result:
                        triggered_pairs.append(trigger_result)
            
            if triggered_pairs:
                return {
                    "alert_id": alert_id,
                    "alert_name": alert_name,
                    "user_id": alert.get("user_id"),
                    "user_email": alert.get("user_email"),
                    "calculation_mode": calculation_mode,
                    "triggered_pairs": triggered_pairs,
                    "alert_config": alert,
                    "triggered_at": datetime.now(timezone.utc).isoformat()
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking single RSI correlation alert: {e}")
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

    async def _has_warmup_bars(self, symbol: str, timeframe: str, bars_required: int) -> bool:
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as TF
            tf_map = {"1M": TF.M1, "5M": TF.M5, "15M": TF.M15, "30M": TF.M30, "1H": TF.H1, "4H": TF.H4, "1D": TF.D1, "1W": TF.W1}
            mtf = tf_map.get(timeframe)
            if not mtf:
                return False
            bars = get_ohlc_data(symbol, mtf, bars_required)
            return len(bars) >= bars_required
        except Exception:
            return False
    
    async def _check_rsi_threshold_mode(
        self, 
        alert: Dict[str, Any], 
        symbol1: str, 
        symbol2: str, 
        timeframe: str,
        market_data1: Dict[str, Any], 
        market_data2: Dict[str, Any],
        alert_conditions: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Check RSI threshold mode conditions"""
        
        try:
            # RSI settings
            rsi_period = alert.get("rsi_period", 14)
            rsi_overbought = alert.get("rsi_overbought_threshold", 70)
            rsi_oversold = alert.get("rsi_oversold_threshold", 30)
            
            # Calculate RSI for both symbols
            rsi1 = await self._calculate_rsi(market_data1, rsi_period)
            rsi2 = await self._calculate_rsi(market_data2, rsi_period)
            
            if rsi1 is None or rsi2 is None:
                return None
            
            # Check RSI threshold conditions
            trigger_condition = self._check_rsi_threshold_conditions(
                rsi1, rsi2, alert_conditions, rsi_overbought, rsi_oversold
            )
            
            if trigger_condition:
                return {
                    "symbol1": symbol1,
                    "symbol2": symbol2,
                    "timeframe": timeframe,
                    "rsi1": round(rsi1, 2),
                    "rsi2": round(rsi2, 2),
                    "trigger_condition": trigger_condition,
                    "current_price1": market_data1.get("close", 0),
                    "current_price2": market_data2.get("close", 0),
                    "price_change1": self._calculate_price_change_percent(market_data1),
                    "price_change2": self._calculate_price_change_percent(market_data2),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking RSI threshold mode: {e}")
            return None
    
    async def _check_real_correlation_mode(
        self, 
        alert: Dict[str, Any], 
        symbol1: str, 
        symbol2: str, 
        timeframe: str,
        market_data1: Dict[str, Any], 
        market_data2: Dict[str, Any],
        alert_conditions: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Check real correlation mode conditions"""
        
        try:
            # Correlation settings
            correlation_window = alert.get("correlation_window", 50)
            strong_threshold = alert.get("strong_correlation_threshold", 0.70)
            moderate_threshold = alert.get("moderate_correlation_threshold", 0.30)
            weak_threshold = alert.get("weak_correlation_threshold", 0.15)
            
            # Calculate correlation between the two symbols
            correlation_value = await self._calculate_correlation(
                market_data1, market_data2, correlation_window
            )
            
            if correlation_value is None:
                return None
            
            # Check correlation conditions
            trigger_condition = self._check_correlation_conditions(
                correlation_value, alert_conditions, strong_threshold, moderate_threshold, weak_threshold
            )
            
            if trigger_condition:
                return {
                    "symbol1": symbol1,
                    "symbol2": symbol2,
                    "timeframe": timeframe,
                    "correlation_value": round(correlation_value, 3),
                    "trigger_condition": trigger_condition,
                    "current_price1": market_data1.get("close", 0),
                    "current_price2": market_data2.get("close", 0),
                    "price_change1": self._calculate_price_change_percent(market_data1),
                    "price_change2": self._calculate_price_change_percent(market_data2),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking real correlation mode: {e}")
            return None
    
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
    
    async def _calculate_correlation(self, market_data1: Dict[str, Any], market_data2: Dict[str, Any], window: int = 50) -> Optional[float]:
        """Calculate Pearson correlation of returns over the last `window` bars using MT5 OHLC data.

        Falls back to None if insufficient data is available.
        """
        try:
            symbol1 = market_data1.get("symbol")
            symbol2 = market_data2.get("symbol")
            timeframe = market_data1.get("timeframe")
            if not symbol1 or not symbol2 or not timeframe:
                return None

            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as TF
            tf_map = {"1M": TF.M1, "5M": TF.M5, "15M": TF.M15, "30M": TF.M30, "1H": TF.H1, "4H": TF.H4, "1D": TF.D1, "1W": TF.W1}
            mtf = tf_map.get(timeframe)
            if not mtf:
                return None

            # Need window+1 closes to compute window returns
            count = max(window + 5, window + 1)
            ohlc1 = get_ohlc_data(symbol1, mtf, count)
            ohlc2 = get_ohlc_data(symbol2, mtf, count)
            if not ohlc1 or not ohlc2:
                return None

            closes1 = [bar.close for bar in ohlc1][- (window + 1):]
            closes2 = [bar.close for bar in ohlc2][- (window + 1):]
            n = min(len(closes1), len(closes2))
            if n < window + 1:
                return None
            closes1 = closes1[-n:]
            closes2 = closes2[-n:]

            # Compute simple returns
            r1 = [(closes1[i] / closes1[i - 1] - 1.0) for i in range(1, len(closes1))]
            r2 = [(closes2[i] / closes2[i - 1] - 1.0) for i in range(1, len(closes2))]
            m = min(len(r1), len(r2), window)
            if m < 2:
                return None
            r1 = r1[-m:]
            r2 = r2[-m:]

            # Pearson correlation
            mean1 = sum(r1) / m
            mean2 = sum(r2) / m
            num = sum((a - mean1) * (b - mean2) for a, b in zip(r1, r2))
            den1 = (sum((a - mean1) ** 2 for a in r1)) ** 0.5
            den2 = (sum((b - mean2) ** 2 for b in r2)) ** 0.5
            if den1 == 0 or den2 == 0:
                return 0.0
            corr = num / (den1 * den2)
            # Clamp to [-1, 1]
            if corr > 1:
                corr = 1.0
            if corr < -1:
                corr = -1.0
            return float(round(corr, 6))
        except Exception as e:
            logger.error(f"❌ Error calculating correlation: {e}")
            return None
    
    def _check_rsi_threshold_conditions(
        self, 
        rsi1: float, 
        rsi2: float, 
        alert_conditions: List[str],
        rsi_overbought: int,
        rsi_oversold: int
    ) -> Optional[str]:
        """Check RSI threshold mode conditions"""
        
        try:
            for condition in alert_conditions:
                if condition == "positive_mismatch":
                    # Both RSI in opposite zones (one overbought, one oversold)
                    if (rsi1 >= rsi_overbought and rsi2 <= rsi_oversold) or (rsi1 <= rsi_oversold and rsi2 >= rsi_overbought):
                        return "positive_mismatch"
                
                elif condition == "negative_mismatch":
                    # Both RSI in same extreme zone (both overbought or both oversold)
                    if (rsi1 >= rsi_overbought and rsi2 >= rsi_overbought) or (rsi1 <= rsi_oversold and rsi2 <= rsi_oversold):
                        return "negative_mismatch"
                
                elif condition == "neutral_break":
                    # Both RSI in neutral zone (between oversold and overbought)
                    if rsi_oversold < rsi1 < rsi_overbought and rsi_oversold < rsi2 < rsi_overbought:
                        return "neutral_break"
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking RSI threshold conditions: {e}")
            return None
    
    def _check_correlation_conditions(
        self, 
        correlation_value: float, 
        alert_conditions: List[str],
        strong_threshold: float,
        moderate_threshold: float,
        weak_threshold: float
    ) -> Optional[str]:
        """Check real correlation mode conditions"""
        
        try:
            abs_correlation = abs(correlation_value)
            
            for condition in alert_conditions:
                if condition == "strong_positive":
                    if correlation_value >= strong_threshold:
                        return "strong_positive"
                
                elif condition == "strong_negative":
                    if correlation_value <= -strong_threshold:
                        return "strong_negative"
                
                elif condition == "weak_correlation":
                    if abs_correlation <= weak_threshold:
                        return "weak_correlation"
                
                elif condition == "correlation_break":
                    # Correlation breaking from strong to moderate or weak
                    if strong_threshold > abs_correlation >= moderate_threshold:
                        return "correlation_break"
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking correlation conditions: {e}")
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
    
    async def _send_rsi_correlation_alert_notification(self, trigger_data: Dict[str, Any]):
        """Send RSI correlation alert notification via email"""
        
        try:
            user_email = trigger_data.get("user_email")
            alert_name = trigger_data.get("alert_name")
            alert_id = trigger_data.get("alert_id")
            calculation_mode = trigger_data.get("calculation_mode")
            triggered_pairs = trigger_data.get("triggered_pairs", [])
            alert_config = trigger_data.get("alert_config", {})
            
            logger.info(f"📧 Preparing RSI Correlation alert email for user: {user_email}")
            logger.info(f"   Alert: {alert_name} (ID: {alert_id})")
            logger.info(f"   Mode: {calculation_mode}")
            logger.info(f"   Triggered pairs: {len(triggered_pairs)}")
            
            if not user_email:
                logger.warning("⚠️ No user email found for RSI correlation alert notification")
                return
            
            # Log triggered pairs details
            for i, pair in enumerate(triggered_pairs, 1):
                symbol1 = pair.get('symbol1', pair.get('symbol', 'Unknown'))
                symbol2 = pair.get('symbol2', 'Unknown')
                condition = pair.get('trigger_condition', 'Unknown')
                logger.info(f"   Pair {i}: {symbol1}-{symbol2} - {condition}")
            
            # Send email notification
            logger.info(f"📤 Sending RSI Correlation alert email to {user_email}...")
            success = await email_service.send_rsi_correlation_alert(
                user_email=user_email,
                alert_name=alert_name,
                calculation_mode=calculation_mode,
                triggered_pairs=triggered_pairs,
                alert_config=alert_config
            )
            
            if success:
                logger.info(f"✅ RSI Correlation alert email sent successfully to {user_email}")
                logger.info(f"   Alert: {alert_name} (ID: {alert_id})")
                logger.info(f"   Mode: {calculation_mode}")
                logger.info(f"   Pairs: {len(triggered_pairs)}")
            else:
                logger.warning(f"⚠️ Failed to send RSI Correlation alert email to {user_email}")
                logger.warning(f"   Alert: {alert_name} (ID: {alert_id})")
                logger.warning(f"   Mode: {calculation_mode}")
            
            # Log the trigger in database
            await self._log_rsi_correlation_alert_trigger(trigger_data)
            
        except Exception as e:
            logger.error(f"❌ Error sending RSI correlation alert notification: {e}")
    
    async def _log_rsi_correlation_alert_trigger(self, trigger_data: Dict[str, Any]):
        """Log RSI correlation alert trigger to database"""
        
        try:
            if not self.supabase_service_key:
                logger.warning("Supabase service key not configured, skipping trigger logging")
                return
            
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.supabase_url}/rest/v1/rsi_correlation_alert_triggers"
            
            # Log each triggered pair
            for pair_data in trigger_data.get("triggered_pairs", []):
                trigger_record = {
                    "alert_id": trigger_data.get("alert_id"),
                    "trigger_condition": pair_data.get("trigger_condition"),
                    "symbol1": pair_data.get("symbol1"),
                    "symbol2": pair_data.get("symbol2"),
                    "timeframe": pair_data.get("timeframe"),
                    "rsi1": pair_data.get("rsi1"),
                    "rsi2": pair_data.get("rsi2"),
                    "correlation_value": pair_data.get("correlation_value"),
                    "current_price1": pair_data.get("current_price1"),
                    "current_price2": pair_data.get("current_price2"),
                    "price_change1": pair_data.get("price_change1"),
                    "price_change2": pair_data.get("price_change2"),
                    "triggered_at": datetime.now(timezone.utc).isoformat()
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=trigger_record) as response:
                        if response.status not in [200, 201]:
                            logger.error(f"Failed to log RSI correlation trigger: {response.status}")
            
        except Exception as e:
            logger.error(f"❌ Error logging RSI correlation alert trigger: {e}")
    
    async def create_rsi_correlation_alert(self, alert_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new RSI correlation alert in Supabase"""
        
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.supabase_url}/rest/v1/rsi_correlation_alerts"
            
            # Prepare alert data for Supabase
            supabase_data = {
                "alert_name": alert_data.get("alert_name"),
                "user_email": alert_data.get("user_email"),
                "correlation_pairs": alert_data.get("pairs", alert_data.get("correlation_pairs", [])),
                "timeframes": alert_data.get("timeframes", ["1H"]),
                "calculation_mode": alert_data.get("calculation_mode", "rsi_threshold"),
                "rsi_period": alert_data.get("rsi_period", 14),
                "rsi_overbought_threshold": alert_data.get("rsi_overbought_threshold", 70),
                "rsi_oversold_threshold": alert_data.get("rsi_oversold_threshold", 30),
                "correlation_window": alert_data.get("correlation_window", 50),
                "alert_conditions": alert_data.get("alert_conditions", []),
                "strong_correlation_threshold": alert_data.get("strong_correlation_threshold", 0.70),
                "moderate_correlation_threshold": alert_data.get("moderate_correlation_threshold", 0.30),
                "weak_correlation_threshold": alert_data.get("weak_correlation_threshold", 0.15),
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
                        logger.info(f"✅ RSI correlation alert created: {result.get('id')}")
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to create RSI correlation alert: {response.status} - {error_text}")
                        return None
            
        except Exception as e:
            logger.error(f"❌ Error creating RSI correlation alert: {e}")
            return None

# Create global instance
rsi_correlation_alert_service = RSICorrelationAlertService()

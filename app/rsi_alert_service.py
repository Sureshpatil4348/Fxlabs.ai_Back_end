import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
import aiohttp
import json
import logging
from .logging_config import configure_logging
from .alert_logging import log_debug, log_info, log_warning, log_error

from .email_service import email_service
from .alert_cache import alert_cache
from .concurrency import pair_locks

# Configure logging with timestamps
configure_logging()
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
        # Crossing policy: closed-bar only; immediate on crossing; threshold-level re-arm
        self.only_new_bars = 0           # Not used; preserved for compatibility
        self.confirmation_bars = 0       # No additional bar confirmation
        # Legacy hysteresis thresholds (unused in threshold-level re-arm)
        self.rearm_overbought = 65
        self.rearm_oversold = 35
        # In-memory hysteresis state: key -> { 'armed_overbought': bool, 'armed_oversold': bool }
        self._hysteresis_map: Dict[str, Dict[str, bool]] = {}
        # Track last evaluated closed bar per (symbol, timeframe) for bar-close policy
        self._last_closed_bar_ts: Dict[str, int] = {}
        # Per (alert, symbol, timeframe, side) cooldown (minutes)
        self.pair_cooldown_minutes_default = 30
        self._pair_cooldowns: Dict[str, datetime] = {}
    
    def _should_trigger_alert(self, alert_id: str, cooldown_seconds: int = None) -> bool:
        """Check if alert should be triggered based on cooldown period"""
        if cooldown_seconds is None:
            cooldown_seconds = self.default_cooldown_seconds
        
        if alert_id not in self.last_triggered_alerts:
            return True
        
        last_triggered = self.last_triggered_alerts[alert_id]
        cooldown_duration = timedelta(seconds=cooldown_seconds)
        
        return datetime.now(timezone.utc) - last_triggered >= cooldown_duration

    def _allow_by_alert_frequency(self, alert: Dict[str, Any]) -> bool:
        """Return True if allowed to trigger based on alert_frequency (once|hourly|daily).

        Uses per-alert last_triggered timestamps.
        """
        try:
            alert_id = alert.get("id")
            if not alert_id:
                return True
            alert_frequency = (alert.get("alert_frequency") or "once").lower()
            # First time: always allow
            last_triggered = self.last_triggered_alerts.get(alert_id)
            if last_triggered is None:
                return True
            now = datetime.now(timezone.utc)
            if alert_frequency == "once":
                return False
            if alert_frequency == "hourly":
                return (now - last_triggered) >= timedelta(hours=1)
            if alert_frequency == "daily":
                return (now - last_triggered) >= timedelta(days=1)
            # Default safety: small window
            return (now - last_triggered) >= timedelta(minutes=5)
        except Exception:
            return True
        
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
                        # Gather config snapshot for structured start log
                        pairs_cfg = alert.get("pairs", []) or []
                        timeframes_cfg = alert.get("timeframes", ["1H"]) or ["1H"]
                        rsi_period_cfg = alert.get("rsi_period", 14)
                        rsi_ob_cfg = alert.get("rsi_overbought_threshold", 70)
                        rsi_os_cfg = alert.get("rsi_oversold_threshold", 30)
                        conditions_cfg = alert.get("alert_conditions", []) or []
                        cooldown_cfg = alert.get("cooldown_minutes")
                        log_debug(
                            logger,
                            "alert_eval_start",
                            alert_type="rsi",
                            alert_id=alert_id,
                            alert_name=alert_name,
                            user_email=user_email,
                            pairs=len(pairs_cfg),
                            timeframes=timeframes_cfg,
                            rsi_period=int(rsi_period_cfg),
                            rsi_overbought=int(rsi_ob_cfg),
                            rsi_oversold=int(rsi_os_cfg),
                            conditions=conditions_cfg,
                            cooldown_minutes=cooldown_cfg,
                        )
                        
                        if not alert_id:
                            logger.warning(f"⚠️ Alert {alert_name} has no ID, skipping")
                            continue
                        
                        # Check if this alert should be triggered
                        trigger_result = await self._check_single_rsi_alert(alert, tick_data)
                        
                        if trigger_result:
                            logger.info(f"🚨 ALERT TRIGGERED: {alert_name} (ID: {alert_id}) for user {user_email}")
                            logger.info(f"   Triggered pairs: {len(trigger_result.get('triggered_pairs', []))}")
                            
                            triggered_alerts.append(trigger_result)
                            
                            # Send email notification if configured
                            if "email" in alert.get("notification_methods", []):
                                logger.info(f"📧 Sending email notification for alert {alert_name} to {user_email}")
                                await self._send_rsi_alert_notification(trigger_result)
                            else:
                                logger.info(f"📧 Email notification not configured for alert {alert_name}")
                        # Structured end log (regardless of triggers)
                        log_debug(
                            logger,
                            "alert_eval_end",
                            alert_type="rsi",
                            alert_id=alert_id,
                            alert_name=alert_name,
                            triggered_count=int(len(trigger_result.get("triggered_pairs", [])) if trigger_result else 0),
                        )
            
            # Only log summary if there are alerts to process or triggers occurred
            if total_rsi_alerts > 0:
                if len(triggered_alerts) > 0:
                    logger.info(f"📊 RSI Alert Check Complete: {total_rsi_alerts} alerts processed, {len(triggered_alerts)} triggered")
                    log_info(
                        logger,
                        "rsi_alerts_check_summary",
                        processed=total_rsi_alerts,
                        triggered=len(triggered_alerts),
                    )
                else:
                    # Only log debug level when no triggers to reduce noise
                    logger.debug(f"📊 RSI Alert Check Complete: {total_rsi_alerts} alerts processed, 0 triggered")
                    log_debug(
                        logger,
                        "rsi_alerts_check_summary",
                        processed=total_rsi_alerts,
                        triggered=0,
                    )
            
            return triggered_alerts
            
        except Exception as e:
            logger.error(f"❌ Error checking RSI alerts: {e}")
            return []
    
    async def _check_single_rsi_alert(self, alert: Dict[str, Any], tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check a single RSI alert against current market data"""
        
        try:
            alert_id = alert.get("id")
            alert_name = alert.get("alert_name")
            # Global alert frequency gating (once/hourly/daily)
            if not self._allow_by_alert_frequency(alert):
                return None
            pairs = alert.get("pairs", [])
            timeframes = alert.get("timeframes", ["1H"])
            alert_conditions = alert.get("alert_conditions", [])
            
            # RSI settings
            rsi_period = alert.get("rsi_period", 14)
            rsi_overbought = alert.get("rsi_overbought_threshold", 70)
            rsi_oversold = alert.get("rsi_oversold_threshold", 30)
            
            # Quiet hours suppression removed per spec

            triggered_pairs = []
            total_checks = 0
            
            # Check each pair and timeframe
            for symbol in pairs:
                for timeframe in timeframes:
                    total_checks += 1
                    logger.debug(f"🔍 Checking {symbol} {timeframe} (Check {total_checks})")
                    log_debug(
                        logger,
                        "evaluation_start",
                        alert_id=alert_id,
                        symbol=symbol,
                        timeframe=timeframe,
                        check=total_checks,
                    )

                    key = f"{symbol}:{timeframe}"
                    async with pair_locks.acquire(key):
                        # Get market data for this symbol/timeframe
                        market_data = self._get_market_data_for_symbol(symbol, timeframe, tick_data)

                        if not market_data:
                            logger.warning(f"⚠️ No market data available for {symbol} {timeframe}")
                            log_warning(
                                logger,
                                "market_data_missing",
                                symbol=symbol,
                                timeframe=timeframe,
                            )
                            continue

                        # Stale-bar protection
                        if self._is_stale_market(market_data, timeframe):
                            logger.debug(f"⏭️ Stale data skipped for {symbol} {timeframe}")
                            log_debug(
                                logger,
                                "market_data_stale",
                                symbol=symbol,
                                timeframe=timeframe,
                                ts=market_data.get("timestamp"),
                            )
                            continue

                        # Enforce closed-bar policy always (RSI-closed only)
                        last_ts = await self._get_last_closed_bar_ts(symbol, timeframe)
                        if last_ts is None:
                            logger.debug(f"⏭️ Skipping {symbol} {timeframe}: unknown last closed bar (closed policy)")
                            log_debug(
                                logger,
                                "closed_bar_unknown",
                                symbol=symbol,
                                timeframe=timeframe,
                            )
                            continue
                        last_key = f"{symbol}:{timeframe}"
                        prev_ts = self._last_closed_bar_ts.get(last_key)
                        if prev_ts is not None and prev_ts == last_ts:
                            # Already evaluated for current closed bar
                            continue
                        self._last_closed_bar_ts[last_key] = last_ts

                        logger.debug(f"✅ Market data retrieved for {symbol} {timeframe}: {market_data.get('data_source', 'Unknown')}")
                        log_debug(
                            logger,
                            "market_data_loaded",
                            symbol=symbol,
                            timeframe=timeframe,
                            source=market_data.get("data_source", "Unknown"),
                        )

                        # Calculate RSI
                        rsi_value = await self._calculate_rsi(market_data, rsi_period)

                        if rsi_value is None:
                            logger.warning(f"⚠️ Could not calculate RSI for {symbol} {timeframe}")
                            log_warning(
                                logger,
                                "rsi_calculation_failed",
                                symbol=symbol,
                                timeframe=timeframe,
                            )
                            continue

                        logger.debug(f"📊 RSI calculated for {symbol} {timeframe}: {rsi_value:.2f}")
                        log_debug(
                            logger,
                            "rsi_calculated",
                            symbol=symbol,
                            timeframe=timeframe,
                            rsi=round(float(rsi_value), 2),
                        )

                        # Warm-up: ensure we have prev and current closed-bar RSI
                        bars_needed = 3
                        rsis = await self._get_recent_rsi_series(symbol, timeframe, rsi_period, bars_needed)
                        if not rsis or len(rsis) < bars_needed:
                            logger.debug(f"⏳ Warm-up insufficient for {symbol} {timeframe} (need ≥{bars_needed} RSI points)")
                            log_debug(
                                logger,
                                "warmup_insufficient",
                                symbol=symbol,
                                timeframe=timeframe,
                                required=bars_needed,
                            )
                            continue

                        # RFI support removed to align with core spec

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

                        # RFI-only conditions removed (stick to core RSI spec)

                        if trigger_condition:
                            logger.info(f"🚨 CONDITION MATCHED: {symbol} {timeframe} - {trigger_condition}")
                            logger.info(f"   RSI: {rsi_value:.2f}")
                            log_info(
                                logger,
                                "rsi_cross_event",
                                alert_id=alert_id,
                                symbol=symbol,
                                timeframe=timeframe,
                                condition=trigger_condition,
                                rsi=round(float(rsi_value), 2),
                            )

                            # Enforce per (alert, symbol, timeframe, side) cooldown
                            side = "overbought" if "overbought" in trigger_condition else (
                                "oversold" if "oversold" in trigger_condition else "neutral"
                            )
                            if side != "neutral" and not self._allow_by_pair_cooldown(alert, alert_id, symbol, timeframe, side):
                                logger.debug(f"⏳ Cooldown active for {symbol} {timeframe} {side}, skipping")
                                log_debug(
                                    logger,
                                    "pair_cooldown_block",
                                    alert_id=alert_id,
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    side=side,
                                )
                                continue

                            triggered_pairs.append({
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "rsi_value": round(rsi_value, 2),
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
                # Update last triggered time for alert frequency enforcement
                self.last_triggered_alerts[alert_id] = datetime.now(timezone.utc)
                log_info(
                    logger,
                    "rsi_alert_triggers",
                    alert_id=alert_id,
                    alert_name=alert_name,
                    count=len(triggered_pairs),
                )
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
            log_error(
                logger,
                "rsi_single_check_error",
                alert_id=alert.get("id"),
                error=str(e),
            )
            return None

    # Quiet hours helpers removed per spec

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
            from .mt5_utils import get_ohlc_data, get_current_tick
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
            ohlc_data = get_ohlc_data(symbol, mt5_timeframe, 50)
            if not ohlc_data:
                return None
            latest_bar = ohlc_data[-1]
            current_tick = get_current_tick(symbol)
            log_debug(
                logger,
                "market_data_loaded",
                symbol=symbol,
                timeframe=timeframe,
                source="MT5_REAL",
            )
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "open": latest_bar.open,
                "high": latest_bar.high,
                "low": latest_bar.low,
                "close": latest_bar.close,
                "volume": latest_bar.volume,
                "timestamp": latest_bar.time_iso,
                "bid": current_tick.bid if current_tick else None,
                "ask": current_tick.ask if current_tick else None,
                "data_source": "MT5_REAL"
            }
        except Exception as e:
            logger.error(f"❌ Error getting market data for {symbol}: {e}")
            return None
    
    async def _calculate_rsi(self, market_data: Dict[str, Any], period: int = 14) -> Optional[float]:
        """Calculate real RSI using historical OHLC data from MT5"""
        
        try:
            if market_data.get("data_source") != "MT5_REAL":
                return None
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe
            symbol = market_data["symbol"]
            timeframe = market_data["timeframe"]
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
            ohlc_data = get_ohlc_data(symbol, mt5_timeframe, period + 10)
            if not ohlc_data or len(ohlc_data) < period + 1:
                return None
            closes = [bar.close for bar in ohlc_data]
            rsi_value = self._calculate_rsi_from_closes(closes, period)
            return rsi_value
        except Exception as e:
            log_error(
                logger,
                "rsi_calculation_error",
                error=str(e),
            )
            return None

    def _allow_by_pair_cooldown(self, alert: Dict[str, Any], alert_id: str, symbol: str, timeframe: str, side: str) -> bool:
        """Check/update per (alert, symbol, timeframe, side) cooldown window.

        Returns True if allowed to trigger now; updates last time on allow.
        """
        try:
            cd_min = alert.get("cooldown_minutes")
            cooldown_minutes = int(cd_min) if cd_min is not None else self.pair_cooldown_minutes_default
        except Exception:
            cooldown_minutes = self.pair_cooldown_minutes_default

        key = f"{alert_id}:{symbol}:{timeframe}:{side}"
        now = datetime.now(timezone.utc)
        last = self._pair_cooldowns.get(key)
        if last is not None:
            delta = (now - last).total_seconds() / 60.0
            if delta < cooldown_minutes:
                return False
        # Allowed -> update last
        self._pair_cooldowns[key] = now
        return True
    
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
    
    # RFI helpers removed (stick to core RSI spec)
    
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
            
            logger.info(
                f"📧 Scheduling RSI email -> user={user_email}, alert={alert_name}, pairs={len(triggered_pairs)}"
            )
            log_info(
                logger,
                "email_queue",
                alert_type="rsi",
                alert_id=alert_id,
                user_email=user_email,
                pairs=len(triggered_pairs),
            )
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
                log_info(
                    logger,
                    "email_send_success",
                    alert_type="rsi",
                    alert_id=alert_id,
                    user_email=user_email,
                )
                logger.info(f"   Alert: {alert_name} (ID: {alert_id})")
                logger.info(f"   Pairs: {len(triggered_pairs)}")
            else:
                logger.warning(f"⚠️ Failed to send RSI alert email to {user_email}")
                log_warning(
                    logger,
                    "email_send_failed",
                    alert_type="rsi",
                    alert_id=alert_id,
                    user_email=user_email,
                )
                logger.warning(f"   Alert: {alert_name} (ID: {alert_id})")
                # Email diagnostics removed per spec
            
            # Log the trigger in database
            logger.info(f"📝 Logging RSI alert trigger to database...")
            await self._log_rsi_alert_trigger(trigger_data)
            logger.info(f"✅ RSI alert trigger logged to database")
            log_info(
                logger,
                "db_trigger_logged",
                alert_type="rsi",
                alert_id=alert_id,
                pairs=len(triggered_pairs),
            )
            
        except Exception as e:
            log_error(
                logger,
                "rsi_email_queue_error",
                error=str(e),
            )
    
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
                    "current_price": pair_data.get("current_price"),
                    "price_change_percent": pair_data.get("price_change_percent"),
                    "triggered_at": datetime.now(timezone.utc).isoformat()
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=trigger_record) as response:
                        if response.status not in [200, 201]:
                            log_error(
                                logger,
                                "db_trigger_log_failed",
                                status=response.status,
                                alert_type="rsi",
                                alert_id=trigger_data.get("alert_id"),
                                symbol=pair_data.get("symbol"),
                                timeframe=pair_data.get("timeframe"),
                            )
                        else:
                            log_info(
                                logger,
                                "db_trigger_logged",
                                alert_type="rsi",
                                alert_id=trigger_data.get("alert_id"),
                                symbol=pair_data.get("symbol"),
                                timeframe=pair_data.get("timeframe"),
                            )
            
        except Exception as e:
            log_error(
                logger,
                "db_trigger_log_error",
                alert_type="rsi",
                error=str(e),
            )

    async def _get_last_closed_bar_ts(self, symbol: str, timeframe: str) -> Optional[int]:
        """Return timestamp (ms) of the last closed bar using MT5 OHLC data; None if unavailable."""
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as MT5Timeframe
            tf_map = {
                "1M": MT5Timeframe.M1,
                "5M": MT5Timeframe.M5,
                "15M": MT5Timeframe.M15,
                "30M": MT5Timeframe.M30,
                "1H": MT5Timeframe.H1,
                "4H": MT5Timeframe.H4,
                "1D": MT5Timeframe.D1,
                "1W": MT5Timeframe.W1,
            }
            mt5_tf = tf_map.get(timeframe)
            if not mt5_tf:
                return None
            bars = get_ohlc_data(symbol, mt5_tf, 2)
            if not bars:
                return None
            return int(bars[-1].time)
        except Exception as e:
            logger.debug(f"Bar-close ts unavailable for {symbol} {timeframe}: {e}")
            return None
    
    async def _detect_rsi_crossing(
        self,
        alert_id: str,
        symbol: str,
        timeframe: str,
        period: int,
        overbought: int,
        oversold: int,
    ) -> Optional[str]:
        """Detect RSI threshold crossings at current closed bar with threshold-level re-arm.

        Returns one of: "overbought_cross", "oversold_cross", or None.
        """
        try:
            rsis = await self._get_recent_rsi_series(symbol, timeframe, period, bars_needed=3)
            if not rsis or len(rsis) < 2:
                return None

            # Hysteresis arm/disarm state per alert-symbol-timeframe
            key = f"{alert_id}:{symbol}:{timeframe}"
            st = self._hysteresis_map.setdefault(key, {"armed_overbought": True, "armed_oversold": True})

            prev_val = rsis[-2]
            curr_val = rsis[-1]

            # Threshold-level re-arm: re-enable once RSI returns to the opposite side of the threshold
            if not st["armed_overbought"] and curr_val < overbought:
                st["armed_overbought"] = True
            if not st["armed_oversold"] and curr_val > oversold:
                st["armed_oversold"] = True

            # Overbought crossing at current closed bar
            if st["armed_overbought"] and prev_val < overbought and curr_val >= overbought:
                st["armed_overbought"] = False
                return "overbought_cross"

            # Oversold crossing at current closed bar
            if st["armed_oversold"] and prev_val > oversold and curr_val <= oversold:
                st["armed_oversold"] = False
                return "oversold_cross"

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
                "cooldown_minutes": alert_data.get("cooldown_minutes", 30),
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

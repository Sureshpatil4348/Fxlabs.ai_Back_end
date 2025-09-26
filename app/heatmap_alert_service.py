import asyncio
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
import aiohttp
import json
import logging
from .logging_config import configure_logging

from .email_service import email_service
from .alert_cache import alert_cache
from .concurrency import pair_locks

# Configure logging with timestamps
configure_logging()
logger = logging.getLogger(__name__)

class HeatmapAlertService:
    """Service for checking and triggering heatmap alerts"""
    
    def __init__(self):
        self.supabase_url = os.environ.get("SUPABASE_URL", "https://hyajwhtkwldrmlhfiuwg.supabase.co")
        self.supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh5YWp3aHRrd2xkcm1saGZpdXdnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NjI5NjUzNCwiZXhwIjoyMDcxODcyNTM0fQ.UDqYHY5Io0o-fQTswCYQmMdC6UCPQI2gf3aTb9o09SE")
        self.last_triggered_alerts: Dict[str, datetime] = {}  # Track last trigger time per alert
        # Hysteresis controls for Buy Now % (defaults mirror spec: 70/65, 30/35)
        self.hysteresis_buy_margin = 5    # re‑arm BUY after dropping below (buy_min - margin)
        self.hysteresis_sell_margin = 5   # re‑arm SELL after rising above (sell_max + margin)
        # In‑memory per (alert_id, symbol) arming state
        self._hysteresis_map: Dict[str, Dict[str, bool]] = {}
        # Per (alert, symbol, direction) cooldown map
        self.pair_cooldown_minutes_default = 30
        self._pair_cooldowns: Dict[str, datetime] = {}
        
        # Flip detection defaults (Type B Indicator Flip)
        self.flip_only_new_bars = 3      # Only NEW within last K bars
        self.flip_confirmation_bars = 1  # 1-bar confirmation
        # Per (pair, timeframe, indicator) cooldown for flips
        self.flip_cooldown_minutes_default = 30
        self._flip_cooldowns: Dict[str, datetime] = {}
        
    async def check_heatmap_alerts(self, tick_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check all heatmap alerts against current tick data"""
        
        try:
            # Get all active heatmap alerts from cache
            all_alerts = await alert_cache.get_all_alerts()
            
            triggered_alerts = []
            total_heatmap_alerts = 0
            
            for user_id, user_alerts in all_alerts.items():
                for alert in user_alerts:
                    if alert.get("type") == "heatmap" and alert.get("is_active", True):
                        total_heatmap_alerts += 1
                        alert_id = alert.get("id")
                        alert_name = alert.get("alert_name", "Unknown")
                        user_email = alert.get("user_email", "Unknown")
                        
                        if not alert_id:
                            logger.warning(f"⚠️ Alert {alert_name} has no ID, skipping")
                            continue
                        
                        # Check if this alert should be triggered
                        trigger_result = await self._check_single_heatmap_alert(alert, tick_data)
                        
                        if trigger_result:
                            logger.info(f"🚨 ALERT TRIGGERED: {alert_name} (ID: {alert_id}) for user {user_email}")
                            logger.info(f"   Triggered pairs: {len(trigger_result.get('triggered_pairs', []))}")
                            
                            triggered_alerts.append(trigger_result)
                            
                            # Send email notification if configured
                            if "email" in alert.get("notification_methods", []):
                                logger.info(f"📧 Sending email notification for alert {alert_name} to {user_email}")
                                await self._send_alert_notification(trigger_result)
                            else:
                                logger.info(f"📧 Email notification not configured for alert {alert_name}")
            
            # Only log summary if there are alerts to process or triggers occurred
            if total_heatmap_alerts > 0:
                if len(triggered_alerts) > 0:
                    logger.info(f"📊 Heatmap Alert Check Complete: {total_heatmap_alerts} alerts processed, {len(triggered_alerts)} triggered")
                else:
                    # Only log debug level when no triggers to reduce noise
                    logger.debug(f"📊 Heatmap Alert Check Complete: {total_heatmap_alerts} alerts processed, 0 triggered")
            
            return triggered_alerts
            
        except Exception as e:
            logger.error(f"❌ Error checking heatmap alerts: {e}")
            return []
    
    async def _check_single_heatmap_alert(self, alert: Dict[str, Any], tick_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check if a single heatmap alert should be triggered"""
        
        try:
            alert_id = alert.get("id")
            alert_name = alert.get("alert_name")
            
            # Check alert frequency to avoid spam
            if not self._should_trigger_alert(alert_id, alert):
                return None
            
            # Get alert configuration
            pairs = alert.get("pairs", [])
            timeframes = alert.get("timeframes", [])
            buy_threshold_min = alert.get("buy_threshold_min", 70)
            buy_threshold_max = alert.get("buy_threshold_max", 100)
            sell_threshold_min = alert.get("sell_threshold_min", 0)
            sell_threshold_max = alert.get("sell_threshold_max", 30)
            selected_indicators = alert.get("selected_indicators", [])
            
            # Calculate heatmap data for each pair
            triggered_pairs = []
            
            for pair in pairs:
                tf_strengths = {}
                flip_candidates: List[Dict[str, Any]] = []
                for timeframe in timeframes:
                    key = f"{pair}:{timeframe}"
                    async with pair_locks.acquire(key):
                        # Get current market data for the pair
                        market_data = await self._get_market_data(pair, timeframe)
                        if not market_data:
                            continue

                        # Stale-bar protection: skip if latest bar is too old for TF
                        if self._is_stale_market(market_data, timeframe):
                            logger.debug(f"⏭️ Stale data skipped for {pair} {timeframe}")
                            continue

                        # Warm-up: if RSI requested, ensure sufficient lookback bars exist
                        if any(ind.lower() == "rsi" for ind in selected_indicators):
                            has_warmup = await self._has_warmup_bars(pair, timeframe, 20)
                            if not has_warmup:
                                logger.debug(f"⏳ Warm-up insufficient for {pair} {timeframe} (need ≥20 bars)")
                                continue

                        # Calculate indicators and strength
                        strength_data = await self._calculate_indicators_strength(
                            market_data, selected_indicators
                        )
                        if not strength_data:
                            continue
                        tf_strengths[timeframe] = strength_data.get("overall_strength", 50)

                        # Detect indicator flips (Type B) on this timeframe
                        flip = await self._detect_indicator_flips(pair, timeframe, selected_indicators)
                        if flip:
                            flip_candidates.append({
                                "symbol": pair,
                                "timeframe": timeframe,
                                "signal": flip.get("signal"),
                                "indicator": flip.get("indicator"),
                                "trigger_condition": flip.get("condition"),
                                "strength": strength_data.get("overall_strength", 50),
                            })

                # Aggregate across timeframes using style weights
                if not tf_strengths:
                    continue

                trading_style = (alert.get("trading_style") or alert.get("style") or "dayTrader").lower()
                style_weights = self._style_tf_weights(trading_style)
                final_score = self._compute_final_score(tf_strengths, style_weights)
                buy_now_percent = round((final_score + 100) / 2, 2)

                # Optional minimum alignment requirement across TFs (N cells)
                min_alignment = 0
                try:
                    min_alignment = int(alert.get("min_alignment") or 0)
                    if min_alignment < 0:
                        min_alignment = 0
                except Exception:
                    min_alignment = 0

                signal = self._determine_style_signal(
                    buy_now_percent,
                    buy_threshold_min,
                    buy_threshold_max,
                    sell_threshold_min,
                    sell_threshold_max,
                )

                # Enforce minimum alignment if configured
                aligned_buy = [tf for tf, val in tf_strengths.items() if val >= buy_threshold_min]
                aligned_sell = [tf for tf, val in tf_strengths.items() if val <= sell_threshold_max]
                if min_alignment > 0:
                    if signal == "BUY" and len(aligned_buy) < min_alignment:
                        signal = None
                    if signal == "SELL" and len(aligned_sell) < min_alignment:
                        signal = None

                # Apply hysteresis gating (disable repeated triggers while in zone)
                if signal:
                    key = f"{alert_id}:{pair}"
                    if not self._hysteresis_allow_and_update(
                        key,
                        current=buy_now_percent,
                        buy_min=buy_threshold_min,
                        sell_max=sell_threshold_max,
                        direction=signal,
                    ):
                        signal = None

                # Per-pair cooldown (per direction)
                if signal:
                    if not self._allow_by_pair_cooldown(alert, alert_id, pair, signal):
                        signal = None

                if signal:
                    triggered_pairs.append({
                        "symbol": pair,
                        "timeframes": list(tf_strengths.keys()),
                        "final_score": round(final_score, 2),
                        "buy_now_percent": buy_now_percent,
                        "trigger_score": buy_now_percent,
                        "strength": buy_now_percent,  # legacy field for email summaries
                        "signal": signal,
                        "style": trading_style,
                        "aligned_buy": aligned_buy,
                        "aligned_sell": aligned_sell,
                        "min_alignment": min_alignment,
                        "hysteresis_buy_rearm": max(0, buy_threshold_min - self.hysteresis_buy_margin),
                        "hysteresis_sell_rearm": min(100, sell_threshold_max + self.hysteresis_sell_margin),
                        "timeframe": "style-weighted",
                        "price": None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                # Optional gate for Type B flips by style-weighted Buy Now %
                try:
                    gate_enabled = bool(alert.get("gate_by_buy_now"))
                except Exception:
                    gate_enabled = False
                try:
                    gate_buy_min = float(alert.get("gate_buy_min")) if alert.get("gate_buy_min") is not None else 60.0
                except Exception:
                    gate_buy_min = 60.0
                try:
                    gate_sell_max = float(alert.get("gate_sell_max")) if alert.get("gate_sell_max") is not None else 40.0
                except Exception:
                    gate_sell_max = 40.0

                for fc in flip_candidates:
                    sig = fc.get("signal")
                    if gate_enabled:
                        if sig == "BUY" and not (buy_now_percent >= gate_buy_min):
                            continue
                        if sig == "SELL" and not (buy_now_percent <= gate_sell_max):
                            continue
                    # Enforce per (pair, timeframe, indicator) cooldown for flips
                    if not self._allow_by_flip_cooldown(
                        alert,
                        alert_id,
                        pair,
                        fc.get("timeframe"),
                        fc.get("indicator"),
                    ):
                        continue
                    triggered_pairs.append({
                        **fc,
                        "buy_now_percent": buy_now_percent,
                        "gated_by_buy_now": gate_enabled,
                        "style": trading_style,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            
            # If we have triggered pairs, return the alert trigger
            if triggered_pairs:
                # Update last trigger time
                self.last_triggered_alerts[alert_id] = datetime.now(timezone.utc)
                
                return {
                    "alert_id": alert_id,
                    "alert_name": alert_name,
                    "user_email": alert.get("user_email", ""),
                    "triggered_pairs": triggered_pairs,
                    "trigger_time": datetime.now(timezone.utc),
                    "alert_config": alert
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error checking single heatmap alert {alert.get('alert_name', 'Unknown')}: {e}")
            return None

    def _hysteresis_allow_and_update(
        self,
        key: str,
        current: float,
        buy_min: int,
        sell_max: int,
        direction: str,
    ) -> bool:
        """Return True if trigger for direction is allowed by hysteresis; update state accordingly.

        - After BUY trigger, disarm BUY; re‑arm when current <= (buy_min - margin).
        - After SELL trigger, disarm SELL; re‑arm when current >= (sell_max + margin).
        """
        state = self._hysteresis_map.setdefault(key, {"armed_buy": True, "armed_sell": True})

        # Re‑arm checks regardless of direction
        buy_rearm = max(0, buy_min - self.hysteresis_buy_margin)
        sell_rearm = min(100, sell_max + self.hysteresis_sell_margin)
        if not state["armed_buy"] and current <= buy_rearm:
            state["armed_buy"] = True
        if not state["armed_sell"] and current >= sell_rearm:
            state["armed_sell"] = True

        if direction == "BUY":
            if not state["armed_buy"]:
                return False
            # Disarm BUY on trigger
            state["armed_buy"] = False
            return True
        else:  # SELL
            if not state["armed_sell"]:
                return False
            # Disarm SELL on trigger
            state["armed_sell"] = False
            return True

    def _allow_by_pair_cooldown(self, alert: Dict[str, Any], alert_id: str, symbol: str, direction: str) -> bool:
        """Check/update per (alert, symbol, direction) cooldown window.

        Returns True if allowed to trigger now; updates last time on allow.
        """
        try:
            cd_min = alert.get("cooldown_minutes")
            cooldown_minutes = int(cd_min) if cd_min is not None else self.pair_cooldown_minutes_default
        except Exception:
            cooldown_minutes = self.pair_cooldown_minutes_default

        key = f"{alert_id}:{symbol}:{direction}"
        now = datetime.now(timezone.utc)
        last = self._pair_cooldowns.get(key)
        if last is not None:
            delta = (now - last).total_seconds() / 60.0
            if delta < cooldown_minutes:
                return False
        # Allowed -> update last
        self._pair_cooldowns[key] = now
        return True

    def _allow_by_flip_cooldown(
        self,
        alert: Dict[str, Any],
        alert_id: str,
        symbol: str,
        timeframe: str,
        indicator: str,
    ) -> bool:
        """Check/update per (pair, timeframe, indicator) cooldown window for flips.

        Returns True if allowed to trigger now; updates last time on allow.
        """
        try:
            cd_min = alert.get("cooldown_minutes")
            cooldown_minutes = int(cd_min) if cd_min is not None else self.flip_cooldown_minutes_default
        except Exception:
            cooldown_minutes = self.flip_cooldown_minutes_default

        # Normalize indicator to lowercase for key stability
        ind = (indicator or "").lower()
        tf = timeframe or ""
        key = f"{alert_id}:{symbol}:{tf}:{ind}"
        now = datetime.now(timezone.utc)
        last = self._flip_cooldowns.get(key)
        if last is not None:
            delta = (now - last).total_seconds() / 60.0
            if delta < cooldown_minutes:
                return False
        # Allowed -> update last
        self._flip_cooldowns[key] = now
        return True

    async def _get_ohlc_arrays(self, symbol: str, timeframe: str, count: int = 120):
        try:
            from .mt5_utils import get_ohlc_data
            from .models import Timeframe as TF
            tf_map = {"1M": TF.M1, "5M": TF.M5, "15M": TF.M15, "30M": TF.M30, "1H": TF.H1, "4H": TF.H4, "1D": TF.D1, "1W": TF.W1}
            mtf = tf_map.get(timeframe)
            if not mtf:
                return None
            ohlc = get_ohlc_data(symbol, mtf, count)
            if not ohlc:
                return None
            closes = [bar.close for bar in ohlc]
            highs = [bar.high for bar in ohlc]
            lows = [bar.low for bar in ohlc]
            return closes, highs, lows
        except Exception:
            return None

    def _ema_series(self, values: list, period: int) -> list:
        if not values or len(values) < period:
            return []
        k = 2 / (period + 1)
        ema = [sum(values[:period]) / period]
        for v in values[period:]:
            ema.append(v * k + ema[-1] * (1 - k))
        return [None] * (period - 1) + ema

    def _macd_series(self, closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
        if len(closes) < slow + signal + 2:
            return [], []
        ema_fast = self._ema_series(closes, fast)
        ema_slow = self._ema_series(closes, slow)
        macd = []
        for a, b in zip(ema_fast, ema_slow):
            macd.append((a - b) if a is not None and b is not None else None)
        macd_clean = [m for m in macd if m is not None]
        if len(macd_clean) < signal + 1:
            return [], []
        sig_series = self._ema_series([m for m in macd if m is not None], signal)
        pad = macd.index(macd_clean[0]) if macd_clean else 0
        signal_aligned = [None] * pad + sig_series
        return macd, signal_aligned

    def _atr_series(self, highs: list, lows: list, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        trs = []
        prev_close = closes[0]
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
            trs.append(tr)
            prev_close = closes[i]
        atr = self._ema_series(trs, period)
        return [None] + atr

    async def _detect_indicator_flips(self, symbol: str, timeframe: str, selected_indicators: list) -> Optional[Dict[str, Any]]:
        try:
            if not selected_indicators:
                return None
            sel = [s.lower() for s in selected_indicators]
            data = await self._get_ohlc_arrays(symbol, timeframe, count=120)
            if not data:
                return None
            closes, highs, lows = data
            k = self.flip_only_new_bars
            c = self.flip_confirmation_bars

            # EMA flips
            for p, name in [(21, "ema21"), (50, "ema50"), (200, "ema200")]:
                if name in sel:
                    ema = self._ema_series(closes, p)
                    for i in range(len(closes) - k - c, len(closes) - c):
                        if i <= 0 or i + c >= len(closes) or ema[i] is None or ema[i - 1] is None or ema[i + c] is None:
                            continue
                        prev_above = closes[i - 1] > ema[i - 1]
                        curr_above = closes[i] > ema[i]
                        slope_up = ema[i] > ema[i - 1]
                        slope_dn = ema[i] < ema[i - 1]
                        if (not prev_above) and curr_above and slope_up and closes[i + c] > ema[i + c]:
                            return {"indicator": name, "signal": "BUY", "condition": f"{name}_flip_buy"}
                        if prev_above and (not curr_above) and slope_dn and closes[i + c] < ema[i + c]:
                            return {"indicator": name, "signal": "SELL", "condition": f"{name}_flip_sell"}

            # MACD flips
            if "macd" in sel:
                macd, sig = self._macd_series(closes)
                if macd and sig:
                    for i in range(len(closes) - k - c, len(closes) - c):
                        if i <= 0 or macd[i - 1] is None or macd[i] is None or sig[i] is None or sig[i - 1] is None or macd[i + c] is None or sig[i + c] is None:
                            continue
                        prev = macd[i - 1] - sig[i - 1]
                        curr = macd[i] - sig[i]
                        if prev <= 0 and curr > 0 and macd[i] > 0 and macd[i + c] > sig[i + c] and macd[i + c] > 0:
                            return {"indicator": "macd", "signal": "BUY", "condition": "macd_flip_buy"}
                        if prev >= 0 and curr < 0 and macd[i] < 0 and macd[i + c] < sig[i + c] and macd[i + c] < 0:
                            return {"indicator": "macd", "signal": "SELL", "condition": "macd_flip_sell"}

            # Ichimoku Tenkan/Kijun cross
            if "ichimokuclone" in sel:
                def mid(highs, lows, period):
                    res = []
                    for i in range(len(highs)):
                        if i + 1 < period:
                            res.append(None)
                        else:
                            window_h = highs[i - period + 1:i + 1]
                            window_l = lows[i - period + 1:i + 1]
                            res.append((max(window_h) + min(window_l)) / 2.0)
                    return res
                tenkan = mid(highs, lows, 9)
                kijun = mid(highs, lows, 26)
                for i in range(len(closes) - k - c, len(closes) - c):
                    if i <= 0 or tenkan[i] is None or kijun[i] is None or tenkan[i - 1] is None or kijun[i - 1] is None or tenkan[i + c] is None or kijun[i + c] is None:
                        continue
                    prev = tenkan[i - 1] - kijun[i - 1]
                    curr = tenkan[i] - kijun[i]
                    if prev <= 0 and curr > 0 and tenkan[i + c] > kijun[i + c]:
                        return {"indicator": "ichimoku", "signal": "BUY", "condition": "ichimoku_tk_flip_buy"}
                    if prev >= 0 and curr < 0 and tenkan[i + c] < kijun[i + c]:
                        return {"indicator": "ichimoku", "signal": "SELL", "condition": "ichimoku_tk_flip_sell"}

            # UTBOT (simplified): EMA(10) ± 0.5*ATR(10) regime flip
            if "utbot" in sel:
                ema10 = self._ema_series(closes, 10)
                atr10 = self._atr_series(highs, lows, closes, 10)
                for i in range(len(closes) - k - c, len(closes) - c):
                    if i <= 0 or ema10[i] is None or ema10[i - 1] is None or atr10[i] is None or atr10[i + c] is None or ema10[i + c] is None:
                        continue
                    upper = ema10[i] + 0.5 * atr10[i]
                    lower = ema10[i] - 0.5 * atr10[i]
                    prev_long = closes[i - 1] > upper
                    curr_long = closes[i] > upper
                    prev_short = closes[i - 1] < lower
                    curr_short = closes[i] < lower
                    if not prev_long and curr_long and closes[i + c] > (ema10[i + c] + 0.5 * atr10[i + c]):
                        return {"indicator": "utbot", "signal": "BUY", "condition": "utbot_flip_buy"}
                    if not prev_short and curr_short and closes[i + c] < (ema10[i + c] - 0.5 * atr10[i + c]):
                        return {"indicator": "utbot", "signal": "SELL", "condition": "utbot_flip_sell"}

            return None
        except Exception:
            return None

    def _style_tf_weights(self, trading_style: str) -> Dict[str, float]:
        """Return default timeframe weights for a given trading style."""
        s = trading_style.lower()
        if s in ("scalper", "scalp", "scalping"):
            return {"1M": 0.2, "5M": 0.4, "15M": 0.3, "30M": 0.1}
        if s in ("swing", "swingtrader", "swing_trader"):
            return {"1H": 0.25, "4H": 0.45, "1D": 0.30}
        # default: day trader
        return {"15M": 0.2, "30M": 0.35, "1H": 0.35, "4H": 0.10}

    def _compute_final_score(self, tf_strengths: Dict[str, float], weights: Dict[str, float]) -> float:
        """Compute style-weighted Final Score in [-100, 100] from per‑TF strengths [0..100]."""
        # Convert strengths (0..100) -> scores (-100..100)
        scored = {tf: (val - 50.0) * 2.0 for tf, val in tf_strengths.items()}
        # Use weights for provided TFs; fallback to uniform over provided TFs if no overlap
        active = {tf: w for tf, w in weights.items() if tf in scored}
        if not active:
            w = 1.0 / max(len(scored), 1)
            return sum(score * w for score in scored.values())
        total_w = sum(active.values()) or 1.0
        return sum(scored[tf] * (w / total_w) for tf, w in active.items())

    def _determine_style_signal(
        self,
        buy_now_percent: float,
        buy_min: int,
        buy_max: int,
        sell_min: int,
        sell_max: int,
    ) -> Optional[str]:
        """Decide BUY/SELL from Buy Now % thresholds.

        BUY if ≥ buy_min (and ≤ buy_max when provided), SELL if ≤ sell_max (and ≥ sell_min).
        """
        # BUY path
        if buy_now_percent >= buy_min and (buy_max is None or buy_now_percent <= buy_max):
            return "BUY"
        # SELL path
        if buy_now_percent <= sell_max and buy_now_percent >= sell_min:
            return "SELL"
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
    
    def _should_trigger_alert(self, alert_id: str, alert: Dict[str, Any]) -> bool:
        """Check if alert should be triggered based on frequency settings"""
        
        alert_frequency = alert.get("alert_frequency", "once")
        current_time = datetime.now(timezone.utc)
        
        if alert_frequency == "once":
            # Only trigger once per alert
            return alert_id not in self.last_triggered_alerts
        
        elif alert_frequency == "hourly":
            # Trigger once per hour
            last_trigger = self.last_triggered_alerts.get(alert_id)
            if not last_trigger:
                return True
            return (current_time - last_trigger).total_seconds() >= 3600
        
        elif alert_frequency == "daily":
            # Trigger once per day
            last_trigger = self.last_triggered_alerts.get(alert_id)
            if not last_trigger:
                return True
            return (current_time - last_trigger).total_seconds() >= 86400
        
        return True  # Default to allowing trigger
    
    async def _get_market_data(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """Get current market data for a symbol and timeframe using real MT5 data"""
        
        try:
            # Try to get real MT5 data first
            try:
                from .mt5_utils import get_ohlc_data
                from .models import Timeframe as MT5Timeframe
                import MetaTrader5 as mt5
                
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
                    ohlc_data = get_ohlc_data(symbol, mt5_timeframe, 1)
                    if ohlc_data and len(ohlc_data) > 0:
                        latest_bar = ohlc_data[-1]
                        
                        # Get real tick data from MT5
                        tick_info = mt5.symbol_info_tick(symbol)
                        
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
                            "bid": tick_info.bid if tick_info else None,
                            "ask": tick_info.ask if tick_info else None,
                            "data_source": "MT5_REAL"
                        }
            except ImportError:
                logger.warning(f"⚠️ MT5 not available, using fallback data for {symbol}")
            except Exception as mt5_error:
                logger.warning(f"⚠️ MT5 error for {symbol}: {mt5_error}, using fallback data")
            
            # Fallback: simulate market data
            import random
            logger.debug(f"⚠️ Using simulated data for {symbol} - no real data available")
            
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "open": 1.1000 + random.uniform(-0.01, 0.01),
                "high": 1.1020 + random.uniform(-0.01, 0.01),
                "low": 1.0980 + random.uniform(-0.01, 0.01),
                "close": 1.1005 + random.uniform(-0.01, 0.01),
                "volume": random.randint(1000, 10000),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_source": "SIMULATED"
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting market data for {symbol} {timeframe}: {e}")
            return None
    
    async def _calculate_indicators_strength(
        self, 
        market_data: Dict[str, Any], 
        selected_indicators: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Calculate indicator strength for the given market data"""
        
        try:
            indicators = {}
            overall_strength = 0
            
            for indicator in selected_indicators:
                # Normalize indicator name to lowercase for comparison
                indicator_lower = indicator.lower()
                
                if indicator_lower == "rsi":
                    # Calculate real RSI using MT5 data
                    rsi_value = await self._calculate_real_rsi(market_data)
                    if rsi_value is not None:
                        indicators["rsi"] = rsi_value
                        overall_strength += rsi_value
                
                elif indicator_lower == "macd":
                    # Simulate MACD calculation
                    macd_value = (market_data.get("close", 1.1) - 1.1) * 50
                    indicators["macd"] = macd_value
                    overall_strength += 50 + macd_value
                
                elif indicator_lower == "bollinger":
                    # Simulate Bollinger Bands
                    bb_value = 50 + (market_data.get("close", 1.1) - 1.1) * 30
                    indicators["bollinger"] = bb_value
                    overall_strength += bb_value
                
                elif indicator_lower == "stochastic":
                    # Simulate Stochastic
                    stoch_value = 50 + (market_data.get("close", 1.1) - 1.1) * 40
                    indicators["stochastic"] = stoch_value
                    overall_strength += stoch_value
                
                elif indicator_lower == "ema21":
                    # Simulate EMA 21 calculation
                    ema21_value = 50 + (market_data.get("close", 1.1) - 1.1) * 60
                    ema21_value = max(0, min(100, ema21_value))
                    indicators["ema21"] = ema21_value
                    overall_strength += ema21_value
                
                elif indicator_lower == "ema50":
                    # Simulate EMA 50 calculation
                    ema50_value = 50 + (market_data.get("close", 1.1) - 1.1) * 45
                    ema50_value = max(0, min(100, ema50_value))
                    indicators["ema50"] = ema50_value
                    overall_strength += ema50_value
                
                elif indicator_lower == "ema200":
                    # Simulate EMA 200 calculation
                    ema200_value = 50 + (market_data.get("close", 1.1) - 1.1) * 35
                    ema200_value = max(0, min(100, ema200_value))
                    indicators["ema200"] = ema200_value
                    overall_strength += ema200_value
                
                elif indicator_lower == "utbot":
                    # Simulate UTBOT calculation
                    utbot_value = 50 + (market_data.get("close", 1.1) - 1.1) * 25
                    utbot_value = max(0, min(100, utbot_value))
                    indicators["utbot"] = utbot_value
                    overall_strength += utbot_value
                
                elif indicator_lower == "ichimokuclone":
                    # Simulate Ichimoku Clone calculation
                    ichimoku_value = 50 + (market_data.get("close", 1.1) - 1.1) * 40
                    ichimoku_value = max(0, min(100, ichimoku_value))
                    indicators["ichimokuclone"] = ichimoku_value
                    overall_strength += ichimoku_value
                
                else:
                    # Unknown indicator - log warning but continue
                    logger.warning(f"⚠️ Unknown indicator: {indicator}")
                    # Use default neutral value
                    indicators[indicator_lower] = 50
                    overall_strength += 50
            
            # Calculate average strength
            if selected_indicators:
                overall_strength = overall_strength / len(selected_indicators)
            else:
                overall_strength = 50  # Default neutral
            
            return {
                "overall_strength": round(overall_strength, 2),
                "indicators": indicators
            }
            
        except Exception as e:
            logger.error(f"❌ Error calculating indicators strength: {e}")
            return None
    
    async def _calculate_real_rsi(self, market_data: Dict[str, Any]) -> Optional[float]:
        """Calculate real RSI using MT5 data"""
        try:
            symbol = market_data.get("symbol")
            timeframe = market_data.get("timeframe")
            
            if not symbol or not timeframe:
                return None
            
            # Get more OHLC data for RSI calculation (need at least 14 periods)
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
            
            # Get 20 periods of data for RSI calculation
            ohlc_data = get_ohlc_data(symbol, mt5_timeframe, 20)
            if len(ohlc_data) < 14:
                return None
            
            # Calculate RSI
            closes = [bar.close for bar in ohlc_data]
            rsi_value = self._calculate_rsi_from_closes(closes, 14)
            
            logger.debug(f"✅ Calculated real RSI for {symbol}: {rsi_value:.2f}")
            return rsi_value
            
        except Exception as e:
            logger.error(f"❌ Error calculating real RSI: {e}")
            return None
    
    def _calculate_rsi_from_closes(self, closes: List[float], period: int = 14) -> float:
        """Calculate RSI from a list of closing prices"""
        if len(closes) < period + 1:
            return 50.0  # Default neutral RSI
        
        # Calculate price changes
        deltas = []
        for i in range(1, len(closes)):
            deltas.append(closes[i] - closes[i-1])
        
        # Separate gains and losses
        gains = [delta if delta > 0 else 0 for delta in deltas]
        losses = [-delta if delta < 0 else 0 for delta in deltas]
        
        # Calculate initial averages
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        # Calculate RSI using Wilder's smoothing
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        # Calculate RSI
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return max(0, min(100, rsi))
    
    def _determine_signal(
        self, 
        strength_data: Dict[str, Any],
        buy_threshold_min: int,
        buy_threshold_max: int,
        sell_threshold_min: int,
        sell_threshold_max: int
    ) -> Optional[str]:
        """Determine if the strength data triggers a buy or sell signal"""
        
        indicators = strength_data.get("indicators", {})
        rsi_value = indicators.get("rsi")
        
        if rsi_value is None:
            return None
        
        # Check for buy signal (oversold - RSI below threshold)
        if sell_threshold_min <= rsi_value <= sell_threshold_max:
            return "BUY"  # Oversold = Buy opportunity
        
        # Check for sell signal (overbought - RSI above threshold)
        if buy_threshold_min <= rsi_value <= buy_threshold_max:
            return "SELL"  # Overbought = Sell opportunity
        
        return None
    
    async def _send_alert_notification(self, trigger_data: Dict[str, Any]):
        """Send email notification for triggered alert"""
        
        try:
            user_email = trigger_data.get("user_email")
            alert_name = trigger_data.get("alert_name")
            alert_id = trigger_data.get("alert_id")
            triggered_pairs = trigger_data.get("triggered_pairs", [])
            alert_config = trigger_data.get("alert_config", {})
            
            logger.info(f"📧 Preparing Heatmap alert email for user: {user_email}")
            logger.info(f"   Alert: {alert_name} (ID: {alert_id})")
            logger.info(f"   Triggered pairs: {len(triggered_pairs)}")
            
            if not user_email:
                logger.warning("⚠️ No user email found for alert notification")
                return
            
            # Log triggered pairs details
            for i, pair in enumerate(triggered_pairs, 1):
                logger.info(f"   Pair {i}: {pair.get('symbol')} - {pair.get('trigger_condition')} (Score: {pair.get('trigger_score')})")
            
            # Send email using the email service
            logger.info(f"📤 Sending Heatmap alert email to {user_email}...")
            success = await email_service.send_heatmap_alert(
                user_email=user_email,
                alert_name=alert_name,
                triggered_pairs=triggered_pairs,
                alert_config=alert_config
            )
            
            if success:
                logger.info(f"✅ Heatmap alert email sent successfully to {user_email}")
                logger.info(f"   Alert: {alert_name} (ID: {alert_id})")
                logger.info(f"   Pairs: {len(triggered_pairs)}")
            else:
                logger.warning(f"⚠️ Failed to send Heatmap alert email to {user_email}")
                logger.warning(f"   Alert: {alert_name} (ID: {alert_id})")
                
        except Exception as e:
            logger.error(f"❌ Error sending alert notification: {e}")
    
    async def create_heatmap_alert(self, alert_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new heatmap alert in Supabase"""
        
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.supabase_url}/rest/v1/heatmap_alerts"
            
            # Prepare alert data for Supabase
            supabase_data = {
                "alert_name": alert_data.get("alert_name"),
                "user_email": alert_data.get("user_email"),
                "pairs": alert_data.get("pairs", []),
                "timeframes": alert_data.get("timeframes", []),
                "selected_indicators": alert_data.get("selected_indicators", []),
                "trading_style": alert_data.get("trading_style", "dayTrader"),
                "buy_threshold_min": alert_data.get("buy_threshold_min", 70),
                "buy_threshold_max": alert_data.get("buy_threshold_max", 100),
                "sell_threshold_min": alert_data.get("sell_threshold_min", 0),
                "sell_threshold_max": alert_data.get("sell_threshold_max", 30),
                "notification_methods": alert_data.get("notification_methods", ["email"]),
                "alert_frequency": alert_data.get("alert_frequency", "once"),
                "trigger_on_crossing": alert_data.get("trigger_on_crossing", True),
                "is_active": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=supabase_data) as response:
                    if response.status in [200, 201]:
                        result = await response.json()
                        logger.info(f"✅ Heatmap alert created: {alert_data.get('alert_name')}")
                        
                        # Refresh alert cache
                        await alert_cache._refresh_cache()
                        
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to create heatmap alert: {response.status} - {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"❌ Error creating heatmap alert: {e}")
            return None
    
    async def get_user_heatmap_alerts(self, user_email: str) -> List[Dict[str, Any]]:
        """Get all heatmap alerts for a specific user"""
        
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.supabase_url}/rest/v1/heatmap_alerts"
            params = {
                "select": "*",
                "user_email": f"eq.{user_email}",
                "order": "created_at.desc"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"❌ Failed to get user heatmap alerts: {response.status}")
                        return []
                        
        except Exception as e:
            logger.error(f"❌ Error getting user heatmap alerts: {e}")
            return []
    
    async def delete_heatmap_alert(self, alert_id: str) -> bool:
        """Delete a heatmap alert"""
        
        try:
            headers = {
                "apikey": self.supabase_service_key,
                "Authorization": f"Bearer {self.supabase_service_key}",
                "Content-Type": "application/json"
            }
            
            url = f"{self.supabase_url}/rest/v1/heatmap_alerts"
            params = {"id": f"eq.{alert_id}"}
            
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, headers=headers, params=params) as response:
                    if response.status in [200, 204]:
                        logger.info(f"✅ Heatmap alert deleted: {alert_id}")
                        
                        # Refresh alert cache
                        await alert_cache._refresh_cache()
                        
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to delete heatmap alert: {response.status} - {error_text}")
                        return False
                        
        except Exception as e:
            logger.error(f"❌ Error deleting heatmap alert: {e}")
            return False

# Global heatmap alert service instance
heatmap_alert_service = HeatmapAlertService()

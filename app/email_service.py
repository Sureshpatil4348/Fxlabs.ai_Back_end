import os
import base64
import asyncio
import hashlib
import hmac
import json
import re
from urllib.parse import quote as url_quote
from threading import RLock
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Mail,
        Email,
        To,
        Content,
        TrackingSettings,
        ClickTracking,
        OpenTracking,
        Attachment,
    )
except Exception:  # Module may be missing in some environments
    SendGridAPIClient = None
    Mail = Email = To = Content = None
    TrackingSettings = ClickTracking = OpenTracking = None
    Attachment = None
import logging

# Configure logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

from .config import SENDGRID_API_KEY, FROM_EMAIL, FROM_NAME, PUBLIC_BASE_URL, DAILY_TZ_NAME, BYPASS_EMAIL_ALERTS
from .tenancy import get_tenant_config
from .alert_logging import log_debug, log_info, log_warning, log_error
 


class EmailService:
    """SendGrid email service for sending heatmap alerts with cooldown mechanism"""
    
    def __init__(self):
        # Strictly tenant-scoped configuration (no global/env fallbacks)
        ten = get_tenant_config()
        self.tenant_name = ten.name
        self.sendgrid_api_key = (SENDGRID_API_KEY or "").strip()
        self.from_email = (FROM_EMAIL or "").strip()
        self.from_name = (FROM_NAME or "").strip()
        # Tenant-aware timezone for all email timestamps (FXLabs → IST by default)
        self.tz_name = (DAILY_TZ_NAME or "Asia/Kolkata")
        # Unsubscribe feature removed per spec
        
        # Smart cooldown mechanism - value-based cooldown for similar alerts
        self.cooldown_minutes = 10  # Reduced to 10 minutes for better responsiveness
        self.rsi_threshold = 5.0  # RSI values within 5 points are considered similar
        self.alert_cooldowns = {}  # {alert_hash: last_sent_timestamp}
        self.alert_values = {}  # {alert_hash: last_sent_values} for value comparison
        
        # Initialize SendGrid client if available and configured
        if SendGridAPIClient and self.sendgrid_api_key:
            try:
                self.sg = SendGridAPIClient(api_key=self.sendgrid_api_key)
                logger.info("✅ SendGrid email service initialized with smart value-based cooldown (10min, 5 RSI threshold)")
            except Exception as e:
                self.sg = None
                logger.warning(f"⚠️ Could not initialize SendGrid client: {e}")
                # Log diagnostics to explain why it's effectively not configured
                self._log_config_diagnostics(context="initialization")
        else:
            self.sg = None
            if not SendGridAPIClient:
                logger.warning("⚠️ SendGrid library not installed. Email sending is disabled.")
            elif not self.sendgrid_api_key:
                logger.warning("⚠️ Tenant email credentials missing (API key). Email sending is disabled.")
            # Provide comprehensive diagnostics on what's missing/misaligned
            self._log_config_diagnostics(context="startup")

    
    def _generate_alert_hash(self, user_email: str, alert_name: str, triggered_pairs: List[Dict[str, Any]], calculation_mode: str = None) -> str:
        """Generate a unique hash for similar alerts to implement value-based cooldown (supports all alert types)"""
        # Create a normalized string from alert data including actual values
        pairs_summary = []
        
        for pair in triggered_pairs:
            # RSI Alerts: {symbol: "EURUSD", rsi: 70.1, condition: "overbought"} or {symbol: "EURUSD", rsi_value: 70.1, trigger_condition: "overbought"}
            if ('rsi' in pair or 'rsi_value' in pair) and 'symbol' in pair:
                symbol = pair['symbol']
                # Support both field name variants
                condition = pair.get('condition', pair.get('trigger_condition', ''))
                rsi_value = pair.get('rsi', pair.get('rsi_value'))
                rsi_rounded = round(float(rsi_value), 1)
                pairs_summary.append(f"{symbol}:{condition}:{rsi_rounded}")
            
            # RSI Correlation Alerts: {symbol1: "EURUSD", symbol2: "GBPUSD", rsi1: 70.1, rsi2: 30.2}
            elif 'rsi1' in pair and 'symbol1' in pair:
                symbol1 = pair['symbol1']
                symbol2 = pair['symbol2']
                rsi1 = round(float(pair['rsi1']), 1)
                rsi2 = round(float(pair['rsi2']), 1)
                condition = pair.get('trigger_condition', '')
                pairs_summary.append(f"{symbol1}_{symbol2}:{condition}:{rsi1}_{rsi2}")
            
            # Heatmap Alerts: {symbol: "EURUSD", strength: 75.5, signal: "buy"}
            elif 'strength' in pair and 'symbol' in pair:
                symbol = pair['symbol']
                signal = pair.get('signal', '')
                strength = round(float(pair['strength']), 1)
                pairs_summary.append(f"{symbol}:{signal}:{strength}")
                
                # Also include RSI if available in indicators
                indicators = pair.get('indicators', {})
                if 'rsi' in indicators:
                    rsi = round(float(indicators['rsi']), 1)
                    pairs_summary.append(f"{symbol}:rsi:{rsi}")
            
            # Heatmap Tracker Alerts (Probability Signal): {symbol, trigger_condition: 'buy'|'sell', buy_percent, sell_percent, final_score}
            elif ('buy_percent' in pair or 'sell_percent' in pair or 'final_score' in pair) and 'symbol' in pair:
                symbol = pair['symbol']
                condition = pair.get('trigger_condition', '')
                buy_pct = pair.get('buy_percent')
                sell_pct = pair.get('sell_percent')
                final = pair.get('final_score')
                parts: List[str] = []
                try:
                    if buy_pct is not None:
                        parts.append(f"buy={round(float(buy_pct), 1)}")
                except Exception:
                    pass
                try:
                    if sell_pct is not None:
                        parts.append(f"sell={round(float(sell_pct), 1)}")
                except Exception:
                    pass
                try:
                    if final is not None:
                        parts.append(f"score={round(float(final), 1)}")
                except Exception:
                    pass
                meta = ",".join(parts) if parts else ""
                pairs_summary.append(f"{symbol}:{condition}:{meta}")
            
            # Fallback for unknown structure
            else:
                symbol = pair.get('symbol', pair.get('symbol1', 'unknown'))
                # Support both field name variants
                cond_parts: List[str] = []
                condition = pair.get('condition', pair.get('trigger_condition', 'unknown'))
                indicator_name = pair.get('indicator')
                if indicator_name:
                    cond_parts.append(f"ind={indicator_name}")
                cond_meta = ",".join(cond_parts)
                pairs_summary.append(f"{symbol}:{condition}")
        
        # Sort to ensure consistent hashing
        pairs_summary.sort()
        alert_data = f"{user_email}:{alert_name}:{':'.join(pairs_summary)}"
        
        # Include calculation mode for RSI correlation alerts
        if calculation_mode:
            alert_data += f":{calculation_mode}"
        
        # Generate hash using secure algorithm
        return hashlib.blake2b(alert_data.encode(), digest_size=32).hexdigest()

    def _unsuffix_symbol(self, symbol: str) -> str:
        try:
            s = str(symbol).strip()
            return s[:-1] if s.endswith("m") else s
        except Exception:
            return str(symbol)

    def _pair_display(self, symbol: str) -> str:
        """Return user-facing display for a trading symbol as ABC/DEF.

        Non-breaking: only affects presentation; does not alter underlying symbols.
        """
        raw = self._unsuffix_symbol(symbol)
        try:
            if len(raw) >= 6:
                base = raw[:3]
                quote = raw[3:6]
                return f"{base}/{quote}"
            return raw
        except Exception:
            return raw

    def _zoneinfo_or_fallback(self, tz_name: str):
        """Return tzinfo for tz_name. Fallback to fixed IST or UTC when ZoneInfo is unavailable."""
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_name)
        except Exception:
            if tz_name == "Asia/Kolkata":
                try:
                    return timezone(timedelta(hours=5, minutes=30), name="IST")
                except Exception:
                    pass
            return timezone.utc

    def _format_now_local(self, tz_name: str = "Asia/Kolkata") -> str:
        """Return current time formatted with local timezone for display (default IST)."""
        try:
            tz = self._zoneinfo_or_fallback(tz_name)
            dt = datetime.now(tz)
            label = "IST" if tz_name == "Asia/Kolkata" else tz_name
            return dt.strftime(f"%Y-%m-%d %H:%M {label}")
        except Exception:
            # Final fallback to UTC string
            return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    
    def _get_local_date_time_strings(self, tz_name: str = "Asia/Kolkata") -> Tuple[str, str, str]:
        """Return (date_str, time_str, tz_label) for the given timezone, defaulting to IST."""
        try:
            tz = self._zoneinfo_or_fallback(tz_name)
            dt = datetime.now(tz)
            label = "IST" if tz_name == "Asia/Kolkata" else tz_name
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), label
        except Exception:
            dt = datetime.now(timezone.utc)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M"), "UTC"

    def _build_common_header(self, alert_type: str, tz_name: str = "Asia/Kolkata", date_override: Optional[str] = None, time_label_override: Optional[str] = None) -> str:
        """Build a common green header bar used across all alert emails.

        Layout: [Logo] FXLabs • <Alert Type> • <Local Date IST> • <Local Time IST (small)>
        Brand color: #07c05c, text in white, time rendered slightly smaller.
        """
        date_str, time_str, tz_label = self._get_local_date_time_strings(tz_name)
        date_display = (date_override or date_str)
        if tz_label and (tz_label not in (date_display or "")):
            date_display = f"{date_display} {tz_label}".strip()
        time_display = time_label_override if time_label_override else f"{time_str} {tz_label}".strip()

        logo_img = '<img src="cid:fx-logo" width="18" height="18" alt="FxLabs" style="vertical-align:middle;display:inline-block" />'

        return (
            f"<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" "
            f"style=\"width:600px;background:#07c05c;color:#ffffff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;\">"
            f"<tr><td style=\"padding:14px 16px;\">"
            f"<span style=\"display:inline-block;vertical-align:middle;\">{logo_img}</span>"
            f"<span style=\"display:inline-block;vertical-align:middle;font-weight:700;margin-left:8px;\">FXLabs</span>"
            f"<span style=\"display:inline-block;margin:0 6px;vertical-align:middle;\">•</span>"
            f"<span style=\"display:inline-block;vertical-align:middle;\">{alert_type}</span>"
            f"<span style=\"display:inline-block;margin:0 6px;vertical-align:middle;\">•</span>"
            f"<span style=\"display:inline-block;vertical-align:middle;\">{date_display}</span>"
            f"<span style=\"display:inline-block;margin:0 6px;vertical-align:middle;\">•</span>"
            f"<span style=\"display:inline-block;vertical-align:middle;font-size:12px;opacity:0.95;\">{time_display}</span>"
            f"</td></tr></table><div style=\"height:12px\"></div>"
        )
    
    def _is_alert_in_cooldown(self, alert_hash: str, triggered_pairs: List[Dict[str, Any]] = None) -> bool:
        """Check if alert is still in cooldown period with value-based intelligence"""
        if alert_hash not in self.alert_cooldowns:
            return False
        
        last_sent = self.alert_cooldowns[alert_hash]
        cooldown_duration = timedelta(minutes=self.cooldown_minutes)
        
        # Check time-based cooldown first
        if datetime.now(timezone.utc) - last_sent >= cooldown_duration:
            return False
        
        # If we have triggered pairs, check value-based cooldown
        if triggered_pairs and alert_hash in self.alert_values:
            return self._is_value_similar(triggered_pairs, self.alert_values[alert_hash])
        
        # Fallback to time-based cooldown
        return True
    
    def _is_value_similar(self, current_pairs: List[Dict[str, Any]], last_pairs: List[Dict[str, Any]]) -> bool:
        """Check if current values are similar to last sent values (supports all alert types)"""
        if not current_pairs or not last_pairs:
            return True  # If no data, apply cooldown
        
        # Extract values based on alert type
        current_values = self._extract_alert_values(current_pairs)
        last_values = self._extract_alert_values(last_pairs)
        
        # Check if any values are significantly different
        for key, current_value in current_values.items():
            if key in last_values:
                last_value = last_values[key]
                value_diff = abs(float(current_value) - float(last_value))
                
                # If value difference is significant, allow the alert
                if value_diff >= self.rsi_threshold:
                    logger.info(f"🔄 Value difference {value_diff:.1f} >= {self.rsi_threshold} for {key}. Allowing alert despite cooldown.")
                    return False
        
        # All values are similar, apply cooldown
        return True
    
    def _add_transactional_headers(self, mail: Mail, category: str = "fx-labs-alerts", to_email_addr: Optional[str] = None):
        """Add transactional email headers to avoid spam filters"""
        # Category header (older X-SMTPAPI style is acceptable and harmless when ignored)
        try:
            mail.add_header("X-SMTPAPI", f'{{"category": ["{category}"]}}')
        except Exception:
            pass
        # Friendly mailer id
        try:
            mail.add_header("X-Mailer", "FX Labs Alert System")
        except Exception:
            pass
        # List-Unsubscribe headers removed per spec
        return mail

    def _disable_tracking(self, mail: Mail) -> None:
        """Disable click/open tracking to avoid link rewriting and tracking pixel (can hurt inboxing)."""
        try:
            if TrackingSettings and ClickTracking and OpenTracking:
                ts = TrackingSettings()
                ts.click_tracking = ClickTracking(False, False)
                ts.open_tracking = OpenTracking(False)
                mail.tracking_settings = ts
        except Exception:
            # Best-effort only
            pass

    def _build_mail(
        self,
        subject: str,
        to_email_addr: str,
        html_body: str,
        text_body: str,
        category: str,
        ref_id: Optional[str] = None,
    ) -> Mail:
        """Create a Mail with text+html, transactional headers, and tracking disabled."""
        from_email = Email(self.from_email, self.from_name)
        to_email = To(to_email_addr)
        mail = Mail(from_email, to_email, subject)
        # Add text first, then HTML per MIME best practices
        try:
            mail.add_content(Content("text/plain", text_body or ""))
        except Exception:
            pass
        try:
            mail.add_content(Content("text/html", html_body or ""))
        except Exception:
            pass
        # Optional reply-to mirrors from address
        try:
            mail.reply_to = Email(self.from_email, self.from_name)
        except Exception:
            pass
        # Add headers and tracking settings
        self._add_transactional_headers(mail, category=category, to_email_addr=to_email_addr)
        self._disable_tracking(mail)
        # Add a stable reference id for threading/diagnostics
        if ref_id:
            try:
                mail.add_header("X-Entity-Ref-ID", ref_id)
            except Exception:
                pass
        # Attach inline logo (CID) for email header if available
        try:
            if Attachment:
                logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "images", "fxlabs_logo_white.png")
                if not os.path.exists(logo_path):
                    # Fallback to project root assets path
                    logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "images", "fxlabs_logo_white.png")
                if os.path.exists(logo_path):
                    with open(logo_path, "rb") as f:
                        data = base64.b64encode(f.read()).decode()
                    attachment = Attachment()
                    attachment.file_content = data
                    attachment.file_type = "image/png"
                    attachment.file_name = "fxlabs_logo_white.png"
                    # Use Content-ID so <img src="cid:fx-logo"> can reference
                    attachment.disposition = "inline"
                    attachment.content_id = "fx-logo"
                    # Some helper libs use different property names; assign defensively
                    try:
                        mail.add_attachment(attachment)
                    except Exception:
                        # Older helper version: .attachments list
                        try:
                            mail.attachments = getattr(mail, "attachments", []) + [attachment]
                        except Exception:
                            pass
        except Exception:
            # Non-fatal if attachment fails
            pass
        return mail

    # Unsubscribe management removed per spec

    # Per-user rate limiting removed per product decision

    def is_unsubscribed(self, email: str) -> bool:
        # Unsubscribe support removed: always False
        return False

    def _build_plain_text_rsi(self, alert_name: str, pairs: List[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
        lines = [
            f"RSI Alert - {alert_name}",
            f"Pairs: {len(pairs)}",
        ]
        ob = cfg.get("rsi_overbought_threshold", 70)
        os_ = cfg.get("rsi_oversold_threshold", 30)
        lines.append(f"OB>={ob} OS<={os_}")
        for p in pairs:
            sym = self._pair_display(p.get("symbol", "?"))
            rsi = p.get("rsi", p.get("rsi_value", "?"))
            price = p.get("current_price", "?")
            chg = p.get("price_change_percent", "?")
            lines.append(f"- {sym}: RSI {rsi}, Px {price}, Chg {chg}%")
        return "\n".join(lines)

    def _build_plain_text_heatmap(self, alert_name: str, pairs: List[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
        lines = [
            f"Heatmap Alert - {alert_name}",
            f"Pairs: {len(pairs)}",
        ]
        for p in pairs:
            sym = self._pair_display(p.get("symbol", "?"))
            strength = p.get("strength", "?")
            signal = p.get("signal", "?")
            tf = p.get("timeframe", "?")
            lines.append(f"- {sym} [{tf}]: {signal} {strength}%")
        return "\n".join(lines)

    # Correlation plain text builder removed

    def _safe_float_conversion(self, value: Any) -> Optional[float]:
        """Safely convert value to float with fallback for unparsable values"""
        if value is None:
            return None
        
        try:
            return float(value)
        except (ValueError, TypeError):
            logger.warning(f"⚠️ Could not convert value '{value}' to float, skipping")
            return None
    
    def _get_rsi_value(self, pair: Dict[str, Any], rsi_key: str) -> Optional[float]:
        """Get RSI value from pair, checking both 'rsi' and 'rsi_value' keys"""
        # Try primary key first (e.g., 'rsi', 'rsi1', 'rsi2')
        if rsi_key in pair:
            return self._safe_float_conversion(pair[rsi_key])
        
        # Try alternative key with '_value' suffix (e.g., 'rsi_value', 'rsi1_value', 'rsi2_value')
        alt_key = f"{rsi_key}_value"
        if alt_key in pair:
            return self._safe_float_conversion(pair[alt_key])
        
        return None

    def _extract_alert_values(self, pairs: List[Dict[str, Any]]) -> Dict[str, float]:
        """Extract comparable values from different alert types"""
        values = {}
        
        for pair in pairs:
            # RSI Alerts: {symbol: "EURUSD", rsi: 70.1, condition: "overbought"}
            # Also handles: {symbol: "EURUSD", rsi_value: 70.1, condition: "overbought"}
            if ('rsi' in pair or 'rsi_value' in pair) and 'symbol' in pair:
                symbol = pair['symbol']
                rsi = self._get_rsi_value(pair, 'rsi')
                if rsi is not None:
                    values[f"{symbol}_rsi"] = rsi
            
            # RSI Correlation Alerts: {symbol1: "EURUSD", symbol2: "GBPUSD", rsi1: 70.1, rsi2: 30.2}
            # Also handles: {symbol1: "EURUSD", symbol2: "GBPUSD", rsi1_value: 70.1, rsi2_value: 30.2}
            elif (('rsi1' in pair or 'rsi1_value' in pair) and 
                  ('rsi2' in pair or 'rsi2_value' in pair) and 
                  'symbol1' in pair and 'symbol2' in pair):
                symbol1 = pair['symbol1']
                symbol2 = pair['symbol2']
                rsi1 = self._get_rsi_value(pair, 'rsi1')
                rsi2 = self._get_rsi_value(pair, 'rsi2')
                
                if rsi1 is not None:
                    values[f"{symbol1}_{symbol2}_rsi1"] = rsi1
                if rsi2 is not None:
                    values[f"{symbol1}_{symbol2}_rsi2"] = rsi2
            
            # Heatmap Alerts: {symbol: "EURUSD", strength: 75.5, indicators: {rsi: 70.1}}
            elif 'strength' in pair and 'symbol' in pair:
                symbol = pair['symbol']
                strength = self._safe_float_conversion(pair['strength'])
                if strength is not None:
                    values[f"{symbol}_strength"] = strength
                
                # Also check for RSI in indicators if available
                # Handles both 'rsi' and 'rsi_value' in indicators
                indicators = pair.get('indicators', {})
                if indicators:
                    rsi = self._get_rsi_value(indicators, 'rsi')
                    if rsi is not None:
                        values[f"{symbol}_rsi"] = rsi
            
            # Heatmap Tracker Alerts (Probability Signal)
            elif ('buy_percent' in pair or 'sell_percent' in pair or 'final_score' in pair) and 'symbol' in pair:
                symbol = pair['symbol']
                buy_pct = self._safe_float_conversion(pair.get('buy_percent'))
                sell_pct = self._safe_float_conversion(pair.get('sell_percent'))
                final = self._safe_float_conversion(pair.get('final_score'))
                if buy_pct is not None:
                    values[f"{symbol}_buy_percent"] = buy_pct
                if sell_pct is not None:
                    values[f"{symbol}_sell_percent"] = sell_pct
                if final is not None:
                    values[f"{symbol}_final_score"] = final
        
        return values

    # ------------------ Configuration diagnostics ------------------
    def _mask(self, value: Optional[str], keep_tail: int = 4) -> str:
        if not value:
            return "MISSING"
        v = str(value)
        if len(v) <= keep_tail:
            return "*" * len(v)
        # Preserve common SendGrid prefix for clarity (e.g., SG.)
        prefix = "SG." if v.startswith("SG.") else v[:2]
        tail = v[-keep_tail:]
        return f"{prefix}{'*' * max(len(v) - len(prefix) - keep_tail, 0)}{tail}"

    def _looks_like_email(self, email: str) -> bool:
        try:
            return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(email)))
        except Exception:
            return False

    def get_config_diagnostics(self) -> Dict[str, Any]:
        """Return a diagnostics dict describing current email configuration state.

        Does not leak secrets; values are masked where appropriate.
        """
        issues: List[str] = []
        sg_lib = bool(SendGridAPIClient)
        api_key = self.sendgrid_api_key or ""
        from_email = self.from_email or ""
        from_name = self.from_name or ""
        # Determine expected tenant-specific variable names
        ten = getattr(self, "tenant_name", "") or get_tenant_config().name
        if ten == "FXLabs":
            exp_key, exp_from_email, exp_from_name = (
                "FXLABS_SENDGRID_API_KEY", "FXLABS_FROM_EMAIL", "FXLABS_FROM_NAME"
            )
        else:
            exp_key, exp_from_email, exp_from_name = (
                "HEXTECH_SENDGRID_API_KEY", "HEXTECH_FROM_EMAIL", "HEXTECH_FROM_NAME"
            )

        # Library presence
        if not sg_lib:
            issues.append("sendgrid library not installed (pip install sendgrid)")

        # API key checks
        if not api_key:
            issues.append(f"Tenant API key missing (set {exp_key})")
        elif not api_key.startswith("SG."):
            issues.append('SENDGRID_API_KEY does not look like a SendGrid key (expected to start with "SG.")')

        # From email checks
        if not from_email:
            issues.append(f"FROM_EMAIL missing (set {exp_from_email})")
        elif not self._looks_like_email(from_email):
            issues.append("FROM_EMAIL invalid format")

        # From name checks
        if not from_name:
            issues.append(f"FROM_NAME missing (set {exp_from_name})")

        configured = self.sg is not None
        return {
            "configured": configured,
            "client_initialized": configured,
            "issues": issues,
            "values": {
                "SENDGRID_API_KEY": self._mask(api_key),
                "FROM_EMAIL": from_email or "",
                "FROM_NAME": from_name or "",
            },
        }

    def get_config_diagnostics_text(self) -> str:
        """Return a one-line human readable diagnostics summary for logs."""
        diag = self.get_config_diagnostics()
        if diag.get("configured"):
            return ""
        issues = diag.get("issues", [])
        vals = diag.get("values", {})
        key_mask = vals.get("SENDGRID_API_KEY", "")
        from_email = vals.get("FROM_EMAIL", "")
        from_name = vals.get("FROM_NAME", "")
        parts = []
        if issues:
            parts.append("; ".join(issues))
        parts.append(f"key={key_mask}")
        parts.append(f"from_email={from_email or 'MISSING'}")
        parts.append(f"from_name={from_name or 'MISSING'}")
        return ", ".join(parts)

    def _log_config_diagnostics(self, context: str) -> None:
        """Emit structured warnings about why email sending is not configured."""
        diag = self.get_config_diagnostics()
        if diag.get("configured"):
            return
        issues = diag.get("issues", [])
        vals = diag.get("values", {})
        logger.warning("⚠️ Email service not configured — %s", context)
        if issues:
            for idx, msg in enumerate(issues, 1):
                logger.warning("   %d) %s", idx, msg)
        logger.warning(
            "   Values (masked): SENDGRID_API_KEY=%s, FROM_EMAIL=%s, FROM_NAME=%s",
            vals.get("SENDGRID_API_KEY", ""), vals.get("FROM_EMAIL", ""), vals.get("FROM_NAME", ""),
        )
    
    def _log_sendgrid_exception(self, context: str, error: Exception, to_email: Optional[str] = None) -> None:
        """Log structured details for SendGrid HTTP errors without leaking secrets."""
        try:
            # Try to import sendgrid's HTTPError for richer details
            from python_http_client.exceptions import HTTPError  # type: ignore
        except Exception:
            HTTPError = tuple()  # fallback to never matching

        status = getattr(error, "status_code", None)
        body = getattr(error, "body", None)
        headers = getattr(error, "headers", None)

        # If the exception provides a dict view, include masked summary
        details: Dict[str, Any] = {}
        try:
            to_dict = getattr(error, "to_dict", None)
            if callable(to_dict):
                details = to_dict()
        except Exception:
            pass

        try:
            masked_key = self._mask(self.sendgrid_api_key)
            logger.error("   SendGrid diagnostics → status=%s, from=%s, to=%s, key=%s", status, self.from_email, (to_email or ""), masked_key)
        except Exception:
            pass

        # Log response body (trimmed)
        try:
            if body:
                if isinstance(body, (bytes, bytearray)):
                    preview = body[:512].decode(errors="ignore")
                else:
                    preview = str(body)[:512]
                logger.error("   SendGrid response body (trimmed): %s", preview)
        except Exception:
            pass

        # Heuristics for common 403 causes
        try:
            hint = None
            text = ""
            if body:
                text = body.decode(errors="ignore") if isinstance(body, (bytes, bytearray)) else str(body)
            if (status == 403) or ("403" in str(error)):
                if "verified Sender Identity" in text or "from address does not match" in text:
                    hint = f"From address is not a verified Sender Identity in SendGrid. Current FROM_EMAIL={self.from_email}. Verify Single Sender or authenticate domain."
                elif "not have permission" in text or "not authorized" in text:
                    hint = "API key likely missing 'Mail Send' permission. Regenerate with 'Full Access' or at least 'Mail Send'."
                elif "ip" in text and "access" in text:
                    hint = "IP Access Management may be enabled. Whitelist the server IP in SendGrid (Settings → IP Access Management)."
            if hint:
                logger.error("   Hint: %s", hint)
        except Exception:
            pass
        try:
            ten = getattr(self, "tenant_name", "") or get_tenant_config().name
            if ten == "FXLabs":
                exp_key, exp_from_email, exp_from_name = (
                    "FXLABS_SENDGRID_API_KEY", "FXLABS_FROM_EMAIL", "FXLABS_FROM_NAME"
                )
            else:
                exp_key, exp_from_email, exp_from_name = (
                    "HEXTECH_SENDGRID_API_KEY", "HEXTECH_FROM_EMAIL", "HEXTECH_FROM_NAME"
                )
            logger.warning(
                "   Hint: configure tenant-specific email credentials (%s, %s, %s). No global defaults are used.",
                exp_key, exp_from_email, exp_from_name,
            )
        except Exception:
            pass
    
    def _update_alert_cooldown(self, alert_hash: str, triggered_pairs: List[Dict[str, Any]] = None):
        """Update the last sent timestamp and values for an alert"""
        self.alert_cooldowns[alert_hash] = datetime.now(timezone.utc)
        if triggered_pairs:
            self.alert_values[alert_hash] = triggered_pairs.copy()
    
    def _cleanup_old_cooldowns(self):
        """Clean up old cooldown entries to prevent memory leaks"""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)  # Keep only last 24 hours
        self.alert_cooldowns = {
            alert_hash: timestamp 
            for alert_hash, timestamp in self.alert_cooldowns.items()
            if timestamp > cutoff_time
        }
        # Also cleanup values for removed cooldowns
        self.alert_values = {
            alert_hash: values 
            for alert_hash, values in self.alert_values.items()
            if alert_hash in self.alert_cooldowns
        }

    # Digest helpers removed per product decision
    
    async def send_heatmap_alert(
        self, 
        user_email: str, 
        alert_name: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> bool:
        """Send heatmap alert email to user with cooldown protection"""
        
        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - Heatmap alert for {user_email} ({alert_name}) would have been sent")
            return True
        
        if not self.sg:
            self._log_config_diagnostics(context="heatmap alert email")
            return False
        # Unsubscribe support removed
        # Check smart cooldown before rate limit so attempts don't consume quota
        alert_hash = self._generate_alert_hash(user_email, alert_name, triggered_pairs)
        if self._is_alert_in_cooldown(alert_hash, triggered_pairs):
            logger.info(f"🕐 Heatmap alert for {user_email} ({alert_name}) is in cooldown period. Skipping email.")
            return False
        
        # Clean up old cooldowns periodically
        self._cleanup_old_cooldowns()
        
        try:
            # Create email content
            subject = f"Trading Alert: {alert_name}"
            
            # Build email body
            body = self._build_heatmap_alert_email_body(
                alert_name, triggered_pairs, alert_config
            )
            
            # Create email with text alternative and transactional headers
            text_body = self._build_plain_text_heatmap(alert_name, triggered_pairs, alert_config)
            mail = self._build_mail(
                subject=subject,
                to_email_addr=user_email,
                html_body=body,
                text_body=text_body,
                category="heatmap",
                ref_id=alert_hash[:24]
            )
            
            # Send email asynchronously
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ Heatmap alert email sent to {user_email}")
                # Update cooldown after successful send
                self._update_alert_cooldown(alert_hash, triggered_pairs)
                return True
            else:
                logger.error(f"❌ Failed to send email: status={response.status_code}")
                try:
                    body_preview = str(getattr(response, "body", ""))
                    if len(body_preview) > 512:
                        body_preview = body_preview[:512]
                    if body_preview:
                        logger.error(f"   Response body (trimmed): {body_preview}")
                except Exception:
                    pass
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending heatmap alert email: {e}")
            self._log_sendgrid_exception(context="heatmap", error=e, to_email=user_email)
            return False

    async def send_heatmap_tracker_alert(
        self,
        user_email: str,
        alert_name: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> bool:
        """Send Heatmap/Quantum Tracker (Probability Signal) email using simplified template."""

        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - Heatmap tracker alert for {user_email} ({alert_name}) would have been sent")
            return True

        if not self.sg:
            self._log_config_diagnostics(context="heatmap tracker alert email")
            return False

        # Smart cooldown/hash
        alert_hash = self._generate_alert_hash(user_email, alert_name, triggered_pairs)
        if self._is_alert_in_cooldown(alert_hash, triggered_pairs):
            logger.info(f"🕐 Heatmap tracker alert for {user_email} ({alert_name}) is in cooldown period. Skipping email.")
            return False

        self._cleanup_old_cooldowns()

        try:
            subject = f"Trading Alert: {alert_name}"
            body = self._build_heatmap_tracker_email_body(alert_name, triggered_pairs, alert_config)
            text_body = self._build_plain_text_heatmap_tracker(alert_name, triggered_pairs, alert_config)
            mail = self._build_mail(
                subject=subject,
                to_email_addr=user_email,
                html_body=body,
                text_body=text_body,
                category="heatmap-tracker",
                ref_id=alert_hash[:24]
            )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ Heatmap tracker alert email sent to {user_email}")
                self._update_alert_cooldown(alert_hash, triggered_pairs)
                return True
            else:
                logger.error(f"❌ Failed to send heatmap tracker alert email: status={response.status_code}")
                try:
                    body_preview = str(getattr(response, "body", ""))
                    if len(body_preview) > 512:
                        body_preview = body_preview[:512]
                    if body_preview:
                        logger.error(f"   Response body (trimmed): {body_preview}")
                except Exception:
                    pass
                return False
        except Exception as e:
            logger.error(f"❌ Error sending heatmap tracker alert email: {e}")
            self._log_sendgrid_exception(context="heatmap-tracker", error=e, to_email=user_email)
            return False
    
    def _build_heatmap_alert_email_body(
        self, 
        alert_name: str, 
        triggered_pairs: List[Dict[str, Any]], 
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML email body for heatmap alert"""
        
        # Get alert configuration details
        trading_style = alert_config.get("trading_style", "scalper")
        buy_threshold = f"{alert_config.get('buy_threshold_min', 70)}-{alert_config.get('buy_threshold_max', 100)}"
        sell_threshold = f"{alert_config.get('sell_threshold_min', 0)}-{alert_config.get('sell_threshold_max', 30)}"
        indicators = ", ".join(alert_config.get("selected_indicators", []))
        
        # Build triggered pairs table
        pairs_table = ""
        for pair in triggered_pairs:
            symbol = self._pair_display(pair.get("symbol", "N/A"))
            strength = pair.get("strength", 0)
            signal = pair.get("signal", "N/A")
            timeframe = pair.get("timeframe", "N/A")
            
            # Color coding for signals
            signal_color = "#28a745" if signal == "BUY" else "#dc3545" if signal == "SELL" else "#6c757d"
            
            pairs_table += f"""
            <tr style="border-bottom: 1px solid #dee2e6;">
                <td style="padding: 8px; font-weight: bold;">{symbol}</td>
                <td style="padding: 8px; text-align: center;">{strength}%</td>
                <td style="padding: 8px; text-align: center; color: {signal_color}; font-weight: bold;">{signal}</td>
                <td style="padding: 8px; text-align: center;">{timeframe}</td>
            </tr>
            """
        
        # Current timestamp (IST display)
        current_time = self._format_now_local(self.tz_name)
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Heatmap Alert - {alert_name}</title>
        </head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            {self._build_common_header('Heatmap', self.tz_name)}
            
            <!-- Alert Details -->
            <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h3 style="margin-top: 0; color: #495057;">Alert Configuration</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 5px 0; font-weight: bold; width: 40%;">Trading Style:</td>
                        <td style="padding: 5px 0;">{trading_style}</td>
                    </tr>
                    <tr>
                        <td style="padding: 5px 0; font-weight: bold;">Buy Threshold:</td>
                        <td style="padding: 5px 0;">{buy_threshold}%</td>
                    </tr>
                    <tr>
                        <td style="padding: 5px 0; font-weight: bold;">Sell Threshold:</td>
                        <td style="padding: 5px 0;">{sell_threshold}%</td>
                    </tr>
                    <tr>
                        <td style="padding: 5px 0; font-weight: bold;">Indicators:</td>
                        <td style="padding: 5px 0;">{indicators}</td>
                    </tr>
                </table>
            </div>
            
            <!-- Triggered Pairs -->
            <div style="margin-bottom: 20px;">
                <h3 style="color: #495057;">Triggered Currency Pairs ({len(triggered_pairs)} pairs)</h3>
                <table style="width: 100%; border-collapse: collapse; border: 1px solid #dee2e6; border-radius: 8px; overflow: hidden;">
                    <thead>
                        <tr style="background: #e9ecef;">
                            <th style="padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6;">Symbol</th>
                            <th style="padding: 12px; text-align: center; border-bottom: 2px solid #dee2e6;">Strength</th>
                            <th style="padding: 12px; text-align: center; border-bottom: 2px solid #dee2e6;">Signal</th>
                            <th style="padding: 12px; text-align: center; border-bottom: 2px solid #dee2e6;">Timeframe</th>
                        </tr>
                    </thead>
                    <tbody>
                        {pairs_table}
                    </tbody>
                </table>
            </div>
            
            <!-- Footer -->
            <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center; color: #6c757d; font-size: 14px;">
                <p style="margin: 0;">Alert triggered at: {current_time}</p>
                <p style="margin: 5px 0 0 0;">Powered by <strong>FX Labs</strong> - Advanced Trading Analytics</p>
            </div>
            
            <!-- Disclaimer -->
            <div style="margin-top: 20px; padding: 15px; background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px;">
                <p style="margin: 0; font-size: 12px; color: #856404;">
                    <strong>Disclaimer:</strong> This alert is for informational purposes only and should not be considered as financial advice. 
                    Always do your own research and consider your risk tolerance before making trading decisions.
                </p>
            </div>
            
        </body>
        </html>
        """
        
        return html_body

    def _build_heatmap_tracker_email_body(
        self,
        alert_name: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML email body for Heatmap/Quantum Tracker using simplified per‑pair cards."""

        cards: List[str] = []
        for pair in triggered_pairs:
            symbol = self._pair_display(pair.get("symbol", "N/A"))
            timeframe = pair.get("timeframe", "style-weighted")
            cond = str(pair.get("trigger_condition", "")).strip().upper()
            if cond == "BUY":
                probability = pair.get("buy_percent", 0)
                threshold = alert_config.get("buy_threshold", 70)
            else:
                probability = pair.get("sell_percent", 0)
                threshold = alert_config.get("sell_threshold", 30)
            color = "#0CCC7C" if cond == "BUY" else "#E5494D"

            # Contributors if known; otherwise "-"
            top_inds: Optional[str] = None
            try:
                selected = alert_config.get("selected_indicators") or []
                if isinstance(selected, list) and selected:
                    top_inds = ", ".join(selected)
            except Exception:
                top_inds = None
            if not top_inds:
                top_inds = "-"

            card = f"""
<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">
  <tr><td style=\"padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;\">New Signal Above Threshold</td></tr>
  <tr><td style=\"padding:20px;\">
    <div style=\"font-size:16px;margin-bottom:8px;\"><strong>{symbol}</strong> ({timeframe})</div>
    <div style=\"margin-bottom:12px;\">
      <span style=\"display:inline-block;padding:6px 12px;border-radius:999px;background:{color};color:#fff;font-weight:700;text-transform:uppercase;\">
        {cond} • {round(float(probability), 2)}%
      </span>
    </div>
    <div style=\"font-size:13px;color:#374151;\">Contributors: {top_inds}</div>
    <div style=\"margin-top:14px;padding:12px;background:#F9FAFB;border-radius:10px;font-size:13px;\">Your alert threshold: {threshold}%</div>
  </td></tr>
</table>
<div style=\"height:12px\"></div>
            """
            cards.append(card)

        return f"""
<!doctype html>
<html lang=\"en\">
<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>FxLabs • Probability Signal</title></head>
<body style=\"margin:0;background:#F5F7FB;\">\n
<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\"><tr><td align=\"center\" style=\"padding:24px 12px;\">\n{self._build_common_header('Probability Signal', self.tz_name)}\n{''.join(cards)}\n<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">Education only. © FxLabs AI</td></tr>\n</table>\n</td></tr></table>
</body></html>
        """

    def _build_plain_text_heatmap_tracker(self, alert_name: str, pairs: List[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
        lines = [
            f"Probability Signal - {alert_name}",
            f"Pairs: {len(pairs)}",
        ]
        for p in pairs:
            sym = self._pair_display(p.get("symbol", "?"))
            tf = p.get("timeframe", "style-weighted")
            cond = str(p.get("trigger_condition", "")).upper() or "N/A"
            if cond == "BUY":
                prob = p.get("buy_percent", "?")
                thr = cfg.get("buy_threshold", "?")
            else:
                prob = p.get("sell_percent", "?")
                thr = cfg.get("sell_threshold", "?")
            lines.append(f"- {sym} [{tf}]: {cond} {prob}% (thr {thr}%)")
        return "\n".join(lines)
    
    async def send_test_email(self, user_email: str) -> bool:
        """Send a test email to verify email service is working"""
        
        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - Test email for {user_email} would have been sent")
            return True
        
        if not self.sg:
            logger.warning("SendGrid not configured, cannot send test email")
            return False
        # Unsubscribe support removed
        
        try:
            subject = "System Test - FX Labs"
            
            body = f"""
            <!DOCTYPE html>
            <html>
            <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 0; background-color: #ffffff;">
                <div style="max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                    <div style="text-align: center; margin-bottom: 30px;">
                        <h1 style="color: #1a1a1a; font-size: 24px; font-weight: 600; margin: 0;">FX Labs</h1>
                        <p style="color: #666666; font-size: 14px; margin: 5px 0 0 0;">Trading System</p>
                    </div>
                    
                    <div style="background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 24px; margin-bottom: 24px;">
                        <h2 style="color: #1a1a1a; font-size: 18px; font-weight: 600; margin: 0 0 16px 0;">System Test</h2>
                        <p style="color: #4a5568; line-height: 1.6; margin: 0 0 12px 0;">Email delivery system operational.</p>
                        <p style="color: #718096; font-size: 14px; margin: 0;">{self._format_now_local(self.tz_name)}</p>
                    </div>
                    
                    <div style="text-align: center; padding-top: 20px; border-top: 1px solid #e9ecef;">
                        <p style="color: #a0aec0; font-size: 12px; margin: 0;">FX Labs Trading System</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text = "FX Labs System Test\nEmail delivery system operational."
            mail = self._build_mail(
                subject=subject,
                to_email_addr=user_email,
                html_body=body,
                text_body=text,
                category="test"
            )
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ Test email sent to {user_email}")
                return True
            else:
                logger.error(f"❌ Failed to send test email: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending test email: {e}")
            return False
    
    async def send_rsi_alert(
        self, 
        user_email: str, 
        alert_name: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> bool:
        """Send RSI alert email to user with cooldown protection"""
        
        logger.info(f"📧 RSI Alert Email Service - Starting email process")
        logger.info(f"   User: {user_email}")
        logger.info(f"   Alert: {alert_name}")
        logger.info(f"   Triggered pairs: {len(triggered_pairs)}")
        
        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - RSI alert for {user_email} ({alert_name}) would have been sent")
            return True
        
        if not self.sg:
            self._log_config_diagnostics(context="RSI alert email")
            return False
        # Unsubscribe support removed
        # Check smart cooldown before rate limit so attempts don't consume quota
        alert_hash = self._generate_alert_hash(user_email, alert_name, triggered_pairs)
        logger.info(f"🔍 Generated alert hash: {alert_hash[:16]}...")
        if self._is_alert_in_cooldown(alert_hash, triggered_pairs):
            logger.info(f"🕐 RSI alert for {user_email} ({alert_name}) is in cooldown period. Skipping email.")
            return False

        logger.info(f"✅ RSI alert passed cooldown check, proceeding with email")
        
        # Clean up old cooldowns periodically
        self._cleanup_old_cooldowns()
        
        try:
            # Create email content
            subject = f"RSI Alert - {alert_name}"
            logger.info(f"📝 Email subject: {subject}")
            
            # Build email body
            logger.info(f"🔨 Building RSI alert email body...")
            body = self._build_rsi_alert_email_body(
                alert_name, triggered_pairs, alert_config
            )
            logger.info(f"✅ Email body built successfully ({len(body)} characters)")
            
            # Create email with text alternative and transactional headers
            text_body = self._build_plain_text_rsi(alert_name, triggered_pairs, alert_config)
            mail = self._build_mail(
                subject=subject,
                to_email_addr=user_email,
                html_body=body,
                text_body=text_body,
                category="rsi",
                ref_id=alert_hash[:24]
            )
            logger.info(f"📧 Email object created successfully")
            
            # Send email asynchronously
            logger.info(f"📤 Sending RSI alert email via SendGrid...")
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            
            logger.info(f"📊 SendGrid response: Status {response.status_code}")
            try:
                # Log error body for non-2xx responses
                if response.status_code not in [200, 201, 202]:
                    body_preview = str(getattr(response, "body", ""))
                    if len(body_preview) > 512:
                        body_preview = body_preview[:512]
                    if body_preview:
                        logger.error(f"   Response body (trimmed): {body_preview}")
            except Exception:
                pass
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ RSI alert email sent successfully to {user_email}")
                logger.info(f"   Alert: {alert_name}")
                logger.info(f"   Pairs: {len(triggered_pairs)}")
                logger.info(f"   Response: {response.status_code}")
                
                # Update cooldown after successful send
                self._update_alert_cooldown(alert_hash, triggered_pairs)
                logger.info(f"⏰ Updated cooldown for alert hash: {alert_hash[:16]}...")
                return True
            else:
                logger.error(f"❌ Failed to send RSI alert email: status={response.status_code}")
                logger.error(f"   User: {user_email}")
                logger.error(f"   Alert: {alert_name}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending RSI alert email: {e}")
            logger.error(f"   User: {user_email}")
            logger.error(f"   Alert: {alert_name}")
            self._log_sendgrid_exception(context="rsi", error=e, to_email=user_email)
            return False

    async def send_custom_indicator_alert(
        self,
        user_email: str,
        alert_name: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> bool:
        """Send Custom Indicator alert email (flip to BUY/SELL) using compact template."""

        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - Custom indicator alert for {user_email} ({alert_name}) would have been sent")
            return True

        if not self.sg:
            self._log_config_diagnostics(context="custom indicator alert email")
            return False

        alert_hash = self._generate_alert_hash(user_email, alert_name, triggered_pairs)
        if self._is_alert_in_cooldown(alert_hash, triggered_pairs):
            logger.info(f"🕐 Custom indicator alert for {user_email} ({alert_name}) is in cooldown period. Skipping email.")
            return False

        self._cleanup_old_cooldowns()

        try:
            subject = f"Trading Alert: {alert_name}"
            body = self._build_custom_indicator_email_body(alert_name, triggered_pairs, alert_config)
            text_body = self._build_plain_text_custom_indicator(alert_name, triggered_pairs, alert_config)
            mail = self._build_mail(
                subject=subject,
                to_email_addr=user_email,
                html_body=body,
                text_body=text_body,
                category="custom-indicator",
                ref_id=alert_hash[:24]
            )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ Custom indicator alert email sent to {user_email}")
                self._update_alert_cooldown(alert_hash, triggered_pairs)
                return True
            else:
                logger.error(f"❌ Failed to send custom indicator alert email: status={response.status_code}")
                try:
                    body_preview = str(getattr(response, "body", ""))
                    if len(body_preview) > 512:
                        body_preview = body_preview[:512]
                    if body_preview:
                        logger.error(f"   Response body (trimmed): {body_preview}")
                except Exception:
                    pass
                return False
        except Exception as e:
            logger.error(f"❌ Error sending custom indicator alert email: {e}")
            self._log_sendgrid_exception(context="custom-indicator", error=e, to_email=user_email)
            return False
    
    def _build_rsi_alert_email_body(
        self, 
        alert_name: str, 
        triggered_pairs: List[Dict[str, Any]], 
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML email body for RSI alert using compact per‑pair cards"""

        # Build one card (provided template) per triggered pair
        cards: List[str] = []
        ts_local = self._format_now_local(self.tz_name)
        for pair in triggered_pairs:
            symbol = self._pair_display(pair.get("symbol", "N/A"))
            timeframe = pair.get("timeframe", "N/A")
            rsi_value = pair.get("rsi_value", 0)
            price = pair.get("current_price", 0)
            cond = str(pair.get("trigger_condition", "")).lower()
            if "overbought" in cond:
                zone = "Overbought"
            elif "oversold" in cond:
                zone = "Oversold"
            else:
                zone = "RSI signal"

            card = f"""
<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">
  <tr><td style=\"padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;\">RSI Alert • {symbol} ({timeframe})</td></tr>
  <tr><td style=\"padding:20px;\">
    <div style=\"margin-bottom:10px;\">RSI has entered <strong>{zone}</strong>.</div>
    <div style=\"font-size:14px;line-height:1.6\">
      <strong>Current RSI:</strong> {rsi_value}<br>
      <strong>Price:</strong> {price}<br>
      <strong>Time:</strong> {ts_local}
    </div>
    <div style=\"margin-top:16px;padding:12px;border-radius:10px;background:#F9FAFB;color:#374151;font-size:13px;\">
      Heads-up: Oversold/Overbought readings can precede reversals or trend continuation. Combine with your plan.
    </div>
  </td></tr>
</table>
<div style=\"height:12px\"></div>
            """
            cards.append(card)

        cards_html = "".join(cards)

        # Outer background wrapper (single body, multiple cards)
        return f"""
<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>FxLabs • RSI Alert</title>
</head>
<body style=\"margin:0;background:#F5F7FB;\">\n
<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\"><tr><td align=\"center\" style=\"padding:24px 12px;\">\n{self._build_common_header('RSI', self.tz_name)}\n{cards_html}\n<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">Not financial advice. © FxLabs AI</td></tr>\n</table>\n</td></tr></table>
</body></html>
        """

    def _build_custom_indicator_email_body(
        self,
        alert_name: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML for Custom Indicator signal using provided compact template."""

        cards: List[str] = []
        ts_local = self._format_now_local(self.tz_name)
        indicators_csv = ", ".join((alert_config.get("selected_indicators") or [])) or "-"
        for pair in triggered_pairs:
            symbol = self._pair_display(pair.get("symbol", "N/A"))
            timeframe = pair.get("timeframe", "N/A")
            cond = str(pair.get("trigger_condition", "")).strip().upper()
            color = "#0CCC7C" if cond == "BUY" else "#E5494D"
            if cond == "BUY":
                probability = pair.get("buy_percent", pair.get("probability", 0))
            else:
                probability = pair.get("sell_percent", pair.get("probability", 0))

            card = f"""
<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">
  <tr><td style=\"padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;\">Custom Indicator Alert</td></tr>
  <tr><td style=\"padding:20px;\">
    <div style=\"font-size:16px;margin-bottom:4px;\"><strong>{symbol}</strong></div>
    <div style=\"font-size:13px;color:#374151;margin-bottom:10px;\">Indicators selected: {indicators_csv}</div>
    <div style=\"margin-bottom:12px;\">
      <span style=\"display:inline-block;padding:6px 12px;border-radius:999px;background:{color};color:#fff;font-weight:700;text-transform:uppercase;\">
        {cond} • {round(float(probability), 2)}%
      </span>
    </div>
    <div style=\"font-size:13px;color:#374151;\">Generated at {ts_local} (TF: {timeframe})</div>
  </td></tr>
</table>
<div style=\"height:12px\"></div>
            """
            cards.append(card)

        return f"""
<!doctype html>
<html lang=\"en\">
<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>FxLabs • Custom Indicator Signal</title></head>
<body style=\"margin:0;background:#F5F7FB;\">\n
<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\"><tr><td align=\"center\" style=\"padding:24px 12px;\">\n{self._build_common_header('Indicator Tracker', self.tz_name)}\n{''.join(cards)}\n<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">Education only. © FxLabs AI</td></tr>\n</table>\n</td></tr></table>
</body></html>
        """

    def _build_plain_text_custom_indicator(self, alert_name: str, pairs: List[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
        lines = [
            f"Custom Indicator - {alert_name}",
            f"Pairs: {len(pairs)}",
        ]
        inds = ", ".join((cfg.get("selected_indicators") or [])) or "-"
        for p in pairs:
            sym = p.get("symbol", "?")
            tf = p.get("timeframe", "?")
            cond = str(p.get("trigger_condition", "")).upper() or "N/A"
            if cond == "BUY":
                prob = p.get("buy_percent", p.get("probability", "?"))
            else:
                prob = p.get("sell_percent", p.get("probability", "?"))
            lines.append(f"- {sym} [{tf}]: {cond} {prob}% | Indicators: {inds}")
        return "\n".join(lines)
    
    async def send_rsi_correlation_alert(
        self, 
        user_email: str, 
        alert_name: str,
        calculation_mode: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> bool:
        """Send RSI correlation alert email to user with cooldown protection"""
        
        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - RSI correlation alert for {user_email} ({alert_name}) would have been sent")
            return True
        
        if not self.sg:
            self._log_config_diagnostics(context="RSI correlation alert email")
            return False
        # Unsubscribe support removed
        # Check smart cooldown before rate limit so attempts don't consume quota
        alert_hash = self._generate_alert_hash(user_email, alert_name, triggered_pairs, calculation_mode)
        if self._is_alert_in_cooldown(alert_hash, triggered_pairs):
            logger.info(f"🕐 RSI correlation alert for {user_email} ({alert_name}) is in cooldown period. Skipping email.")
            return False

        # Clean up old cooldowns periodically
        self._cleanup_old_cooldowns()
        
        try:
            # Create email content
            subject = f"Trading Alert: {alert_name}"
            
            # Build email body
            body = self._build_rsi_correlation_alert_email_body(
                alert_name, calculation_mode, triggered_pairs, alert_config
            )
            
            # Create email with text alternative and transactional headers
            text_body = self._build_plain_text_corr(alert_name, calculation_mode, triggered_pairs, alert_config)
            mail = self._build_mail(
                subject=subject,
                to_email_addr=user_email,
                html_body=body,
                text_body=text_body,
                category="correlation",
                ref_id=alert_hash[:24]
            )
            
            # Send email asynchronously
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ RSI correlation alert email sent to {user_email}")
                # Update cooldown after successful send
                self._update_alert_cooldown(alert_hash, triggered_pairs)
                return True
            else:
                logger.error(f"❌ Failed to send RSI correlation alert email: status={response.status_code}")
                try:
                    body_preview = str(getattr(response, "body", ""))
                    if len(body_preview) > 512:
                        body_preview = body_preview[:512]
                    if body_preview:
                        logger.error(f"   Response body (trimmed): {body_preview}")
                except Exception:
                    pass
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending RSI correlation alert email: {e}")
            self._log_sendgrid_exception(context="rsi-correlation", error=e, to_email=user_email)
            return False
    
    def _build_rsi_correlation_alert_email_body(
        self, 
        alert_name: str, 
        calculation_mode: str,
        triggered_pairs: List[Dict[str, Any]], 
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML email body for RSI correlation alert"""

        # Use the provided compact template for REAL correlation mode (actual price correlation)
        if calculation_mode == "real_correlation":
            def format_expected_and_rule(pair: Dict[str, Any]) -> Tuple[str, str]:
                condition = str(pair.get("trigger_condition", "")).strip()
                strong_threshold = alert_config.get("strong_correlation_threshold", 0.70)
                moderate_threshold = alert_config.get("moderate_correlation_threshold", 0.30)
                weak_threshold = alert_config.get("weak_correlation_threshold", 0.15)

                # expected_corr shown based on rule triggered
                if condition == "strong_positive":
                    expected = f"≥ {strong_threshold:.2f}"
                    rule = "Strong positive correlation"
                elif condition == "strong_negative":
                    expected = f"≤ {-strong_threshold:.2f}"
                    rule = "Strong negative correlation"
                elif condition == "weak_correlation":
                    expected = f"|corr| ≤ {weak_threshold:.2f}"
                    rule = "Weak correlation"
                elif condition == "correlation_break":
                    expected = f"{moderate_threshold:.2f} ≤ |corr| < {strong_threshold:.2f}"
                    rule = "Correlation break from strong"
                else:
                    expected = "Configured threshold"
                    rule = condition.replace("_", " ").title() if condition else "Correlation signal"
                return expected, rule

            lookback = RSI_CORRELATION_WINDOW
            # Build one content block per triggered pair inside the container
            pair_blocks: List[str] = []
            for pair in triggered_pairs:
                pair_a = self._pair_display(pair.get("symbol1", "N/A"))
                pair_b = self._pair_display(pair.get("symbol2", "N/A"))
                timeframe = pair.get("timeframe", "N/A")
                actual_corr = pair.get("correlation_value", 0)
                expected_corr, trigger_rule = format_expected_and_rule(pair)

                block = f"""
    <tr><td style=\"padding:20px;\">
      <div style=\"margin-bottom:12px;\"><strong>{pair_a}</strong> vs <strong>{pair_b}</strong> • Window: {lookback} • TF: {timeframe}</div>
      <table role=\"presentation\" width=\"100%\" style=\"border:1px solid #E5E7EB;border-radius:10px\">
        <tr style=\"background:#F9FAFB;color:#6B7280;font-size:12px;\">
          <td style=\"padding:10px\">Expected</td><td style=\"padding:10px\">Actual Now</td><td style=\"padding:10px\">Trigger</td>
        </tr>
        <tr>
          <td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{expected_corr}</td>
          <td style=\"padding:10px;border-top:1px solid #E5E7EB;\"><strong>{actual_corr}</strong></td>
          <td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{trigger_rule}</td>
        </tr>
      </table>
      <div style=\"margin-top:14px;padding:12px;background:#FFF7ED;border:1px solid #FED7AA;border-radius:10px;font-size:13px;\">
        Signal: <strong>Mismatch detected</strong>. Consider hedge/arbitrage per your strategy.
      </div>
    </td></tr>
                """
                pair_blocks.append(block)

            blocks_html = "".join(pair_blocks)

            # Wrap with the outer container from the provided template
            return f"""
<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>FxLabs • Correlation Alert</title></head>
<body style=\"margin:0;background:#F5F7FB;\">\n
<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\"><tr><td align=\"center\" style=\"padding:24px 12px;\">\n{self._build_common_header('RSI', self.tz_name)}\n<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;\">RSI Alert</td></tr>\n  {blocks_html}\n</table>\n<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">Not financial advice. © FxLabs AI</td></tr>\n</table>\n</td></tr></table>
</body></html>
        """

    def _build_news_reminder_html(
        self,
        event_title: str,
        event_time_local: str,
        impact: str,
        previous: str,
        forecast: str,
        expected: str,
        bias: str,
    ) -> str:
        return f"""
<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>FxLabs • News Reminder</title></head>
<body style=\"margin:0;background:#F5F7FB;\">\n
<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\"><tr><td align=\"center\" style=\"padding:24px 12px;\">\n{self._build_common_header('News', self.tz_name)}\n
<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;\">Starts in 5 Minutes</td></tr>\n  <tr><td style=\"padding:20px;\">\n    <div style=\"font-size:16px;margin-bottom:6px;\"><strong>{event_title}</strong></div>\n    <div style=\"font-size:13px;color:#374151;margin-bottom:12px;\">\n      Time: {event_time_local} • Impact: <strong>{impact}</strong>\n    </div>\n    <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"border:1px solid #E5E7EB;border-radius:10px;width:100%;\">\n      <tr style=\"background:#F9FAFB;color:#6B7280;font-size:12px;\">\n        <td style=\"padding:10px\">Previous</td><td style=\"padding:10px\">Forecast</td><td style=\"padding:10px\">Expected</td><td style=\"padding:10px\">Bias</td>\n      </tr>\n      <tr>\n        <td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{previous}</td>\n        <td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{forecast}</td>\n        <td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{expected}</td>\n        <td style=\"padding:10px;border-top:1px solid #E5E7EB;\"><strong>{bias}</strong></td>\n      </tr>\n    </table>\n    <div style=\"margin-top:14px;padding:12px;background:#FEF3C7;border:1px solid #FDE68A;border-radius:10px;font-size:13px;\">\n      Volatility risk. Consider spreads, slippage and cooldown windows.\n    </div>\n  </td></tr>\n</table>\n
<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">Not financial advice. © FxLabs AI</td></tr>\n</table>\n
</td></tr></table>\n
</body></html>
        """

    def _build_news_reminder_text(
        self,
        event_title: str,
        event_time_local: str,
        impact: str,
        previous: str,
        forecast: str,
        expected: str,
        bias: str,
    ) -> str:
        return (
            f"Starts in 5 Minutes\n"
            f"{event_title}\n"
            f"Time: {event_time_local} • Impact: {impact}\n"
            f"Previous: {previous} | Forecast: {forecast} | Expected: {expected} | Bias: {bias}\n"
            f"Volatility risk. Consider spreads, slippage and cooldown windows.\n"
            f"Not financial advice. © FxLabs AI"
        )

    async def send_news_reminder(
        self,
        user_email: str,
        event_title: str,
        event_time_local: str,
        impact: Optional[str],
        previous: Optional[str],
        forecast: Optional[str],
        expected: Optional[str],
        bias: Optional[str],
    ) -> bool:
        """Send the 5-minute news reminder email to a user.

        No cooldown/rate-limit applies: this is a scheduled one-off per event.
        """
        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - News reminder for {user_email} ({event_title}) would have been sent")
            return True
        
        if not self.sg:
            self._log_config_diagnostics(context="news reminder email")
            return False

        # Normalize display values
        def _fmt(v: Optional[str], default: str = "-") -> str:
            try:
                s = (v or "").strip()
                return s if s else default
            except Exception:
                return default

        html = self._build_news_reminder_html(
            event_title=_fmt(event_title, "News Event"),
            event_time_local=_fmt(event_time_local, ""),
            impact=_fmt(impact, "-"),
            previous=_fmt(previous, "-"),
            forecast=_fmt(forecast, "-"),
            expected=_fmt(expected, "-"),
            bias=_fmt(bias, "-"),
        )
        text = self._build_news_reminder_text(
            event_title=_fmt(event_title, "News Event"),
            event_time_local=_fmt(event_time_local, ""),
            impact=_fmt(impact, "-"),
            previous=_fmt(previous, "-"),
            forecast=_fmt(forecast, "-"),
            expected=_fmt(expected, "-"),
            bias=_fmt(bias, "-"),
        )

        subject = "News reminder"
        mail = self._build_mail(
            subject=subject,
            to_email_addr=user_email,
            html_body=html,
            text_body=text,
            category="news-reminder",
            ref_id=None,
        )

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ News reminder sent to {user_email}")
                return True
            else:
                logger.error(f"❌ Failed to send news reminder: status={response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Error sending news reminder: {e}")
            return False

        # RSI threshold mode: use provided compact RSI correlation mismatch template with RSI correlation
        # One card per triggered pair
        if calculation_mode == "rsi_threshold":
            # Enforce RSI(14) for display
            rsi_len = 14
            def expected_and_rule(pair: Dict[str, Any]) -> Tuple[str, str]:
                condition = str(pair.get("trigger_condition", "")).strip()
                ob = alert_config.get("rsi_overbought_threshold", 70)
                os_ = alert_config.get("rsi_oversold_threshold", 30)
                if condition == "positive_mismatch":
                    exp = f"One ≥ {ob}, one ≤ {os_}"
                    rule = "Positive mismatch"
                elif condition == "negative_mismatch":
                    exp = f"Both ≥ {ob} or both ≤ {os_}"
                    rule = "Negative mismatch"
                elif condition == "neutral_break":
                    exp = f"Both between {os_} and {ob}"
                    rule = "Neutral break"
                else:
                    exp = "Configured RSI condition"
                    rule = condition.replace("_", " ").title() if condition else "RSI condition"
                return exp, rule

            cards: List[str] = []
            for pair in triggered_pairs:
                pair_a = self._pair_display(pair.get("symbol1", "N/A"))
                pair_b = self._pair_display(pair.get("symbol2", "N/A"))
                timeframe = pair.get("timeframe", "N/A")
                rsi_corr_now = pair.get("rsi_corr_now")
                expected_corr, trigger_rule = expected_and_rule(pair)

                card = f"""
<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">
  <tr><td style=\"padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;\">RSI Alert</td></tr>
  <tr><td style=\"padding:20px;\">
    <div style=\"margin-bottom:12px;\"><strong>{pair_a}</strong> vs <strong>{pair_b}</strong> • RSI({rsi_len}) • TF: {timeframe}</div>
    <table role=\"presentation\" width=\"100%\" style=\"border:1px solid #E5E7EB;border-radius:10px\">
      <tr style=\"background:#F9FAFB;color:#6B7280;font-size:12px;\">
        <td style=\"padding:10px\">Expected</td><td style=\"padding:10px\">RSI Corr Now</td><td style=\"padding:10px\">Trigger</td>
      </tr>
      <tr>
        <td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{expected_corr}</td>
        <td style=\"padding:10px;border-top:1px solid #E5E7EB;\"><strong>{rsi_corr_now if rsi_corr_now is not None else '-'}</strong></td>
        <td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{trigger_rule}</td>
      </tr>
    </table>
    <div style=\"margin-top:14px;padding:12px;background:#ECFEF3;border:1px solid #A7F3D0;border-radius:10px;font-size:13px;\">
      Note: RSI-based divergences can revert faster than price-corr; size risk accordingly.
    </div>
  </td></tr>
</table>
<div style=\"height:12px\"></div>
                """
                cards.append(card)

            return f"""
<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>FxLabs • RSI Alert</title></head>
<body style=\"margin:0;background:#F5F7FB;\">\n
<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\"><tr><td align=\"center\" style=\"padding:24px 12px;\">\n{self._build_common_header('RSI', self.tz_name)}\n{''.join(cards)}\n<table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n  <tr><td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">Not financial advice. © FxLabs AI</td></tr>\n</table>\n</td></tr></table>
</body></html>
            """

    def _build_daily_html(self, payload: Dict[str, Any]) -> str:
        def esc(v: Any) -> str:
            try:
                s = str(v)
                return s
            except Exception:
                return ""
        date_local = esc(payload.get("date_local", ""))
        time_label = esc(payload.get("time_label", ""))
        # Core signals rows
        rows = []
        for s in payload.get("core_signals", []) or []:
            pair = esc(s.get("pair", ""))
            signal = esc(s.get("signal", ""))
            probability = esc(s.get("probability", ""))
            tf = esc(s.get("tf", ""))
            badge_bg = esc(s.get("badge_bg", "#6B7280"))
            rows.append(f"""
                <tr>
                  <td style=\"padding:10px 8px;border-top:1px solid #F1F5F9;\">{pair}</td>
                  <td style=\"padding:10px 8px;border-top:1px solid #F1F5F9;\">
                    <span style=\"display:inline-block;padding:4px 10px;border-radius:999px;background:{badge_bg};color:#ffffff;font-size:12px;font-weight:700;text-transform:uppercase;\">{signal}</span>
                  </td>
                  <td style=\"padding:10px 8px;border-top:1px solid #F1F5F9;\">{probability}%</td>
                  <td style=\"padding:10px 8px;border-top:1px solid #F1F5F9;\">{tf}</td>
                </tr>
            """)
        core_html = "\n".join(rows)

        # H4 lists
        os_list = "\n".join([
            f"<div style=\"padding:6px 0;border-top:1px dashed #E5E7EB;\"><strong>{esc(x.get('pair',''))}</strong> • RSI {esc(x.get('rsi',''))}</div>"
            for x in (payload.get("rsi_oversold") or [])
        ])
        ob_list = "\n".join([
            f"<div style=\"padding:6px 0;border-top:1px dashed #E5E7EB;\"><strong>{esc(x.get('pair',''))}</strong> • RSI {esc(x.get('rsi',''))}</div>"
            for x in (payload.get("rsi_overbought") or [])
        ])

        # News rows
        news_rows = []
        for n in payload.get("news", []) or []:
            title = esc(n.get("title", ""))
            time_local = esc(n.get("time_local", ""))
            expected = esc(n.get("expected", "-"))
            forecast = esc(n.get("forecast", "-"))
            bias = esc(n.get("bias", "-"))
            news_rows.append(f"""
                <tr>
                  <td style=\"padding:10px;border-bottom:1px solid #E5E7EB;\">
                    <div style=\"font-size:14px;font-weight:700;\">{title} <span style=\"font-weight:400;color:#6B7280\">• {time_local}</span></div>
                    <div style=\"font-size:13px;margin-top:4px;\">
                      Exp: <strong>{expected}</strong> | Fcast: <strong>{forecast}</strong> | Bias: <strong>{bias}</strong>
                    </div>
                  </td>
                </tr>
            """)
        news_html = "\n".join(news_rows)

        return f"""
<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">\n<title>FxLabs • Daily Morning Brief</title>
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<style>
@media screen and (max-width:600px){{ .container{{width:100%!important}} .stack{{display:block!important;width:100%!important}}}}
</style>
</head>
<body style=\"margin:0;background:#F5F7FB;\">\n  <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\">\n    <tr>\n      <td align=\"center\" style=\"padding:24px 12px;\">\n        {self._build_common_header('Daily', payload.get('tz_name', self.tz_name), date_override=date_local, time_label_override=time_label)}\n        <table role=\"presentation\" class=\"container\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:600px;background:#ffffff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827;\">\n          <tr>\n            <td style=\"padding:20px;\">\n              <div style=\"font-weight:700;margin-bottom:8px;\">Signal Summary (Core Pairs)</div>\n              <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border-collapse:collapse;\">\n                <tr style=\"background:#F9FAFB;font-size:12px;color:#6B7280;\">\n                  <td style=\"padding:10px 8px;\">Pair</td>\n                  <td style=\"padding:10px 8px;\">Signal</td>\n                  <td style=\"padding:10px 8px;\">Probability</td>\n                  <td style=\"padding:10px 8px;\">Timeframe</td>\n                </tr>\n                {core_html}\n              </table>\n            </td>\n          </tr>\n\n          <tr>\n            <td style=\"padding:0 20px 20px;\">\n              <div style=\"font-weight:700;margin-bottom:8px;\">H4 Overbought / Oversold</div>\n              <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border:1px solid #E5E7EB;border-radius:10px;\">\n                {news_html}\n              </table>\n            </td>\n          </tr>\n\n          <tr>\n            <td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">\n              This information is for education only and not financial advice. © FxLabs AI\n            </td>\n          </tr>\n        </table>\n      </td>\n    </tr>\n  </table>\n</body>\n</html>
        """

    def _build_daily_text(self, payload: Dict[str, Any]) -> str:
        lines: List[str] = []
        header_date = payload.get('date_local','')
        header_time = payload.get('time_label','')
        header = f"FxLabs Daily • {header_date}"
        if header_time:
            header = f"{header} ({header_time})"
        lines.append(header)
        lines.append("")
        lines.append("Signal Summary (Core Pairs):")
        for s in payload.get("core_signals", []) or []:
            lines.append(f"- {s.get('pair','')}: {s.get('signal','')} {s.get('probability','')}% [{s.get('tf','')}]")
        lines.append("")
        lines.append("H4 Oversold:")
        for x in payload.get("rsi_oversold", []) or []:
            lines.append(f"- {x.get('pair','')}: RSI {x.get('rsi','')}")
        lines.append("H4 Overbought:")
        for x in payload.get("rsi_overbought", []) or []:
            lines.append(f"- {x.get('pair','')}: RSI {x.get('rsi','')}")
        lines.append("")
        lines.append("Today's High/Medium-Impact News:")
        for n in payload.get("news", []) or []:
            lines.append(f"- {n.get('time_local','')} • {n.get('title','')} (Exp {n.get('expected','-')}, Fcast {n.get('forecast','-')}, Bias {n.get('bias','-')})")
        lines.append("")
        lines.append("Education only. © FxLabs AI")
        return "\n".join(lines)

    async def send_daily_brief(self, user_email: str, payload: Dict[str, Any]) -> bool:
        # Check if email alerts are bypassed
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - Daily brief for {user_email} would have been sent")
            return True
        
        if not self.sg:
            self._log_config_diagnostics(context="daily brief email")
            return False
        subject = "FxLabs • Daily Morning Brief"
        html = self._build_daily_html(payload)
        text = self._build_daily_text(payload)
        mail = self._build_mail(
            subject=subject,
            to_email_addr=user_email,
            html_body=html,
            text_body=text,
            category="daily-brief",
            ref_id=None,
        )
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ Daily brief sent to {user_email}")
                return True
            logger.error(f"❌ Failed to send daily brief: status={response.status_code}")
            return False
        except Exception as e:
            logger.error(f"❌ Error sending daily brief: {e}")
            return False

    def _build_currency_strength_email_body(
        self,
        alert_name: str,
        timeframe: str,
        triggered_items: List[Dict[str, Any]],
        prev_winners: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build HTML body for Currency Strength alert (strongest/weakest changes)."""
        try:
            strongest = next((i for i in triggered_items if str(i.get("signal")).lower() == "strongest"), None)
            weakest = next((i for i in triggered_items if str(i.get("signal")).lower() == "weakest"), None)
        except Exception:
            strongest = None
            weakest = None

        s_sym = (strongest or {}).get("symbol", "-")
        s_val = (strongest or {}).get("strength", "-")
        w_sym = (weakest or {}).get("symbol", "-")
        w_val = (weakest or {}).get("strength", "-")

        prev_strong = (prev_winners or {}).get("strongest")
        prev_weak = (prev_winners or {}).get("weakest")

        ts_local = self._format_now_local(self.tz_name)

        body = f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
  <title>FxLabs • Currency Strength Alert</title>
  <style>
    .card{{background:#fff;border-radius:12px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;color:#111827}}
    .row{{display:flex;justify-content:space-between;}}
    .pill{{display:inline-block;padding:4px 8px;border-radius:999px;background:#EEF2FF;color:#3730A3;font-weight:700;font-size:12px;}}
  </style>
  </head>
  <body style=\"margin:0;background:#F5F7FB;\">\n
  <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F5F7FB;\"><tr><td align=\"center\" style=\"padding:24px 12px;\">\n
    {self._build_common_header('Currency Strength', self.tz_name)}

    <table role=\"presentation\" width=\"600\" cellpadding=\"0\" cellspacing=\"0\" class=\"card\">\n
      <tr><td style=\"padding:18px 20px;border-bottom:1px solid #E5E7EB;font-weight:700;\">{alert_name}</td></tr>
      <tr><td style=\"padding:16px 20px;font-size:14px;\">
         <div class=\"row\" style=\"margin-bottom:12px;\">
            <div><span class=\"pill\">Timeframe</span> <strong style=\"margin-left:6px;\">{timeframe}</strong></div>
            <div style=\"font-size:12px;color:#6B7280;\">{ts_local}</div>
         </div>
         <div style=\"margin-top:4px;margin-bottom:14px;color:#374151;\">The strongest/weakest currency has changed based on closed-bar returns.</div>
         <table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"border:1px solid #E5E7EB;border-radius:10px;overflow:hidden;\">
            <tr style=\"background:#F9FAFB;font-weight:600;\"><td style=\"padding:10px\">Role</td><td style=\"padding:10px\">Currency</td><td style=\"padding:10px\">Strength</td></tr>
            <tr><td style=\"padding:10px;color:#065F46;font-weight:700;\">Strongest</td><td style=\"padding:10px;\">{s_sym}</td><td style=\"padding:10px;\">{s_val}</td></tr>
            <tr><td style=\"padding:10px;color:#7F1D1D;font-weight:700;border-top:1px solid #E5E7EB;\">Weakest</td><td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{w_sym}</td><td style=\"padding:10px;border-top:1px solid #E5E7EB;\">{w_val}</td></tr>
         </table>
         <div style=\"margin-top:10px;color:#6B7280;font-size:12px;\">Previous: Strongest = {prev_strong or '-'}, Weakest = {prev_weak or '-'}
         </div>
      </td></tr>
      <tr><td style=\"padding:16px 20px;background:#F9FAFB;font-size:12px;color:#6B7280;border-top:1px solid #E5E7EB;\">For education only. © FxLabs AI</td></tr>
    </table>
  </td></tr></table>
  </body>
  </html>
        """
        return body

    async def send_currency_strength_alert(
        self,
        user_email: str,
        alert_name: str,
        timeframe: str,
        triggered_items: List[Dict[str, Any]],
        prev_winners: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send Currency Strength alert email.

        Semantics: fire on each change of strongest/weakest — bypass value-based cooldowns.
        """
        if BYPASS_EMAIL_ALERTS:
            logger.info(f"🚫 Email alerts bypassed - Currency strength alert for {user_email} ({alert_name}) would have been sent")
            return True
        if not self.sg:
            self._log_config_diagnostics(context="currency strength alert email")
            return False

        try:
            subject = f"Trading Alert: {alert_name}"
            html_body = self._build_currency_strength_email_body(alert_name, timeframe, triggered_items, prev_winners)
            # Build a simple text alternative
            try:
                strong = next((i for i in triggered_items if str(i.get('signal')).lower() == 'strongest'), None)
                weak = next((i for i in triggered_items if str(i.get('signal')).lower() == 'weakest'), None)
                lines = [
                    f"Currency Strength Alert - {alert_name}",
                    f"Timeframe: {timeframe}",
                    f"Strongest: {(strong or {}).get('symbol','-')} { (strong or {}).get('strength','-')}",
                    f"Weakest: {(weak or {}).get('symbol','-')} { (weak or {}).get('strength','-')}",
                ]
                text_body = "\n".join(lines)
            except Exception:
                text_body = f"Currency Strength Alert - {alert_name} ({timeframe})"

            # Do not use cooldown for this alert type; each change is actionable.
            mail = self._build_mail(
                subject=subject,
                to_email_addr=user_email,
                html_body=html_body,
                text_body=text_body,
                category="currency-strength",
            )
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.sg.send(mail))
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ Currency strength alert email sent to {user_email}")
                return True
            else:
                logger.error(f"❌ Failed to send currency strength alert email: status={response.status_code}")
                try:
                    body_preview = str(getattr(response, "body", ""))
                    if len(body_preview) > 512:
                        body_preview = body_preview[:512]
                    if body_preview:
                        logger.error(f"   Response body (trimmed): {body_preview}")
                except Exception:
                    pass
                return False
        except Exception as e:
            logger.error(f"❌ Error sending currency strength alert email: {e}")
            self._log_sendgrid_exception(context="currency-strength", error=e, to_email=user_email)
            return False

# Global email service instance
email_service = EmailService()

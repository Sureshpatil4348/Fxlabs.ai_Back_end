import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailService:
    """SendGrid email service for sending heatmap alerts"""
    
    def __init__(self):
        self.sendgrid_api_key = os.environ.get("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")
        self.from_email = os.environ.get("FROM_EMAIL", "Pinaxalabs@gmail.com")
        self.from_name = os.environ.get("FROM_NAME", "FX Labs Alerts")
        
        if not self.sendgrid_api_key:
            logger.warning("⚠️ SendGrid API key not found. Email notifications will be disabled.")
            self.sg = None
        else:
            self.sg = SendGridAPIClient(api_key=self.sendgrid_api_key)
            logger.info("✅ SendGrid email service initialized")
    
    async def send_heatmap_alert(
        self, 
        user_email: str, 
        alert_name: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> bool:
        """Send heatmap alert email to user"""
        
        if not self.sg:
            logger.warning("SendGrid not configured, skipping email")
            return False
        
        try:
            # Create email content
            subject = f"🔥 Heatmap Alert: {alert_name}"
            
            # Build email body
            body = self._build_heatmap_alert_email_body(
                alert_name, triggered_pairs, alert_config
            )
            
            # Create email
            from_email = Email(self.from_email, self.from_name)
            to_email = To(user_email)
            content = Content("text/html", body)
            
            mail = Mail(from_email, to_email, subject, content)
            
            # Send email asynchronously
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.sg.send(mail)
            )
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ Heatmap alert email sent to {user_email}")
                return True
            else:
                logger.error(f"❌ Failed to send email: {response.status_code} - {response.body}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending heatmap alert email: {e}")
            return False
    
    def _build_heatmap_alert_email_body(
        self, 
        alert_name: str, 
        triggered_pairs: List[Dict[str, Any]], 
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML email body for heatmap alert"""
        
        # Get alert configuration details
        trading_style = alert_config.get("trading_style", "dayTrader")
        buy_threshold = f"{alert_config.get('buy_threshold_min', 70)}-{alert_config.get('buy_threshold_max', 100)}"
        sell_threshold = f"{alert_config.get('sell_threshold_min', 0)}-{alert_config.get('sell_threshold_max', 30)}"
        indicators = ", ".join(alert_config.get("selected_indicators", []))
        
        # Build triggered pairs table
        pairs_table = ""
        for pair in triggered_pairs:
            symbol = pair.get("symbol", "N/A")
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
        
        # Current timestamp
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Heatmap Alert - {alert_name}</title>
        </head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            
            <!-- Header -->
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; text-align: center; margin-bottom: 20px;">
                <h1 style="margin: 0; font-size: 24px;">🔥 Heatmap Alert Triggered</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">{alert_name}</p>
            </div>
            
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
    
    async def send_test_email(self, user_email: str) -> bool:
        """Send a test email to verify email service is working"""
        
        if not self.sg:
            logger.warning("SendGrid not configured, cannot send test email")
            return False
        
        try:
            subject = "🧪 FX Labs - Email Service Test"
            
            body = f"""
            <!DOCTYPE html>
            <html>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h2>✅ Email Service Test Successful</h2>
                <p>Your email notifications are working correctly!</p>
                <p><strong>Test Time:</strong> {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}</p>
                <p>You will receive heatmap alerts at this email address.</p>
                <hr>
                <p style="color: #666; font-size: 12px;">Powered by FX Labs</p>
            </body>
            </html>
            """
            
            from_email = Email(self.from_email, self.from_name)
            to_email = To(user_email)
            content = Content("text/html", body)
            
            mail = Mail(from_email, to_email, subject, content)
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.sg.send(mail)
            )
            
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
        """Send RSI alert email to user"""
        
        if not self.sg:
            logger.warning("SendGrid not configured, skipping RSI alert email")
            return False
        
        try:
            # Create email content
            subject = f"📊 RSI Alert: {alert_name}"
            
            # Build email body
            body = self._build_rsi_alert_email_body(
                alert_name, triggered_pairs, alert_config
            )
            
            # Create email
            from_email = Email(self.from_email, self.from_name)
            to_email = To(user_email)
            content = Content("text/html", body)
            
            mail = Mail(from_email, to_email, subject, content)
            
            # Send email asynchronously
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.sg.send(mail)
            )
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ RSI alert email sent to {user_email}")
                return True
            else:
                logger.error(f"❌ Failed to send RSI alert email: {response.status_code} - {response.body}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending RSI alert email: {e}")
            return False
    
    def _build_rsi_alert_email_body(
        self, 
        alert_name: str, 
        triggered_pairs: List[Dict[str, Any]], 
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML email body for RSI alert"""
        
        # Build triggered pairs table
        pairs_table = ""
        for pair in triggered_pairs:
            symbol = pair.get("symbol", "N/A")
            timeframe = pair.get("timeframe", "N/A")
            rsi_value = pair.get("rsi_value", 0)
            rfi_score = pair.get("rfi_score", 0)
            condition = pair.get("trigger_condition", "N/A")
            price = pair.get("current_price", 0)
            price_change = pair.get("price_change_percent", 0)
            
            # Color code based on condition
            condition_color = "#e74c3c"  # Red for overbought
            if condition == "oversold":
                condition_color = "#27ae60"  # Green for oversold
            elif condition == "rfi_strong":
                condition_color = "#f39c12"  # Orange for RFI strong
            elif condition == "rfi_moderate":
                condition_color = "#3498db"  # Blue for RFI moderate
            
            pairs_table += f"""
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 12px; font-weight: bold; color: #2c3e50;">{symbol}</td>
                <td style="padding: 12px; color: #7f8c8d;">{timeframe}</td>
                <td style="padding: 12px; font-weight: bold; color: {condition_color};">{condition.replace('_', ' ').title()}</td>
                <td style="padding: 12px; color: #2c3e50;">{rsi_value}</td>
                <td style="padding: 12px; color: #2c3e50;">{rfi_score if rfi_score else 'N/A'}</td>
                <td style="padding: 12px; color: #2c3e50;">{price}</td>
                <td style="padding: 12px; color: {'#27ae60' if price_change >= 0 else '#e74c3c'};">{price_change:+.2f}%</td>
            </tr>
            """
        
        # Get alert configuration details
        rsi_period = alert_config.get("rsi_period", 14)
        rsi_overbought = alert_config.get("rsi_overbought_threshold", 70)
        rsi_oversold = alert_config.get("rsi_oversold_threshold", 30)
        alert_conditions = alert_config.get("alert_conditions", [])
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>RSI Alert - {alert_name}</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa;">
            <div style="max-width: 800px; margin: 0 auto; background-color: white; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center;">
                    <h1 style="color: white; margin: 0; font-size: 28px; font-weight: 300;">
                        📊 RSI Alert Triggered
                    </h1>
                    <p style="color: rgba(255, 255, 255, 0.9); margin: 10px 0 0 0; font-size: 16px;">
                        {alert_name}
                    </p>
                </div>
                
                <!-- Alert Summary -->
                <div style="padding: 30px; background-color: #fff;">
                    <div style="background-color: #e8f4fd; border-left: 4px solid #3498db; padding: 20px; margin-bottom: 25px; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #2c3e50; font-size: 18px;">🚨 Alert Summary</h3>
                        <p style="margin: 0; color: #34495e; line-height: 1.6;">
                            <strong>{len(triggered_pairs)} trading pair(s)</strong> have triggered your RSI alert conditions. 
                            Review the details below and consider your trading strategy.
                        </p>
                    </div>
                    
                    <!-- Alert Configuration -->
                    <div style="background-color: #f8f9fa; padding: 20px; margin-bottom: 25px; border-radius: 8px; border: 1px solid #e9ecef;">
                        <h3 style="margin: 0 0 15px 0; color: #2c3e50; font-size: 16px;">⚙️ Alert Configuration</h3>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                            <div>
                                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">RSI Period</strong>
                                <p style="margin: 5px 0 0 0; color: #2c3e50; font-size: 14px;">{rsi_period}</p>
                            </div>
                            <div>
                                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Overbought Threshold</strong>
                                <p style="margin: 5px 0 0 0; color: #e74c3c; font-size: 14px;">{rsi_overbought}</p>
                            </div>
                            <div>
                                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Oversold Threshold</strong>
                                <p style="margin: 5px 0 0 0; color: #27ae60; font-size: 14px;">{rsi_oversold}</p>
                            </div>
                            <div>
                                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Alert Conditions</strong>
                                <p style="margin: 5px 0 0 0; color: #2c3e50; font-size: 14px;">{', '.join([c.replace('_', ' ').title() for c in alert_conditions])}</p>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Triggered Pairs Table -->
                    <h3 style="margin: 0 0 20px 0; color: #2c3e50; font-size: 18px;">📈 Triggered Trading Pairs</h3>
                    <div style="overflow-x: auto;">
                        <table style="width: 100%; border-collapse: collapse; background-color: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);">
                            <thead>
                                <tr style="background-color: #34495e; color: white;">
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Symbol</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Timeframe</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Condition</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">RSI Value</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">RFI Score</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Current Price</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Price Change</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pairs_table}
                            </tbody>
                        </table>
                    </div>
                    
                    <!-- Trading Tips -->
                    <div style="background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 20px; margin-top: 25px; border-radius: 8px;">
                        <h3 style="margin: 0 0 15px 0; color: #856404; font-size: 16px;">💡 Trading Tips</h3>
                        <ul style="margin: 0; padding-left: 20px; color: #856404; line-height: 1.6;">
                            <li><strong>Overbought (RSI ≥ {rsi_overbought}):</strong> Consider potential selling opportunities or short positions</li>
                            <li><strong>Oversold (RSI ≤ {rsi_oversold}):</strong> Look for potential buying opportunities or long positions</li>
                            <li><strong>RFI Strong:</strong> High volume and price movement - strong signal reliability</li>
                            <li><strong>RFI Moderate:</strong> Moderate volume activity - use with other indicators</li>
                            <li>Always combine RSI signals with other technical analysis tools</li>
                            <li>Consider market context and overall trend before making trading decisions</li>
                        </ul>
                    </div>
                </div>
                
                <!-- Footer -->
                <div style="background-color: #2c3e50; padding: 25px; text-align: center;">
                    <p style="color: #bdc3c7; margin: 0 0 10px 0; font-size: 14px;">
                        This alert was generated by <strong style="color: #ecf0f1;">FX Labs</strong>
                    </p>
                    <p style="color: #95a5a6; margin: 0; font-size: 12px;">
                        {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}
                    </p>
                    <div style="margin-top: 15px;">
                        <a href="#" style="color: #3498db; text-decoration: none; font-size: 12px; margin: 0 10px;">Manage Alerts</a>
                        <a href="#" style="color: #3498db; text-decoration: none; font-size: 12px; margin: 0 10px;">Trading Dashboard</a>
                        <a href="#" style="color: #3498db; text-decoration: none; font-size: 12px; margin: 0 10px;">Support</a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
    
    async def send_rsi_correlation_alert(
        self, 
        user_email: str, 
        alert_name: str,
        calculation_mode: str,
        triggered_pairs: List[Dict[str, Any]],
        alert_config: Dict[str, Any]
    ) -> bool:
        """Send RSI correlation alert email to user"""
        
        if not self.sg:
            logger.warning("SendGrid not configured, skipping RSI correlation alert email")
            return False
        
        try:
            # Create email content
            subject = f"🔗 RSI Correlation Alert: {alert_name}"
            
            # Build email body
            body = self._build_rsi_correlation_alert_email_body(
                alert_name, calculation_mode, triggered_pairs, alert_config
            )
            
            # Create email
            from_email = Email(self.from_email, self.from_name)
            to_email = To(user_email)
            content = Content("text/html", body)
            
            mail = Mail(from_email, to_email, subject, content)
            
            # Send email asynchronously
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.sg.send(mail)
            )
            
            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ RSI correlation alert email sent to {user_email}")
                return True
            else:
                logger.error(f"❌ Failed to send RSI correlation alert email: {response.status_code} - {response.body}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error sending RSI correlation alert email: {e}")
            return False
    
    def _build_rsi_correlation_alert_email_body(
        self, 
        alert_name: str, 
        calculation_mode: str,
        triggered_pairs: List[Dict[str, Any]], 
        alert_config: Dict[str, Any]
    ) -> str:
        """Build HTML email body for RSI correlation alert"""
        
        # Build triggered pairs table
        pairs_table = ""
        for pair in triggered_pairs:
            symbol1 = pair.get("symbol1", "N/A")
            symbol2 = pair.get("symbol2", "N/A")
            timeframe = pair.get("timeframe", "N/A")
            condition = pair.get("trigger_condition", "N/A")
            price1 = pair.get("current_price1", 0)
            price2 = pair.get("current_price2", 0)
            price_change1 = pair.get("price_change1", 0)
            price_change2 = pair.get("price_change2", 0)
            
            # Color code based on condition
            condition_color = "#3498db"  # Blue for default
            if "positive" in condition:
                condition_color = "#27ae60"  # Green for positive
            elif "negative" in condition:
                condition_color = "#e74c3c"  # Red for negative
            elif "weak" in condition:
                condition_color = "#f39c12"  # Orange for weak
            elif "break" in condition:
                condition_color = "#9b59b6"  # Purple for break
            
            # Add mode-specific data
            if calculation_mode == "rsi_threshold":
                rsi1 = pair.get("rsi1", 0)
                rsi2 = pair.get("rsi2", 0)
                rsi_data = f"""
                <td style="padding: 12px; color: #2c3e50;">{rsi1}</td>
                <td style="padding: 12px; color: #2c3e50;">{rsi2}</td>
                <td style="padding: 12px; color: #7f8c8d;">-</td>
                """
            else:  # real_correlation
                correlation = pair.get("correlation_value", 0)
                rsi_data = f"""
                <td style="padding: 12px; color: #7f8c8d;">-</td>
                <td style="padding: 12px; color: #7f8c8d;">-</td>
                <td style="padding: 12px; color: #2c3e50; font-weight: bold;">{correlation}</td>
                """
            
            pairs_table += f"""
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 12px; font-weight: bold; color: #2c3e50;">{symbol1}</td>
                <td style="padding: 12px; font-weight: bold; color: #2c3e50;">{symbol2}</td>
                <td style="padding: 12px; color: #7f8c8d;">{timeframe}</td>
                <td style="padding: 12px; font-weight: bold; color: {condition_color};">{condition.replace('_', ' ').title()}</td>
                {rsi_data}
                <td style="padding: 12px; color: #2c3e50;">{price1}</td>
                <td style="padding: 12px; color: #2c3e50;">{price2}</td>
                <td style="padding: 12px; color: {'#27ae60' if price_change1 >= 0 else '#e74c3c'};">{price_change1:+.2f}%</td>
                <td style="padding: 12px; color: {'#27ae60' if price_change2 >= 0 else '#e74c3c'};">{price_change2:+.2f}%</td>
            </tr>
            """
        
        # Get alert configuration details
        mode_display = "RSI Threshold" if calculation_mode == "rsi_threshold" else "Real Correlation"
        
        if calculation_mode == "rsi_threshold":
            rsi_period = alert_config.get("rsi_period", 14)
            rsi_overbought = alert_config.get("rsi_overbought_threshold", 70)
            rsi_oversold = alert_config.get("rsi_oversold_threshold", 30)
            config_details = f"""
            <div>
                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">RSI Period</strong>
                <p style="margin: 5px 0 0 0; color: #2c3e50; font-size: 14px;">{rsi_period}</p>
            </div>
            <div>
                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Overbought Threshold</strong>
                <p style="margin: 5px 0 0 0; color: #e74c3c; font-size: 14px;">{rsi_overbought}</p>
            </div>
            <div>
                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Oversold Threshold</strong>
                <p style="margin: 5px 0 0 0; color: #27ae60; font-size: 14px;">{rsi_oversold}</p>
            </div>
            """
        else:  # real_correlation
            correlation_window = alert_config.get("correlation_window", 50)
            strong_threshold = alert_config.get("strong_correlation_threshold", 0.70)
            moderate_threshold = alert_config.get("moderate_correlation_threshold", 0.30)
            weak_threshold = alert_config.get("weak_correlation_threshold", 0.15)
            config_details = f"""
            <div>
                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Correlation Window</strong>
                <p style="margin: 5px 0 0 0; color: #2c3e50; font-size: 14px;">{correlation_window}</p>
            </div>
            <div>
                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Strong Threshold</strong>
                <p style="margin: 5px 0 0 0; color: #e74c3c; font-size: 14px;">{strong_threshold}</p>
            </div>
            <div>
                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Moderate Threshold</strong>
                <p style="margin: 5px 0 0 0; color: #f39c12; font-size: 14px;">{moderate_threshold}</p>
            </div>
            <div>
                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Weak Threshold</strong>
                <p style="margin: 5px 0 0 0; color: #3498db; font-size: 14px;">{weak_threshold}</p>
            </div>
            """
        
        alert_conditions = alert_config.get("alert_conditions", [])
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>RSI Correlation Alert - {alert_name}</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa;">
            <div style="max-width: 900px; margin: 0 auto; background-color: white; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center;">
                    <h1 style="color: white; margin: 0; font-size: 28px; font-weight: 300;">
                        🔗 RSI Correlation Alert Triggered
                    </h1>
                    <p style="color: rgba(255, 255, 255, 0.9); margin: 10px 0 0 0; font-size: 16px;">
                        {alert_name} - {mode_display} Mode
                    </p>
                </div>
                
                <!-- Alert Summary -->
                <div style="padding: 30px; background-color: #fff;">
                    <div style="background-color: #e8f4fd; border-left: 4px solid #3498db; padding: 20px; margin-bottom: 25px; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #2c3e50; font-size: 18px;">🚨 Correlation Alert Summary</h3>
                        <p style="margin: 0; color: #34495e; line-height: 1.6;">
                            <strong>{len(triggered_pairs)} correlation pair(s)</strong> have triggered your RSI correlation alert conditions. 
                            Review the correlation analysis below and consider your trading strategy.
                        </p>
                    </div>
                    
                    <!-- Alert Configuration -->
                    <div style="background-color: #f8f9fa; padding: 20px; margin-bottom: 25px; border-radius: 8px; border: 1px solid #e9ecef;">
                        <h3 style="margin: 0 0 15px 0; color: #2c3e50; font-size: 16px;">⚙️ Alert Configuration</h3>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                            <div>
                                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Calculation Mode</strong>
                                <p style="margin: 5px 0 0 0; color: #2c3e50; font-size: 14px; font-weight: bold;">{mode_display}</p>
                            </div>
                            <div>
                                <strong style="color: #7f8c8d; font-size: 12px; text-transform: uppercase;">Alert Conditions</strong>
                                <p style="margin: 5px 0 0 0; color: #2c3e50; font-size: 14px;">{', '.join([c.replace('_', ' ').title() for c in alert_conditions])}</p>
                            </div>
                            {config_details}
                        </div>
                    </div>
                    
                    <!-- Triggered Pairs Table -->
                    <h3 style="margin: 0 0 20px 0; color: #2c3e50; font-size: 18px;">📈 Triggered Correlation Pairs</h3>
                    <div style="overflow-x: auto;">
                        <table style="width: 100%; border-collapse: collapse; background-color: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);">
                            <thead>
                                <tr style="background-color: #34495e; color: white;">
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Symbol 1</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Symbol 2</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Timeframe</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Condition</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">RSI 1</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">RSI 2</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Correlation</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Price 1</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Price 2</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Change 1</th>
                                    <th style="padding: 15px; text-align: left; font-weight: 600; font-size: 14px;">Change 2</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pairs_table}
                            </tbody>
                        </table>
                    </div>
                    
                    <!-- Trading Tips -->
                    <div style="background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 20px; margin-top: 25px; border-radius: 8px;">
                        <h3 style="margin: 0 0 15px 0; color: #856404; font-size: 16px;">💡 Correlation Trading Tips</h3>
                        <ul style="margin: 0; padding-left: 20px; color: #856404; line-height: 1.6;">
                            <li><strong>Positive Mismatch:</strong> One pair overbought, one oversold - potential divergence opportunity</li>
                            <li><strong>Negative Mismatch:</strong> Both pairs in same extreme zone - trend continuation signal</li>
                            <li><strong>Strong Positive Correlation:</strong> Pairs moving together - hedge or pair trading opportunities</li>
                            <li><strong>Strong Negative Correlation:</strong> Pairs moving opposite - diversification benefits</li>
                            <li><strong>Weak Correlation:</strong> Pairs moving independently - individual analysis needed</li>
                            <li><strong>Correlation Break:</strong> Relationship changing - monitor for trend shifts</li>
                            <li>Always consider market context and overall trend before making trading decisions</li>
                        </ul>
                    </div>
                </div>
                
                <!-- Footer -->
                <div style="background-color: #2c3e50; padding: 25px; text-align: center;">
                    <p style="color: #bdc3c7; margin: 0 0 10px 0; font-size: 14px;">
                        This alert was generated by <strong style="color: #ecf0f1;">FX Labs</strong>
                    </p>
                    <p style="color: #95a5a6; margin: 0; font-size: 12px;">
                        {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}
                    </p>
                    <div style="margin-top: 15px;">
                        <a href="#" style="color: #3498db; text-decoration: none; font-size: 12px; margin: 0 10px;">Manage Alerts</a>
                        <a href="#" style="color: #3498db; text-decoration: none; font-size: 12px; margin: 0 10px;">Trading Dashboard</a>
                        <a href="#" style="color: #3498db; text-decoration: none; font-size: 12px; margin: 0 10px;">Support</a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

# Global email service instance
email_service = EmailService()

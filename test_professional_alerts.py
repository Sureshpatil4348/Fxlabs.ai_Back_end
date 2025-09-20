#!/usr/bin/env python3
"""
Professional Alert Test Suite
Tests trading alert notifications with realistic market data simulation
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from typing import Dict, List, Any

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.email_service import EmailService

class ProfessionalAlertTester:
    """Test suite for professional trading alert notifications"""
    
    def __init__(self):
        self.email_service = EmailService()
        self.test_email = "theashish.y@gmail.com"
        
    def create_realistic_rsi_alert_data(self) -> List[Dict[str, Any]]:
        """Create realistic RSI alert data based on current market conditions"""
        return [
            {
                "symbol": "EURUSD",
                "timeframe": "1H",
                "rsi_value": 78.5,
                "trigger_condition": "overbought",
                "current_price": 1.0856,
                "price_change_percent": 0.45,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rsi_period": 14,
                "rfi_score": 0.82
            },
            {
                "symbol": "GBPUSD",
                "timeframe": "4H", 
                "rsi_value": 25.2,
                "trigger_condition": "oversold",
                "current_price": 1.2634,
                "price_change_percent": -0.32,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rsi_period": 14,
                "rfi_score": 0.15
            }
        ]
    
    def create_realistic_heatmap_alert_data(self) -> List[Dict[str, Any]]:
        """Create realistic heatmap alert data"""
        return [
            {
                "symbol": "EURUSD",
                "timeframe": "1H",
                "strength": 85.2,
                "indicators": {
                    "rsi": 78.5,
                    "macd": 0.0023,
                    "bollinger": 1.8
                },
                "trading_style": "scalping",
                "signal": "buy",
                "current_price": 1.0856,
                "price_change_percent": 0.45,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            {
                "symbol": "USDJPY",
                "timeframe": "4H",
                "strength": 72.8,
                "indicators": {
                    "rsi": 65.2,
                    "macd": -0.0015,
                    "bollinger": -0.9
                },
                "trading_style": "swing",
                "signal": "sell",
                "current_price": 149.23,
                "price_change_percent": -0.28,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    
    def create_realistic_rsi_correlation_alert_data(self) -> List[Dict[str, Any]]:
        """Create realistic RSI correlation alert data"""
        return [
            {
                "symbol1": "EURUSD",
                "symbol2": "GBPUSD",
                "timeframe": "1H",
                "rsi1_value": 78.5,
                "rsi2_value": 25.2,
                "correlation": 0.85,
                "signal": "divergence",
                "current_price1": 1.0856,
                "current_price2": 1.2634,
                "price_change1": 0.45,
                "price_change2": -0.32,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    
    async def test_professional_rsi_alert(self):
        """Test professional RSI alert notification"""
        print("🧪 Testing Professional RSI Alert")
        print("=" * 40)
        
        alert_name = "EURUSD & GBPUSD RSI Alert"
        triggered_pairs = self.create_realistic_rsi_alert_data()
        alert_config = {
            "pairs": ["EURUSD", "GBPUSD"],
            "timeframes": ["1H", "4H"],
            "rsi_period": 14,
            "overbought_threshold": 70,
            "oversold_threshold": 30,
            "alert_conditions": ["overbought", "oversold"],
            "notification_methods": ["email"],
            "alert_frequency": "hourly"
        }
        
        print(f"📊 Alert: {alert_name}")
        print(f"📈 Triggered Pairs: {len(triggered_pairs)}")
        for pair in triggered_pairs:
            print(f"   • {pair['symbol']} ({pair['timeframe']}): RSI {pair['rsi_value']} - {pair['trigger_condition']}")
        print()
        
        try:
            success = await self.email_service.send_rsi_alert(
                user_email=self.test_email,
                alert_name=alert_name,
                triggered_pairs=triggered_pairs,
                alert_config=alert_config
            )
            
            if success:
                print("✅ Professional RSI Alert sent successfully!")
                print(f"📧 Check your inbox: {self.test_email}")
                return True
            else:
                print("❌ Failed to send professional RSI alert")
                return False
                
        except Exception as e:
            print(f"❌ Error sending professional RSI alert: {e}")
            return False
    
    async def test_professional_heatmap_alert(self):
        """Test professional heatmap alert notification"""
        print("🧪 Testing Professional Heatmap Alert")
        print("=" * 40)
        
        alert_name = "Multi-Currency Heatmap Alert"
        triggered_pairs = self.create_realistic_heatmap_alert_data()
        alert_config = {
            "pairs": ["EURUSD", "USDJPY"],
            "timeframes": ["1H", "4H"],
            "selected_indicators": ["rsi", "macd", "bollinger"],
            "trading_style": "mixed",
            "buy_threshold_min": 70,
            "buy_threshold_max": 90,
            "sell_threshold_min": 10,
            "sell_threshold_max": 30,
            "notification_methods": ["email"],
            "alert_frequency": "hourly"
        }
        
        print(f"🔥 Alert: {alert_name}")
        print(f"📊 Triggered Pairs: {len(triggered_pairs)}")
        for pair in triggered_pairs:
            print(f"   • {pair['symbol']} ({pair['timeframe']}): Strength {pair['strength']} - Signal: {pair['signal']}")
        print()
        
        try:
            success = await self.email_service.send_heatmap_alert(
                user_email=self.test_email,
                alert_name=alert_name,
                triggered_pairs=triggered_pairs,
                alert_config=alert_config
            )
            
            if success:
                print("✅ Professional Heatmap Alert sent successfully!")
                print(f"📧 Check your inbox: {self.test_email}")
                return True
            else:
                print("❌ Failed to send professional heatmap alert")
                return False
                
        except Exception as e:
            print(f"❌ Error sending professional heatmap alert: {e}")
            return False
    
    async def test_professional_rsi_correlation_alert(self):
        """Test professional RSI correlation alert notification"""
        print("🧪 Testing Professional RSI Correlation Alert")
        print("=" * 40)
        
        alert_name = "EURUSD-GBPUSD Correlation Alert"
        triggered_pairs = self.create_realistic_rsi_correlation_alert_data()
        alert_config = {
            "pairs": ["EURUSD", "GBPUSD"],
            "timeframes": ["1H"],
            "rsi_period": 14,
            "correlation_threshold": 0.8,
            "notification_methods": ["email"],
            "alert_frequency": "hourly"
        }
        
        print(f"🔗 Alert: {alert_name}")
        print(f"📈 Triggered Pairs: {len(triggered_pairs)}")
        for pair in triggered_pairs:
            print(f"   • {pair['symbol1']}-{pair['symbol2']}: RSI1 {pair['rsi1_value']}, RSI2 {pair['rsi2_value']}, Correlation {pair['correlation']}")
        print()
        
        try:
            success = await self.email_service.send_rsi_correlation_alert(
                user_email=self.test_email,
                alert_name=alert_name,
                calculation_mode="divergence",
                triggered_pairs=triggered_pairs,
                alert_config=alert_config
            )
            
            if success:
                print("✅ Professional RSI Correlation Alert sent successfully!")
                print(f"📧 Check your inbox: {self.test_email}")
                return True
            else:
                print("❌ Failed to send professional RSI correlation alert")
                return False
                
        except Exception as e:
            print(f"❌ Error sending professional RSI correlation alert: {e}")
            return False
    
    async def run_all_professional_tests(self):
        """Run all professional alert notification tests"""
        print("🚀 Professional Trading Alert Test Suite")
        print("Testing premium, professional, minimal transactional emails")
        print("=" * 60)
        print()
        
        if not self.email_service.sg:
            print("❌ SendGrid not configured!")
            return False
        
        print(f"📧 From: {self.email_service.from_email}")
        print(f"👤 From Name: {self.email_service.from_name}")
        print(f"📬 Test Email: {self.test_email}")
        print()
        
        results = []
        
        # Test Professional RSI Alert
        rsi_result = await self.test_professional_rsi_alert()
        results.append(("Professional RSI Alert", rsi_result))
        print()
        
        # Test Professional Heatmap Alert
        heatmap_result = await self.test_professional_heatmap_alert()
        results.append(("Professional Heatmap Alert", heatmap_result))
        print()
        
        # Test Professional RSI Correlation Alert
        correlation_result = await self.test_professional_rsi_correlation_alert()
        results.append(("Professional RSI Correlation Alert", correlation_result))
        print()
        
        # Summary
        print("📊 Professional Test Results:")
        print("=" * 30)
        for test_name, result in results:
            status = "✅ PASSED" if result else "❌ FAILED"
            print(f"{test_name}: {status}")
        
        total_passed = sum(1 for _, result in results if result)
        total_tests = len(results)
        
        print()
        print(f"🎯 Overall: {total_passed}/{total_tests} tests passed")
        
        if total_passed == total_tests:
            print("🎉 All professional alert notifications working!")
            print("📧 Check your inbox for premium, professional emails")
            print("🚀 Trading alert system ready with professional formatting!")
        else:
            print("⚠️ Some tests failed - check configuration")
        
        return total_passed == total_tests

async def main():
    """Main test function"""
    print("🧪 Professional Trading Alert Test")
    print("Testing premium, professional, minimal transactional emails")
    print()
    
    # Check if running from correct directory
    if not os.path.exists("app/email_service.py"):
        print("❌ Error: Cannot find app/email_service.py")
        print("💡 Make sure to run this script from the Fxlabs.ai_Back_end directory")
        return
    
    tester = ProfessionalAlertTester()
    success = await tester.run_all_professional_tests()
    
    if success:
        print("\n🎯 All professional tests completed successfully!")
        print("📧 You should receive 3 professional alert emails")
        print("✅ Trading alert system is production ready with premium formatting!")
    else:
        print("\n💥 Some tests failed - check configuration")

if __name__ == "__main__":
    asyncio.run(main())

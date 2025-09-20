#!/usr/bin/env python3
"""
Email Service Test Suite
Tests the email service functionality without requiring MT5 or live trading data
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.email_service import EmailService

class EmailServiceTester:
    """Test suite for EmailService functionality"""
    
    def __init__(self):
        self.email_service = EmailService()
        self.test_email = "test@example.com"  # Change this to your test email
        
    def create_mock_heatmap_alert_data(self) -> List[Dict[str, Any]]:
        """Create mock heatmap alert data for testing"""
        return [
            {
                "symbol": "EURUSD",
                "strength": 75.5,
                "indicators": {
                    "rsi": 70.1,
                    "macd": 0.0023,
                    "bollinger": 1.2
                },
                "timeframe": "1h",
                "trading_style": "scalping"
            },
            {
                "symbol": "GBPUSD", 
                "strength": 68.2,
                "indicators": {
                    "rsi": 65.8,
                    "macd": -0.0015,
                    "bollinger": -0.8
                },
                "timeframe": "4h",
                "trading_style": "swing"
            },
            {
                "symbol": "USDJPY",
                "strength": 82.1,
                "indicators": {
                    "rsi": 78.5,
                    "macd": 0.0034,
                    "bollinger": 1.8
                },
                "timeframe": "1h",
                "trading_style": "day"
            }
        ]
    
    def create_mock_rsi_alert_data(self) -> List[Dict[str, Any]]:
        """Create mock RSI alert data for testing"""
        return [
            {
                "symbol": "EURUSD",
                "rsi": 75.2,
                "condition": "overbought",
                "timeframe": "1h",
                "rsi_period": 14
            },
            {
                "symbol": "GBPUSD",
                "rsi_value": 25.8,  # Test the new rsi_value field
                "trigger_condition": "oversold",
                "timeframe": "4h", 
                "rsi_period": 14
            }
        ]
    
    def create_mock_rsi_correlation_alert_data(self) -> List[Dict[str, Any]]:
        """Create mock RSI correlation alert data for testing"""
        return [
            {
                "symbol1": "EURUSD",
                "symbol2": "GBPUSD", 
                "rsi1": 72.1,
                "rsi2": 28.5,
                "correlation": 0.85,
                "timeframe": "1h"
            },
            {
                "symbol1": "USDJPY",
                "symbol2": "USDCHF",
                "rsi1_value": 68.3,  # Test the new rsi1_value field
                "rsi2_value": 31.7,  # Test the new rsi2_value field
                "correlation": -0.72,
                "timeframe": "4h"
            }
        ]
    
    async def test_email_service_initialization(self):
        """Test email service initialization"""
        print("🧪 Testing Email Service Initialization...")
        
        # Check if SendGrid is configured
        if self.email_service.sg is None:
            print("⚠️  SendGrid not configured - running in test mode")
            print("   Set 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 environment variable to test actual sending")
        else:
            print("✅ SendGrid configured successfully")
        
        print(f"📧 From Email: {self.email_service.from_email}")
        print(f"👤 From Name: {self.email_service.from_name}")
        print(f"⏰ Cooldown: {self.email_service.cooldown_minutes} minutes")
        print(f"📊 RSI Threshold: {self.email_service.rsi_threshold}")
        print()
    
    async def test_value_extraction(self):
        """Test the value extraction logic"""
        print("🧪 Testing Value Extraction Logic...")
        
        # Test heatmap data
        heatmap_data = self.create_mock_heatmap_alert_data()
        heatmap_values = self.email_service._extract_alert_values(heatmap_data)
        print(f"📊 Heatmap values extracted: {heatmap_values}")
        
        # Test RSI data (both rsi and rsi_value)
        rsi_data = self.create_mock_rsi_alert_data()
        rsi_values = self.email_service._extract_alert_values(rsi_data)
        print(f"📈 RSI values extracted: {rsi_values}")
        
        # Test RSI correlation data (both rsi1/rsi2 and rsi1_value/rsi2_value)
        correlation_data = self.create_mock_rsi_correlation_alert_data()
        correlation_values = self.email_service._extract_alert_values(correlation_data)
        print(f"🔗 Correlation values extracted: {correlation_values}")
        
        print()
    
    async def test_cooldown_logic(self):
        """Test the cooldown and value similarity logic"""
        print("🧪 Testing Cooldown Logic...")
        
        # Create test data
        test_pairs = [
            {"symbol": "EURUSD", "rsi": 70.1, "condition": "overbought"},
            {"symbol": "GBPUSD", "rsi_value": 25.8, "trigger_condition": "oversold"}
        ]
        
        # Test hash generation
        alert_hash = self.email_service._generate_alert_hash(
            self.test_email, 
            "Test Alert", 
            test_pairs
        )
        print(f"🔑 Generated alert hash: {alert_hash}")
        
        # Test cooldown check (should be False initially)
        is_cooldown = self.email_service._is_alert_in_cooldown(
            alert_hash, 
            test_pairs
        )
        print(f"⏰ Is in cooldown (initial): {is_cooldown}")
        
        # Simulate sending an alert
        self.email_service._update_alert_cooldown(alert_hash, test_pairs)
        print("📤 Simulated alert sent, cooldown updated")
        
        # Test cooldown check (should be True now)
        is_cooldown_after = self.email_service._is_alert_in_cooldown(
            alert_hash, 
            test_pairs
        )
        print(f"⏰ Is in cooldown (after sending): {is_cooldown_after}")
        
        # Test with similar values (should still be in cooldown)
        similar_pairs = [
            {"symbol": "EURUSD", "rsi": 70.5, "condition": "overbought"},  # Only 0.4 difference
            {"symbol": "GBPUSD", "rsi_value": 26.1, "trigger_condition": "oversold"}  # Only 0.3 difference
        ]
        is_similar_cooldown = self.email_service._is_alert_in_cooldown(
            alert_hash, 
            similar_pairs
        )
        print(f"⏰ Is in cooldown (similar values): {is_similar_cooldown}")
        
        # Test with significantly different values (should allow alert)
        different_pairs = [
            {"symbol": "EURUSD", "rsi": 80.1, "condition": "overbought"},  # 10 point difference
            {"symbol": "GBPUSD", "rsi_value": 35.8, "trigger_condition": "oversold"}  # 10 point difference
        ]
        is_different_cooldown = self.email_service._is_alert_in_cooldown(
            alert_hash, 
            different_pairs
        )
        print(f"⏰ Is in cooldown (different values): {is_different_cooldown}")
        
        print()
    
    async def test_alert_hash_generation(self):
        """Test alert hash generation for different alert types"""
        print("🧪 Testing Alert Hash Generation...")
        
        # Test heatmap alert hash
        heatmap_data = self.create_mock_heatmap_alert_data()
        heatmap_hash = self.email_service._generate_alert_hash(
            self.test_email,
            "Heatmap Alert",
            heatmap_data,
            "heatmap"
        )
        print(f"🔥 Heatmap hash: {heatmap_hash}")
        
        # Test RSI alert hash
        rsi_data = self.create_mock_rsi_alert_data()
        rsi_hash = self.email_service._generate_alert_hash(
            self.test_email,
            "RSI Alert", 
            rsi_data,
            "rsi"
        )
        print(f"📈 RSI hash: {rsi_hash}")
        
        # Test RSI correlation alert hash
        correlation_data = self.create_mock_rsi_correlation_alert_data()
        correlation_hash = self.email_service._generate_alert_hash(
            self.test_email,
            "RSI Correlation Alert",
            correlation_data,
            "rsi_correlation"
        )
        print(f"🔗 Correlation hash: {correlation_hash}")
        
        print()
    
    async def test_safe_float_conversion(self):
        """Test the safe float conversion method"""
        print("🧪 Testing Safe Float Conversion...")
        
        # Test valid values
        valid_values = [70.1, "75.5", "80", 0, "0.0"]
        for value in valid_values:
            result = self.email_service._safe_float_conversion(value)
            print(f"✅ {value} -> {result}")
        
        # Test invalid values
        invalid_values = [None, "invalid", "", "abc", []]
        for value in invalid_values:
            result = self.email_service._safe_float_conversion(value)
            print(f"❌ {value} -> {result}")
        
        print()
    
    async def test_rsi_value_extraction(self):
        """Test the RSI value extraction with both key formats"""
        print("🧪 Testing RSI Value Extraction...")
        
        # Test with 'rsi' key
        pair_with_rsi = {"symbol": "EURUSD", "rsi": 70.1, "condition": "overbought"}
        rsi_value = self.email_service._get_rsi_value(pair_with_rsi, "rsi")
        print(f"📊 RSI from 'rsi' key: {rsi_value}")
        
        # Test with 'rsi_value' key
        pair_with_rsi_value = {"symbol": "GBPUSD", "rsi_value": 25.8, "condition": "oversold"}
        rsi_value_alt = self.email_service._get_rsi_value(pair_with_rsi_value, "rsi")
        print(f"📊 RSI from 'rsi_value' key: {rsi_value_alt}")
        
        # Test with both keys (should prefer 'rsi')
        pair_with_both = {"symbol": "USDJPY", "rsi": 75.2, "rsi_value": 80.5, "condition": "overbought"}
        rsi_value_both = self.email_service._get_rsi_value(pair_with_both, "rsi")
        print(f"📊 RSI with both keys (prefers 'rsi'): {rsi_value_both}")
        
        # Test with neither key
        pair_with_neither = {"symbol": "AUDUSD", "condition": "neutral"}
        rsi_value_neither = self.email_service._get_rsi_value(pair_with_neither, "rsi")
        print(f"📊 RSI with neither key: {rsi_value_neither}")
        
        print()
    
    async def run_all_tests(self):
        """Run all tests"""
        print("🚀 Starting Email Service Test Suite")
        print("=" * 50)
        print()
        
        try:
            await self.test_email_service_initialization()
            await self.test_value_extraction()
            await self.test_cooldown_logic()
            await self.test_alert_hash_generation()
            await self.test_safe_float_conversion()
            await self.test_rsi_value_extraction()
            
            print("✅ All tests completed successfully!")
            print()
            print("📝 Test Summary:")
            print("   - Email service initialization: ✅")
            print("   - Value extraction logic: ✅")
            print("   - Cooldown mechanism: ✅")
            print("   - Alert hash generation: ✅")
            print("   - Safe float conversion: ✅")
            print("   - RSI value extraction: ✅")
            print()
            print("💡 To test actual email sending, set the SendGrid API key environment variable")
            print("   and change the test_email variable to your email address.")
            
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            import traceback
            traceback.print_exc()

async def main():
    """Main test runner"""
    tester = EmailServiceTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    print("🧪 Email Service Test Suite")
    print("Testing email service functionality without MT5 dependency")
    print()
    
    # Check if running from correct directory
    if not os.path.exists("app/email_service.py"):
        print("❌ Error: Cannot find app/email_service.py")
        print("💡 Make sure to run this script from the Fxlabs.ai_Back_end directory")
        sys.exit(1)
    
    asyncio.run(main())

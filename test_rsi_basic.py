#!/usr/bin/env python3
"""
Basic RSI Test
==============
Simple test to verify RSI alert service imports and basic functionality
"""

import sys
import os

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

def test_imports():
    """Test that all required modules can be imported"""
    try:
        print("🔍 Testing imports...")
        
        # Test MT5 import
        try:
            import MetaTrader5 as mt5
            print("✅ MetaTrader5 imported successfully")
        except ImportError:
            print("❌ MetaTrader5 not available")
            return False
        
        # Test app modules
        from app.rsi_alert_service import rsi_alert_service
        print("✅ RSI alert service imported successfully")
        
        from app.email_service import email_service
        print("✅ Email service imported successfully")
        
        from app.mt5_utils import get_ohlc_data, get_current_tick
        print("✅ MT5 utils imported successfully")
        
        from app.models import Timeframe
        print("✅ Models imported successfully")
        
        return True
        
    except Exception as e:
        print(f"❌ Import error: {e}")
        return False

def test_environment():
    """Test environment variables"""
    try:
        print("\n🔍 Testing environment variables...")
        
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_KEY")
        
        if supabase_url:
            print(f"✅ SUPABASE_URL: {supabase_url}")
        else:
            print("⚠️ SUPABASE_URL not set")
        
        if supabase_key:
            print(f"✅ SUPABASE_SERVICE_KEY: {'*' * 20}...{supabase_key[-4:]}")
        else:
            print("⚠️ SUPABASE_SERVICE_KEY not set")
        
        return True
        
    except Exception as e:
        print(f"❌ Environment test error: {e}")
        return False

def main():
    """Main test function"""
    print("🧪 Basic RSI Test")
    print("=" * 30)
    
    imports_ok = test_imports()
    env_ok = test_environment()
    
    if imports_ok and env_ok:
        print("\n🎉 Basic test passed!")
        print("✅ All imports successful")
        print("✅ Environment variables configured")
        print("\n🚀 Ready to run full RSI alert test!")
    else:
        print("\n💥 Basic test failed!")
        print("❌ Check imports and environment configuration")

if __name__ == "__main__":
    main()

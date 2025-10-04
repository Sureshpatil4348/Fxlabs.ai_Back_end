#!/usr/bin/env python3
"""
Test script to verify MT5 connection and basic functionality
Run this after setup to ensure everything is working
"""

import os
import sys
import asyncio
from datetime import datetime

def test_mt5_import():
    """Test if MT5 module can be imported"""
    try:
        import MetaTrader5 as mt5
        print("✓ MetaTrader5 module imported successfully")
        return mt5
    except ImportError as e:
        print(f"✗ Failed to import MetaTrader5: {e}")
        return None

def test_mt5_connection(mt5):
    """Test MT5 connection"""
    try:
        # Try to initialize MT5
        if not mt5.initialize():
            error = mt5.last_error()
            print(f"✗ MT5 initialization failed: {error}")
            return False
        
        version = mt5.version()
        print(f"✓ MT5 connected successfully. Version: {version}")
        return True
    except Exception as e:
        print(f"✗ MT5 connection error: {e}")
        return False

def test_symbol_access(mt5):
    """Test if we can access symbols"""
    try:
        symbols = mt5.symbols_get()
        if symbols:
            print(f"✓ Found {len(symbols)} symbols")
            # Try to get info for a common symbol
            common_symbols = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD']
            for symbol in common_symbols:
                info = mt5.symbol_info(symbol)
                if info:
                    print(f"✓ Symbol {symbol} is available")
                    return True
            print("⚠ No common symbols found, but MT5 is accessible")
            return True
        else:
            print("✗ No symbols found")
            return False
    except Exception as e:
        print(f"✗ Symbol access error: {e}")
        return False

def test_tick_data(mt5):
    """Test if we can get tick data"""
    try:
        # Try to get tick data for EURUSD
        tick = mt5.symbol_info_tick('EURUSD')
        if tick:
            print(f"✓ Tick data available for EURUSD")
            print(f"  Bid: {tick.bid}, Ask: {tick.ask}, Time: {datetime.fromtimestamp(tick.time)}")
            return True
        else:
            print("⚠ No tick data available for EURUSD")
            return False
    except Exception as e:
        print(f"✗ Tick data error: {e}")
        return False

def test_environment():
    """Test environment variables"""
    print("\nEnvironment Configuration:")
    
    api_token = os.environ.get("API_TOKEN")
    if api_token:
        print(f"✓ API_TOKEN is set ({'*' * min(len(api_token), 8)}...)")
    else:
        print("⚠ API_TOKEN not set (using empty string)")
    
    allowed_origins = os.environ.get("ALLOWED_ORIGINS")
    if allowed_origins:
        print(f"✓ ALLOWED_ORIGINS: {allowed_origins}")
    else:
        print("⚠ ALLOWED_ORIGINS not set (allowing all origins)")
    
    mt5_path = os.environ.get("MT5_TERMINAL_PATH")
    if mt5_path:
        print(f"✓ MT5_TERMINAL_PATH: {mt5_path}")
    else:
        print("⚠ MT5_TERMINAL_PATH not set (using auto-detection)")

def main():
    """Run all tests"""
    print("MT5 Tick Stream - Setup Test")
    print("=" * 40)
    
    # Test environment
    test_environment()
    
    print("\nTesting MT5 Integration:")
    print("-" * 25)
    
    # Test MT5 import
    mt5 = test_mt5_import()
    if not mt5:
        print("\n❌ Setup failed: Cannot import MT5 module")
        print("Please ensure MetaTrader5 package is installed:")
        print("pip install MetaTrader5==5.0.45")
        return False
    
    # Test MT5 connection
    if not test_mt5_connection(mt5):
        print("\n❌ Setup failed: Cannot connect to MT5")
        print("Please ensure:")
        print("1. MetaTrader 5 terminal is installed and running")
        print("2. You are logged into MT5")
        print("3. Set MT5_TERMINAL_PATH if auto-detection fails")
        return False
    
    # Test symbol access
    if not test_symbol_access(mt5):
        print("\n⚠ Warning: Limited symbol access")
    
    # Test tick data
    if not test_tick_data(mt5):
        print("\n⚠ Warning: Limited tick data access")
    
    print("\n✅ Setup test completed successfully!")
    print("\nNext steps:")
    print("1. Start the server: python server.py")
    print("2. Test the API: http://127.0.0.1:8000/health")
    print("3. Test WebSocket (v2): ws://127.0.0.1:8000/market-v2")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

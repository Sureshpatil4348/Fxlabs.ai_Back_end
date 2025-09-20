#!/usr/bin/env python3
"""
MT5 Connection Test
Simple test to check MT5 connection and data retrieval
"""

import sys
import os
from datetime import datetime, timezone

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

def test_mt5_import():
    """Test if MetaTrader5 can be imported"""
    print("🧪 Testing MT5 Import")
    print("=" * 30)
    
    try:
        import MetaTrader5 as mt5
        print("✅ MetaTrader5 module imported successfully")
        return mt5
    except ImportError as e:
        print(f"❌ Failed to import MetaTrader5: {e}")
        print("💡 Install MetaTrader5 package: pip install MetaTrader5==5.0.45")
        return None

def test_mt5_connection(mt5):
    """Test MT5 connection"""
    print("\n🔌 Testing MT5 Connection")
    print("=" * 30)
    
    if not mt5:
        print("❌ MT5 module not available")
        return False
    
    try:
        # Initialize MT5
        print("📡 Attempting to connect to MT5...")
        if mt5.initialize():
            print("✅ MT5 connected successfully")
            
            # Get account info
            account_info = mt5.account_info()
            if account_info:
                print(f"📊 Account: {account_info.login}")
                print(f"📊 Server: {account_info.server}")
                print(f"📊 Balance: {account_info.balance}")
            else:
                print("⚠️ Could not get account info")
            
            return True
        else:
            print("❌ MT5 initialization failed")
            error_code = mt5.last_error()
            print(f"💡 MT5 Error Code: {error_code}")
            print("💡 Make sure MetaTrader 5 is running and logged in")
            return False
            
    except Exception as e:
        print(f"❌ MT5 connection error: {e}")
        return False

def test_mt5_data_retrieval(mt5):
    """Test MT5 data retrieval"""
    print("\n📊 Testing MT5 Data Retrieval")
    print("=" * 30)
    
    if not mt5:
        print("❌ MT5 module not available")
        return False
    
    test_symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    
    for symbol in test_symbols:
        print(f"\n🔍 Testing {symbol}:")
        
        try:
            # Check if symbol is available
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info:
                print(f"  ✅ Symbol {symbol} is available")
                print(f"  📊 Bid: {symbol_info.bid}")
                print(f"  📊 Ask: {symbol_info.ask}")
                print(f"  📊 Spread: {symbol_info.spread}")
            else:
                print(f"  ❌ Symbol {symbol} not available")
                continue
            
            # Get current tick
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                print(f"  📈 Current Tick:")
                print(f"    Time: {datetime.fromtimestamp(tick.time)}")
                print(f"    Bid: {tick.bid}")
                print(f"    Ask: {tick.ask}")
                print(f"    Volume: {tick.volume}")
            else:
                print(f"  ❌ Could not get tick data for {symbol}")
            
            # Get OHLC data (last 5 bars)
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 5)
            if rates is not None and len(rates) > 0:
                print(f"  📊 OHLC Data (last 5 H1 bars):")
                for i, rate in enumerate(rates):
                    time_str = datetime.fromtimestamp(rate['time']).strftime('%Y-%m-%d %H:%M')
                    print(f"    {i+1}. {time_str}: O={rate['open']:.5f} H={rate['high']:.5f} L={rate['low']:.5f} C={rate['close']:.5f}")
            else:
                print(f"  ❌ Could not get OHLC data for {symbol}")
                
        except Exception as e:
            print(f"  ❌ Error testing {symbol}: {e}")
    
    return True

def test_mt5_utils():
    """Test MT5 utils functions"""
    print("\n🛠️ Testing MT5 Utils")
    print("=" * 30)
    
    try:
        from app.mt5_utils import get_ohlc_data, get_current_ohlc
        from app.models import Timeframe
        
        print("✅ MT5 utils imported successfully")
        
        # Test get_ohlc_data
        print("\n📊 Testing get_ohlc_data:")
        ohlc_data = get_ohlc_data("EURUSD", Timeframe.H1, 5)
        if ohlc_data:
            print(f"  ✅ Got {len(ohlc_data)} OHLC bars")
            for i, bar in enumerate(ohlc_data):
                print(f"    {i+1}. {bar.time_iso}: O={bar.open:.5f} H={bar.high:.5f} L={bar.low:.5f} C={bar.close:.5f}")
        else:
            print("  ❌ No OHLC data returned")
        
        # Test get_current_ohlc
        print("\n📊 Testing get_current_ohlc:")
        current_ohlc = get_current_ohlc("EURUSD", Timeframe.H1)
        if current_ohlc:
            print(f"  ✅ Current OHLC: O={current_ohlc.open:.5f} H={current_ohlc.high:.5f} L={current_ohlc.low:.5f} C={current_ohlc.close:.5f}")
        else:
            print("  ❌ No current OHLC data")
            
    except Exception as e:
        print(f"❌ MT5 utils test failed: {e}")

def main():
    """Main test function"""
    print("🧪 MT5 Connection and Data Test")
    print("=" * 50)
    print()
    
    # Test 1: Import
    mt5 = test_mt5_import()
    
    # Test 2: Connection
    connected = test_mt5_connection(mt5)
    
    # Test 3: Data Retrieval
    if connected:
        test_mt5_data_retrieval(mt5)
        test_mt5_utils()
    else:
        print("\n⚠️ Skipping data tests - MT5 not connected")
    
    # Cleanup
    if mt5:
        mt5.shutdown()
        print("\n🔌 MT5 connection closed")
    
    print("\n📊 Test Summary:")
    print("=" * 20)
    if mt5 and connected:
        print("✅ MT5 is working correctly!")
        print("💡 Your alert services should be able to get real market data")
    else:
        print("❌ MT5 is not working")
        print("💡 Install MetaTrader5 package and ensure MT5 terminal is running")

if __name__ == "__main__":
    main()

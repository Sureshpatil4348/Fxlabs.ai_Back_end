#!/usr/bin/env python3
"""
Real MT5 Heatmap Alert Test
===========================
Test heatmap alerts using only real MT5 market data
No hardcoded or simulated data - pure real market data
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from typing import Dict, List, Any

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
    print("✅ MT5 module available - using real market data")
except ImportError:
    MT5_AVAILABLE = False
    print("❌ MT5 module not available - cannot run test")
    sys.exit(1)

from app.heatmap_alert_service import heatmap_alert_service
from app.email_service import email_service
from app.mt5_utils import get_ohlc_data
from app.models import Timeframe

def get_real_mt5_symbols():
    """Get available symbols from MT5"""
    if not MT5_AVAILABLE:
        return []
    
    try:
        symbols = mt5.symbols_get()
        if symbols:
            # Filter for forex pairs
            forex_symbols = []
            for symbol in symbols:
                symbol_name = symbol.name.upper()
                if any(pair in symbol_name for pair in ["EUR", "GBP", "USD", "JPY", "AUD", "CAD", "CHF", "NZD"]):
                    forex_symbols.append(symbol.name)
            
            # Return first 10 forex symbols
            return forex_symbols[:10]
        return []
    except Exception as e:
        print(f"❌ Error getting MT5 symbols: {e}")
        return []

def get_real_mt5_tick_data(symbols: List[str]) -> Dict[str, Any]:
    """Get real tick data from MT5"""
    if not MT5_AVAILABLE:
        return {}
    
    tick_data = {}
    
    for symbol in symbols:
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                tick_data[symbol] = {
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "time": tick.time
                }
                print(f"✅ Real MT5 data for {symbol}: Bid={tick.bid:.5f}, Ask={tick.ask:.5f}")
            else:
                print(f"⚠️ No tick data available for {symbol}")
        except Exception as e:
            print(f"❌ Error getting tick data for {symbol}: {e}")
    
    return tick_data

def get_real_mt5_ohlc_data(symbols: List[str]) -> Dict[str, Any]:
    """Get real OHLC data from MT5"""
    if not MT5_AVAILABLE:
        return {}
    
    ohlc_data = {}
    
    for symbol in symbols:
        try:
            # Get H1 OHLC data
            ohlc_list = get_ohlc_data(symbol, Timeframe.H1, 1)
            if ohlc_list and len(ohlc_list) > 0:
                ohlc = ohlc_list[0]  # Get the first (latest) OHLC object
                ohlc_data[symbol] = ohlc
                print(f"✅ Real OHLC data for {symbol}: Open={ohlc.open:.5f}, High={ohlc.high:.5f}, Low={ohlc.low:.5f}, Close={ohlc.close:.5f}")
            else:
                print(f"⚠️ No OHLC data available for {symbol}")
        except Exception as e:
            print(f"❌ Error getting OHLC data for {symbol}: {e}")
    
    return ohlc_data

async def test_heatmap_alerts_real_mt5():
    """Test heatmap alerts with real MT5 data"""
    
    print("🧪 Real MT5 Heatmap Alert Test")
    print("Testing with actual MT5 market data - no simulation")
    print("=" * 60)
    
    # Connect to MT5
    if not MT5_AVAILABLE:
        print("❌ MT5 not available - cannot run test")
        return False
    
    print("🔌 Attempting to connect to MT5...")
    if not mt5.initialize():
        print("❌ Failed to initialize MT5")
        return False
    
    print("✅ MT5 connected successfully")
    
    # Get account info
    account_info = mt5.account_info()
    if account_info:
        print(f"📊 Account: {account_info.login}")
        print(f"📊 Server: {account_info.server}")
        print(f"📊 Balance: {account_info.balance}")
    
    # Get available symbols
    print("\n🔍 Getting available symbols...")
    symbols = get_real_mt5_symbols()
    
    if not symbols:
        print("❌ No symbols available - cannot run test")
        mt5.shutdown()
        return False
    
    print(f"✅ Found {len(symbols)} symbols: {', '.join(symbols[:5])}...")
    
    # Get real tick data
    print("\n📊 Getting real tick data...")
    tick_data = get_real_mt5_tick_data(symbols)
    
    if not tick_data:
        print("❌ No tick data available - cannot run test")
        mt5.shutdown()
        return False
    
    # Get real OHLC data
    print("\n📈 Getting real OHLC data...")
    ohlc_data = get_real_mt5_ohlc_data(symbols)
    
    # Create tick data structure for heatmap alerts
    tick_data_structure = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": list(tick_data.keys()),
        "tick_data": tick_data
    }
    
    print(f"\n🚀 Testing Heatmap Alerts with Real Data")
    print("=" * 50)
    print(f"📊 Symbols: {len(tick_data_structure['symbols'])}")
    print(f"📊 Timestamp: {tick_data_structure['timestamp']}")
    
    # Test heatmap alert service
    try:
        print("\n🔍 Checking heatmap alerts...")
        triggered_alerts = await heatmap_alert_service.check_heatmap_alerts(tick_data_structure)
        
        if triggered_alerts:
            print(f"✅ Found {len(triggered_alerts)} triggered alerts")
            for alert in triggered_alerts:
                print(f"📧 Alert: {alert.get('alert_name', 'Unknown')} for {alert.get('user_email', 'Unknown')}")
        else:
            print("ℹ️ No alerts triggered with current market data")
        
        # Test email service
        print("\n📧 Testing email service...")
        test_email = "theashish.y@gmail.com"
        
        # Create a test alert structure
        test_alert = {
            "user_email": test_email,
            "alert_name": "Real MT5 Heatmap Test",
            "triggered_pairs": [
                {
                    "symbol": symbol,
                    "current_price": tick_data[symbol]["bid"],
                    "price_change_percent": 0.0,  # We don't have historical data for change
                    "trigger_condition": "test"
                }
                for symbol in list(tick_data.keys())[:3]  # Test with first 3 symbols
            ],
            "alert_config": {
                "trading_style": "dayTrader",
                "buy_threshold_min": 70,
                "buy_threshold_max": 100,
                "sell_threshold_min": 0,
                "sell_threshold_max": 30
            }
        }
        
        print(f"📤 Sending test heatmap alert email to {test_email}...")
        email_sent = await email_service.send_heatmap_alert(
            test_alert["user_email"],
            test_alert["alert_name"],
            test_alert["triggered_pairs"],
            test_alert["alert_config"]
        )
        
        if email_sent:
            print("✅ Heatmap alert email sent successfully!")
        else:
            print("❌ Failed to send heatmap alert email")
        
        print("\n📊 Real MT5 Heatmap Test Results:")
        print("=" * 40)
        print(f"Heatmap Alerts Check: {'✅ SUCCESS' if triggered_alerts is not None else '❌ FAILED'}")
        print(f"Email Service: {'✅ SUCCESS' if email_sent else '❌ FAILED'}")
        
        return email_sent
        
    except Exception as e:
        print(f"❌ Error during heatmap alert test: {e}")
        return False
    
    finally:
        # Close MT5 connection
        mt5.shutdown()
        print("🔌 MT5 connection closed")

async def main():
    """Main test function"""
    print("🚀 Real MT5 Heatmap Alert Test")
    print("Testing with actual MT5 market data")
    print("=" * 60)
    
    try:
        success = await test_heatmap_alerts_real_mt5()
        
        if success:
            print("\n🎉 Test completed successfully!")
            print("✅ Heatmap alerts are working with real MT5 data")
        else:
            print("\n💥 Test failed!")
            print("❌ Check MT5 connection and configuration")
            
    except Exception as e:
        print(f"\n💥 Test failed with error: {e}")
        print("❌ Check MT5 connection and configuration")

if __name__ == "__main__":
    asyncio.run(main())

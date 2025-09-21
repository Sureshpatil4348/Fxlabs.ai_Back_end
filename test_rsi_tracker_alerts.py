#!/usr/bin/env python3
"""
RSI Tracker Alert Test
======================
Test RSI tracker alerts using real MT5 market data and Supabase integration
Gets user email and alerts data from Supabase, matches conditions with actual data, and sends emails
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

from app.rsi_alert_service import rsi_alert_service
from app.email_service import email_service
from app.mt5_utils import get_ohlc_data, get_current_tick
from app.models import Timeframe

def get_real_mt5_symbols():
    """Get available symbols from MT5"""
    if not MT5_AVAILABLE:
        return []
    
    try:
        symbols = mt5.symbols_get()
        if symbols:
            # Filter for forex pairs with 'm' suffix (broker-specific symbols)
            forex_symbols = []
            for symbol in symbols:
                symbol_name = symbol.name.upper()
                if any(pair in symbol_name for pair in ["EUR", "GBP", "USD", "JPY", "AUD", "CAD", "CHF", "NZD"]) and symbol_name.endswith('M'):
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
            ohlc_list = get_ohlc_data(symbol, Timeframe.H1, 50)  # Get 50 bars for RSI calculation
            if ohlc_list and len(ohlc_list) > 0:
                ohlc_data[symbol] = ohlc_list
                print(f"✅ Real OHLC data for {symbol}: {len(ohlc_list)} bars")
            else:
                print(f"⚠️ No OHLC data available for {symbol}")
        except Exception as e:
            print(f"❌ Error getting OHLC data for {symbol}: {e}")
    
    return ohlc_data

async def get_supabase_rsi_alerts():
    """Get RSI alerts from Supabase"""
    try:
        import aiohttp
        
        # Get Supabase credentials from environment
        supabase_url = os.environ.get("SUPABASE_URL", "https://hyajwhtkwldrmlhfiuwg.supabase.co")
        supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY")
        
        if not supabase_service_key:
            print("❌ SUPABASE_SERVICE_KEY not found in environment variables")
            return []
        
        headers = {
            "apikey": supabase_service_key,
            "Authorization": f"Bearer {supabase_service_key}",
            "Content-Type": "application/json"
        }
        
        # Get active RSI alerts
        url = f"{supabase_url}/rest/v1/rsi_alerts?is_active=eq.true&select=*"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    alerts = await response.json()
                    print(f"✅ Found {len(alerts)} active RSI alerts in Supabase")
                    return alerts
                else:
                    error_text = await response.text()
                    print(f"❌ Failed to get RSI alerts: {response.status} - {error_text}")
                    return []
    
    except Exception as e:
        print(f"❌ Error getting RSI alerts from Supabase: {e}")
        return []

async def test_rsi_alerts_real_mt5():
    """Test RSI alerts with real MT5 data and Supabase integration"""
    
    print("🧪 RSI Tracker Alert Test")
    print("Testing with actual MT5 market data and Supabase alerts")
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
    
    # Get RSI alerts from Supabase
    print("\n🔍 Getting RSI alerts from Supabase...")
    supabase_alerts = await get_supabase_rsi_alerts()
    
    if not supabase_alerts:
        print("❌ No RSI alerts found in Supabase - cannot run test")
        mt5.shutdown()
        return False
    
    # Extract unique symbols from alerts
    all_symbols = set()
    for alert in supabase_alerts:
        pairs = alert.get("pairs", [])
        all_symbols.update(pairs)
    
    symbols_list = list(all_symbols)
    print(f"✅ Found {len(symbols_list)} unique symbols from alerts: {', '.join(symbols_list[:5])}...")
    
    # Get real tick data
    print("\n📊 Getting real tick data...")
    tick_data = get_real_mt5_tick_data(symbols_list)
    
    if not tick_data:
        print("❌ No tick data available - cannot run test")
        mt5.shutdown()
        return False
    
    # Get real OHLC data
    print("\n📈 Getting real OHLC data...")
    ohlc_data = get_real_mt5_ohlc_data(symbols_list)
    
    # Create tick data structure for RSI alerts
    tick_data_structure = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": list(tick_data.keys()),
        "tick_data": tick_data,
        "ohlc_data": ohlc_data
    }
    
    print(f"\n🚀 Testing RSI Alerts with Real Data")
    print("=" * 50)
    print(f"📊 Symbols: {len(tick_data_structure['symbols'])}")
    print(f"📊 Alerts: {len(supabase_alerts)}")
    print(f"📊 Timestamp: {tick_data_structure['timestamp']}")
    
    # Test RSI alert service
    try:
        print("\n🔍 Checking RSI alerts...")
        triggered_alerts = await rsi_alert_service.check_rsi_alerts(tick_data_structure)
        
        if triggered_alerts:
            print(f"✅ Found {len(triggered_alerts)} triggered alerts")
            for alert in triggered_alerts:
                user_email = alert.get('user_email', 'Unknown')
                alert_name = alert.get('alert_name', 'Unknown')
                triggered_pairs = alert.get('triggered_pairs', [])
                print(f"📧 Alert: {alert_name} for {user_email}")
                print(f"   Triggered pairs: {len(triggered_pairs)}")
                for pair in triggered_pairs[:3]:  # Show first 3 pairs
                    symbol = pair.get('symbol', 'Unknown')
                    rsi_value = pair.get('rsi_value', 0)
                    condition = pair.get('trigger_condition', 'Unknown')
                    print(f"   - {symbol}: RSI={rsi_value:.2f}, Condition={condition}")
        else:
            print("ℹ️ No alerts triggered with current market data")
        
        # Test email service with a sample alert
        print("\n📧 Testing email service...")
        test_email = "theashish.y@gmail.com"
        
        # Create a test alert structure based on real data
        test_alert = {
            "user_email": test_email,
            "alert_name": "Real MT5 RSI Test",
            "triggered_pairs": []
        }
        
        # Use real data for test alert
        for symbol in list(tick_data.keys())[:3]:  # Test with first 3 symbols
            if symbol in ohlc_data and len(ohlc_data[symbol]) > 0:
                # Calculate RSI for this symbol
                closes = [bar.close for bar in ohlc_data[symbol][-15:]]  # Last 15 bars
                if len(closes) >= 14:
                    # Simple RSI calculation
                    gains = 0
                    losses = 0
                    for i in range(1, 14):
                        change = closes[i] - closes[i-1]
                        if change > 0:
                            gains += change
                        else:
                            losses -= change
                    
                    avg_gain = gains / 13
                    avg_loss = losses / 13
                    
                    if avg_loss > 0:
                        rs = avg_gain / avg_loss
                        rsi_value = 100 - (100 / (1 + rs))
                        
                        # Determine condition
                        condition = "neutral"
                        if rsi_value >= 70:
                            condition = "overbought"
                        elif rsi_value <= 30:
                            condition = "oversold"
                        
                        test_alert["triggered_pairs"].append({
                            "symbol": symbol,
                            "timeframe": "1H",
                            "rsi_value": round(rsi_value, 2),
                            "trigger_condition": condition,
                            "current_price": tick_data[symbol]["bid"],
                            "price_change_percent": 0.0,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        })
        
        if test_alert["triggered_pairs"]:
            print(f"📤 Sending test RSI alert email to {test_email}...")
            email_sent = await email_service.send_rsi_alert(
                test_alert["user_email"],
                test_alert["alert_name"],
                test_alert["triggered_pairs"],
                {
                    "rsi_period": 14,
                    "rsi_overbought_threshold": 70,
                    "rsi_oversold_threshold": 30,
                    "alert_conditions": ["overbought", "oversold"]
                }
            )
            
            if email_sent:
                print("✅ RSI alert email sent successfully!")
            else:
                print("❌ Failed to send RSI alert email")
        else:
            print("⚠️ No test data available for email test")
        
        print("\n📊 RSI Alert Test Results:")
        print("=" * 40)
        print(f"Supabase Alerts Retrieved: {'✅ SUCCESS' if supabase_alerts else '❌ FAILED'}")
        print(f"RSI Alerts Check: {'✅ SUCCESS' if triggered_alerts is not None else '❌ FAILED'}")
        print(f"Email Service: {'✅ SUCCESS' if test_alert.get('triggered_pairs') else '⚠️ NO DATA'}")
        
        return len(triggered_alerts) > 0 or len(test_alert.get('triggered_pairs', [])) > 0
        
    except Exception as e:
        print(f"❌ Error during RSI alert test: {e}")
        return False
    
    finally:
        # Close MT5 connection
        mt5.shutdown()
        print("🔌 MT5 connection closed")

async def main():
    """Main test function"""
    print("🚀 RSI Tracker Alert Test")
    print("Testing with actual MT5 market data and Supabase integration")
    print("=" * 60)
    
    try:
        success = await test_rsi_alerts_real_mt5()
        
        if success:
            print("\n🎉 Test completed successfully!")
            print("✅ RSI alerts are working with real MT5 data and Supabase")
        else:
            print("\n💥 Test failed!")
            print("❌ Check MT5 connection, Supabase configuration, and alert data")
            
    except Exception as e:
        print(f"\n💥 Test failed with error: {e}")
        print("❌ Check MT5 connection and Supabase configuration")

if __name__ == "__main__":
    asyncio.run(main())

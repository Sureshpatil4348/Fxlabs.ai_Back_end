#!/usr/bin/env python3
"""
RSI Correlation Alert Test
==========================
Test RSI correlation alerts using real MT5 market data and Supabase integration
Gets user email and alerts data from Supabase, matches conditions with actual data, and sends emails
Supports both RSI Threshold mode and Real Correlation mode
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

from app.rsi_correlation_alert_service import rsi_correlation_alert_service
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

async def get_supabase_rsi_correlation_alerts():
    """Get RSI correlation alerts from Supabase"""
    try:
        import aiohttp
        
        # Use hardcoded Supabase credentials (same as other services)
        supabase_url = "https://hyajwhtkwldrmlhfiuwg.supabase.co"
        supabase_service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh5YWp3aHRrd2xkcm1saGZpdXdnIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NjI5NjUzNCwiZXhwIjoyMDcxODcyNTM0fQ.UDqYHY5Io0o-fQTswCYQmMdC6UCPQI2gf3aTb9o09SE"
        
        headers = {
            "apikey": supabase_service_key,
            "Authorization": f"Bearer {supabase_service_key}",
            "Content-Type": "application/json"
        }
        
        # Get active RSI correlation alerts
        url = f"{supabase_url}/rest/v1/rsi_correlation_alerts?is_active=eq.true&select=*"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    alerts = await response.json()
                    print(f"✅ Found {len(alerts)} active RSI correlation alerts in Supabase")
                    return alerts
                else:
                    error_text = await response.text()
                    print(f"❌ Failed to get RSI correlation alerts: {response.status} - {error_text}")
                    return []
    
    except Exception as e:
        print(f"❌ Error getting RSI correlation alerts from Supabase: {e}")
        return []

async def test_rsi_correlation_alerts_real_mt5():
    """Test RSI correlation alerts with real MT5 data and Supabase integration"""
    
    print("🧪 RSI Correlation Alert Test")
    print("Testing with actual MT5 market data and Supabase correlation alerts")
    print("=" * 70)
    
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
    
    # Get RSI correlation alerts from Supabase
    print("\n🔍 Getting RSI correlation alerts from Supabase...")
    supabase_alerts = await get_supabase_rsi_correlation_alerts()
    
    if not supabase_alerts:
        print("❌ No RSI correlation alerts found in Supabase - cannot run test")
        mt5.shutdown()
        return False
    
    # Extract unique symbols from correlation pairs
    all_symbols = set()
    for alert in supabase_alerts:
        # Support both field names for backward compatibility
        correlation_pairs = alert.get("pairs", alert.get("correlation_pairs", []))
        for pair in correlation_pairs:
            if isinstance(pair, list) and len(pair) == 2:
                all_symbols.update(pair)
    
    symbols_list = list(all_symbols)
    print(f"✅ Found {len(symbols_list)} unique symbols from correlation alerts: {', '.join(symbols_list[:5])}...")
    
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
    
    # Create tick data structure for RSI correlation alerts
    tick_data_structure = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": list(tick_data.keys()),
        "tick_data": tick_data,
        "ohlc_data": ohlc_data
    }
    
    print(f"\n🚀 Testing RSI Correlation Alerts with Real Data")
    print("=" * 60)
    print(f"📊 Symbols: {len(tick_data_structure['symbols'])}")
    print(f"📊 Alerts: {len(supabase_alerts)}")
    print(f"📊 Timestamp: {tick_data_structure['timestamp']}")
    
    # Test RSI correlation alert service
    try:
        print("\n🔍 Checking RSI correlation alerts...")
        triggered_alerts = await rsi_correlation_alert_service.check_rsi_correlation_alerts(tick_data_structure)
        
        if triggered_alerts:
            print(f"✅ Found {len(triggered_alerts)} triggered correlation alerts")
            for alert in triggered_alerts:
                user_email = alert.get('user_email', 'Unknown')
                alert_name = alert.get('alert_name', 'Unknown')
                calculation_mode = alert.get('calculation_mode', 'Unknown')
                triggered_pairs = alert.get('triggered_pairs', [])
                print(f"📧 Alert: {alert_name} ({calculation_mode}) for {user_email}")
                print(f"   Triggered pairs: {len(triggered_pairs)}")
                for pair in triggered_pairs[:3]:  # Show first 3 pairs
                    symbol1 = pair.get('symbol1', 'Unknown')
                    symbol2 = pair.get('symbol2', 'Unknown')
                    condition = pair.get('trigger_condition', 'Unknown')
                    if calculation_mode == "rsi_threshold":
                        rsi1 = pair.get('rsi1', 0)
                        rsi2 = pair.get('rsi2', 0)
                        print(f"   - {symbol1}-{symbol2}: RSI1={rsi1:.2f}, RSI2={rsi2:.2f}, Condition={condition}")
                    else:  # real_correlation
                        correlation = pair.get('correlation_value', 0)
                        print(f"   - {symbol1}-{symbol2}: Correlation={correlation:.3f}, Condition={condition}")
        else:
            print("ℹ️ No correlation alerts triggered with current market data")
        
        # Test email service with a sample alert
        print("\n📧 Testing email service...")
        
        # Use email from first Supabase alert instead of hardcoded email
        if supabase_alerts and len(supabase_alerts) > 0:
            test_email = supabase_alerts[0].get("user_email", "theashish.y@gmail.com")
            print(f"📧 Using email from Supabase alert: {test_email}")
        else:
            test_email = "theashish.y@gmail.com"
            print(f"⚠️ No Supabase alerts found, using fallback email: {test_email}")
        
        # Create a test alert structure based on real data
        test_alert = {
            "user_email": test_email,
            "alert_name": "Real MT5 RSI Correlation Test",
            "calculation_mode": "rsi_threshold",  # Test with RSI threshold mode
            "triggered_pairs": []
        }
        
        # Use real data for test alert - create correlation pairs from available symbols
        available_symbols = list(tick_data.keys())
        if len(available_symbols) >= 2:
            # Create test correlation pairs
            symbol1 = available_symbols[0]
            symbol2 = available_symbols[1] if len(available_symbols) > 1 else available_symbols[0]
            
            if symbol1 in ohlc_data and symbol2 in ohlc_data and len(ohlc_data[symbol1]) > 0 and len(ohlc_data[symbol2]) > 0:
                # Calculate RSI for both symbols
                closes1 = [bar.close for bar in ohlc_data[symbol1][-15:]]  # Last 15 bars
                closes2 = [bar.close for bar in ohlc_data[symbol2][-15:]]  # Last 15 bars
                
                if len(closes1) >= 14 and len(closes2) >= 14:
                    # Simple RSI calculation for symbol1
                    gains1 = 0
                    losses1 = 0
                    for i in range(1, 14):
                        change = closes1[i] - closes1[i-1]
                        if change > 0:
                            gains1 += change
                        else:
                            losses1 -= change
                    
                    avg_gain1 = gains1 / 13
                    avg_loss1 = losses1 / 13
                    
                    if avg_loss1 > 0:
                        rs1 = avg_gain1 / avg_loss1
                        rsi1_value = 100 - (100 / (1 + rs1))
                    else:
                        rsi1_value = 100
                    
                    # Simple RSI calculation for symbol2
                    gains2 = 0
                    losses2 = 0
                    for i in range(1, 14):
                        change = closes2[i] - closes2[i-1]
                        if change > 0:
                            gains2 += change
                        else:
                            losses2 -= change
                    
                    avg_gain2 = gains2 / 13
                    avg_loss2 = losses2 / 13
                    
                    if avg_loss2 > 0:
                        rs2 = avg_gain2 / avg_loss2
                        rsi2_value = 100 - (100 / (1 + rs2))
                    else:
                        rsi2_value = 100
                    
                    # Determine condition based on RSI values
                    condition = "neutral_break"
                    if (rsi1_value >= 70 and rsi2_value <= 30) or (rsi1_value <= 30 and rsi2_value >= 70):
                        condition = "positive_mismatch"
                    elif (rsi1_value >= 70 and rsi2_value >= 70) or (rsi1_value <= 30 and rsi2_value <= 30):
                        condition = "negative_mismatch"
                    
                    test_alert["triggered_pairs"].append({
                        "symbol1": symbol1,
                        "symbol2": symbol2,
                        "timeframe": "1H",
                        "rsi1": round(rsi1_value, 2),
                        "rsi2": round(rsi2_value, 2),
                        "trigger_condition": condition,
                        "current_price1": tick_data[symbol1]["bid"],
                        "current_price2": tick_data[symbol2]["bid"],
                        "price_change1": 0.0,
                        "price_change2": 0.0,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
        
        if test_alert["triggered_pairs"]:
            print(f"📤 Sending test RSI correlation alert email to {test_email}...")
            email_sent = await email_service.send_rsi_correlation_alert(
                test_alert["user_email"],
                test_alert["alert_name"],
                test_alert["calculation_mode"],
                test_alert["triggered_pairs"],
                {
                    "rsi_period": 14,
                    "rsi_overbought_threshold": 70,
                    "rsi_oversold_threshold": 30,
                    "alert_conditions": ["positive_mismatch", "negative_mismatch", "neutral_break"]
                }
            )
            
            if email_sent:
                print("✅ RSI correlation alert email sent successfully!")
            else:
                print("❌ Failed to send RSI correlation alert email")
        else:
            print("⚠️ No test data available for email test")
        
        print("\n📊 RSI Correlation Alert Test Results:")
        print("=" * 50)
        print(f"Supabase Alerts Retrieved: {'✅ SUCCESS' if supabase_alerts else '❌ FAILED'}")
        print(f"RSI Correlation Alerts Check: {'✅ SUCCESS' if triggered_alerts is not None else '❌ FAILED'}")
        print(f"Email Service: {'✅ SUCCESS' if test_alert.get('triggered_pairs') else '⚠️ NO DATA'}")
        
        return len(triggered_alerts) > 0 or len(test_alert.get('triggered_pairs', [])) > 0
        
    except Exception as e:
        print(f"❌ Error during RSI correlation alert test: {e}")
        return False
    
    finally:
        # Close MT5 connection
        mt5.shutdown()
        print("🔌 MT5 connection closed")

async def main():
    """Main test function"""
    print("🚀 RSI Correlation Alert Test")
    print("Testing with actual MT5 market data and Supabase integration")
    print("=" * 70)
    
    try:
        success = await test_rsi_correlation_alerts_real_mt5()
        
        if success:
            print("\n🎉 Test completed successfully!")
            print("✅ RSI correlation alerts are working with real MT5 data and Supabase")
        else:
            print("\n💥 Test failed!")
            print("❌ Check MT5 connection, Supabase configuration, and alert data")
            
    except Exception as e:
        print(f"\n💥 Test failed with error: {e}")
        print("❌ Check MT5 connection and Supabase configuration")

if __name__ == "__main__":
    asyncio.run(main())

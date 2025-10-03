#!/usr/bin/env python3
"""
Test client for the new MT5 Market Data WebSocket server
"""

import asyncio
import json
import websockets
from datetime import datetime

async def test_websocket_connection():
    """Test the WebSocket v2 market data endpoint"""
    uri = "ws://localhost:8000/market-v2"
    
    try:
        print("🔌 Connecting to WebSocket...")
        async with websockets.connect(uri) as websocket:
            print("✅ Connected successfully!")
            
            # Wait for welcome message
            welcome = await websocket.recv()
            welcome_data = json.loads(welcome)
            print(f"📨 Welcome message: {welcome_data}")
            
            # Test subscription to EURUSD with 1M timeframe
            subscription = {
                "action": "subscribe",
                "symbol": "EURUSD",
                "timeframe": "1M",
                "data_types": ["ticks", "indicators"]
            }
            
            print(f"📤 Sending subscription: {subscription}")
            await websocket.send(json.dumps(subscription))
            
            # Wait for subscription confirmation
            response = await websocket.recv()
            response_data = json.loads(response)
            print(f"📨 Subscription response: {response_data}")
            
            # Optionally wait for initial_indicators
            initial_msg = await websocket.recv()
            initial_data = json.loads(initial_msg)
            print(f"📊 Initial message type: {initial_data.get('type')}")
            
            # Listen for updates for 30 seconds
            print("👂 Listening for updates for 30 seconds...")
            timeout_time = datetime.now().timestamp() + 30
            
            while datetime.now().timestamp() < timeout_time:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                    data = json.loads(message)
                    
                    if data.get('type') == 'ticks':
                        print(f"📈 Received {len(data.get('data', []))} tick updates")
                    elif data.get('type') == 'indicator_update':
                        print(f"📊 Indicator Update: {data.get('symbol')} {data.get('timeframe')} bar_time={data.get('data',{}).get('bar_time')}")
                    else:
                        print(f"📨 Other message: {data}")
                        
                except asyncio.TimeoutError:
                    print("⏰ No messages received in last 5 seconds")
                    continue
            
            # Test ping
            print("🏓 Testing ping...")
            await websocket.send(json.dumps({"action": "ping"}))
            pong = await websocket.recv()
            print(f"🏓 Pong response: {json.loads(pong)}")
            
            # Test unsubscribe
            print("❌ Testing unsubscribe...")
            await websocket.send(json.dumps({
                "action": "unsubscribe",
                "symbol": "EURUSD"
            }))
            unsub_response = await websocket.recv()
            print(f"❌ Unsubscribe response: {json.loads(unsub_response)}")
            
    except ConnectionRefusedError:
        print("❌ Connection refused. Make sure the server is running on localhost:8000")
    except Exception as e:
        print(f"❌ Error: {e}")

async def test_legacy_websocket():
    print("\nℹ️ Legacy endpoints removed. Skipping legacy WebSocket test.")

async def test_rest_api():
    """Test the REST API endpoint"""
    import aiohttp
    
    try:
        print("\n🌐 Testing REST API...")
        async with aiohttp.ClientSession() as session:
            url = "http://localhost:8000/api/values?timeframe=1M&symbols=EURUSDm"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    print(f"📊 REST OHLC: Got {data.get('count', 0)} bars for {data.get('symbol')}")
                    if data.get('data'):
                        print(f"📊 Latest REST bar: {data['data'][-1]}")
                else:
                    print(f"❌ REST API error: {response.status}")
    except Exception as e:
        print(f"❌ REST test error: {e}")

if __name__ == "__main__":
    print("🧪 Testing MT5 Market Data Server")
    print("=" * 50)
    
    asyncio.run(test_websocket_connection())
    asyncio.run(test_legacy_websocket())
    asyncio.run(test_rest_api())
    
    print("\n✅ Test completed!")

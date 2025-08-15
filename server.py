import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import MetaTrader5 as mt5
import orjson
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Config via environment variables
API_TOKEN = os.environ.get("API_TOKEN", "")
ALLOWED_ORIGINS = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
MT5_TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", "")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    kwargs = {}
    if MT5_TERMINAL_PATH:
        kwargs["path"] = MT5_TERMINAL_PATH
    if not mt5.initialize(**kwargs):
        err = mt5.last_error()
        print(f"MT5 initialize failed: {err}", file=sys.stderr, flush=True)
        raise RuntimeError(f"MT5 initialize failed: {err}")
    v = mt5.version()
    print(f"MT5 initialized. Version: {v}", flush=True)
    
    yield
    
    # Shutdown
    mt5.shutdown()

app = FastAPI(title="MT5 Tick Stream", version="1.0.0", lifespan=lifespan)

# Always add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],  # Allow all origins in development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Tick(BaseModel):
    symbol: str
    time: int              # epoch ms
    time_iso: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    flags: Optional[int] = None

def _to_tick(symbol: str, info) -> Optional[Tick]:
    if info is None:
        return None
    ts_ms = getattr(info, "time_msc", 0) or int(getattr(info, "time", 0)) * 1000
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return Tick(
        symbol=symbol,
        time=ts_ms,
        time_iso=dt.isoformat(),
        bid=getattr(info, "bid", None),
        ask=getattr(info, "ask", None),
        last=getattr(info, "last", None),
        volume=getattr(info, "volume_real", None) or getattr(info, "volume", None),
        flags=getattr(info, "flags", None),
    )

def ensure_symbol_selected(symbol: str) -> None:
    if not mt5.symbol_select(symbol, True):
        # Try enabling if not already
        info = mt5.symbol_info(symbol)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise HTTPException(status_code=400, detail=f"Failed to select symbol: {symbol}")

def require_api_token_header(x_api_key: Optional[str] = None):
    # For REST: expect header "X-API-Key"
    if API_TOKEN and x_api_key != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/health")
def health():
    v = mt5.version()
    return {"status": "ok", "mt5_version": v}

@app.get("/test-ws")
def test_websocket():
    return {"message": "WebSocket endpoint should be available at /ws/ticks"}

@app.get("/api/tick/{symbol}")
def get_tick(symbol: str, x_api_key: Optional[str] = Depends(require_api_token_header)):
    sym = symbol.upper()
    ensure_symbol_selected(sym)
    info = mt5.symbol_info_tick(sym)
    tick = _to_tick(sym, info)
    if tick is None:
        raise HTTPException(status_code=404, detail="No tick available")
    return tick.model_dump()

@app.get("/api/symbols")
def search_symbols(q: str = Query(..., min_length=1), x_api_key: Optional[str] = Depends(require_api_token_header)):
    q_upper = q.upper()
    res = []
    for s in mt5.symbols_get():
        name = getattr(s, "name", "")
        if q_upper in name.upper():
            res.append({"name": name, "path": getattr(s, "path", "")})
            if len(res) >= 50:
                break
    return {"results": res}

class WSClient:
    def __init__(self, websocket: WebSocket, token: str):
        self.websocket = websocket
        self.token = token
        self.symbols: Set[str] = set()
        self._last_sent_ts: Dict[str, int] = {}
        self._task: Optional[asyncio.Task] = None
        self._send_interval_s: float = 0.10  # 10 Hz

    async def start(self):
        # WebSocket is already accepted in the main handler
        # Just start the background task
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        try:
            while True:
                await self._send_updates_once()
                await asyncio.sleep(self._send_interval_s)
        except asyncio.CancelledError:
            return

    async def _send_updates_once(self):
        if not self.symbols:
            return
        updates: List[dict] = []
        for sym in list(self.symbols):
            try:
                ensure_symbol_selected(sym)
                info = mt5.symbol_info_tick(sym)
                if info is None:
                    continue
                ts_ms = getattr(info, "time_msc", 0) or int(getattr(info, "time", 0)) * 1000
                if self._last_sent_ts.get(sym) == ts_ms:
                    continue
                tick = _to_tick(sym, info)
                if tick:
                    updates.append(tick.model_dump())
                    self._last_sent_ts[sym] = ts_ms
            except HTTPException:
                # symbol disappeared or invalid; drop it
                self.symbols.discard(sym)
        if updates:
            await self.websocket.send_bytes(orjson.dumps({"type": "ticks", "data": updates}))

    async def handle_message(self, message: dict):
        action = message.get("action")
        if action == "subscribe":
            syms = [s.upper() for s in message.get("symbols", [])]
            for s in syms:
                ensure_symbol_selected(s)
                self.symbols.add(s)
            await self.websocket.send_json({"type": "subscribed", "symbols": sorted(self.symbols)})
        elif action == "unsubscribe":
            syms = [s.upper() for s in message.get("symbols", [])]
            for s in syms:
                self.symbols.discard(s)
            await self.websocket.send_json({"type": "unsubscribed", "symbols": sorted(self.symbols)})
        elif action == "ping":
            await self.websocket.send_json({"type": "pong"})
        else:
            await self.websocket.send_json({"type": "error", "error": "unknown_action"})

@app.websocket("/ws/ticks")
async def ws_ticks(websocket: WebSocket):
    print("🔌 WebSocket connection attempt received")
    
    try:
        await websocket.accept()
        print("✅ WebSocket connection accepted")
        
        # Send a welcome message
        await websocket.send_json({"type": "connected", "message": "WebSocket connected successfully"})
        print("📤 Sent welcome message")
        
        # Create WSClient for real MT5 data
        client = WSClient(websocket, "")
        await client.start()
        print("📊 WSClient started with MT5 integration")
        
        # Handle incoming messages
        while True:
            data = await websocket.receive_text()
            print(f"📥 Received: {data}")
            
            try:
                message = orjson.loads(data)
                print(f"📋 Parsed message: {message}")
                await client.handle_message(message)
                
            except Exception as parse_error:
                print(f"❌ Error parsing message: {parse_error}")
                await websocket.send_json({"type": "error", "error": str(parse_error)})
                
    except WebSocketDisconnect:
        print("🔌 WebSocket disconnected normally")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🧹 Cleaning up WSClient...")
        await client.stop()

def _install_sigterm_handler(loop: asyncio.AbstractEventLoop):
    def _handler():
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.stop()
    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handler)
    except NotImplementedError:
        # Windows without Proactor might not support signal handlers; ignore.
        pass

if __name__ == "__main__":
    # Run with: python server.py
    import uvicorn
    _install_sigterm_handler(asyncio.get_event_loop())
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False, server_header=False, date_header=False)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from decimal import Decimal
from typing import List, Optional, Dict
from datetime import datetime
import asyncio
import os

from ..core.order import Order, Side, OrderType, OrderStatus
from ..core.matching_engine import MatchingEngineManager
from ..data.level2_feed import MockLevel2Feed
from ..data.market_data import TradeEvent, QuoteEvent


# ─────────── FastAPI App ───────────

app = FastAPI(
    title="高精度队列模拟撮合系统",
    description="基于 Level-2 逐笔成交和盘口行情的队列模拟撮合系统",
    version="1.0.0",
)

# 挂载静态文件服务（前端页面）
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
else:
    # 如果前端目录不存在，创建一个
    os.makedirs(frontend_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

# 全局引擎管理器
engine_manager = MatchingEngineManager()

class OrderRequest(BaseModel):
    symbol: str = Field(..., description="标的代码，如 000001.SZ")
    side: str = Field(..., description="买卖方向: buy/sell")
    price: str = Field(..., description="委托价格")
    quantity: int = Field(..., description="委托数量", ge=1)
    order_type: str = Field(default="limit", description="订单类型: limit/market")


class OrderResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[dict] = None


class CancelResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[dict] = None


class TradeQuery(BaseModel):
    symbol: Optional[str] = None
    order_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    page: int = 1
    page_size: int = 20


# ─────────── FastAPI App ───────────

app = FastAPI(
    title="高精度队列模拟撮合系统",
    description="基于 Level-2 逐笔成交和盘口行情的队列模拟撮合系统",
    version="1.0.0",
)

# 全局引擎管理器
engine_manager = MatchingEngineManager()

# 行情源（模拟）
feed_handlers: Dict[str, MockLevel2Feed] = {}


@app.on_event("startup")
async def startup_event():
    """启动事件"""
    print("撮合系统启动中...")


@app.on_event("shutdown")
async def shutdown_event():
    """关闭事件"""
    print("撮合系统关闭中...")
    for feed in feed_handlers.values():
        await feed.stop()
    await engine_manager.shutdown_all()


# ─────────── REST API ───────────

@app.post("/api/v1/orders", response_model=OrderResponse)
async def create_order(req: OrderRequest):
    """提交委托"""
    try:
        side = Side(req.side.lower())
        order_type = OrderType(req.order_type.lower())
        price = Decimal(req.price) if req.price else Decimal("0")
        
        order = Order(
            symbol=req.symbol,
            side=side,
            price=price,
            quantity=req.quantity,
            order_type=order_type,
        )
        
        result = await engine_manager.place_order(order)
        
        return OrderResponse(
            code=0,
            message="success",
            data=result.to_dict(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/orders/{order_id}", response_model=CancelResponse)
async def cancel_order(order_id: str, symbol: Optional[str] = None):
    """撤销委托"""
    try:
        # 如果没有提供 symbol，需要遍历查找
        if symbol:
            result = await engine_manager.cancel_order(symbol, order_id)
        else:
            # 遍历所有引擎查找订单
            result = None
            for sym, engine in engine_manager.get_all_engines().items():
                result = await engine.cancel_order(order_id)
                if result:
                    break
        
        if result is None:
            raise HTTPException(status_code=404, detail="Order not found or already filled/cancelled")
        
        return CancelResponse(
            code=0,
            message="success",
            data=result.to_dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/orders/{order_id}", response_model=OrderResponse)
async def get_order(order_id: str, symbol: Optional[str] = None):
    """查询单笔委托"""
    try:
        order = None
        if symbol:
            order = engine_manager.get_order(symbol, order_id)
        else:
            for sym, engine in engine_manager.get_all_engines().items():
                order = engine.get_order(order_id)
                if order:
                    break
        
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        
        return OrderResponse(
            code=0,
            message="success",
            data=order.to_dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/orders", response_model=OrderResponse)
async def list_orders(
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    side: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """查询委托列表"""
    try:
        orders = []
        
        engines = engine_manager.get_all_engines()
        if symbol:
            engines = {symbol: engines.get(symbol)} if symbol in engines else {}
        
        status_filter = OrderStatus(status) if status else None
        side_filter = Side(side) if side else None
        
        for sym, engine in engines.items():
            if engine is None:
                continue
            engine_orders = engine.order_book.get_all_orders(status_filter, side_filter)
            orders.extend(engine_orders)
        
        # 分页
        total = len(orders)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = orders[start:end]
        
        return OrderResponse(
            code=0,
            message="success",
            data={
                "total": total,
                "page": page,
                "page_size": page_size,
                "orders": [o.to_dict() for o in paginated],
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/trades", response_model=OrderResponse)
async def list_trades(
    symbol: Optional[str] = None,
    order_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """查询成交记录"""
    try:
        trades = []
        
        engines = engine_manager.get_all_engines()
        if symbol:
            engines = {symbol: engines.get(symbol)} if symbol in engines else {}
        
        for sym, engine in engines.items():
            if engine is None:
                continue
            engine_trades = engine.order_book.get_trades(order_id)
            trades.extend(engine_trades)
        
        # 时间过滤
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                trades = [t for t in trades if t.trade_time >= start_dt]
            except:
                pass
        
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                trades = [t for t in trades if t.trade_time <= end_dt]
            except:
                pass
        
        # 分页
        total = len(trades)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = trades[start:end]
        
        return OrderResponse(
            code=0,
            message="success",
            data={
                "total": total,
                "page": page,
                "page_size": page_size,
                "trades": [t.to_dict() for t in paginated],
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/orderbook/{symbol}", response_model=OrderResponse)
async def get_orderbook(symbol: str, depth: int = 10):
    """查询订单簿快照"""
    try:
        engine = await engine_manager.get_or_create_engine(symbol)
        snapshot = engine.get_orderbook_snapshot(depth)
        
        return OrderResponse(
            code=0,
            message="success",
            data=snapshot,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/symbols", response_model=OrderResponse)
async def list_symbols():
    """查询已激活的标的列表"""
    try:
        engines = engine_manager.get_all_engines()
        symbols = []
        for symbol, engine in engines.items():
            stats = engine.get_stats()
            symbols.append({
                "symbol": symbol,
                "status": "active",
                "orders_received": stats.get("orders_received", 0),
                "orders_filled": stats.get("orders_filled", 0),
                "orders_queued": stats.get("orders_queued", 0),
            })
        
        return OrderResponse(
            code=0,
            message="success",
            data={"symbols": symbols},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/stats/{symbol}", response_model=OrderResponse)
async def get_stats(symbol: str):
    """查询标的统计信息"""
    try:
        engine = engine_manager.get_all_engines().get(symbol)
        if engine is None:
            raise HTTPException(status_code=404, detail="Symbol not found")
        
        return OrderResponse(
            code=0,
            message="success",
            data=engine.get_stats(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────── WebSocket ───────────

class ConnectionManager:
    """WebSocket 连接管理"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
    
    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)
    
    async def send_to(self, client_id: str, message: dict):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_json(message)
    
    async def broadcast(self, message: dict):
        for connection in self.active_connections.values():
            await connection.send_json(message)


ws_manager = ConnectionManager()


@app.websocket("/ws/v1")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时推送"""
    client_id = f"ws-{id(websocket)}"
    await ws_manager.connect(websocket, client_id)
    
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "subscribe":
                channel = data.get("channel")
                if channel == "market":
                    symbols = data.get("symbols", [])
                    # 注册行情推送
                    for symbol in symbols:
                        await _subscribe_market(symbol, client_id)
                elif channel == "orders":
                    order_ids = data.get("order_ids", [])
                    # 注册订单状态推送
                    pass
            
            elif action == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
    
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id)
    except Exception as e:
        ws_manager.disconnect(client_id)


async def _subscribe_market(symbol: str, client_id: str):
    """订阅行情推送"""
    if symbol not in feed_handlers:
        feed = MockLevel2Feed(symbol=symbol)
        feed_handlers[symbol] = feed
        
        # 注册回调
        async def on_trade(trade: TradeEvent):
            await engine_manager.process_trade(trade.symbol, trade.to_dict())
            await ws_manager.send_to(client_id, {
                "type": "trade",
                **trade.to_dict(),
            })
        
        async def on_quote(quote: QuoteEvent):
            await engine_manager.process_quote(quote.symbol, quote.to_dict())
            await ws_manager.send_to(client_id, {
                "type": "quote",
                **quote.to_dict(),
            })
        
        feed.on_trade(on_trade)
        feed.on_quote(on_quote)
        await feed.start()

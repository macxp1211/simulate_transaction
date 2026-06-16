from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from decimal import Decimal
from typing import List, Optional, Dict
from datetime import datetime
import asyncio
import os

from ..core.order import Order, Side, OrderType, OrderStatus
from ..core.matching_engine import MatchingEngineManager
from ..core.account import Account
from ..core.fee import AShareFeeCalculator
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

# 全局账户、费用模型与引擎管理器
# 默认账户附带一定底仓，便于模拟卖出场景；今日买入仍需 settle 后变为可用
account = Account(initial_position=100000)
fee_calculator = AShareFeeCalculator()
engine_manager = MatchingEngineManager(account=account, fee_calculator=fee_calculator)

# 行情源（模拟）
feed_handlers: Dict[str, MockLevel2Feed] = {}

# 行情订阅者：symbol -> set(client_id)
market_subscribers: Dict[str, set] = {}

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


@app.get("/")
async def root():
    """根路径重定向到委托终端页面"""
    return RedirectResponse(url="/static/index.html")


@app.on_event("startup")
async def startup_event():
    """启动事件"""
    print("撮合系统启动中...")

    # 注册全局成交回调：撮合引擎产生成交后广播给对应标的的订阅者
    async def on_trade_generated(trade):
        await _broadcast_trade(trade)

    engine_manager.on_trade_generated(on_trade_generated)

    # 启动默认标的的模拟行情源，使系统即使没有 WebSocket 订阅也具备流动性
    await _start_market_feed("000001.SZ")
    # 启动盘口快照广播任务（无订阅者时会自动退出）
    if "000001.SZ" not in quote_broadcast_tasks or quote_broadcast_tasks["000001.SZ"].done():
        quote_broadcast_tasks["000001.SZ"] = asyncio.create_task(_quote_broadcast_loop("000001.SZ"))


async def _broadcast_trade(trade):
    """将成交广播给订阅了该标的的所有客户端"""
    message = {"type": "trade", **trade.to_dict()}
    for cid in list(market_subscribers.get(trade.symbol, set())):
        await ws_manager.send_to(cid, message)


# 标的 -> 盘口快照广播任务
quote_broadcast_tasks: Dict[str, asyncio.Task] = {}


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

        if result.status == OrderStatus.REJECTED:
            detail = result.reject_reason or "委托被拒绝"
            raise HTTPException(status_code=400, detail=detail)

        return OrderResponse(
            code=0,
            message="success",
            data=result.to_dict(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
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
        
        # 按成交时间倒序，最新成交在前
        trades.sort(key=lambda t: t.trade_time, reverse=True)
        
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


@app.get("/api/v1/account", response_model=OrderResponse)
async def get_account():
    """查询账户快照"""
    try:
        acc = engine_manager.get_account()
        if acc is None:
            raise HTTPException(status_code=404, detail="Account not found")

        return OrderResponse(
            code=0,
            message="success",
            data=acc.to_dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/account/settle", response_model=OrderResponse)
async def settle_account():
    """日终结算：将今日买入的冻结仓位转为可用仓位"""
    try:
        acc = engine_manager.get_account()
        if acc is None:
            raise HTTPException(status_code=404, detail="Account not found")

        acc.settle()
        return OrderResponse(
            code=0,
            message="success",
            data=acc.to_dict(),
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
        _unsubscribe_market(client_id)
        ws_manager.disconnect(client_id)
    except Exception as e:
        _unsubscribe_market(client_id)
        ws_manager.disconnect(client_id)


def _unsubscribe_market(client_id: str):
    """客户端断开时清理行情订阅"""
    for symbol in list(market_subscribers.keys()):
        subscribers = market_subscribers[symbol]
        subscribers.discard(client_id)
        if not subscribers:
            market_subscribers.pop(symbol, None)
            feed = feed_handlers.pop(symbol, None)
            if feed:
                asyncio.create_task(feed.stop())


async def _start_market_feed(symbol: str):
    """启动指定标的的模拟行情源"""
    if symbol in feed_handlers:
        return

    def book_provider():
        engine = engine_manager.get_all_engines().get(symbol)
        return engine.get_orderbook_snapshot(depth=5) if engine else None

    feed = MockLevel2Feed(symbol=symbol, book_provider=book_provider)
    feed_handlers[symbol] = feed

    # 注册模拟委托回调：将 mock 委托放入撮合引擎订单簿
    # mock 订单仅用于构造盘口/队列，不参与真实账户冻结
    async def on_order(order_data: dict):
        order = Order(
            symbol=order_data["symbol"],
            side=Side(order_data["side"]),
            price=Decimal(order_data["price"]),
            quantity=order_data["quantity"],
            order_type=OrderType.LIMIT,
            order_id=order_data["order_id"],
            is_mock=True,
        )
        await engine_manager.place_order(order)

    feed.on_order(on_order)

    # 注册行情撤单回调：将 mock 撤单事件交给撮合引擎处理队列消耗
    async def on_cancel(cancel_data: dict):
        await engine_manager.process_cancel_feed(symbol, cancel_data)

    feed.on_cancel(on_cancel)
    await feed.start()

    # 启动该标的的盘口快照广播任务
    if symbol not in quote_broadcast_tasks or quote_broadcast_tasks[symbol].done():
        quote_broadcast_tasks[symbol] = asyncio.create_task(_quote_broadcast_loop(symbol))


async def _subscribe_market(symbol: str, client_id: str):
    """订阅行情推送"""
    subscribers = market_subscribers.setdefault(symbol, set())
    subscribers.add(client_id)

    await _start_market_feed(symbol)


async def _quote_broadcast_loop(symbol: str):
    """定期从引擎订单簿生成盘口快照并广播"""
    while True:
        try:
            subscribers = market_subscribers.get(symbol, set())
            if not subscribers:
                break

            engine = engine_manager.get_all_engines().get(symbol)
            if engine:
                snapshot = engine.get_orderbook_snapshot(depth=5)
                message = {"type": "quote", **snapshot}
                for cid in list(subscribers):
                    await ws_manager.send_to(cid, message)

            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"[{symbol}] Quote broadcast error: {e}")
            await asyncio.sleep(1.0)

    quote_broadcast_tasks.pop(symbol, None)

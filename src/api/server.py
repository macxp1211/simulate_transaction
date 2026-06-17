from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from decimal import Decimal
from typing import List, Optional, Dict
from datetime import datetime
import asyncio
import concurrent.futures
import os

from ..core.order import Order, Side, OrderType, OrderStatus
from ..core.matching_engine import MatchingEngineManager
from ..core.latency_injector import LatencyInjector
from ..core.account import Account
from ..core.fee import AShareFeeCalculator
from ..data.level2_feed import MockLevel2Feed
from ..data.market_data import TradeEvent, QuoteEvent
from ..persistence import PersistenceManager


# ─────────── FastAPI App ───────────

app = FastAPI(
    title="高精度队列模拟撮合系统",
    description="基于 Level-2 逐笔成交和盘口行情的队列模拟撮合系统",
    version="2.0.0",
)

# 挂载静态文件服务（前端页面）
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
else:
    # 如果前端目录不存在，创建一个
    os.makedirs(frontend_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

# 全局账户、费用模型、引擎管理器与持久化
account = Account(initial_position=100000)
fee_calculator = AShareFeeCalculator()
# 延迟注入器：模拟不同来源的网络/撮合延迟（毫秒）
# 默认无延迟，可通过 API 动态开启以进行策略压力测试
latency_injector = LatencyInjector(default_latency_ms=0.0)
engine_manager = MatchingEngineManager(
    account=account, fee_calculator=fee_calculator, latency_injector=latency_injector
)
persistence = PersistenceManager(data_dir="data")

# 用于执行同步持久化 IO 的线程池，避免阻塞 asyncio 事件循环
_persistence_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="persistence")


async def _run_persistence(method, *args):
    """在线程池中执行同步持久化方法"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_persistence_executor, method, *args)


# 行情源（模拟）
feed_handlers: Dict[str, MockLevel2Feed] = {}

# 行情参与者配置缓存，确保行情源重建后仍使用用户设置
participant_config_cache: Dict[str, Dict] = {}

# 行情订阅者：symbol -> set(client_id)
market_subscribers: Dict[str, set] = {}

# 成交历史缓存（用于前端行情展示）
trade_history_cache: List[dict] = []
max_trade_history = 500

# 价格历史缓存（用于前端走势图）
price_history_cache: Dict[str, List[dict]] = {}
max_price_history = 300

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


class ParticipantConfigRequest(BaseModel):
    symbol: Optional[str] = "000001.SZ"
    target_price: Optional[float] = None
    market_maker_count: Optional[int] = None
    trend_follower_count: Optional[int] = None
    mean_reversion_count: Optional[int] = None
    noise_trader_count: Optional[int] = None
    aggressive_trader_count: Optional[int] = None
    order_interval: Optional[float] = None
    algorithmic_trader_count: Optional[int] = None
    stop_loss_trader_count: Optional[int] = None
    order_book_imbalance_count: Optional[int] = None
    iceberg_participant_count: Optional[int] = None
    directional_trader_count: Optional[int] = None
    chip_collector_count: Optional[int] = None
    day_trader_count: Optional[int] = None

class ParticipantConfigResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[dict] = None

class MarketRulesUpdateRequest(BaseModel):
    previous_close: Optional[str] = Field(default=None, description="昨收价，如 10.50")
    market_type: Optional[str] = Field(default=None, description="市场类型: main_board/st_board/star_market/chinext/bse")

class AccountResetRequest(BaseModel):
    initial_cash: Optional[str] = Field(default=None, description="初始现金，如 1000000.00")
    initial_position: Optional[int] = Field(default=None, description="初始可用持仓")



# ─────────── FastAPI App ───────────


@app.get("/")
async def root():
    """根路径重定向到委托终端页面"""
    return RedirectResponse(url="/static/index.html")


async def _restore_state():
    """启动时从 SQLite 恢复账户状态和活跃订单"""
    print("[Restore] 开始从持久化恢复状态...")

    # 1. 恢复账户：优先使用最新日终结算记录
    try:
        settlement = await _run_persistence(persistence.get_latest_settlement)
        if settlement:
            account.restore_from_dict(settlement)
            print(
                f"[Restore] 账户已恢复: cash={account.cash}, "
                f"available={account.available_position}, "
                f"frozen={account.frozen_position}, "
                f"today_bought={account.today_bought_position}"
            )
        else:
            print("[Restore] 未找到结算记录，使用默认账户状态")
    except Exception as e:
        print(f"[Restore] 账户恢复失败: {e}")

    # 2. 恢复活跃订单到对应引擎订单簿
    try:
        active_order_dicts = await _run_persistence(persistence.get_active_orders)
        if not active_order_dicts:
            print("[Restore] 没有活跃订单需要恢复")
        else:
            # 按 symbol 分组并按创建时间排序，保持原始 FIFO 顺序
            orders_by_symbol: Dict[str, List[Order]] = {}
            for od in active_order_dicts:
                try:
                    order = Order.from_dict(od)
                    orders_by_symbol.setdefault(order.symbol, []).append(order)
                except Exception as e:
                    print(f"[Restore] 跳过无效订单 {od.get('order_id')}: {e}")

            for symbol, orders in orders_by_symbol.items():
                orders.sort(key=lambda o: o.create_time)
                engine = await engine_manager.get_or_create_engine(symbol)
                for order in orders:
                    engine.order_book.restore_order(order)
                print(f"[Restore] {symbol} 已恢复 {len(orders)} 个活跃订单")
    except Exception as e:
        print(f"[Restore] 订单恢复失败: {e}")

    # 3. 恢复前端缓存（成交历史、价格历史）
    try:
        recent_trades = await _run_persistence(persistence.get_recent_trades, max_trade_history)
        # 清空旧缓存并按时间正序填充
        trade_history_cache.clear()
        trade_history_cache.extend(recent_trades)

        price_history_cache.clear()
        for t in recent_trades:
            symbol = t.get("symbol")
            if not symbol:
                continue
            if symbol not in price_history_cache:
                price_history_cache[symbol] = []
            price_history_cache[symbol].append({
                "time": t.get("trade_time"),
                "price": float(t.get("price", 0)),
                "quantity": t.get("quantity", 0),
                "side": t.get("side"),
            })
        print(f"[Restore] 已恢复 {len(recent_trades)} 条成交记录到前端缓存")
    except Exception as e:
        print(f"[Restore] 缓存恢复失败: {e}")


@app.on_event("startup")
async def startup_event():
    """启动事件"""
    print("撮合系统启动中...")

    # 优先从持久化恢复状态（账户、活跃订单、缓存）
    await _restore_state()

    # 注册全局成交回调：撮合引擎产生成交后广播给对应标的的订阅者
    async def on_trade_generated(trade):
        # 广播给 WebSocket 订阅者
        await _broadcast_trade(trade)
        # 保存到持久化（在线程池中执行，避免阻塞事件循环）
        try:
            await _run_persistence(persistence.save_trade, trade.to_dict())
            # 同时持久化该笔成交对应订单的最新状态
            engine = engine_manager.get_all_engines().get(trade.symbol)
            
            # 通知主动方参与者
            order = engine.get_order(trade.order_id) if engine else None
            if order:
                await _run_persistence(persistence.save_order, order.to_dict())
                # 通知对应参与者订单已成交，清理其 pending 列表
                _notify_participant_filled(trade.order_id, trade.to_dict())
            
            # 通知对手方参与者（被动成交方）
            if trade.counterparty_order_id:
                counter_order = engine.get_order(trade.counterparty_order_id) if engine else None
                if counter_order:
                    await _run_persistence(persistence.save_order, counter_order.to_dict())
                    # 从对手方视角构造成交信息：side 取反
                    counter_trade_info = trade.to_dict()
                    counter_trade_info["side"] = "sell" if trade.side == "buy" else "buy"
                    counter_trade_info["order_id"] = trade.counterparty_order_id
                    counter_trade_info["counterparty_order_id"] = trade.order_id
                    _notify_participant_filled(trade.counterparty_order_id, counter_trade_info)
        except Exception as e:
            print(f"[Persistence] save trade/order error: {e}")

        # 更新共享市场状态（按 symbol 隔离）
        try:
            from src.data.participants import get_shared_market_state
            get_shared_market_state(trade.symbol).on_trade(trade.to_dict())
        except Exception as e:
            print(f"[SharedMarketState] update error: {e}")

        # 在日志中记录成交来源（用户订单或 mock 参与者）
        participant_id = order.participant_id if order else None
        source = participant_id or "USER"
        print(
            f"[Trade] {source} {trade.side} {trade.symbol} @ {trade.price} x {trade.quantity} "
            f"(order={trade.order_id}, source={trade.match_source})"
        )
        # 缓存到成交历史
        trade_dict = trade.to_dict()
        trade_history_cache.append(trade_dict)
        if len(trade_history_cache) > max_trade_history:
            trade_history_cache.pop(0)
        # 更新价格历史
        symbol = trade.symbol
        if symbol not in price_history_cache:
            price_history_cache[symbol] = []
        ph = price_history_cache[symbol]
        ph.append({
            "time": trade.trade_time.isoformat(),
            "price": float(trade.price),
            "quantity": trade.quantity,
            "side": trade.side,
        })
        if len(ph) > max_price_history:
            ph.pop(0)

    engine_manager.on_trade_generated(on_trade_generated)

    # 启动默认标的的模拟行情源
    await _start_market_feed("000001.SZ")
    # 启动盘口快照广播任务
    if "000001.SZ" not in quote_broadcast_tasks or quote_broadcast_tasks["000001.SZ"].done():
        quote_broadcast_tasks["000001.SZ"] = asyncio.create_task(_quote_broadcast_loop("000001.SZ"))
    # 启动持久化快照任务
    asyncio.create_task(_persistence_snapshot_loop())
    # 启动价格历史广播任务
    asyncio.create_task(_price_history_broadcast_loop())
    # 启动排行榜计算任务
    asyncio.create_task(_leaderboard_loop())


async def _broadcast_trade(trade):
    """将成交广播给订阅了该标的的所有客户端"""
    message = {"type": "trade", **trade.to_dict()}
    for cid in list(market_subscribers.get(trade.symbol, set())):
        await ws_manager.send_to(cid, message)


def _notify_participant_filled(order_id: str, trade_info: dict):
    """当参与者生成的 mock 订单成交时，通知对应参与者清理 pending 列表"""
    # order_id 形如 "MM-1-000001"，前缀为 participant_id
    # 先尝试从 feed 的 registry 中查找
    for feed in list(feed_handlers.values()):
        participant = feed.registry.get_participant(order_id)
        if participant:
            participant.on_order_filled(order_id, trade_info)
            return
        # 按前缀匹配
        if "-" in order_id:
            prefix = order_id.rsplit("-", 1)[0]
            participant = feed.registry.get_participant(prefix)
            if participant:
                participant.on_order_filled(order_id, trade_info)
                return


# 标的 -> 盘口快照广播任务
quote_broadcast_tasks: Dict[str, asyncio.Task] = {}


@app.on_event("shutdown")
async def shutdown_event():
    """关闭事件"""
    print("撮合系统关闭中...")
    for feed in list(feed_handlers.values()):
        await feed.stop()
    await engine_manager.shutdown_all()


async def _persistence_snapshot_loop():
    """定期保存订单簿快照到持久化"""
    while True:
        try:
            await asyncio.sleep(5.0)
            for symbol, engine in engine_manager.get_all_engines().items():
                try:
                    snapshot = engine.get_orderbook_snapshot(depth=10)
                    await _run_persistence(persistence.save_snapshot, snapshot)
                except Exception as e:
                    print(f"[Persistence] snapshot error {symbol}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Persistence] loop error: {e}")


async def _price_history_broadcast_loop():
    """定期广播最新价格点给所有 WebSocket 客户端（增量推送）"""
    last_broadcast_idx: Dict[str, int] = {}
    while True:
        try:
            await asyncio.sleep(2.0)
            for symbol, history in list(price_history_cache.items()):
                if not history:
                    continue
                last_idx = last_broadcast_idx.get(symbol, -1)
                if len(history) - 1 <= last_idx:
                    continue
                new_points = history[last_idx + 1:]
                last_broadcast_idx[symbol] = len(history) - 1
                message = {
                    "type": "price_history_delta",
                    "symbol": symbol,
                    "data": new_points,
                }
                for cid in list(market_subscribers.get(symbol, set())):
                    await ws_manager.send_to(cid, message)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[PriceHistory] broadcast error: {e}")


async def _leaderboard_loop():
    """定期计算并广播排行榜"""
    while True:
        try:
            await asyncio.sleep(10.0)
            await _compute_and_broadcast_leaderboard()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Leaderboard] loop error: {e}")


async def _compute_and_broadcast_leaderboard():
    """计算所有行情源的参与者排行榜并持久化/广播"""
    from src.core.leaderboard import compute_participant_rankings

    for symbol, feed in list(feed_handlers.items()):
        participants = feed.registry.get_participants()
        if not participants:
            continue
        rankings = compute_participant_rankings(participants)
        if not rankings:
            continue

        # 持久化
        try:
            await _run_persistence(persistence.save_leaderboard, symbol, rankings)
        except Exception as e:
            print(f"[Persistence] save leaderboard error: {e}")

        # 广播给订阅了该标的的客户端
        message = {
            "type": "leaderboard",
            "symbol": symbol,
            "data": rankings,
        }
        for cid in list(market_subscribers.get(symbol, set())):
            try:
                await ws_manager.send_to(cid, message)
            except Exception as e:
                print(f"[Leaderboard] broadcast to {cid} error: {e}")


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
            source="internal",
        )
        
        result = await engine_manager.place_order(order)

        # 持久化订单（成功/拒绝均记录）
        try:
            await _run_persistence(persistence.save_order, result.to_dict())
        except Exception as e:
            print(f"[Persistence] save order error: {e}")

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

        # 持久化撤单后的订单状态
        try:
            await _run_persistence(persistence.save_order, result.to_dict())
        except Exception as e:
            print(f"[Persistence] save cancel order error: {e}")
        
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
        # 保存结算记录到持久化（在线程池中执行）
        try:
            for symbol in engine_manager.get_all_engines().keys():
                await _run_persistence(persistence.save_settlement, symbol, acc.to_dict())
        except Exception as e:
            print(f"[Persistence] settle save error: {e}")
        return OrderResponse(
            code=0,
            message="success",
            data=acc.to_dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/account/reset", response_model=OrderResponse)
async def reset_account(req: AccountResetRequest):
    """重置账户（可修改初始现金与初始持仓），并清空所有运行中引擎"""
    try:
        await engine_manager.reset_account(
            initial_cash=req.initial_cash,
            initial_position=req.initial_position,
        )
        # 清空持久化缓存的历史数据，避免旧数据干扰新模拟
        trade_history_cache.clear()
        price_history_cache.clear()
        for symbol in list(feed_handlers.keys()):
            await feed_handlers[symbol].stop()
        feed_handlers.clear()
        # 保留 market_subscribers，让已连接的客户端继续收到后续行情；
        # 只清除缓存和 feed，重置账户与引擎状态

        acc = engine_manager.get_account()
        return OrderResponse(
            code=0,
            message="success",
            data=acc.to_dict(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────── 行情与参与者配置 API ───────────

@app.get("/api/v1/market/trade_history", response_model=OrderResponse)
async def get_trade_history(symbol: Optional[str] = None, limit: int = 100):
    """获取实时成交历史（内存缓存）"""
    try:
        trades = trade_history_cache
        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]
        trades = trades[-limit:]
        return OrderResponse(
            code=0,
            message="success",
            data={"trades": trades, "total": len(trades)},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/market/price_history", response_model=OrderResponse)
async def get_price_history(symbol: str = "000001.SZ", limit: int = 200):
    """获取价格历史（用于走势图）"""
    try:
        history = price_history_cache.get(symbol, [])
        history = history[-limit:]
        return OrderResponse(
            code=0,
            message="success",
            data={"symbol": symbol, "history": history},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/market/participants", response_model=OrderResponse)
async def get_participants(symbol: Optional[str] = None):
    """获取行情参与者状态和统计"""
    try:
        all_stats = []
        for sym, feed in list(feed_handlers.items()):
            if symbol and sym != symbol:
                continue
            all_stats.extend(feed.participant_stats)
        return OrderResponse(
            code=0,
            message="success",
            data={"participants": all_stats},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/market/participants/config", response_model=OrderResponse)
async def update_participant_config(req: ParticipantConfigRequest):
    """更新行情参与者配置（目标价格、数量、频率等）"""
    try:
        symbol = req.symbol or "000001.SZ"
        config = {}
        if req.target_price is not None:
            config["target_price"] = req.target_price
        if req.market_maker_count is not None:
            config["market_maker_count"] = req.market_maker_count
        if req.trend_follower_count is not None:
            config["trend_follower_count"] = req.trend_follower_count
        if req.mean_reversion_count is not None:
            config["mean_reversion_count"] = req.mean_reversion_count
        if req.noise_trader_count is not None:
            config["noise_trader_count"] = req.noise_trader_count
        if req.aggressive_trader_count is not None:
            config["aggressive_trader_count"] = req.aggressive_trader_count
        if req.order_interval is not None:
            config["order_interval"] = req.order_interval
        if req.algorithmic_trader_count is not None:
            config["algorithmic_trader_count"] = req.algorithmic_trader_count
        if req.stop_loss_trader_count is not None:
            config["stop_loss_trader_count"] = req.stop_loss_trader_count
        if req.order_book_imbalance_count is not None:
            config["order_book_imbalance_count"] = req.order_book_imbalance_count
        if req.iceberg_participant_count is not None:
            config["iceberg_participant_count"] = req.iceberg_participant_count
        if req.directional_trader_count is not None:
            config["directional_trader_count"] = req.directional_trader_count
        if req.chip_collector_count is not None:
            config["chip_collector_count"] = req.chip_collector_count
        if req.day_trader_count is not None:
            config["day_trader_count"] = req.day_trader_count

        # 同步更新全局配置缓存，确保行情源重建后仍使用用户设置
        cached = participant_config_cache.setdefault(symbol, {})
        cached.update(config)

        feed = feed_handlers.get(symbol)
        if feed is not None:
            feed.update_participant_config(config)
            current = feed.get_participant_config()
        else:
            # 如果行情源未启动，直接更新注册表配置
            from ..data.participants import ParticipantRegistry
            registry = ParticipantRegistry(symbol=symbol, base_price=config.get("target_price", 10.50))
            registry.update_config(config)
            current = registry.get_config()

        return OrderResponse(
            code=0,
            message="success",
            data={"config": current, "symbol": symbol},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/market/participants/config", response_model=OrderResponse)
async def get_participant_config(symbol: str = "000001.SZ"):
    """获取当前行情参与者配置"""
    try:
        feed = feed_handlers.get(symbol)
        if feed is not None:
            current = feed.get_participant_config()
        else:
            from ..data.participants import ParticipantRegistry
            registry = ParticipantRegistry(symbol=symbol, base_price=10.50)
            current = registry.get_config()
        return OrderResponse(
            code=0,
            message="success",
            data={"config": current, "symbol": symbol},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────── 持久化 API ───────────

@app.post("/api/v1/persistence/export", response_model=OrderResponse)
async def export_persistence(symbol: str = "000001.SZ"):
    """导出指定标的的订单和成交记录到 CSV"""
    try:
        result = persistence.export_to_csv(symbol)
        return OrderResponse(
            code=0,
            message="success",
            data={"export_result": result},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/persistence/snapshot", response_model=OrderResponse)
async def get_persistence_snapshot(symbol: str = "000001.SZ"):
    """获取持久化的最新订单簿快照"""
    try:
        snapshot = persistence.get_latest_snapshot(symbol)
        return OrderResponse(
            code=0,
            message="success",
            data=snapshot,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────── 市场规则 API ───────────

@app.get("/api/v1/market/rules/{symbol}", response_model=OrderResponse)
async def get_market_rules_api(symbol: str):
    """获取某标的的市场规则（涨跌停、价格笼子等）"""
    try:
        from ..core.market_rules import get_market_rules
        rules = get_market_rules(symbol)
        # 基于当前盘口确定价格笼子基准价
        benchmark = None
        engine = engine_manager.get_all_engines().get(symbol)
        if engine:
            benchmark = (
                engine.order_book.best_ask
                or engine.order_book.best_bid
                or engine._last_trade_price
                or rules.previous_close
            )
        return OrderResponse(
            code=0,
            message="success",
            data=rules.to_dict(benchmark=benchmark),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/market/rules/{symbol}", response_model=OrderResponse)
async def update_market_rules_api(symbol: str, req: MarketRulesUpdateRequest):
    """更新某标的的市场规则"""
    try:
        from ..core.market_rules import get_market_rules, MarketType
        rules = get_market_rules(symbol)
        if req.previous_close is not None and str(req.previous_close).strip() != "":
            rules.previous_close = Decimal(str(req.previous_close))
        if req.market_type is not None:
            try:
                rules.market_type = MarketType(req.market_type)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"未知市场类型: {req.market_type}")
        return OrderResponse(
            code=0,
            message="success",
            data=rules.to_dict(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────── 排行榜 API ───────────

class MarketRegimeRequest(BaseModel):
    regime: str = Field(..., description="市场模式：normal / flash_crash / pump")


@app.get("/api/v1/leaderboard/{symbol}", response_model=OrderResponse)
async def get_leaderboard_api(symbol: str):
    """获取某标的的最新排行榜"""
    try:
        from src.core.leaderboard import compute_participant_rankings
        feed = feed_handlers.get(symbol)
        if feed is not None:
            rankings = compute_participant_rankings(feed.registry.get_participants())
        else:
            rankings = persistence.get_latest_leaderboard(symbol=symbol)
        return OrderResponse(
            code=0,
            message="success",
            data={"symbol": symbol, "rankings": rankings},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/market/regime/{symbol}", response_model=OrderResponse)
async def set_market_regime_api(symbol: str, req: MarketRegimeRequest):
    """切换市场微观结构模式"""
    try:
        feed = feed_handlers.get(symbol)
        if feed is None:
            raise HTTPException(status_code=404, detail=f"标的 {symbol} 行情源未启动")
        feed.set_market_regime(req.regime)
        return OrderResponse(
            code=0,
            message="success",
            data={"symbol": symbol, "regime": req.regime},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/market/regime/{symbol}", response_model=OrderResponse)
async def get_market_regime_api(symbol: str):
    """获取当前市场微观结构模式"""
    try:
        feed = feed_handlers.get(symbol)
        if feed is None:
            return OrderResponse(code=0, message="success", data={"symbol": symbol, "regime": "normal"})
        return OrderResponse(
            code=0,
            message="success",
            data={"symbol": symbol, "regime": feed.market_regime},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────── 分析 API ───────────

@app.get("/api/v1/analytics/order_flow/{symbol}", response_model=OrderResponse)
async def get_order_flow_analytics(symbol: str):
    """获取订单流分析指标"""
    try:
        engine = engine_manager.get_all_engines().get(symbol)
        if engine is None:
            return OrderResponse(code=0, message="success", data={"symbol": symbol, "note": "引擎未启动"})
        snapshot = engine.get_orderbook_snapshot(depth=10)
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        bid_depth = sum(b.get("total_quantity", 0) for b in bids)
        ask_depth = sum(a.get("total_quantity", 0) for a in asks)
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0
        spread = snapshot.get("spread")
        best_bid = snapshot.get("best_bid")
        best_ask = snapshot.get("best_ask")
        return OrderResponse(
            code=0,
            message="success",
            data={
                "symbol": symbol,
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "total_depth": total_depth,
                "imbalance": round(imbalance, 4),
                "spread": spread,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_levels": len(bids),
                "ask_levels": len(asks),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/analytics/participants/pnl", response_model=OrderResponse)
async def get_participants_pnl(symbol: Optional[str] = None):
    """获取参与者 P&L 排名"""
    try:
        all_stats = []
        for sym, feed in list(feed_handlers.items()):
            if symbol and sym != symbol:
                continue
            all_stats.extend(feed.participant_stats)
        # 按 P&L 排序
        all_stats.sort(key=lambda x: x.get("pnl", 0), reverse=True)
        return OrderResponse(
            code=0,
            message="success",
            data={"participants": all_stats, "count": len(all_stats)},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/analytics/depth/{symbol}", response_model=OrderResponse)
async def get_depth_chart_data(symbol: str):
    """获取深度图数据（买卖盘累积深度）"""
    try:
        engine = engine_manager.get_all_engines().get(symbol)
        if engine is None:
            return OrderResponse(code=0, message="success", data={"symbol": symbol, "note": "引擎未启动"})
        snapshot = engine.get_orderbook_snapshot(depth=20)
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        # 买盘累积深度（从最高价格往下累积）
        bid_depths = []
        cumulative = 0
        for b in bids:
            cumulative += b.get("total_quantity", 0)
            bid_depths.append({
                "price": b.get("price"),
                "quantity": b.get("total_quantity"),
                "cumulative": cumulative,
            })
        # 卖盘累积深度（从最低价格往上累积）
        ask_depths = []
        cumulative = 0
        for a in reversed(asks):
            cumulative += a.get("total_quantity", 0)
            ask_depths.insert(0, {
                "price": a.get("price"),
                "quantity": a.get("total_quantity"),
                "cumulative": cumulative,
            })
        return OrderResponse(
            code=0,
            message="success",
            data={
                "symbol": symbol,
                "bid_depths": bid_depths,
                "ask_depths": ask_depths,
                "best_bid": snapshot.get("best_bid"),
                "best_ask": snapshot.get("best_ask"),
                "spread": snapshot.get("spread"),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LatencyConfigRequest(BaseModel):
    source: str = Field(..., description="来源：external 或 internal")
    latency_ms: float = Field(..., ge=0, description="延迟毫秒数")


@app.get("/api/v1/latency", response_model=OrderResponse)
async def get_latency_config():
    """获取当前延迟注入配置"""
    try:
        return OrderResponse(
            code=0,
            message="success",
            data={
                "default_ms": latency_injector.get_latency("__default__"),
                "external_ms": latency_injector.get_latency("external"),
                "internal_ms": latency_injector.get_latency("internal"),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/latency", response_model=OrderResponse)
async def set_latency_config(req: LatencyConfigRequest):
    """设置某类来源的延迟（毫秒）"""
    try:
        latency_injector.set_latency(req.source, req.latency_ms)
        return OrderResponse(
            code=0,
            message="success",
            data={
                "source": req.source,
                "latency_ms": latency_injector.get_latency(req.source),
            },
        )
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

            elif action == "unsubscribe":
                channel = data.get("channel")
                if channel == "market":
                    symbols = data.get("symbols", [])
                    for symbol in symbols:
                        _unsubscribe_symbol(client_id, symbol)

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
        _unsubscribe_symbol(client_id, symbol)


def _unsubscribe_symbol(client_id: str, symbol: str):
    """取消订阅某标的行情"""
    subscribers = market_subscribers.get(symbol)
    if not subscribers:
        return
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

    config = participant_config_cache.get(symbol)
    feed = MockLevel2Feed(
        symbol=symbol,
        book_provider=book_provider,
        participant_config=config,
    )
    feed_handlers[symbol] = feed

    # 注册模拟委托回调：将 mock 委托放入撮合引擎订单簿
    # mock 订单仅用于构造盘口/队列，不参与真实账户冻结
    async def on_order(order_data: dict):
        participant_id = order_data.get("participant_id")
        order = Order(
            symbol=order_data["symbol"],
            side=Side(order_data["side"]),
            price=Decimal(order_data["price"]),
            quantity=order_data["quantity"],
            order_type=OrderType.LIMIT,
            order_id=order_data["order_id"],
            is_mock=True,
            participant_id=participant_id,
            source="external",
        )
        # 日志记录参与者下单
        print(
            f"[Feed] {participant_id or 'unknown'} placed {order.side.value} "
            f"order {order.order_id} @ {order.price} x {order.quantity}"
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

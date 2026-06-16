from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import uuid

from .order import Order, Side, OrderStatus, TradeRecord, OrderType
from .order_book import OrderBook


@dataclass
class MatchingConfig:
    """撮合配置"""
    price_tick: Decimal = Decimal("0.01")
    lot_size: int = 100
    max_queue_depth: int = 10000
    enable_queue_simulation: bool = True


class SymbolMatchingEngine:
    """单标的撮合引擎"""
    
    def __init__(self, symbol: str, config: Optional[MatchingConfig] = None):
        self.symbol = symbol
        self.config = config or MatchingConfig()
        self.order_book = OrderBook(symbol)
        
        # 事件队列（串行处理保证顺序）
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # 统计
        self._stats = {
            "orders_received": 0,
            "orders_filled": 0,
            "orders_queued": 0,
            "orders_cancelled": 0,
            "trades_generated": 0,
            "trades_from_feed": 0,
            "trades_from_cross": 0,
        }
    
    async def start(self):
        """启动撮合循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
    
    async def stop(self):
        """停止撮合循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _run_loop(self):
        """主事件循环"""
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                await self._process_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                # 日志记录错误
                print(f"[{self.symbol}] Error processing event: {e}")
    
    async def _process_event(self, event: dict):
        """处理事件"""
        event_type = event.get("type")
        
        if event_type == "order":
            await self._handle_order(event["order"])
        elif event_type == "cancel":
            await self._handle_cancel(event["order_id"])
        elif event_type == "trade":
            await self._handle_trade(event["trade"])
        elif event_type == "quote":
            await self._handle_quote(event["quote"])
    
    async def _handle_order(self, order: Order):
        """处理新委托"""
        self._stats["orders_received"] += 1
        
        # 参数校验
        if not self._validate_order(order):
            order.status = OrderStatus.REJECTED
            return
        
        # 添加到订单簿
        status, trades = self.order_book.add_order(order)
        
        if status == OrderStatus.FILLED:
            self._stats["orders_filled"] += 1
        elif status == OrderStatus.QUEUED:
            self._stats["orders_queued"] += 1
        elif status == OrderStatus.PARTIAL:
            self._stats["orders_queued"] += 1
        
        # 统计成交
        for trade in trades:
            self._stats["trades_generated"] += 1
            if trade.match_source == "order_cross":
                self._stats["trades_from_cross"] += 1
            else:
                self._stats["trades_from_feed"] += 1
    
    async def _handle_cancel(self, order_id: str):
        """处理撤单"""
        order = self.order_book.cancel_order(order_id)
        if order:
            self._stats["orders_cancelled"] += 1
    
    async def _handle_trade(self, trade: dict):
        """处理逐笔成交（驱动队列消耗）"""
        trade_price = Decimal(str(trade["price"]))
        trade_qty = int(trade["quantity"])
        trade_direction = trade.get("direction", "unknown")
        trade_id = trade.get("trade_id", f"feed-{uuid.uuid4().hex[:8]}")
        
        # 根据成交方向消耗队列
        if trade_direction in ("buy", "sell"):
            trades = self.order_book.consume_queue_on_trade(
                trade_price, trade_qty, trade_direction, trade_id
            )
            for t in trades:
                self._stats["trades_generated"] += 1
                self._stats["trades_from_feed"] += 1
    
    async def _handle_quote(self, quote: dict):
        """处理盘口快照（更新参考价格）"""
        # 盘口快照主要用于监控和验证，不直接驱动撮合
        # 撮合由逐笔成交驱动
        pass
    
    def _validate_order(self, order: Order) -> bool:
        """校验委托参数"""
        if order.symbol != self.symbol:
            return False
        if order.quantity <= 0:
            return False
        if order.quantity % self.config.lot_size != 0:
            return False
        if order.price <= 0 and order.order_type == OrderType.LIMIT:
            return False
        return True
    
    # ─────────── 公共接口 ───────────
    
    async def place_order(self, order: Order) -> Order:
        """提交委托"""
        await self._event_queue.put({"type": "order", "order": order})
        # 等待处理完成（简单轮询）
        for _ in range(100):
            if order.status != OrderStatus.PENDING:
                break
            await asyncio.sleep(0.001)
        return order
    
    async def cancel_order(self, order_id: str) -> Optional[Order]:
        """撤销委托"""
        await self._event_queue.put({"type": "cancel", "order_id": order_id})
        await asyncio.sleep(0.01)  # 给处理时间
        return self.order_book.get_order(order_id)
    
    async def process_trade(self, trade_data: dict):
        """处理逐笔成交"""
        await self._event_queue.put({"type": "trade", "trade": trade_data})
    
    async def process_quote(self, quote_data: dict):
        """处理盘口快照"""
        await self._event_queue.put({"type": "quote", "quote": quote_data})
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """查询订单"""
        return self.order_book.get_order(order_id)
    
    def get_orderbook_snapshot(self, depth: int = 10) -> dict:
        """获取订单簿快照"""
        return self.order_book.get_snapshot(depth)
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return self._stats.copy()


class MatchingEngineManager:
    """多标的撮合引擎管理器"""
    
    def __init__(self):
        self._engines: Dict[str, SymbolMatchingEngine] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_create_engine(self, symbol: str) -> SymbolMatchingEngine:
        """获取或创建标的引擎"""
        async with self._lock:
            if symbol not in self._engines:
                engine = SymbolMatchingEngine(symbol)
                self._engines[symbol] = engine
                await engine.start()
            return self._engines[symbol]
    
    async def place_order(self, order: Order) -> Order:
        """提交委托到对应标的引擎"""
        engine = await self.get_or_create_engine(order.symbol)
        return await engine.place_order(order)
    
    async def cancel_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """撤销委托"""
        engine = await self.get_or_create_engine(symbol)
        return await engine.cancel_order(order_id)
    
    async def process_trade(self, symbol: str, trade_data: dict):
        """分发逐笔成交到对应引擎"""
        engine = await self.get_or_create_engine(symbol)
        await engine.process_trade(trade_data)
    
    async def process_quote(self, symbol: str, quote_data: dict):
        """分发盘口快照到对应引擎"""
        engine = await self.get_or_create_engine(symbol)
        await engine.process_quote(quote_data)
    
    def get_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """查询订单"""
        engine = self._engines.get(symbol)
        return engine.get_order(order_id) if engine else None
    
    def get_all_engines(self) -> Dict[str, SymbolMatchingEngine]:
        """获取所有引擎"""
        return self._engines.copy()
    
    async def shutdown_all(self):
        """关闭所有引擎"""
        for engine in self._engines.values():
            await engine.stop()
        self._engines.clear()

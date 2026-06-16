from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import uuid

from .order import Order, Side, OrderStatus, TradeRecord, OrderType
from .order_book import OrderBook
from .fee import FeeCalculator, AShareFeeCalculator
from .account import Account


@dataclass
class MatchingConfig:
    """撮合配置"""
    price_tick: Decimal = Decimal("0.01")
    lot_size: int = 100
    max_queue_depth: int = 10000
    enable_queue_simulation: bool = True
    price_limit_up: Decimal = Decimal("1.10")   # 涨停比例
    price_limit_down: Decimal = Decimal("0.90") # 跌停比例


class SymbolMatchingEngine:
    """单标的撮合引擎"""

    def __init__(
        self,
        symbol: str,
        config: Optional[MatchingConfig] = None,
        account: Optional[Account] = None,
        fee_calculator: Optional[FeeCalculator] = None,
    ):
        self.symbol = symbol
        self.config = config or MatchingConfig()
        self.order_book = OrderBook(symbol)

        # 账户与费用模型
        self.account = account
        self.fee_calculator = fee_calculator or AShareFeeCalculator()

        # 事件队列（串行处理保证顺序）
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 成交回调
        self._trade_callbacks: List[Callable] = []

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

    def on_trade_generated(self, callback: Callable):
        """注册成交生成回调"""
        self._trade_callbacks.append(callback)
    
    async def start(self):
        """启动撮合循环"""
        if self._running and self._task and not self._task.done():
            return
        self._running = True
        self._event_queue = asyncio.Queue()
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

        try:
            if event_type == "order":
                await self._handle_order(event["order"])
            elif event_type == "cancel":
                await self._handle_cancel(event["order_id"])
            elif event_type == "cancel_feed":
                await self._handle_cancel_feed(event["cancel"])
            elif event_type == "trade":
                await self._handle_trade(event["trade"])
            elif event_type == "quote":
                await self._handle_quote(event["quote"])
        finally:
            done = event.get("_done")
            if done is not None:
                done.set()
    
    async def _handle_order(self, order: Order):
        """处理新委托"""
        self._stats["orders_received"] += 1

        # 参数校验
        if not self._validate_order(order):
            order.status = OrderStatus.REJECTED
            return

        # 模拟行情订单不参与真实账户风控与冻结
        if order.is_mock:
            status, trades = self.order_book.add_order(order)
            if status == OrderStatus.FILLED:
                self._stats["orders_filled"] += 1
            elif status in (OrderStatus.QUEUED, OrderStatus.PARTIAL):
                self._stats["orders_queued"] += 1
            for trade in trades:
                self._stats["trades_generated"] += 1
                await self._update_account_on_trade(trade)
                for cb in self._trade_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(trade)
                        else:
                            cb(trade)
                    except Exception as e:
                        print(f"[{self.symbol}] Error in trade callback: {e}")
            return

        # 账户级风控校验（资金/仓位）
        if not self._validate_account_constraints(order):
            return

        # 添加到订单簿
        status, trades = self.order_book.add_order(order)

        if status == OrderStatus.FILLED:
            self._stats["orders_filled"] += 1
        elif status == OrderStatus.QUEUED:
            self._stats["orders_queued"] += 1
        elif status == OrderStatus.PARTIAL:
            self._stats["orders_queued"] += 1

        # 处理成交：更新账户、触发回调
        for trade in trades:
            self._stats["trades_generated"] += 1
            if trade.match_source == "order_cross":
                self._stats["trades_from_cross"] += 1
            else:
                self._stats["trades_from_feed"] += 1

            await self._update_account_on_trade(trade)

            for cb in self._trade_callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(trade)
                    else:
                        cb(trade)
                except Exception as e:
                    print(f"[{self.symbol}] Error in trade callback: {e}")

        # 处理未成交部分的冻结
        if order.is_active and order.remaining_qty > 0:
            self._freeze_account_for_order(order)

    def _freeze_account_for_order(self, order: Order):
        """为排队中的订单冻结资金或仓位"""
        if self.account is None:
            return

        if order.side == Side.BUY:
            if order.frozen_total is not None:
                return
            estimated_fee = self.fee_calculator.estimate_for_buy(
                order.price, order.remaining_qty
            )
            total = order.price * Decimal(order.remaining_qty) + estimated_fee
            order.frozen_total = self.account.on_buy_queued(
                order.remaining_qty, order.price, estimated_fee
            )
        else:
            if order.frozen_position_qty is not None:
                return
            order.frozen_position_qty = order.remaining_qty
            self.account.on_sell_queued(order.remaining_qty)

    def _unfreeze_account_for_order(self, order: Order):
        """为订单解冻资金或仓位"""
        if self.account is None:
            return

        if order.side == Side.BUY and order.frozen_total is not None:
            self.account.on_buy_unqueued(order.frozen_total)
            order.frozen_total = None
        elif order.side == Side.SELL and order.frozen_position_qty is not None:
            self.account.on_sell_unqueued(order.frozen_position_qty)
            order.frozen_position_qty = None

    async def _update_account_on_trade(self, trade: TradeRecord):
        """根据成交记录更新账户资金和仓位"""
        if self.account is None:
            return

        fee = self.fee_calculator.calculate(trade.side, trade.price, trade.quantity)
        trade.fee = fee

        order = self.order_book.get_order(trade.order_id)

        # 模拟行情订单的成交只记录 fee/net_amount，不更新真实账户
        if order and order.is_mock:
            if trade.side == "buy":
                trade.net_amount = -(trade.price * Decimal(trade.quantity) + fee)
            else:
                trade.net_amount = trade.price * Decimal(trade.quantity) - fee
            return

        if trade.side == "buy":
            trade.net_amount = -(trade.price * Decimal(trade.quantity) + fee)
            if order and order.frozen_total is not None:
                # 按成交比例从冻结资金中释放，并结算实际成本
                ratio = Decimal(trade.quantity) / Decimal(order.quantity)
                release = (order.frozen_total * ratio).quantize(
                    Decimal("0.01"), rounding="ROUND_HALF_UP"
                )
                order.frozen_total -= release
                self.account.frozen_cash -= release
                self.account.cash += release
                self.account.on_buy_fill(trade.quantity, trade.price, fee, release)
            else:
                self.account.on_buy_fill(
                    trade.quantity,
                    trade.price,
                    fee,
                    trade.price * Decimal(trade.quantity) + fee,
                )
        else:
            trade.net_amount = trade.price * Decimal(trade.quantity) - fee
            if order and order.frozen_position_qty is not None:
                ratio = trade.quantity / order.quantity
                release_qty = max(1, int(order.frozen_position_qty * ratio))
                order.frozen_position_qty -= release_qty
                self.account.frozen_position -= release_qty
            self.account.on_sell_fill(trade.quantity, trade.price, fee)

    async def _handle_cancel(self, order_id: str):
        """处理撤单"""
        order = self.order_book.cancel_order(order_id)
        if order:
            self._stats["orders_cancelled"] += 1
            self._unfreeze_account_for_order(order)

    async def _handle_cancel_feed(self, cancel_data: dict):
        """处理行情撤单事件（驱动队列消耗）"""
        price = Decimal(str(cancel_data["price"]))
        cancel_qty = int(cancel_data["quantity"])
        side = cancel_data.get("side", "unknown")

        if side not in ("buy", "sell"):
            return

        actual = self.order_book.consume_queue_on_cancel(price, cancel_qty, side)
        if actual > 0:
            self._stats["trades_generated"] += 1
    
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

    def _validate_account_constraints(self, order: Order) -> bool:
        """校验账户资金/仓位约束"""
        if self.account is None:
            return True

        if order.side == Side.BUY:
            # 市价买入使用 best_ask 估算，限价买入使用委托价
            estimated_price = self._estimate_price_for_account(order)
            estimated_fee = self.fee_calculator.estimate_for_buy(
                estimated_price, order.remaining_qty
            )
            total_cost = estimated_price * Decimal(order.remaining_qty) + estimated_fee
            if not self.account.can_buy(total_cost):
                order.status = OrderStatus.REJECTED
                order.reject_reason = (
                    f"资金不足: 需要 {float(total_cost):.2f}，"
                    f"现金 {float(self.account.cash):.2f}"
                )
                order.update_time = datetime.now()
                return False
        else:
            # 卖出：需要可用底仓 >= 卖出数量
            if not self.account.can_sell(order.remaining_qty):
                order.status = OrderStatus.REJECTED
                order.reject_reason = (
                    f"可用仓位不足: 需要 {order.remaining_qty}，"
                    f"可用 {self.account.available_position}"
                )
                order.update_time = datetime.now()
                return False

        return True

    def _estimate_price_for_account(self, order: Order) -> Decimal:
        """为账户校验估算成交价格"""
        if order.order_type == OrderType.MARKET:
            if order.side == Side.BUY:
                return self.order_book.best_ask or Decimal("999999.99")
            else:
                return self.order_book.best_bid or Decimal("0.01")
        return order.price
    
    # ─────────── 公共接口 ───────────
    
    async def place_order(self, order: Order) -> Order:
        """提交委托"""
        if not self._running or (self._task and self._task.done()):
            await self.start()
        await self._event_queue.put({"type": "order", "order": order})
        # 等待处理完成（简单轮询）
        for _ in range(100):
            if order.status != OrderStatus.PENDING:
                break
            await asyncio.sleep(0.001)
        return order
    
    async def cancel_order(self, order_id: str) -> Optional[Order]:
        """撤销委托"""
        if not self._running or (self._task and self._task.done()):
            await self.start()
        await self._event_queue.put({"type": "cancel", "order_id": order_id})
        # 等待处理完成（轮询检查状态）
        for _ in range(100):
            order = self.order_book.get_order(order_id)
            if order and order.status == OrderStatus.CANCELLED:
                break
            await asyncio.sleep(0.001)
        return self.order_book.get_order(order_id)
    
    async def process_trade(self, trade_data: dict):
        """处理逐笔成交"""
        if not self._running or (self._task and self._task.done()):
            await self.start()
        await self._event_queue.put({"type": "trade", "trade": trade_data})

    async def process_cancel_feed(self, cancel_data: dict):
        """处理行情撤单事件"""
        if not self._running or (self._task and self._task.done()):
            await self.start()
        done = asyncio.Event()
        await self._event_queue.put({"type": "cancel_feed", "cancel": cancel_data, "_done": done})
        await done.wait()

    async def process_quote(self, quote_data: dict):
        """处理盘口快照"""
        if not self._running or (self._task and self._task.done()):
            await self.start()
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

    def __init__(
        self,
        account: Optional[Account] = None,
        fee_calculator: Optional[FeeCalculator] = None,
    ):
        self._engines: Dict[str, SymbolMatchingEngine] = {}
        self._lock = asyncio.Lock()
        self._trade_callbacks: List[Callable] = []
        self._account = account or Account()
        self._fee_calculator = fee_calculator or AShareFeeCalculator()

    def on_trade_generated(self, callback: Callable):
        """注册全局成交生成回调"""
        self._trade_callbacks.append(callback)
        # 同时注册到已存在的引擎
        for engine in self._engines.values():
            engine.on_trade_generated(callback)

    async def get_or_create_engine(self, symbol: str) -> SymbolMatchingEngine:
        """获取或创建标的引擎"""
        async with self._lock:
            if symbol not in self._engines:
                engine = SymbolMatchingEngine(
                    symbol,
                    account=self._account,
                    fee_calculator=self._fee_calculator,
                )
                # 注册全局成交回调
                for cb in self._trade_callbacks:
                    engine.on_trade_generated(cb)
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

    async def process_cancel_feed(self, symbol: str, cancel_data: dict):
        """分发行情撤单事件到对应引擎"""
        engine = await self.get_or_create_engine(symbol)
        await engine.process_cancel_feed(cancel_data)
    
    def get_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """查询订单"""
        engine = self._engines.get(symbol)
        return engine.get_order(order_id) if engine else None
    
    def get_all_engines(self) -> Dict[str, SymbolMatchingEngine]:
        """获取所有引擎"""
        return self._engines.copy()

    def get_account(self) -> Optional[Account]:
        """获取关联账户"""
        return self._account
    
    async def shutdown_all(self):
        """关闭所有引擎"""
        for engine in self._engines.values():
            await engine.stop()
        self._engines.clear()

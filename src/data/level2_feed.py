import asyncio
import random
from decimal import Decimal
from datetime import datetime
from typing import Callable, Optional, Dict, List, Tuple
import uuid

from .market_data import TradeEvent, QuoteEvent


class Level2FeedHandler:
    """Level-2 行情处理器基类"""

    def __init__(self):
        self._trade_callbacks: List[Callable] = []
        self._quote_callbacks: List[Callable] = []
        self._order_callbacks: List[Callable] = []
        self._cancel_callbacks: List[Callable] = []
        self._running = False

    def on_trade(self, callback: Callable):
        """注册逐笔成交回调"""
        self._trade_callbacks.append(callback)

    def on_quote(self, callback: Callable):
        """注册盘口快照回调"""
        self._quote_callbacks.append(callback)

    def on_order(self, callback: Callable):
        """注册模拟委托回调"""
        self._order_callbacks.append(callback)

    def on_cancel(self, callback: Callable):
        """注册行情撤单回调"""
        self._cancel_callbacks.append(callback)

    async def _emit_trade(self, trade: TradeEvent):
        """发送逐笔成交事件"""
        for cb in self._trade_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(trade)
                else:
                    cb(trade)
            except Exception as e:
                print(f"Error in trade callback: {e}")

    async def _emit_quote(self, quote: QuoteEvent):
        """发送盘口快照事件"""
        for cb in self._quote_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(quote)
                else:
                    cb(quote)
            except Exception as e:
                print(f"Error in quote callback: {e}")

    async def _emit_order(self, order: dict):
        """发送模拟委托事件"""
        for cb in self._order_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(order)
                else:
                    cb(order)
            except Exception as e:
                print(f"Error in order callback: {e}")

    async def _emit_cancel(self, cancel: dict):
        """发送行情撤单事件"""
        for cb in self._cancel_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(cancel)
                else:
                    cb(cancel)
            except Exception as e:
                print(f"Error in cancel callback: {e}")

    async def start(self):
        """启动行情接收"""
        raise NotImplementedError
    
    async def stop(self):
        """停止行情接收"""
        self._running = False


class MockLevel2Feed(Level2FeedHandler):
    """模拟 Level-2 行情生成器（用于测试和演示）

    改进点：
    - 不再直接生成成交/盘口事件，而是生成模拟委托；
    - 模拟委托进入撮合引擎订单簿后，与用户委托按价格优先、时间优先规则自动撮合；
    - 委托价格随随机游走变化，主动买入推高价格、主动卖出推低价格。
    """

    def __init__(self, symbol: str = "000001.SZ", base_price: float = 10.50,
                 order_interval: float = 0.2, quote_interval: float = 1.0,
                 book_provider: Optional[Callable[[], Optional[dict]]] = None):
        super().__init__()
        self.symbol = symbol
        self.base_price = Decimal(str(base_price))
        self.order_interval = order_interval
        self.quote_interval = quote_interval
        self.book_provider = book_provider

        self._current_price = self.base_price
        self._order_seq = 0
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._generate_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _generate_loop(self):
        """生成模拟行情"""
        order_task = asyncio.create_task(self._generate_orders())
        quote_task = asyncio.create_task(self._generate_quotes())

        try:
            await asyncio.gather(order_task, quote_task)
        except asyncio.CancelledError:
            pass

    async def _generate_orders(self):
        """生成模拟委托并推送到订单簿

        生成策略：
        - 先注入初始双边流动性；
        - 随后大部分订单为 passive（挂买低于现价/挂卖高于现价），维持盘口深度；
        - 小比例为 aggressive（买价高于现价/卖价低于现价），主动吃掉对方队列并推动价格。
        """
        await self._seed_initial_book()

        while self._running:
            await asyncio.sleep(self.order_interval)

            snapshot = self._get_book_snapshot()
            side, price, quantity = self._decide_next_order(snapshot)

            self._order_seq += 1
            order = {
                "symbol": self.symbol,
                "side": side,
                "price": str(price),
                "quantity": quantity,
                "order_id": f"mock-{self._order_seq}",
                "timestamp": datetime.now().isoformat(),
            }

            await self._emit_order(order)

            # 小概率生成撤单事件，消耗队列并推动位置前移
            if random.random() < 0.10:
                await self._generate_cancel(snapshot)

    def _get_book_snapshot(self) -> Optional[dict]:
        """获取当前订单簿快照，若未提供 provider 则返回 None"""
        if self.book_provider is None:
            return None
        try:
            return self.book_provider()
        except Exception:
            return None

    def _decide_next_order(self, snapshot: Optional[dict]) -> Tuple[str, Decimal, int]:
        """根据当前盘口决定下一笔模拟委托的方向、价格和数量"""
        # 价格随机游走，限制在 ±10% 以内
        price_change = Decimal(str(random.uniform(-0.05, 0.05)))
        self._current_price = max(
            self.base_price * Decimal("0.9"),
            min(self.base_price * Decimal("1.1"),
                self._current_price + price_change)
        )
        self._current_price = Decimal(str(round(float(self._current_price), 2)))

        spread = Decimal("0.02")
        quantity = random.randint(1, 10) * 100

        bids = snapshot.get("bids", []) if snapshot else []
        asks = snapshot.get("asks", []) if snapshot else []
        bid_qty = sum(b["total_quantity"] for b in bids)
        ask_qty = sum(a["total_quantity"] for a in asks)

        # 优先补充缺失的一侧流动性
        if not asks or ask_qty < bid_qty * 0.5:
            side = "sell"
            price = self._current_price + spread
        elif not bids or bid_qty < ask_qty * 0.5:
            side = "buy"
            price = self._current_price - spread
        else:
            # 两侧流动性充足时，随机选择方向；小概率生成 aggressive 订单
            side = random.choice(["buy", "sell"])
            aggressive = random.random() < 0.20
            if side == "buy":
                price = self._current_price + spread if aggressive else self._current_price - spread
            else:
                price = self._current_price - spread if aggressive else self._current_price + spread

        return side, price, quantity

    async def _generate_cancel(self, snapshot: Optional[dict]):
        """生成行情撤单事件，消耗订单簿队列并推动 current_queue_position 前移"""
        if not snapshot:
            return

        # 选择有深度的一侧进行撤单
        side = random.choice(["buy", "sell"])
        levels = snapshot.get("bids" if side == "buy" else "asks", [])
        if not levels:
            side = "sell" if side == "buy" else "buy"
            levels = snapshot.get("bids" if side == "buy" else "asks", [])
        if not levels:
            return

        level = levels[0]
        cancel_qty = random.randint(1, 5) * 100
        cancel_qty = min(cancel_qty, level["total_quantity"])
        if cancel_qty <= 0:
            return

        cancel = {
            "symbol": self.symbol,
            "side": side,
            "price": str(Decimal(str(level["price"]))),
            "quantity": cancel_qty,
            "timestamp": datetime.now().isoformat(),
        }
        await self._emit_cancel(cancel)

    async def _seed_initial_book(self):
        """注入初始双边流动性，确保启动后盘口两侧均有挂单"""
        spread = Decimal("0.02")
        for i in range(1, 6):
            self._order_seq += 1
            await self._emit_order({
                "symbol": self.symbol,
                "side": "buy",
                "price": str(self._current_price - spread * i),
                "quantity": random.randint(3, 12) * 100,
                "order_id": f"mock-{self._order_seq}",
                "timestamp": datetime.now().isoformat(),
            })
            self._order_seq += 1
            await self._emit_order({
                "symbol": self.symbol,
                "side": "sell",
                "price": str(self._current_price + spread * i),
                "quantity": random.randint(3, 12) * 100,
                "order_id": f"mock-{self._order_seq}",
                "timestamp": datetime.now().isoformat(),
            })

    async def _generate_quotes(self):
        """由调用方根据自身订单簿生成盘口快照，此处仅保留占位循环"""
        while self._running:
            await asyncio.sleep(self.quote_interval)
            # Quote 消息现在由 server.py 根据引擎订单簿快照推送
            pass


class FileReplayFeed(Level2FeedHandler):
    """从文件回放 Level-2 行情（用于历史回测）"""
    
    def __init__(self, trade_file: Optional[str] = None, quote_file: Optional[str] = None,
                 speed: float = 1.0):
        """
        Args:
            trade_file: 逐笔成交数据文件路径（CSV/JSONL）
            quote_file: 盘口快照数据文件路径（CSV/JSONL）
            speed: 回放速度倍率（1.0 = 真实速度）
        """
        super().__init__()
        self.trade_file = trade_file
        self.quote_file = quote_file
        self.speed = speed
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._replay_loop())
    
    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _replay_loop(self):
        """回放循环"""
        # TODO: 实现文件读取和回放逻辑
        pass


class WebSocketFeed(Level2FeedHandler):
    """WebSocket 实时行情接入（预留接口）"""
    
    def __init__(self, ws_url: str, symbol: str):
        super().__init__()
        self.ws_url = ws_url
        self.symbol = symbol
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._connect_and_listen())
    
    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _connect_and_listen(self):
        """连接 WebSocket 并监听行情"""
        # TODO: 实现 WebSocket 连接和消息解析
        # 可以使用 websockets 库
        pass

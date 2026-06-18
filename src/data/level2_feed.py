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
    """模拟 Level-2 行情生成器（基于多类参与者）

    核心改进：
    - 使用 ParticipantRegistry 管理多类市场参与者
    - 每类参与者有独立策略：做市商、趋势跟踪、均值回归、噪声交易、激进交易
    - 参与者生成的模拟委托进入撮合引擎订单簿，与用户委托自动撮合
    - 支持目标价格设置，参与者围绕目标价格交易
    - 支持动态参数调整（数量、频率、目标价格等）
    """

    def __init__(self, symbol: str = "000001.SZ", base_price: float = 10.50,
                 order_interval: float = 0.2, quote_interval: float = 1.0,
                 book_provider: Optional[Callable[[], Optional[dict]]] = None,
                 participant_config: Optional[dict] = None):
        super().__init__()
        self.symbol = symbol
        self.base_price = Decimal(str(base_price))
        self.order_interval = order_interval
        self.quote_interval = quote_interval
        self.book_provider = book_provider

        self._current_price = self.base_price
        self._task: Optional[asyncio.Task] = None
        self._regime: str = "normal"

        # 参与者注册表
        from ..data.participants import ParticipantRegistry
        self.registry = ParticipantRegistry(symbol=symbol, base_price=base_price)
        if participant_config:
            self.registry.update_config(participant_config)

    @property
    def market_regime(self) -> str:
        return self._regime

    def set_market_regime(self, regime: str):
        """设置市场微观结构模式"""
        if regime not in ("normal", "flash_crash", "pump"):
            raise ValueError(f"unknown regime: {regime}")
        self._regime = regime
        # 同步到共享市场状态
        from ..data.participants import get_shared_market_state
        get_shared_market_state(self.symbol).regime = regime

    @property
    def participant_stats(self) -> List[dict]:
        return self.registry.get_all_stats()

    def update_participant_config(self, config: dict):
        """动态更新参与者配置"""
        self.registry.update_config(config)
        # 同步更新 feed 自身的订单生成间隔
        if "order_interval" in config:
            self.order_interval = config["order_interval"]

    def get_participant_config(self) -> dict:
        """获取当前参与者配置"""
        return self.registry.get_config()

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
        """由参与者生成模拟委托并推送到订单簿

        每次循环都从 registry 重新获取参与者列表，确保配置动态更新（包括
        target_price、order_interval、参与者数量重建等）能够立即生效。
        """
        # 先由做市商注入初始流动性
        for p in self.registry.get_participants():
            if hasattr(p, '_seeded'):
                p._seeded = False
        # 让前几个 tick 快速初始化
        for _ in range(5):
            for p in self.registry.get_participants():
                if not p.active:
                    continue
                order = p.generate_order(None)
                if order:
                    await self._emit_order(order)
                    p.on_order_queued(order)
            await asyncio.sleep(0.05)

        while self._running:
            await asyncio.sleep(self.order_interval)

            snapshot = self._get_book_snapshot()

            # 更新共享市场状态的订单簿不平衡指标
            if snapshot:
                from ..data.participants import get_shared_market_state
                get_shared_market_state(self.symbol).on_book_update(snapshot)

            # 根据市场微观结构模式注入额外冲击订单
            shock_order = self._generate_shock_order(snapshot)
            if shock_order:
                await self._emit_order(shock_order)

            # 每次循环重新获取参与者，以便 registry 重建或参数更新后生效
            for p in self.registry.get_participants():
                if not p.active:
                    continue

                # 生成委托
                order = p.generate_order(snapshot)
                if order:
                    await self._emit_order(order)
                    p.on_order_queued(order)

                # 生成撤单
                cancel = p.generate_cancel(snapshot)
                if cancel:
                    await self._emit_cancel(cancel)

            # 更新当前价格
            if snapshot:
                bids = snapshot.get("bids", [])
                asks = snapshot.get("asks", [])
                if bids and asks:
                    self._current_price = (Decimal(str(bids[0]["price"])) + Decimal(str(asks[0]["price"]))) / 2
                elif bids or asks:
                    self._current_price = Decimal(str((bids or asks)[0]["price"]))

    def _generate_shock_order(self, snapshot: Optional[dict]) -> Optional[dict]:
        """根据 regime 生成冲击订单"""
        if self._regime == "normal" or not snapshot:
            return None

        # 闪崩：大额市价卖单；拉涨：大额市价买单
        side = "sell" if self._regime == "flash_crash" else "buy"

        # 30% 概率每个 tick 产生冲击
        if random.random() > 0.30:
            return None

        price = Decimal("0.01") if side == "sell" else Decimal("999999.99")
        quantity = random.randint(50, 200) * 100  # 大额
        return {
            "symbol": self.symbol,
            "side": side,
            "price": str(price),
            "quantity": quantity,
            "order_id": f"shock-{side}-{uuid.uuid4().hex[:8]}",
            "participant_id": "SHOCK",
            "order_type": "market",  # 冲击单作为市价单：未成交部分立即撤销，不污染订单簿
            "timestamp": datetime.now().isoformat(),
        }

    def _get_book_snapshot(self) -> Optional[dict]:
        """获取当前订单簿快照"""
        if self.book_provider is None:
            return None
        try:
            return self.book_provider()
        except Exception:
            return None

    async def _generate_quotes(self):
        """由调用方根据引擎订单簿生成盘口快照推送"""
        while self._running:
            await asyncio.sleep(self.quote_interval)
            # Quote 消息由 server.py 根据引擎订单簿快照推送
            pass

    async def _generate_cancel(self, book_snapshot=None):
        """兼容测试：直接生成一个行情撤单"""
        side = random.choice(["buy", "sell"])
        price = self._current_price or self.base_price
        quantity = random.randint(100, 1000)
        if book_snapshot:
            entries = book_snapshot.get("bids" if side == "buy" else "asks", [])
            if entries:
                price = Decimal(str(entries[0]["price"]))
                quantity = random.randint(1, int(entries[0].get("total_quantity", 1000)))
        cancel = {
            "symbol": self.symbol,
            "side": side,
            "price": str(price),
            "quantity": quantity,
            "order_id": f"mock-cancel-{uuid.uuid4().hex[:8]}",
        }
        await self._emit_cancel(cancel)



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

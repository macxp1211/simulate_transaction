import asyncio
import random
from decimal import Decimal
from datetime import datetime
from typing import Callable, Optional, Dict, List
import uuid

from .market_data import TradeEvent, QuoteEvent


class Level2FeedHandler:
    """Level-2 行情处理器基类"""
    
    def __init__(self):
        self._trade_callbacks: List[Callable] = []
        self._quote_callbacks: List[Callable] = []
        self._running = False
    
    def on_trade(self, callback: Callable):
        """注册逐笔成交回调"""
        self._trade_callbacks.append(callback)
    
    def on_quote(self, callback: Callable):
        """注册盘口快照回调"""
        self._quote_callbacks.append(callback)
    
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
    
    async def start(self):
        """启动行情接收"""
        raise NotImplementedError
    
    async def stop(self):
        """停止行情接收"""
        self._running = False


class MockLevel2Feed(Level2FeedHandler):
    """模拟 Level-2 行情生成器（用于测试和演示）"""
    
    def __init__(self, symbol: str = "000001.SZ", base_price: float = 10.50,
                 trade_interval: float = 0.1, quote_interval: float = 1.0):
        super().__init__()
        self.symbol = symbol
        self.base_price = Decimal(str(base_price))
        self.trade_interval = trade_interval
        self.quote_interval = quote_interval
        
        self._current_price = self.base_price
        self._trade_seq = 0
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
        trade_task = asyncio.create_task(self._generate_trades())
        quote_task = asyncio.create_task(self._generate_quotes())
        
        try:
            await asyncio.gather(trade_task, quote_task)
        except asyncio.CancelledError:
            pass
    
    async def _generate_trades(self):
        """生成模拟逐笔成交"""
        while self._running:
            await asyncio.sleep(self.trade_interval)
            
            # 随机价格变动
            price_change = Decimal(str(random.uniform(-0.05, 0.05)))
            self._current_price = max(self.base_price * Decimal("0.9"), 
                                      min(self.base_price * Decimal("1.1"),
                                          self._current_price + price_change))
            self._current_price = Decimal(str(round(float(self._current_price), 2)))
            
            # 随机数量
            quantity = random.randint(100, 1000)
            # 随机方向
            direction = random.choice(["buy", "sell"])
            
            self._trade_seq += 1
            trade = TradeEvent(
                symbol=self.symbol,
                price=self._current_price,
                quantity=quantity,
                direction=direction,
                trade_id=f"mock-{self._trade_seq}",
                timestamp=datetime.now(),
            )
            
            await self._emit_trade(trade)
    
    async def _generate_quotes(self):
        """生成模拟盘口快照"""
        while self._running:
            await asyncio.sleep(self.quote_interval)
            
            price = self._current_price
            
            # 生成买卖盘
            bids = []
            asks = []
            for i in range(5):
                bid_price = price - Decimal(str(i * 0.01))
                ask_price = price + Decimal(str(i * 0.01))
                bid_qty = random.randint(1000, 10000)
                ask_qty = random.randint(1000, 10000)
                bids.append({
                    "price": bid_price,
                    "quantity": bid_qty,
                    "order_count": random.randint(5, 20),
                })
                asks.append({
                    "price": ask_price,
                    "quantity": ask_qty,
                    "order_count": random.randint(5, 20),
                })
            
            quote = QuoteEvent(
                symbol=self.symbol,
                timestamp=datetime.now(),
                bids=bids,
                asks=asks,
            )
            
            await self._emit_quote(quote)


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

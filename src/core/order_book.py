from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from bisect import bisect_left, insort
from datetime import datetime
import uuid

try:
    from sortedcontainers import SortedDict
    HAS_SORTEDCONTAINERS = True
except ImportError:
    HAS_SORTEDCONTAINERS = False

from .order import Order, Side, OrderStatus, TradeRecord, OrderType


class SimpleSortedDict:
    """使用 bisect 维护有序键列表的简单有序字典（备用实现）"""
    
    def __init__(self, reverse=False):
        self._keys: List[Decimal] = []
        self._data: Dict[Decimal, 'PriceLevel'] = {}
        self._reverse = reverse
    
    def __setitem__(self, key: Decimal, value: 'PriceLevel'):
        if key not in self._data:
            if self._reverse:
                # 降序：从大到小
                idx = bisect_left(self._keys, key)
                self._keys.insert(idx, key)
            else:
                # 升序：从小到大
                idx = bisect_left(self._keys, key)
                self._keys.insert(idx, key)
        self._data[key] = value
    
    def __getitem__(self, key: Decimal) -> 'PriceLevel':
        return self._data[key]
    
    def __contains__(self, key: Decimal) -> bool:
        return key in self._data
    
    def __delitem__(self, key: Decimal):
        if key in self._data:
            self._keys.remove(key)
            del self._data[key]
    
    def __len__(self) -> int:
        return len(self._keys)
    
    def get(self, key: Decimal, default=None) -> Optional['PriceLevel']:
        return self._data.get(key, default)
    
    def keys(self):
        """返回按键排序的列表"""
        if self._reverse:
            return list(reversed(self._keys))
        return list(self._keys)
    
    def items(self):
        """返回按键排序的 (key, value) 列表"""
        if self._reverse:
            return [(key, self._data[key]) for key in reversed(self._keys)]
        return [(key, self._data[key]) for key in self._keys]
    
    def peekitem(self, index: int) -> Tuple[Decimal, 'PriceLevel']:
        """查看指定索引的键值对"""
        key = self._keys[index] if not self._reverse else self._keys[index]
        if self._reverse:
            key = list(reversed(self._keys))[index]
        else:
            key = self._keys[index]
        return key, self._data[key]
    
    def is_empty(self) -> bool:
        return len(self._keys) == 0


class PriceLevel:
    """价格层级 - 同一价格的所有订单"""
    
    def __init__(self, price: Decimal):
        self.price = price
        self.orders: List[Order] = []  # FIFO 队列
        self.total_quantity = 0
    
    def add(self, order: Order):
        """添加订单到队尾"""
        self.orders.append(order)
        self.total_quantity += order.remaining_qty
    
    def remove(self, order: Order) -> bool:
        """从队列中移除订单"""
        try:
            self.orders.remove(order)
            self.total_quantity -= order.remaining_qty
            return True
        except ValueError:
            return False
    
    def peek(self) -> Optional[Order]:
        """查看队首订单"""
        return self.orders[0] if self.orders else None
    
    def pop(self) -> Optional[Order]:
        """取出队首订单"""
        if not self.orders:
            return None
        order = self.orders.pop(0)
        self.total_quantity -= order.remaining_qty
        return order
    
    def is_empty(self) -> bool:
        return len(self.orders) == 0
    
    def __len__(self) -> int:
        return len(self.orders)
    
    def __repr__(self):
        return f"PriceLevel({self.price}, orders={len(self.orders)}, qty={self.total_quantity})"


class OrderBook:
    """订单簿 - 维护买卖队列"""
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        
        if HAS_SORTEDCONTAINERS:
            # 使用 sortedcontainers.SortedDict（高性能）
            # bids 降序：使用负值作为 key，或者直接利用 reversed 参数
            self.bids = SortedDict(lambda x: -x)  # 按负值排序，实现降序
            self.asks = SortedDict()  # 升序
        else:
            # 回退到 SimpleSortedDict
            self.bids = SimpleSortedDict(reverse=True)
            self.asks = SimpleSortedDict(reverse=False)
        
        # 订单索引: order_id -> (side, price, order) 用于 O(1) 定位
        self._order_index: Dict[str, Tuple[Side, Decimal, Order]] = {}
        
        # 所有订单（包括历史）
        self._all_orders: Dict[str, Order] = {}
        
        # 成交记录
        self._trades: List[TradeRecord] = []
    
    # ─────────── 基本查询 ───────────
    
    def _is_bids_empty(self) -> bool:
        if HAS_SORTEDCONTAINERS:
            return len(self.bids) == 0
        return self.bids.is_empty()
    
    def _is_asks_empty(self) -> bool:
        if HAS_SORTEDCONTAINERS:
            return len(self.asks) == 0
        return self.asks.is_empty()
    
    def _get_bid_keys(self):
        if HAS_SORTEDCONTAINERS:
            return list(self.bids.keys())
        return self.bids.keys()
    
    def _get_ask_keys(self):
        if HAS_SORTEDCONTAINERS:
            return list(self.asks.keys())
        return self.asks.keys()
    
    @property
    def best_bid(self) -> Optional[Decimal]:
        """最优买价（最高买价）"""
        if self._is_bids_empty():
            return None
        keys = self._get_bid_keys()
        return keys[0] if keys else None
    
    @property
    def best_ask(self) -> Optional[Decimal]:
        """最优卖价（最低卖价）"""
        if self._is_asks_empty():
            return None
        keys = self._get_ask_keys()
        return keys[0] if keys else None
    
    @property
    def spread(self) -> Optional[Decimal]:
        """买卖价差"""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None
    
    def get_queue_length(self, side: Side, price: Decimal) -> int:
        """获取指定价格层级的队列长度"""
        book = self.bids if side == Side.BUY else self.asks
        level = book.get(price)
        return len(level) if level else 0
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """通过订单ID查询订单"""
        return self._all_orders.get(order_id)
    
    # ─────────── 订单操作 ───────────
    
    def add_order(self, order: Order) -> Tuple[OrderStatus, List[TradeRecord]]:
        """
        添加订单到订单簿
        
        返回: (最终状态, 成交记录列表)
        """
        if order.symbol != self.symbol:
            raise ValueError(f"Symbol mismatch: {order.symbol} != {self.symbol}")
        
        order.status = OrderStatus.ACTIVE
        self._all_orders[order.order_id] = order
        
        trades: List[TradeRecord] = []
        
        # 市价单直接按最优价格处理
        if order.order_type == OrderType.MARKET:
            if order.side == Side.BUY:
                order.price = self.best_ask or Decimal("999999.99")
            else:
                order.price = self.best_bid or Decimal("0.01")
        
        # 检查是否价格交叉（立即撮合）
        if order.side == Side.BUY:
            trades = self._match_buy(order)
        else:
            trades = self._match_sell(order)
        
        # 如果有剩余未成交，进入队列
        if order.remaining_qty > 0 and order.is_active:
            self._enter_queue(order)
        
        return order.status, trades
    
    def _match_buy(self, order: Order) -> List[TradeRecord]:
        """买入订单撮合"""
        trades: List[TradeRecord] = []
        
        while order.remaining_qty > 0:
            best_ask = self.best_ask
            if best_ask is None or order.price < best_ask:
                break  # 无法继续撮合
            
            level = self.asks[best_ask]
            target_order = level.peek()
            
            if target_order is None or not target_order.is_active:
                level.pop()  # 移除无效订单
                if level.is_empty():
                    del self.asks[best_ask]
                continue
            
            fill_qty = min(order.remaining_qty, target_order.remaining_qty)
            trade_price = best_ask
            
            # 创建成交记录
            now = datetime.now()
            trade = TradeRecord(
                trade_id=f"trd-{uuid.uuid4().hex[:12]}",
                order_id=order.order_id,
                symbol=self.symbol,
                side="buy",
                price=trade_price,
                quantity=fill_qty,
                trade_time=now,
                match_source="order_cross",
            )
            order.trades.append(trade)
            self._trades.append(trade)
            trades.append(trade)
            
            # 更新双方订单
            order.fill(fill_qty, now)
            target_order.fill(fill_qty, now)
            
            # 更新价格层级
            level.total_quantity -= fill_qty
            if target_order.is_filled:
                level.pop()
                if level.is_empty():
                    del self.asks[best_ask]
        
        return trades
    
    def _match_sell(self, order: Order) -> List[TradeRecord]:
        """卖出订单撮合"""
        trades: List[TradeRecord] = []
        
        while order.remaining_qty > 0:
            best_bid = self.best_bid
            if best_bid is None or order.price > best_bid:
                break  # 无法继续撮合
            
            level = self.bids[best_bid]
            target_order = level.peek()
            
            if target_order is None or not target_order.is_active:
                level.pop()
                if level.is_empty():
                    del self.bids[best_bid]
                continue
            
            fill_qty = min(order.remaining_qty, target_order.remaining_qty)
            trade_price = best_bid
            
            now = datetime.now()
            trade = TradeRecord(
                trade_id=f"trd-{uuid.uuid4().hex[:12]}",
                order_id=order.order_id,
                symbol=self.symbol,
                side="sell",
                price=trade_price,
                quantity=fill_qty,
                trade_time=now,
                match_source="order_cross",
            )
            order.trades.append(trade)
            self._trades.append(trade)
            trades.append(trade)
            
            order.fill(fill_qty, now)
            target_order.fill(fill_qty, now)
            
            level.total_quantity -= fill_qty
            if target_order.is_filled:
                level.pop()
                if level.is_empty():
                    del self.bids[best_bid]
        
        return trades
    
    def _enter_queue(self, order: Order):
        """将订单进入队列"""
        book = self.bids if order.side == Side.BUY else self.asks
        price = order.price
        
        # 获取或创建价格层级
        if price not in book:
            book[price] = PriceLevel(price)
        
        level = book[price]
        
        # 计算队列位置（进入时的长度 + 1）
        queue_length = len(level) + 1
        queue_position = queue_length
        
        level.add(order)
        order.enter_queue(queue_length, queue_position)
        
        # 更新索引
        self._order_index[order.order_id] = (order.side, price, order)
    
    def cancel_order(self, order_id: str) -> Optional[Order]:
        """撤销订单"""
        order = self._all_orders.get(order_id)
        if order is None:
            return None
        
        if not order.is_in_queue:
            return None  # 不在队列中，无法撤销
        
        # 从队列中移除
        info = self._order_index.get(order_id)
        if info:
            side, price, _ = info
            book = self.bids if side == Side.BUY else self.asks
            level = book.get(price)
            if level:
                level.remove(order)
                if level.is_empty():
                    del book[price]
            del self._order_index[order_id]
        
        order.cancel()
        return order
    
    # ─────────── 逐笔成交驱动的队列消耗 ───────────
    
    def consume_queue_on_trade(self, trade_price: Decimal, trade_qty: int, 
                               trade_direction: str, trigger_trade_id: str) -> List[TradeRecord]:
        """
        当逐笔成交发生时，消耗队列中的订单
        
        Args:
            trade_price: 逐笔成交价格
            trade_qty: 逐笔成交数量
            trade_direction: "buy" (买方主动/外盘) 或 "sell" (卖方主动/内盘)
            trigger_trade_id: 触发的逐笔成交ID
        
        Returns:
            产生的成交记录列表
        """
        trades: List[TradeRecord] = []
        remaining = trade_qty
        
        if trade_direction == "buy":
            # 买方主动成交 → 消耗卖方队列（价格 <= trade_price 的卖单）
            trades.extend(self._consume_asks(trade_price, remaining, trigger_trade_id))
        elif trade_direction == "sell":
            # 卖方主动成交 → 消耗买方队列（价格 >= trade_price 的买单）
            trades.extend(self._consume_bids(trade_price, remaining, trigger_trade_id))
        
        return trades
    
    def _consume_asks(self, max_price: Decimal, total_qty: int, trigger_trade_id: str) -> List[TradeRecord]:
        """消耗卖方队列，价格 <= max_price"""
        trades: List[TradeRecord] = []
        remaining = total_qty
        
        # 从最低价开始遍历
        prices_to_remove = []
        for price, level in self.asks.items():
            if price > max_price or remaining <= 0:
                break
            
            orders_to_remove = []
            for order in level.orders:
                if remaining <= 0:
                    break
                if not order.is_active:
                    orders_to_remove.append(order)
                    continue
                
                fill_qty = min(order.remaining_qty, remaining)
                now = datetime.now()
                
                trade = TradeRecord(
                    trade_id=f"trd-{uuid.uuid4().hex[:12]}",
                    order_id=order.order_id,
                    symbol=self.symbol,
                    side="sell",
                    price=price,
                    quantity=fill_qty,
                    trade_time=now,
                    match_source="trade_event",
                    trigger_trade_id=trigger_trade_id,
                )
                order.trades.append(trade)
                self._trades.append(trade)
                trades.append(trade)
                
                order.fill(fill_qty, now)
                remaining -= fill_qty
                level.total_quantity -= fill_qty
                
                if order.is_filled:
                    orders_to_remove.append(order)
                    if order.order_id in self._order_index:
                        del self._order_index[order.order_id]
            
            for order in orders_to_remove:
                level.remove(order)
            
            if level.is_empty():
                prices_to_remove.append(price)
        
        for price in prices_to_remove:
            del self.asks[price]
        
        return trades
    
    def _consume_bids(self, min_price: Decimal, total_qty: int, trigger_trade_id: str) -> List[TradeRecord]:
        """消耗买方队列，价格 >= min_price"""
        trades: List[TradeRecord] = []
        remaining = total_qty
        
        # bids.keys() 已按降序返回（最高价在前），直接遍历
        prices_to_remove = []
        for price in self.bids.keys():
            if price < min_price or remaining <= 0:
                break
            
            level = self.bids[price]
            orders_to_remove = []
            for order in level.orders:
                if remaining <= 0:
                    break
                if not order.is_active:
                    orders_to_remove.append(order)
                    continue
                
                fill_qty = min(order.remaining_qty, remaining)
                now = datetime.now()
                
                trade = TradeRecord(
                    trade_id=f"trd-{uuid.uuid4().hex[:12]}",
                    order_id=order.order_id,
                    symbol=self.symbol,
                    side="buy",
                    price=price,
                    quantity=fill_qty,
                    trade_time=now,
                    match_source="trade_event",
                    trigger_trade_id=trigger_trade_id,
                )
                order.trades.append(trade)
                self._trades.append(trade)
                trades.append(trade)
                
                order.fill(fill_qty, now)
                remaining -= fill_qty
                level.total_quantity -= fill_qty
                
                if order.is_filled:
                    orders_to_remove.append(order)
                    if order.order_id in self._order_index:
                        del self._order_index[order.order_id]
            
            for order in orders_to_remove:
                level.remove(order)
            
            if level.is_empty():
                prices_to_remove.append(price)
        
        for price in prices_to_remove:
            del self.bids[price]
        
        return trades
    
    # ─────────── 快照 ───────────
    
    def get_snapshot(self, depth: int = 10) -> dict:
        """获取订单簿快照"""
        bids_snapshot = []
        for price in self._get_bid_keys():
            level = self.bids[price]
            bids_snapshot.append({
                "price": str(price),
                "total_quantity": level.total_quantity,
                "order_count": len(level),
            })
            if len(bids_snapshot) >= depth:
                break
        
        asks_snapshot = []
        for price in self._get_ask_keys():
            level = self.asks[price]
            asks_snapshot.append({
                "price": str(price),
                "total_quantity": level.total_quantity,
                "order_count": len(level),
            })
            if len(asks_snapshot) >= depth:
                break
        
        return {
            "symbol": self.symbol,
            "best_bid": str(self.best_bid) if self.best_bid else None,
            "best_ask": str(self.best_ask) if self.best_ask else None,
            "spread": str(self.spread) if self.spread else None,
            "bids": bids_snapshot,
            "asks": asks_snapshot,
        }
    
    def get_all_orders(self, status: Optional[OrderStatus] = None, 
                       side: Optional[Side] = None) -> List[Order]:
        """获取所有订单，可选过滤"""
        result = []
        for order in self._all_orders.values():
            if status and order.status != status:
                continue
            if side and order.side != side:
                continue
            result.append(order)
        return result
    
    def get_trades(self, order_id: Optional[str] = None) -> List[TradeRecord]:
        """获取成交记录"""
        if order_id:
            order = self._all_orders.get(order_id)
            return order.trades if order else []
        return self._trades.copy()

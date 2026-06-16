import random
import uuid
from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from src.core.order import Order, Side, OrderType, OrderStatus
from src.data.market_data import TradeEvent, QuoteEvent


class MockDataGenerator:
    """Mock 数据生成器 - 用于测试"""
    
    def __init__(self, symbol: str = "000001.SZ", base_price: float = 10.50):
        self.symbol = symbol
        self.base_price = Decimal(str(base_price))
        self._order_seq = 0
        self._trade_seq = 0
    
    def reset(self):
        """重置序列号"""
        self._order_seq = 0
        self._trade_seq = 0
    
    def generate_order(
        self,
        side: Side = Side.BUY,
        price: Optional[Decimal] = None,
        quantity: int = 1000,
        order_type: OrderType = OrderType.LIMIT,
    ) -> Order:
        """生成单个委托订单"""
        self._order_seq += 1
        if price is None:
            price = self.base_price
        
        order = Order(
            symbol=self.symbol,
            side=side,
            price=price,
            quantity=quantity,
            order_type=order_type,
        )
        # 覆盖 order_id 使其有序
        order.order_id = f"ord-test-{self._order_seq:04d}"
        return order
    
    def generate_orders(
        self,
        count: int = 10,
        side: Optional[Side] = None,
        price_range: Optional[tuple] = None,
        quantity_range: tuple = (100, 10000),
    ) -> List[Order]:
        """批量生成委托订单"""
        orders = []
        for i in range(count):
            s = side if side else (Side.BUY if i % 2 == 0 else Side.SELL)
            
            if price_range:
                price = Decimal(str(random.uniform(price_range[0], price_range[1])))
                price = Decimal(str(round(float(price), 2)))
            else:
                price = self.base_price
            
            qty = random.randint(quantity_range[0], quantity_range[1])
            # 对齐到 100 的倍数
            qty = (qty // 100) * 100
            if qty < 100:
                qty = 100
            
            orders.append(self.generate_order(side=s, price=price, quantity=qty))
        
        return orders
    
    def generate_bid_orders(
        self,
        count: int = 5,
        start_price: Optional[Decimal] = None,
        step: Decimal = Decimal("0.01"),
    ) -> List[Order]:
        """生成买方队列（价格从高到低）"""
        if start_price is None:
            start_price = self.base_price
        
        orders = []
        for i in range(count):
            price = start_price - step * i
            orders.append(self.generate_order(
                side=Side.BUY,
                price=price,
                quantity=1000,
            ))
        return orders
    
    def generate_ask_orders(
        self,
        count: int = 5,
        start_price: Optional[Decimal] = None,
        step: Decimal = Decimal("0.01"),
    ) -> List[Order]:
        """生成卖方队列（价格从低到高）"""
        if start_price is None:
            start_price = self.base_price + Decimal("0.01")
        
        orders = []
        for i in range(count):
            price = start_price + step * i
            orders.append(self.generate_order(
                side=Side.SELL,
                price=price,
                quantity=1000,
            ))
        return orders
    
    def generate_trade(
        self,
        price: Optional[Decimal] = None,
        quantity: Optional[int] = None,
        direction: Optional[str] = None,
    ) -> TradeEvent:
        """生成单笔逐笔成交"""
        self._trade_seq += 1
        
        if price is None:
            price = self.base_price + Decimal(str(random.uniform(-0.05, 0.05)))
            price = Decimal(str(round(float(price), 2)))
        
        if quantity is None:
            quantity = random.randint(100, 5000)
        
        if direction is None:
            direction = random.choice(["buy", "sell"])
        
        return TradeEvent(
            symbol=self.symbol,
            price=price,
            quantity=quantity,
            direction=direction,
            trade_id=f"trade-test-{self._trade_seq:04d}",
            timestamp=datetime.now(),
        )
    
    def generate_trades(
        self,
        count: int = 10,
        price_range: Optional[tuple] = None,
        quantity_range: tuple = (100, 5000),
    ) -> List[TradeEvent]:
        """批量生成逐笔成交"""
        trades = []
        for i in range(count):
            if price_range:
                price = Decimal(str(random.uniform(price_range[0], price_range[1])))
                price = Decimal(str(round(float(price), 2)))
            else:
                price = None
            
            qty = random.randint(quantity_range[0], quantity_range[1])
            direction = random.choice(["buy", "sell"])
            trades.append(self.generate_trade(price=price, quantity=qty, direction=direction))
        
        return trades
    
    def generate_quote(
        self,
        bid_depth: int = 5,
        ask_depth: int = 5,
    ) -> QuoteEvent:
        """生成盘口快照"""
        bids = []
        asks = []
        
        for i in range(bid_depth):
            price = self.base_price - Decimal(str(i * 0.01))
            qty = random.randint(1000, 10000)
            bids.append({
                "price": price,
                "quantity": qty,
                "order_count": random.randint(5, 20),
            })
        
        for i in range(ask_depth):
            price = self.base_price + Decimal(str(i * 0.01))
            qty = random.randint(1000, 10000)
            asks.append({
                "price": price,
                "quantity": qty,
                "order_count": random.randint(5, 20),
            })
        
        return QuoteEvent(
            symbol=self.symbol,
            timestamp=datetime.now(),
            bids=bids,
            asks=asks,
        )
    
    def generate_market_scenario_1(self) -> Dict:
        """
        生成场景1：立即撮合
        卖盘: 10.50 -> [500, 300], 10.51 -> [200]
        新委托: Buy 10.52, 1000
        """
        return {
            "name": "立即撮合场景",
            "existing_orders": [
                {"side": "sell", "price": "10.50", "quantity": 500},
                {"side": "sell", "price": "10.50", "quantity": 300},
                {"side": "sell", "price": "10.51", "quantity": 200},
            ],
            "new_order": {"side": "buy", "price": "10.52", "quantity": 1000},
            "expected_filled": 1000,
            "expected_status": "filled",
        }
    
    def generate_market_scenario_2(self) -> Dict:
        """
        生成场景2：进入队列
        买盘: 10.48 -> [1000, 500]
        卖盘: 10.50 -> [500]
        新委托: Buy 10.48, 800
        """
        return {
            "name": "进入队列场景",
            "existing_orders": [
                {"side": "buy", "price": "10.48", "quantity": 1000},
                {"side": "buy", "price": "10.48", "quantity": 500},
                {"side": "sell", "price": "10.50", "quantity": 500},
            ],
            "new_order": {"side": "buy", "price": "10.48", "quantity": 800},
            "expected_status": "queued",
            "expected_queue_position": 3,
        }
    
    def generate_market_scenario_3(self) -> Dict:
        """
        生成场景3：逐笔成交触发队列消耗
        买盘: 10.48 -> [1000, 500, 800]
        逐笔成交: Sell-initiated, 10.48, 1200
        """
        return {
            "name": "逐笔成交消耗队列场景",
            "existing_orders": [
                {"side": "buy", "price": "10.48", "quantity": 1000},
                {"side": "buy", "price": "10.48", "quantity": 500},
                {"side": "buy", "price": "10.48", "quantity": 800},
            ],
            "trade": {"price": "10.48", "quantity": 1200, "direction": "sell"},
            "expected_filled_orders": [
                {"index": 0, "filled": 1000, "status": "filled"},
                {"index": 1, "filled": 200, "status": "partial"},
            ],
            "expected_remaining": [
                {"index": 1, "remaining": 300},
                {"index": 2, "remaining": 800},
            ],
        }
    
    def generate_market_scenario_4(self) -> Dict:
        """
        生成场景4：多价格层级消耗
        买盘: 10.50->[100], 10.49->[200], 10.48->[300]
        逐笔成交: Sell-initiated, 10.50, 500
        """
        return {
            "name": "多价格层级消耗场景",
            "existing_orders": [
                {"side": "buy", "price": "10.50", "quantity": 100},
                {"side": "buy", "price": "10.49", "quantity": 200},
                {"side": "buy", "price": "10.48", "quantity": 300},
            ],
            "trade": {"price": "10.50", "quantity": 500, "direction": "sell"},
            "expected_filled_orders": [
                {"index": 0, "filled": 100, "status": "filled"},
                {"index": 1, "filled": 200, "status": "filled"},
                {"index": 2, "filled": 200, "status": "partial"},
            ],
        }

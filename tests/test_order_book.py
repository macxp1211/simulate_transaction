import pytest
from decimal import Decimal

from src.core.order import Order, Side, OrderType, OrderStatus
from src.core.order_book import OrderBook, PriceLevel, SimpleSortedDict
from tests.mock_data import MockDataGenerator


class TestSimpleSortedDict:
    """有序字典测试"""
    
    def test_insert_and_retrieve(self):
        """测试插入和读取"""
        d = SimpleSortedDict()
        d[Decimal("10.50")] = "value1"
        d[Decimal("10.51")] = "value2"
        d[Decimal("10.49")] = "value3"
        
        # 升序排列
        keys = d.keys()
        assert keys == [Decimal("10.49"), Decimal("10.50"), Decimal("10.51")]
    
    def test_reverse_order(self):
        """测试降序排列"""
        d = SimpleSortedDict(reverse=True)
        d[Decimal("10.50")] = "value1"
        d[Decimal("10.51")] = "value2"
        d[Decimal("10.49")] = "value3"
        
        # 降序排列
        keys = d.keys()
        assert keys == [Decimal("10.51"), Decimal("10.50"), Decimal("10.49")]
    
    def test_delete(self):
        """测试删除"""
        d = SimpleSortedDict()
        d[Decimal("10.50")] = "value1"
        d[Decimal("10.51")] = "value2"
        
        del d[Decimal("10.50")]
        
        assert Decimal("10.50") not in d
        assert d.keys() == [Decimal("10.51")]
    
    def test_contains(self):
        """测试包含判断"""
        d = SimpleSortedDict()
        d[Decimal("10.50")] = "value1"
        
        assert Decimal("10.50") in d
        assert Decimal("10.51") not in d
    
    def test_is_empty(self):
        """测试空判断"""
        d = SimpleSortedDict()
        assert d.is_empty()
        
        d[Decimal("10.50")] = "value"
        assert not d.is_empty()


class TestPriceLevel:
    """价格层级测试"""
    
    def test_add_order(self):
        """测试添加订单"""
        level = PriceLevel(Decimal("10.50"))
        order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
        )
        
        level.add(order)
        
        assert len(level) == 1
        assert level.total_quantity == 1000
    
    def test_remove_order(self):
        """测试移除订单"""
        level = PriceLevel(Decimal("10.50"))
        order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
        )
        
        level.add(order)
        result = level.remove(order)
        
        assert result is True
        assert len(level) == 0
        assert level.total_quantity == 0
    
    def test_peek_and_pop(self):
        """测试查看和取出队首"""
        level = PriceLevel(Decimal("10.50"))
        order1 = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
        )
        order2 = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=500,
        )
        
        level.add(order1)
        level.add(order2)
        
        assert level.peek() == order1
        
        popped = level.pop()
        assert popped == order1
        assert len(level) == 1


class TestOrderBook:
    """订单簿测试"""
    
    def test_best_bid_ask(self, sample_order_book):
        """测试最优价格查询"""
        assert sample_order_book.best_bid == Decimal("10.50")
        assert sample_order_book.best_ask == Decimal("10.51")
    
    def test_spread(self, sample_order_book):
        """测试价差计算"""
        assert sample_order_book.spread == Decimal("0.01")
    
    def test_add_order_to_queue(self, empty_order_book, sample_order_buy):
        """测试委托进入队列"""
        status, trades = empty_order_book.add_order(sample_order_buy)
        
        assert status == OrderStatus.QUEUED
        assert len(trades) == 0
        assert sample_order_buy.queue_info is not None
        assert sample_order_buy.queue_info.queue_position_at_enter == 1
    
    def test_add_order_cross_match(self, empty_order_book):
        """测试价格交叉立即撮合"""
        # 先添加卖单
        sell_order = Order(
            symbol="000001.SZ",
            side=Side.SELL,
            price=Decimal("10.50"),
            quantity=500,
        )
        empty_order_book.add_order(sell_order)
        
        # 添加买单（价格 >= 卖价）
        buy_order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.51"),
            quantity=500,
        )
        status, trades = empty_order_book.add_order(buy_order)
        
        assert status == OrderStatus.FILLED
        assert len(trades) == 1
        assert buy_order.filled_qty == 500
        assert sell_order.filled_qty == 500
    
    def test_cancel_order(self, sample_order_book):
        """测试撤单"""
        # 获取一个已入队的订单
        order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
        )
        sample_order_book.add_order(order)
        
        result = sample_order_book.cancel_order(order.order_id)
        
        assert result is not None
        assert result.status == OrderStatus.CANCELLED
    
    def test_cancel_nonexistent_order(self, sample_order_book):
        """测试撤销不存在的订单"""
        result = sample_order_book.cancel_order("nonexistent")
        
        assert result is None
    
    def test_get_order(self, sample_order_book):
        """测试通过 ID 查询订单"""
        order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
        )
        sample_order_book.add_order(order)
        
        found = sample_order_book.get_order(order.order_id)
        
        assert found == order
    
    def test_get_queue_length(self, empty_order_book):
        """测试队列长度查询"""
        # 添加多个同价格买单
        for i in range(3):
            order = Order(
                symbol="000001.SZ",
                side=Side.BUY,
                price=Decimal("10.50"),
                quantity=1000,
            )
            empty_order_book.add_order(order)
        
        length = empty_order_book.get_queue_length(Side.BUY, Decimal("10.50"))
        assert length == 3
    
    def test_snapshot(self, sample_order_book):
        """测试订单簿快照"""
        snapshot = sample_order_book.get_snapshot(depth=3)
        
        assert snapshot["symbol"] == "000001.SZ"
        assert snapshot["best_bid"] == "10.50"
        assert snapshot["best_ask"] == "10.51"
        assert len(snapshot["bids"]) == 3
        assert len(snapshot["asks"]) == 3
    
    def test_consume_queue_on_trade_buy_initiated(self, empty_order_book):
        """测试买方主动成交消耗卖方队列"""
        # 添加卖方队列
        sell_orders = []
        for i in range(3):
            order = Order(
                symbol="000001.SZ",
                side=Side.SELL,
                price=Decimal("10.50") + Decimal(str(i * 0.01)),
                quantity=1000,
            )
            empty_order_book.add_order(order)
            sell_orders.append(order)
        
        # 买方主动成交（消耗卖方队列）
        trades = empty_order_book.consume_queue_on_trade(
            trade_price=Decimal("10.51"),
            trade_qty=1500,
            trade_direction="buy",
            trigger_trade_id="trd-001",
        )
        
        # 应该消耗 10.50 的 1000 和 10.51 的 500
        assert len(trades) == 2
        assert sell_orders[0].is_filled
        assert sell_orders[1].filled_qty == 500
    
    def test_consume_queue_on_trade_sell_initiated(self, empty_order_book):
        """测试卖方主动成交消耗买方队列"""
        # 添加买方队列
        buy_orders = []
        for i in range(3):
            order = Order(
                symbol="000001.SZ",
                side=Side.BUY,
                price=Decimal("10.50") - Decimal(str(i * 0.01)),
                quantity=1000,
            )
            empty_order_book.add_order(order)
            buy_orders.append(order)
        
        # 卖方主动成交（消耗买方队列）
        trades = empty_order_book.consume_queue_on_trade(
            trade_price=Decimal("10.49"),
            trade_qty=1500,
            trade_direction="sell",
            trigger_trade_id="trd-001",
        )
        
        # 应该消耗 10.50 的 1000 和 10.49 的 500
        assert len(trades) == 2
        assert buy_orders[0].is_filled
        assert buy_orders[1].filled_qty == 500
    
    def test_get_all_orders(self, sample_order_book):
        """测试获取所有订单"""
        orders = sample_order_book.get_all_orders()
        
        # 5 个买盘 + 5 个卖盘 = 10 个
        assert len(orders) == 10
    
    def test_get_all_orders_with_filter(self, sample_order_book):
        """测试带过滤的订单查询"""
        buy_orders = sample_order_book.get_all_orders(side=Side.BUY)
        
        assert len(buy_orders) == 5
        for order in buy_orders:
            assert order.side == Side.BUY


class TestOrderBookScenarios:
    """订单簿场景测试"""
    
    def test_scenario_1_immediate_match(self):
        """场景1：立即撮合"""
        book = OrderBook("000001.SZ")
        
        # 卖盘: 10.50 -> [500, 300], 10.51 -> [200]
        book.add_order(Order(
            symbol="000001.SZ", side=Side.SELL, price=Decimal("10.50"), quantity=500
        ))
        book.add_order(Order(
            symbol="000001.SZ", side=Side.SELL, price=Decimal("10.50"), quantity=300
        ))
        book.add_order(Order(
            symbol="000001.SZ", side=Side.SELL, price=Decimal("10.51"), quantity=200
        ))
        
        # 新委托: Buy 10.52, 1000
        buy_order = Order(
            symbol="000001.SZ", side=Side.BUY, price=Decimal("10.52"), quantity=1000
        )
        status, trades = book.add_order(buy_order)
        
        assert status == OrderStatus.FILLED
        assert buy_order.filled_qty == 1000
        assert len(trades) == 3
    
    def test_scenario_2_enter_queue(self):
        """场景2：进入队列"""
        book = OrderBook("000001.SZ")
        
        # 买盘: 10.48 -> [1000, 500]
        book.add_order(Order(
            symbol="000001.SZ", side=Side.BUY, price=Decimal("10.48"), quantity=1000
        ))
        book.add_order(Order(
            symbol="000001.SZ", side=Side.BUY, price=Decimal("10.48"), quantity=500
        ))
        # 卖盘: 10.50 -> [500]
        book.add_order(Order(
            symbol="000001.SZ", side=Side.SELL, price=Decimal("10.50"), quantity=500
        ))
        
        # 新委托: Buy 10.48, 800
        buy_order = Order(
            symbol="000001.SZ", side=Side.BUY, price=Decimal("10.48"), quantity=800
        )
        status, trades = book.add_order(buy_order)
        
        assert status == OrderStatus.QUEUED
        assert buy_order.queue_info.queue_position_at_enter == 3
    
    def test_scenario_3_trade_consumes_queue(self):
        """场景3：逐笔成交触发队列消耗"""
        book = OrderBook("000001.SZ")
        
        # 买盘: 10.48 -> [1000, 500, 800]
        orders = []
        for qty in [1000, 500, 800]:
            order = Order(
                symbol="000001.SZ", side=Side.BUY, price=Decimal("10.48"), quantity=qty
            )
            book.add_order(order)
            orders.append(order)
        
        # 逐笔成交: Sell-initiated, 10.48, 1200
        trades = book.consume_queue_on_trade(
            trade_price=Decimal("10.48"),
            trade_qty=1200,
            trade_direction="sell",
            trigger_trade_id="trd-001",
        )
        
        # OrderX: 1000 全部成交
        assert orders[0].status == OrderStatus.FILLED
        assert orders[0].filled_qty == 1000
        
        # OrderY: 部分成交 200
        assert orders[1].status == OrderStatus.PARTIAL
        assert orders[1].filled_qty == 200
        
        # NewOrder: 未轮到
        assert orders[2].status == OrderStatus.QUEUED
        assert orders[2].filled_qty == 0
    
    def test_scenario_4_multi_price_level_consume(self):
        """场景4：多价格层级消耗"""
        book = OrderBook("000001.SZ")
        
        # 买盘: 10.50->[100], 10.49->[200], 10.48->[300]
        orders = []
        for price, qty in [("10.50", 100), ("10.49", 200), ("10.48", 300)]:
            order = Order(
                symbol="000001.SZ", side=Side.BUY, price=Decimal(price), quantity=qty
            )
            book.add_order(order)
            orders.append(order)
        
        # 逐笔成交: Sell-initiated, 10.50, 500
        trades = book.consume_queue_on_trade(
            trade_price=Decimal("10.50"),
            trade_qty=500,
            trade_direction="sell",
            trigger_trade_id="trd-001",
        )
        
        assert orders[0].status == OrderStatus.FILLED  # 100 全部成交
        assert orders[1].status == OrderStatus.QUEUED   # 10.49 < 10.50，不消耗
        assert orders[2].status == OrderStatus.QUEUED   # 10.48 < 10.50，不消耗
        assert len(trades) == 1
        assert trades[0].quantity == 100

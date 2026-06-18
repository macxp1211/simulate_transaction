"""启动恢复功能测试"""

import pytest
from decimal import Decimal
from datetime import datetime

from src.core.order import Order, Side, OrderType, OrderStatus, QueueInfo
from src.core.order_book import OrderBook
from src.core.account import Account
from src.persistence import PersistenceManager


class TestOrderFromDict:
    def test_order_roundtrip(self):
        order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
            order_type=OrderType.LIMIT,
            order_id="ord-test-001",
            filled_qty=200,
            status=OrderStatus.QUEUED,
            source="internal",
        )
        order.enter_queue(5, 3)

        data = order.to_dict()
        restored = Order.from_dict(data)

        assert restored.symbol == order.symbol
        assert restored.side == order.side
        assert restored.price == order.price
        assert restored.quantity == order.quantity
        assert restored.filled_qty == order.filled_qty
        assert restored.status == order.status
        assert restored.order_type == order.order_type
        assert restored.order_id == order.order_id
        assert restored.source == order.source
        assert restored.queue_info is not None
        assert restored.queue_info.queue_position_at_enter == 3
        assert restored.queue_info.queue_length_at_enter == 5

    def test_market_order_from_dict(self):
        order = Order(
            symbol="000001.SZ",
            side=Side.SELL,
            price=Decimal("0"),
            quantity=500,
            order_type=OrderType.MARKET,
        )
        data = order.to_dict()
        restored = Order.from_dict(data)
        assert restored.order_type == OrderType.MARKET
        assert restored.price == Decimal("0.01")


class TestAccountRestore:
    def test_restore_from_settlement_dict(self):
        acc = Account(initial_cash=Decimal("1000000"), initial_position=100000)
        acc.cash = Decimal("900000")
        acc.frozen_cash = Decimal("50000")
        acc.available_position = 80000
        acc.frozen_position = 10000
        acc.today_bought_position = 5000
        acc.total_fees = Decimal("123.45")
        acc.trade_count = 10

        snapshot = acc.to_dict()
        acc2 = Account(initial_cash=Decimal("1000000"), initial_position=100000)
        acc2.restore_from_dict(snapshot)

        assert acc2.cash == Decimal("900000")
        assert acc2.frozen_cash == Decimal("50000")
        assert acc2.available_position == 80000
        assert acc2.frozen_position == 10000
        assert acc2.today_bought_position == 5000
        assert acc2.total_fees == Decimal("123.45")
        assert acc2.trade_count == 10


class TestOrderBookRestore:
    def test_restore_orders_keep_queue(self):
        ob = OrderBook("000001.SZ")

        o1 = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
            order_id="ord-001",
            status=OrderStatus.QUEUED,
        )
        o2 = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=500,
            order_id="ord-002",
            status=OrderStatus.QUEUED,
        )

        ob.restore_order(o1)
        ob.restore_order(o2)

        assert ob.get_order("ord-001") is o1
        assert ob.get_order("ord-002") is o2
        assert ob.best_bid == Decimal("10.50")
        level = ob.bids[Decimal("10.50")]
        assert len(level.orders) == 2
        assert level.total_quantity == 1500
        assert o1.queue_info.current_queue_position == 1
        assert o2.queue_info.current_queue_position == 2

    def test_restore_does_not_trigger_match(self):
        ob = OrderBook("000001.SZ")
        buy = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.60"),
            quantity=1000,
            order_id="buy-001",
            status=OrderStatus.QUEUED,
        )
        sell = Order(
            symbol="000001.SZ",
            side=Side.SELL,
            price=Decimal("10.50"),
            quantity=1000,
            order_id="sell-001",
            status=OrderStatus.QUEUED,
        )

        ob.restore_order(buy)
        ob.restore_order(sell)

        # 恢复不应触发撮合，两个订单都应仍在队列中
        assert buy.status == OrderStatus.QUEUED
        assert sell.status == OrderStatus.QUEUED
        assert buy.remaining_qty == 1000
        assert sell.remaining_qty == 1000


class TestMarketOrderDoesNotPolluteOrderBook:
    def test_mock_market_order_remaining_cancelled(self, funded_account):
        """mock 市价单未成交部分应立即撤销，不应以极端价格进入订单簿"""
        from src.core.order import Order, Side, OrderType
        from src.core.matching_engine import SymbolMatchingEngine

        engine = SymbolMatchingEngine("000001.SZ", account=funded_account)

        # 模拟一个买方冲击单（无对手盘）
        shock_buy = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("999999.99"),
            quantity=1000,
            order_type=OrderType.MARKET,
            order_id="shock-buy-001",
            is_mock=True,
            source="external",
        )
        status, trades = engine.order_book.add_order(shock_buy)
        assert status in (OrderStatus.CANCELLED, OrderStatus.FILLED)
        assert shock_buy.remaining_qty == 0
        assert engine.order_book.best_bid is None

        # 模拟一个卖方冲击单（无对手盘）
        shock_sell = Order(
            symbol="000001.SZ",
            side=Side.SELL,
            price=Decimal("0.01"),
            quantity=1000,
            order_type=OrderType.MARKET,
            order_id="shock-sell-001",
            is_mock=True,
            source="external",
        )
        status, trades = engine.order_book.add_order(shock_sell)
        assert status in (OrderStatus.CANCELLED, OrderStatus.FILLED)
        assert shock_sell.remaining_qty == 0
        assert engine.order_book.best_ask is None


class TestPersistenceActiveOrders:
    def test_get_active_orders_filters_status(self, tmp_path):
        pm = PersistenceManager(data_dir=str(tmp_path))

        active = {
            "order_id": "active-1",
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 100,
            "filled_qty": 0,
            "cancelled_qty": 0,
            "status": "queued",
            "order_type": "limit",
            "is_mock": False,
            "create_time": datetime.now().isoformat(),
            "update_time": datetime.now().isoformat(),
        }
        filled = dict(active)
        filled["order_id"] = "filled-1"
        filled["status"] = "filled"

        pm.save_order(active)
        pm.save_order(filled)

        results = pm.get_active_orders()
        assert len(results) == 1
        assert results[0]["order_id"] == "active-1"

    def test_get_latest_settlement(self, tmp_path):
        pm = PersistenceManager(data_dir=str(tmp_path))
        account = Account()
        account.cash = Decimal("800000")
        account.available_position = 50000

        pm.save_settlement("000001.SZ", account.to_dict())
        latest = pm.get_latest_settlement("000001.SZ")

        assert latest is not None
        assert Decimal(latest["cash"]) == Decimal("800000")
        assert latest["available_position"] == 50000

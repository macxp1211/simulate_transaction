import pytest
from decimal import Decimal

from src.core.order import Order, Side, OrderType, OrderStatus
from src.core.order_book import OrderBook
from src.core.matching_engine import MatchingEngineManager
from src.core.account import Account
from src.core.fee import AShareFeeCalculator


@pytest.fixture
def account():
    return Account(account_id="test-cancel", initial_cash="1000000.00", initial_position=10000)


@pytest.fixture
def manager(account):
    return MatchingEngineManager(account=account, fee_calculator=AShareFeeCalculator())


class TestOrderBookConsumeQueueOnCancel:
    def test_cancel_reduces_total_quantity(self):
        book = OrderBook("000001.SZ")
        price = Decimal("10.00")

        o1 = Order(symbol="000001.SZ", side=Side.BUY, price=price, quantity=500, source="external")
        o2 = Order(symbol="000001.SZ", side=Side.BUY, price=price, quantity=500, source="external")
        book.add_order(o1)
        book.add_order(o2)

        assert book.get_queue_length(Side.BUY, price) == 2
        level = book.bids[price]
        assert level.total_quantity == 1000
        assert o1.queue_info.current_queue_position == 1
        assert o2.queue_info.current_queue_position == 2

        consumed = book.consume_queue_on_cancel(price, 300, "buy")

        assert consumed == 300
        assert level.total_quantity == 700
        assert o1.remaining_qty == 200
        assert o1.status == OrderStatus.PARTIAL
        assert o2.remaining_qty == 500

    def test_cancel_advances_queue_positions(self):
        book = OrderBook("000001.SZ")
        price = Decimal("10.00")

        o1 = Order(symbol="000001.SZ", side=Side.SELL, price=price, quantity=100, source="external")
        o2 = Order(symbol="000001.SZ", side=Side.SELL, price=price, quantity=100, source="external")
        o3 = Order(symbol="000001.SZ", side=Side.SELL, price=price, quantity=100, source="external")
        for o in (o1, o2, o3):
            book.add_order(o)

        assert o1.queue_info.current_queue_position == 1
        assert o2.queue_info.current_queue_position == 2
        assert o3.queue_info.current_queue_position == 3

        book.consume_queue_on_cancel(price, 150, "sell")

        # o1 fully cancelled, removed from level
        assert o1.status == OrderStatus.CANCELLED
        # remaining orders move forward
        assert o2.queue_info.current_queue_position == 1
        assert o3.queue_info.current_queue_position == 2
        assert book.get_queue_length(Side.SELL, price) == 2

    def test_cancel_more_than_level_quantity(self):
        book = OrderBook("000001.SZ")
        price = Decimal("10.00")
        o1 = Order(symbol="000001.SZ", side=Side.BUY, price=price, quantity=200, source="external")
        book.add_order(o1)

        consumed = book.consume_queue_on_cancel(price, 9999, "buy")
        assert consumed == 200
        assert o1.status == OrderStatus.CANCELLED
        assert price not in book.bids

    def test_cancel_at_missing_price_is_noop(self):
        book = OrderBook("000001.SZ")
        consumed = book.consume_queue_on_cancel(Decimal("99.99"), 100, "buy")
        assert consumed == 0


class TestMatchingEngineCancelFeed:
    @pytest.mark.asyncio
    async def test_process_cancel_feed_advances_user_position(self, manager):
        # 先创建引擎（这会初始化市场规则），然后重新设置
        from src.core.market_rules import get_market_rules, MarketType
        engine = await manager.get_or_create_engine("000001.SZ")
        rules = get_market_rules("000001.SZ")
        rules.previous_close = Decimal("10.00")
        rules.market_type = MarketType.MAIN_BOARD

        # Seed passive sell depth above user's buy price so buy order queues
        for _ in range(3):
            await manager.place_order(Order(
                symbol="000001.SZ", side=Side.SELL, price=Decimal("10.02"),
                quantity=100, order_type=OrderType.LIMIT,
                source="external",
            ))

        # Seed 3 passive buys ahead of the user at 10.00
        for _ in range(3):
            await manager.place_order(Order(
                symbol="000001.SZ", side=Side.BUY, price=Decimal("10.00"),
                quantity=100, order_type=OrderType.LIMIT,
                source="external",
            ))

        # User passive buy at 10.00, should be 4th in queue
        user_order = Order(
            symbol="000001.SZ", side=Side.BUY, price=Decimal("10.00"),
            quantity=100, order_type=OrderType.LIMIT,
        )
        await manager.place_order(user_order)

        engine = await manager.get_or_create_engine("000001.SZ")
        snapshot = engine.get_orderbook_snapshot(depth=5)
        bid_level = next(b for b in snapshot["bids"] if Decimal(b["price"]) == Decimal("10.00"))
        assert bid_level["total_quantity"] == 400
        assert bid_level["order_count"] == 4

        user = manager.get_order("000001.SZ", user_order.order_id)
        assert user.queue_info.current_queue_position == 4

        # Cancel 250 shares from the front of the bid queue
        await manager.process_cancel_feed("000001.SZ", {
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.00",
            "quantity": 250,
        })

        user = manager.get_order("000001.SZ", user_order.order_id)
        # After cancelling 2.5 orders in front, user's position moves from 4 to 2
        # (only full orders ahead are removed; remaining ahead order has 50 left)
        assert user.queue_info.current_queue_position == 2
        assert user.status == OrderStatus.QUEUED

    @pytest.mark.asyncio
    async def test_cancel_feed_does_not_generate_trades(self, manager):
        await manager.place_order(Order(
            symbol="000001.SZ", side=Side.BUY, price=Decimal("10.00"),
            quantity=100, order_type=OrderType.LIMIT,
            source="external",
        ))

        trades_before = len(manager.get_all_engines()["000001.SZ"].order_book.get_trades())
        await manager.process_cancel_feed("000001.SZ", {
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.00",
            "quantity": 50,
        })
        trades_after = len(manager.get_all_engines()["000001.SZ"].order_book.get_trades())

        assert trades_before == trades_after


class TestMockFeedCancelCallback:
    @pytest.mark.asyncio
    async def test_mock_feed_emits_cancel(self):
        from src.data.level2_feed import MockLevel2Feed

        received = []

        def book_provider():
            return {
                "symbol": "000001.SZ",
                "bids": [{"price": "10.00", "total_quantity": 1000, "order_count": 1}],
                "asks": [{"price": "10.02", "total_quantity": 1000, "order_count": 1}],
            }

        feed = MockLevel2Feed(symbol="000001.SZ", base_price=10.0, order_interval=0.01, book_provider=book_provider)
        feed.on_cancel(lambda data: received.append(data))

        # Directly exercise the cancel generator
        await feed._generate_cancel(book_provider())

        assert len(received) == 1
        cancel = received[0]
        assert cancel["symbol"] == "000001.SZ"
        assert cancel["side"] in ("buy", "sell")
        assert "price" in cancel
        assert "quantity" in cancel
        assert cancel["quantity"] <= 1000
        assert cancel["quantity"] > 0

import pytest
import pytest_asyncio
import asyncio
from decimal import Decimal

from src.core.order import Order, Side, OrderType, OrderStatus
from src.core.matching_engine import SymbolMatchingEngine, MatchingEngineManager, MatchingConfig
from tests.mock_data import MockDataGenerator


class TestSymbolMatchingEngine:
    """单标的撮合引擎测试"""
    
    @pytest.fixture
    def mock_gen(self):
        return MockDataGenerator()
    
    @pytest.fixture
    def engine(self):
        return SymbolMatchingEngine("000001.SZ")
    
    @pytest_asyncio.fixture
    async def running_engine(self, engine):
        """已启动的引擎"""
        await engine.start()
        yield engine
        await engine.stop()
    
    @pytest.mark.asyncio
    async def test_engine_start_stop(self, engine):
        """测试引擎启动和停止"""
        await engine.start()
        assert engine._running is True
        assert engine._task is not None
        
        await engine.stop()
        assert engine._running is False
    
    @pytest.mark.asyncio
    async def test_place_order_buy(self, running_engine, mock_gen):
        """测试提交买入委托"""
        order = mock_gen.generate_order(side=Side.BUY, price=Decimal("10.50"))
        
        result = await running_engine.place_order(order)
        
        assert result.status in (OrderStatus.QUEUED, OrderStatus.FILLED)
        assert result.order_id == order.order_id
    
    @pytest.mark.asyncio
    async def test_place_order_sell(self, running_engine, mock_gen):
        """测试提交卖出委托"""
        order = mock_gen.generate_order(side=Side.SELL, price=Decimal("10.50"))
        
        result = await running_engine.place_order(order)
        
        assert result.status in (OrderStatus.QUEUED, OrderStatus.FILLED)
    
    @pytest.mark.asyncio
    async def test_order_cross_match(self, running_engine, mock_gen):
        """测试价格交叉立即撮合"""
        # 先提交卖单
        sell_order = mock_gen.generate_order(
            side=Side.SELL, price=Decimal("10.50"), quantity=500
        )
        await running_engine.place_order(sell_order)
        
        # 提交更高价格的买单
        buy_order = mock_gen.generate_order(
            side=Side.BUY, price=Decimal("10.51"), quantity=500
        )
        result = await running_engine.place_order(buy_order)
        
        assert result.status == OrderStatus.FILLED
        assert result.filled_qty == 500
    
    @pytest.mark.asyncio
    async def test_cancel_order(self, running_engine, mock_gen):
        """测试撤销委托"""
        order = mock_gen.generate_order(side=Side.BUY, price=Decimal("10.50"))
        await running_engine.place_order(order)
        
        result = await running_engine.cancel_order(order.order_id)
        
        assert result is not None
        assert result.status == OrderStatus.CANCELLED
    
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order(self, running_engine):
        """测试撤销不存在的委托"""
        result = await running_engine.cancel_order("nonexistent")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_process_trade_consumes_queue(self, running_engine, mock_gen):
        """测试逐笔成交驱动队列消耗"""
        # 添加买单队列
        buy_order = mock_gen.generate_order(
            side=Side.BUY, price=Decimal("10.50"), quantity=1000
        )
        await running_engine.place_order(buy_order)
        
        # 模拟卖方主动成交
        trade_data = {
            "price": "10.50",
            "quantity": 500,
            "direction": "sell",
            "trade_id": "trd-001",
        }
        await running_engine.process_trade(trade_data)
        await asyncio.sleep(0.05)  # 给处理时间
        
        # 查询订单状态
        result = running_engine.get_order(buy_order.order_id)
        assert result is not None
        assert result.filled_qty == 500
        assert result.status == OrderStatus.PARTIAL
    
    @pytest.mark.asyncio
    async def test_get_stats(self, running_engine, mock_gen):
        """测试统计信息"""
        order = mock_gen.generate_order(side=Side.BUY, price=Decimal("10.50"))
        await running_engine.place_order(order)
        
        stats = running_engine.get_stats()
        
        assert stats["orders_received"] >= 1
    
    @pytest.mark.asyncio
    async def test_get_orderbook_snapshot(self, running_engine, mock_gen):
        """测试订单簿快照"""
        # 添加多个订单
        for i in range(3):
            order = mock_gen.generate_order(
                side=Side.BUY, price=Decimal("10.50") - Decimal(str(i * 0.01))
            )
            await running_engine.place_order(order)
        
        snapshot = running_engine.get_orderbook_snapshot()
        
        assert snapshot["symbol"] == "000001.SZ"
        assert len(snapshot["bids"]) > 0
    
    @pytest.mark.asyncio
    async def test_invalid_order_rejected(self, running_engine, mock_gen):
        """测试无效委托被拒绝"""
        order = mock_gen.generate_order(quantity=50)  # 不是 100 的倍数
        
        result = await running_engine.place_order(order)
        
        assert result.status == OrderStatus.REJECTED


class TestMatchingEngineManager:
    """多标的引擎管理器测试"""
    
    @pytest.fixture
    def manager(self):
        return MatchingEngineManager()
    
    @pytest.mark.asyncio
    async def test_create_engine_on_demand(self, manager):
        """测试按需创建引擎"""
        order = Order(
            symbol="600519.SH",
            side=Side.BUY,
            price=Decimal("1000.00"),
            quantity=1000,
        )
        
        result = await manager.place_order(order)
        
        assert result.status in (OrderStatus.QUEUED, OrderStatus.FILLED)
        assert "600519.SH" in manager.get_all_engines()
    
    @pytest.mark.asyncio
    async def test_multiple_symbols(self, manager):
        """测试多标的并行处理"""
        symbols = ["000001.SZ", "600519.SH", "000858.SZ"]
        orders = []
        
        for symbol in symbols:
            order = Order(
                symbol=symbol,
                side=Side.BUY,
                price=Decimal("100.00"),
                quantity=1000,
            )
            orders.append(manager.place_order(order))
        
        results = await asyncio.gather(*orders)
        
        for result in results:
            assert result.status in (OrderStatus.QUEUED, OrderStatus.FILLED)
        
        assert len(manager.get_all_engines()) == 3
    
    @pytest.mark.asyncio
    async def test_shutdown_all(self, manager):
        """测试关闭所有引擎"""
        order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
        )
        await manager.place_order(order)
        
        assert len(manager.get_all_engines()) > 0
        
        await manager.shutdown_all()
        
        assert len(manager.get_all_engines()) == 0
    
    @pytest.mark.asyncio
    async def test_get_order(self, manager):
        """测试查询订单"""
        order = Order(
            symbol="000001.SZ",
            side=Side.BUY,
            price=Decimal("10.50"),
            quantity=1000,
        )
        result = await manager.place_order(order)
        
        found = manager.get_order("000001.SZ", result.order_id)
        
        assert found is not None
        assert found.order_id == result.order_id

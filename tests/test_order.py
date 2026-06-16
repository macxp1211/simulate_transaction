import pytest
from decimal import Decimal
from datetime import datetime

from src.core.order import Order, Side, OrderType, OrderStatus, QueueInfo, TradeRecord


class TestOrder:
    """订单模型测试"""
    
    def test_order_creation(self, sample_order_buy):
        """测试订单创建"""
        assert sample_order_buy.symbol == "000001.SZ"
        assert sample_order_buy.side == Side.BUY
        assert sample_order_buy.price == Decimal("10.50")
        assert sample_order_buy.quantity == 1000
        assert sample_order_buy.filled_qty == 0
        assert sample_order_buy.status == OrderStatus.PENDING
        assert sample_order_buy.order_type == OrderType.LIMIT
    
    def test_order_remaining_qty(self, sample_order_buy):
        """测试剩余数量计算"""
        assert sample_order_buy.remaining_qty == 1000
        
        sample_order_buy.filled_qty = 500
        assert sample_order_buy.remaining_qty == 500
    
    def test_order_is_filled(self, sample_order_buy):
        """测试全部成交状态"""
        assert not sample_order_buy.is_filled
        
        sample_order_buy.filled_qty = 1000
        assert sample_order_buy.is_filled
    
    def test_order_is_active(self, sample_order_buy):
        """测试活跃状态判断"""
        # PENDING 不算活跃
        assert not sample_order_buy.is_active
        
        sample_order_buy.status = OrderStatus.ACTIVE
        assert sample_order_buy.is_active
        
        sample_order_buy.status = OrderStatus.FILLED
        assert not sample_order_buy.is_active
    
    def test_order_is_in_queue(self, sample_order_buy):
        """测试队列状态判断"""
        assert not sample_order_buy.is_in_queue
        
        sample_order_buy.status = OrderStatus.QUEUED
        assert sample_order_buy.is_in_queue
        
        sample_order_buy.status = OrderStatus.PARTIAL
        assert sample_order_buy.is_in_queue
    
    def test_order_fill(self, sample_order_buy):
        """测试成交操作"""
        sample_order_buy.status = OrderStatus.ACTIVE
        sample_order_buy.fill(500)
        
        assert sample_order_buy.filled_qty == 500
        assert sample_order_buy.status == OrderStatus.PARTIAL
        assert sample_order_buy.remaining_qty == 500
    
    def test_order_fill_complete(self, sample_order_buy):
        """测试全部成交"""
        sample_order_buy.status = OrderStatus.ACTIVE
        sample_order_buy.fill(1000)
        
        assert sample_order_buy.filled_qty == 1000
        assert sample_order_buy.status == OrderStatus.FILLED
    
    def test_order_fill_invalid(self, sample_order_buy):
        """测试无效成交数量"""
        sample_order_buy.status = OrderStatus.ACTIVE
        
        with pytest.raises(ValueError, match="Invalid fill quantity"):
            sample_order_buy.fill(0)
        
        with pytest.raises(ValueError, match="Invalid fill quantity"):
            sample_order_buy.fill(2000)
    
    def test_order_cancel(self, sample_order_buy):
        """测试撤单操作"""
        sample_order_buy.status = OrderStatus.QUEUED
        sample_order_buy.enter_queue(5, 3)
        
        sample_order_buy.cancel()
        
        assert sample_order_buy.status == OrderStatus.CANCELLED
        assert sample_order_buy.queue_info.leave_queue_time is not None
    
    def test_order_cancel_invalid(self, sample_order_buy):
        """测试无效撤单"""
        sample_order_buy.status = OrderStatus.FILLED
        
        with pytest.raises(ValueError, match="Cannot cancel"):
            sample_order_buy.cancel()
    
    def test_order_enter_queue(self, sample_order_buy):
        """测试进入队列"""
        sample_order_buy.enter_queue(10, 5)
        
        assert sample_order_buy.status == OrderStatus.QUEUED
        assert sample_order_buy.queue_info is not None
        assert sample_order_buy.queue_info.queue_length_at_enter == 10
        assert sample_order_buy.queue_info.queue_position_at_enter == 5
        assert sample_order_buy.queue_info.enter_queue_time is not None
    
    def test_order_update_queue_position(self, sample_order_buy):
        """测试更新队列位置"""
        sample_order_buy.enter_queue(10, 5)
        sample_order_buy.update_queue_position(3, 8)
        
        assert sample_order_buy.queue_info.current_queue_position == 3
        assert sample_order_buy.queue_info.current_queue_length == 8
    
    def test_order_to_dict(self, sample_order_buy):
        """测试字典转换"""
        result = sample_order_buy.to_dict()
        
        assert result["symbol"] == "000001.SZ"
        assert result["side"] == "buy"
        assert result["price"] == "10.50"
        assert result["quantity"] == 1000
        assert result["filled_qty"] == 0
        assert result["remaining_qty"] == 1000
        assert result["status"] == "pending"
        assert result["order_type"] == "limit"
        assert "order_id" in result
    
    def test_market_order_buy(self, sample_symbol):
        """测试市价买入"""
        order = Order(
            symbol=sample_symbol,
            side=Side.BUY,
            price=Decimal("0"),  # 会被覆盖
            quantity=1000,
            order_type=OrderType.MARKET,
        )
        
        assert order.price == Decimal("999999.99")
    
    def test_market_order_sell(self, sample_symbol):
        """测试市价卖出"""
        order = Order(
            symbol=sample_symbol,
            side=Side.SELL,
            price=Decimal("0"),  # 会被覆盖
            quantity=1000,
            order_type=OrderType.MARKET,
        )
        
        assert order.price == Decimal("0.01")
    
    def test_queue_wait_ms(self, sample_order_buy):
        """测试队列等待时间计算"""
        sample_order_buy.enter_queue(10, 5)
        # 未离开队列，wait_ms 应该 >= 0
        wait_ms = sample_order_buy._get_queue_wait_ms()
        assert wait_ms >= 0


class TestTradeRecord:
    """成交记录测试"""
    
    def test_trade_record_creation(self):
        """测试成交记录创建"""
        trade = TradeRecord(
            trade_id="trd-001",
            order_id="ord-001",
            symbol="000001.SZ",
            side="buy",
            price=Decimal("10.50"),
            quantity=1000,
            trade_time=datetime.now(),
        )
        
        assert trade.trade_id == "trd-001"
        assert trade.order_id == "ord-001"
        assert trade.price == Decimal("10.50")
        assert trade.quantity == 1000
    
    def test_trade_record_to_dict(self):
        """测试成交记录字典转换"""
        trade = TradeRecord(
            trade_id="trd-001",
            order_id="ord-001",
            symbol="000001.SZ",
            side="buy",
            price=Decimal("10.50"),
            quantity=1000,
            trade_time=datetime.now(),
        )
        
        result = trade.to_dict()
        assert result["trade_id"] == "trd-001"
        assert result["price"] == "10.50"
        assert result["quantity"] == 1000

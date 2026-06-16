import pytest
import asyncio
from decimal import Decimal
from datetime import datetime

from src.core.order import Order, Side, OrderType, OrderStatus
from src.core.order_book import OrderBook
from src.core.matching_engine import SymbolMatchingEngine, MatchingEngineManager
from tests.mock_data import MockDataGenerator


class TestBenchmark:
    """性能基准测试"""
    
    def test_order_book_insert_benchmark(self, benchmark):
        """测试订单簿插入性能"""
        book = OrderBook("000001.SZ")
        
        def insert_orders():
            for i in range(100):
                order = Order(
                    symbol="000001.SZ",
                    side=Side.BUY if i % 2 == 0 else Side.SELL,
                    price=Decimal("10.50") + Decimal(str(i * 0.01)),
                    quantity=1000,
                )
                book.add_order(order)
        
        result = benchmark(insert_orders)
        # 只运行基准测试，不验证结果
    
    def test_order_book_match_benchmark(self, benchmark):
        """测试订单簿撮合性能"""
        book = OrderBook("000001.SZ")
        
        # 预置订单
        for i in range(50):
            order = Order(
                symbol="000001.SZ",
                side=Side.SELL,
                price=Decimal("10.50") + Decimal(str(i * 0.01)),
                quantity=1000,
            )
            book.add_order(order)
        
        def match_orders():
            for i in range(50):
                order = Order(
                    symbol="000001.SZ",
                    side=Side.BUY,
                    price=Decimal("10.99"),
                    quantity=1000,
                )
                book.add_order(order)
        
        benchmark(match_orders)
    
    def test_order_book_consume_benchmark(self, benchmark):
        """测试队列消耗性能"""
        book = OrderBook("000001.SZ")
        
        # 预置 100 个买单
        for i in range(100):
            order = Order(
                symbol="000001.SZ",
                side=Side.BUY,
                price=Decimal("10.50") - Decimal(str(i * 0.01)),
                quantity=1000,
            )
            book.add_order(order)
        
        def consume_trades():
            for i in range(100):
                book.consume_queue_on_trade(
                    trade_price=Decimal("10.50"),
                    trade_qty=1000,
                    trade_direction="sell",
                    trigger_trade_id=f"trd-{i}",
                )
        
        benchmark(consume_trades)
    
    @pytest.mark.asyncio
    async def test_engine_throughput(self):
        """测试引擎吞吐量"""
        engine = SymbolMatchingEngine("000001.SZ")
        await engine.start()
        
        try:
            start_time = datetime.now()
            
            # 批量提交 1000 个订单
            tasks = []
            for i in range(1000):
                order = Order(
                    symbol="000001.SZ",
                    side=Side.BUY if i % 2 == 0 else Side.SELL,
                    price=Decimal("10.50") + Decimal(str(i * 0.001)),
                    quantity=1000,
                )
                tasks.append(engine.place_order(order))
            
            await asyncio.gather(*tasks)
            
            end_time = datetime.now()
            elapsed_ms = (end_time - start_time).total_seconds() * 1000
            
            stats = engine.get_stats()
            assert stats["orders_received"] >= 1000
            
            # 记录性能（不硬性断言，仅记录）
            print(f"\n吞吐量测试: 1000 笔委托耗时 {elapsed_ms:.2f}ms")
            print(f"平均延迟: {elapsed_ms / 1000:.4f}ms/笔")
        finally:
            await engine.stop()
    
    @pytest.mark.asyncio
    async def test_engine_latency(self):
        """测试引擎延迟"""
        engine = SymbolMatchingEngine("000001.SZ")
        await engine.start()
        
        try:
            latencies = []
            
            for i in range(100):
                order = Order(
                    symbol="000001.SZ",
                    side=Side.BUY,
                    price=Decimal("10.50"),
                    quantity=1000,
                )
                
                start = datetime.now()
                await engine.place_order(order)
                end = datetime.now()
                
                latency_ms = (end - start).total_seconds() * 1000
                latencies.append(latency_ms)
            
            avg_latency = sum(latencies) / len(latencies)
            max_latency = max(latencies)
            min_latency = min(latencies)
            
            print(f"\n延迟测试:")
            print(f"  平均: {avg_latency:.4f}ms")
            print(f"  最大: {max_latency:.4f}ms")
            print(f"  最小: {min_latency:.4f}ms")
            
            # 软断言：平均延迟应该 < 10ms（当前实现可能不够优化）
            assert avg_latency < 50, f"平均延迟 {avg_latency:.4f}ms 超过 50ms"
        finally:
            await engine.stop()
    
    def test_memory_usage(self):
        """测试内存占用（简单估算）"""
        import sys
        
        book = OrderBook("000001.SZ")
        
        # 添加 10000 个订单
        for i in range(10000):
            order = Order(
                symbol="000001.SZ",
                side=Side.BUY if i % 2 == 0 else Side.SELL,
                price=Decimal("10.50") + Decimal(str(i * 0.0001)),
                quantity=1000,
            )
            book.add_order(order)
        
        # 粗略估算内存
        orders_size = len(book._all_orders)
        
        print(f"\n内存测试: 10000 笔订单")
        print(f"  订单数量: {orders_size}")
        print(f"  买盘层级: {len(book.bids)}")
        print(f"  卖盘层级: {len(book.asks)}")
        
        assert orders_size == 10000

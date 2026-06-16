import pytest
import asyncio
from decimal import Decimal
from typing import Generator
import sys
import os

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.core.order import Order, Side, OrderType, OrderStatus, TradeRecord
from src.core.order_book import OrderBook
from src.core.matching_engine import SymbolMatchingEngine, MatchingEngineManager, MatchingConfig
from src.api.server import app
from fastapi.testclient import TestClient


@pytest.fixture
def event_loop():
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_symbol() -> str:
    """示例标的代码"""
    return "000001.SZ"


@pytest.fixture
def sample_price() -> Decimal:
    """示例价格"""
    return Decimal("10.50")


@pytest.fixture
def sample_order_buy(sample_symbol, sample_price) -> Order:
    """示例买入委托"""
    return Order(
        symbol=sample_symbol,
        side=Side.BUY,
        price=sample_price,
        quantity=1000,
        order_type=OrderType.LIMIT,
    )


@pytest.fixture
def sample_order_sell(sample_symbol, sample_price) -> Order:
    """示例卖出委托"""
    return Order(
        symbol=sample_symbol,
        side=Side.SELL,
        price=sample_price,
        quantity=1000,
        order_type=OrderType.LIMIT,
    )


@pytest.fixture
def empty_order_book(sample_symbol) -> OrderBook:
    """空订单簿"""
    return OrderBook(sample_symbol)


@pytest.fixture
def sample_order_book(sample_symbol) -> OrderBook:
    """预置了订单的订单簿"""
    book = OrderBook(sample_symbol)
    
    # 添加买盘（价格从高到低）
    for i in range(5):
        order = Order(
            symbol=sample_symbol,
            side=Side.BUY,
            price=Decimal("10.50") - Decimal(str(i * 0.01)),
            quantity=1000,
            order_type=OrderType.LIMIT,
        )
        book.add_order(order)
    
    # 添加卖盘（价格从低到高）
    for i in range(5):
        order = Order(
            symbol=sample_symbol,
            side=Side.SELL,
            price=Decimal("10.51") + Decimal(str(i * 0.01)),
            quantity=1000,
            order_type=OrderType.LIMIT,
        )
        book.add_order(order)
    
    return book


@pytest.fixture
def matching_engine(sample_symbol, funded_account) -> SymbolMatchingEngine:
    """单标的撮合引擎"""
    engine = SymbolMatchingEngine(sample_symbol, account=funded_account)
    return engine


@pytest.fixture
def engine_manager(funded_account) -> MatchingEngineManager:
    """多标的引擎管理器"""
    return MatchingEngineManager(account=funded_account)


@pytest.fixture
def funded_account() -> "Account":
    """带有充足资金和底仓的测试账户"""
    from src.core.account import Account
    return Account(initial_cash="100000000.00", initial_position=100000)


@pytest.fixture
def api_client() -> TestClient:
    """FastAPI 测试客户端"""
    return TestClient(app)

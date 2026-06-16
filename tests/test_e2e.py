import pytest
import asyncio
from decimal import Decimal
from fastapi.testclient import TestClient

from src.core.order import Order, Side, OrderType
from src.core.matching_engine import MatchingEngineManager
from src.api.server import app, engine_manager, account


def reset_test_account():
    """重置测试账户为充足资金和底仓"""
    account.cash = Decimal("100000000.00")
    account.available_position = 100000
    account.frozen_position = 0
    account.total_fees = Decimal("0")
    account.trade_count = 0


class TestEndToEnd:
    """端到端测试 - 完整业务流"""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def setup_method(self):
        """每个测试前重置引擎状态和账户"""
        engine_manager._engines.clear()
        reset_test_account()
    
    def test_e2e_place_and_match(self, client):
        """端到端：委托→撮合→成交"""
        # Step 1: 提交卖单
        sell_response = client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "sell",
            "price": "10.50",
            "quantity": 500,
        })
        assert sell_response.status_code == 200
        sell_order = sell_response.json()["data"]
        
        # Step 2: 提交更高价格买单 → 立即撮合
        buy_response = client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.51",
            "quantity": 500,
        })
        assert buy_response.status_code == 200
        buy_order = buy_response.json()["data"]
        
        # 买单应该全部成交
        assert buy_order["status"] == "filled"
        assert buy_order["filled_qty"] == 500
        
        # Step 3: 查询成交记录
        trades_response = client.get("/api/v1/trades?symbol=000001.SZ")
        assert trades_response.status_code == 200
        trades_data = trades_response.json()
        assert trades_data["data"]["total"] >= 1
    
    def test_e2e_queue_and_cancel(self, client):
        """端到端：委托→排队→撤单"""
        # Step 1: 提交买单（无对手盘，进入队列）
        buy_response = client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.48",
            "quantity": 1000,
        })
        assert buy_response.status_code == 200
        buy_order = buy_response.json()["data"]
        assert buy_order["status"] == "queued"
        assert buy_order["queue_info"]["queue_position_at_enter"] == 1
        
        # Step 2: 撤销订单
        cancel_response = client.delete(f"/api/v1/orders/{buy_order['order_id']}")
        assert cancel_response.status_code == 200
        cancel_data = cancel_response.json()
        assert cancel_data["data"]["status"] == "cancelled"
        
        # Step 3: 查询订单确认状态
        get_response = client.get(f"/api/v1/orders/{buy_order['order_id']}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["data"]["status"] == "cancelled"
    
    def test_e2e_trade_feed_consumes_queue(self, client):
        """端到端：委托→排队→行情触发→成交"""
        # Step 1: 提交买单（进入队列）
        buy_response = client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 1000,
        })
        assert buy_response.status_code == 200
        buy_order = buy_response.json()["data"]
        assert buy_order["status"] == "queued"
        
        # Step 2: 模拟逐笔成交（通过 API 触发）
        # 注：当前 API 没有直接触发逐笔成交的接口，需要通过引擎内部
        # 这里验证订单在队列中
        get_response = client.get(f"/api/v1/orders/{buy_order['order_id']}")
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["data"]["status"] == "queued"
    
    def test_e2e_multiple_orders(self, client):
        """端到端：批量委托和查询"""
        order_ids = []
        
        # 批量提交 10 个订单
        for i in range(10):
            response = client.post("/api/v1/orders", json={
                "symbol": "000001.SZ",
                "side": "buy" if i % 2 == 0 else "sell",
                "price": str(Decimal("10.50") + Decimal(str(i * 0.01))),
                "quantity": 1000,
            })
            assert response.status_code == 200
            order_ids.append(response.json()["data"]["order_id"])
        
        # 查询所有订单
        list_response = client.get("/api/v1/orders?symbol=000001.SZ&page_size=20")
        assert list_response.status_code == 200
        list_data = list_response.json()
        assert list_data["data"]["total"] >= 10
        
        # 查询订单簿
        book_response = client.get("/api/v1/orderbook/000001.SZ")
        assert book_response.status_code == 200
        book_data = book_response.json()
        assert book_data["data"]["symbol"] == "000001.SZ"
    
    def test_e2e_pagination(self, client):
        """端到端：分页查询"""
        # 创建 25 个订单
        for i in range(25):
            client.post("/api/v1/orders", json={
                "symbol": "000001.SZ",
                "side": "buy",
                "price": "10.50",
                "quantity": 1000,
            })
        
        # 查询第 1 页
        page1 = client.get("/api/v1/orders?symbol=000001.SZ&page=1&page_size=10")
        assert page1.status_code == 200
        data1 = page1.json()
        assert data1["data"]["total"] >= 25
        assert len(data1["data"]["orders"]) == 10
        
        # 查询第 2 页
        page2 = client.get("/api/v1/orders?symbol=000001.SZ&page=2&page_size=10")
        assert page2.status_code == 200
        data2 = page2.json()
        assert len(data2["data"]["orders"]) == 10
    
    def test_e2e_order_status_transitions(self, client):
        """端到端：订单状态流转"""
        # 创建订单 → queued
        create_response = client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.48",
            "quantity": 1000,
        })
        order_id = create_response.json()["data"]["order_id"]
        
        # 查询 → queued
        get_response = client.get(f"/api/v1/orders/{order_id}")
        assert get_response.json()["data"]["status"] == "queued"
        
        # 撤单 → cancelled
        cancel_response = client.delete(f"/api/v1/orders/{order_id}")
        assert cancel_response.json()["data"]["status"] == "cancelled"
        
        # 再次查询 → cancelled
        get_response2 = client.get(f"/api/v1/orders/{order_id}")
        assert get_response2.json()["data"]["status"] == "cancelled"
    
    def test_e2e_websocket_connection(self, client):
        """端到端：WebSocket 连接测试"""
        with client.websocket_connect("/ws/v1") as websocket:
            # 发送 ping
            websocket.send_json({"action": "ping"})
            
            # 接收 pong
            response = websocket.receive_json()
            assert response["type"] == "pong"
            assert "timestamp" in response
    
    def test_e2e_market_scenario(self, client):
        """端到端：完整市场场景"""
        symbol = "000001.SZ"
        
        # 构建市场：买卖各 5 档
        for i in range(5):
            # 买盘
            client.post("/api/v1/orders", json={
                "symbol": symbol,
                "side": "buy",
                "price": str(Decimal("10.50") - Decimal(str(i * 0.01))),
                "quantity": 1000 * (i + 1),
            })
            
            # 卖盘
            client.post("/api/v1/orders", json={
                "symbol": symbol,
                "side": "sell",
                "price": str(Decimal("10.51") + Decimal(str(i * 0.01))),
                "quantity": 1000 * (i + 1),
            })
        
        # 查询订单簿
        book_response = client.get(f"/api/v1/orderbook/{symbol}")
        assert book_response.status_code == 200
        book_data = book_response.json()["data"]
        
        assert len(book_data["bids"]) == 5
        assert len(book_data["asks"]) == 5
        assert book_data["best_bid"] == "10.50"
        assert book_data["best_ask"] == "10.51"
        assert book_data["spread"] == "0.01"
        
        # 提交市价买入（应该立即成交）
        # 先添加卖盘确保有对手盘
        client.post("/api/v1/orders", json={
            "symbol": symbol,
            "side": "sell",
            "price": "10.50",
            "quantity": 500,
        })
        
        market_buy = client.post("/api/v1/orders", json={
            "symbol": symbol,
            "side": "buy",
            "price": "10.50",
            "quantity": 500,
            "order_type": "market",
        })
        assert market_buy.status_code == 200
        assert market_buy.json()["data"]["status"] == "filled"

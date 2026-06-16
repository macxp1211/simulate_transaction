import pytest
from fastapi.testclient import TestClient
from decimal import Decimal
import json
import asyncio

from src.api.server import app, engine_manager


class TestAPI:
    """API 集成测试"""
    
    def setup_method(self):
        """每个测试前重置引擎状态"""
        # 同步方式重置全局引擎管理器
        engine_manager._engines.clear()
    
    def test_create_order(self, api_client):
        """测试提交委托"""
        response = api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 1000,
            "order_type": "limit",
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["symbol"] == "000001.SZ"
        assert data["data"]["side"] == "buy"
        assert data["data"]["status"] in ("queued", "filled")
    
    def test_create_order_invalid(self, api_client):
        """测试提交无效委托"""
        response = api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "invalid",
            "price": "10.50",
            "quantity": 1000,
        })
        
        assert response.status_code == 400
    
    def test_get_order(self, api_client):
        """测试查询单笔委托"""
        # 先创建订单
        create_response = api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 1000,
        })
        order_id = create_response.json()["data"]["order_id"]
        
        # 查询订单
        response = api_client.get(f"/api/v1/orders/{order_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["order_id"] == order_id
    
    def test_get_order_not_found(self, api_client):
        """测试查询不存在的订单"""
        response = api_client.get("/api/v1/orders/nonexistent")
        
        assert response.status_code == 404
    
    def test_cancel_order(self, api_client):
        """测试撤销委托"""
        # 先创建订单
        create_response = api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 1000,
        })
        order_id = create_response.json()["data"]["order_id"]
        
        # 撤销订单
        response = api_client.delete(f"/api/v1/orders/{order_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "cancelled"
    
    def test_list_orders(self, api_client):
        """测试查询委托列表"""
        # 创建几个订单
        for _ in range(3):
            api_client.post("/api/v1/orders", json={
                "symbol": "000001.SZ",
                "side": "buy",
                "price": "10.50",
                "quantity": 1000,
            })
        
        response = api_client.get("/api/v1/orders?symbol=000001.SZ")
        
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["total"] >= 3
    
    def test_get_orderbook(self, api_client):
        """测试查询订单簿"""
        # 先创建一些订单
        for i in range(3):
            api_client.post("/api/v1/orders", json={
                "symbol": "000001.SZ",
                "side": "buy",
                "price": str(Decimal("10.50") - Decimal(str(i * 0.01))),
                "quantity": 1000,
            })
        
        response = api_client.get("/api/v1/orderbook/000001.SZ")
        
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["symbol"] == "000001.SZ"
        assert "bids" in data["data"]
        assert "asks" in data["data"]
    
    def test_list_symbols(self, api_client):
        """测试查询标的列表"""
        # 创建订单以激活标的
        api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 1000,
        })
        
        response = api_client.get("/api/v1/symbols")
        
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert len(data["data"]["symbols"]) > 0
    
    def test_get_stats(self, api_client):
        """测试查询统计信息"""
        # 创建订单
        api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 1000,
        })
        
        response = api_client.get("/api/v1/stats/000001.SZ")
        
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["orders_received"] >= 1
    
    def test_create_market_order(self, api_client):
        """测试市价委托"""
        # 先创建对手盘
        api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "sell",
            "price": "10.50",
            "quantity": 1000,
        })
        
        response = api_client.post("/api/v1/orders", json={
            "symbol": "000001.SZ",
            "side": "buy",
            "price": "10.50",
            "quantity": 1000,
            "order_type": "market",
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "filled"

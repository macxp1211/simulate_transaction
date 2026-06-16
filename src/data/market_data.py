from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional


@dataclass
class TradeEvent:
    """逐笔成交事件"""
    symbol: str
    price: Decimal
    quantity: int
    direction: str  # "buy" = 买方主动（外盘）, "sell" = 卖方主动（内盘）, "neutral"
    trade_id: str
    timestamp: datetime
    
    # 扩展字段（根据数据源可能不同）
    buyer_order_id: Optional[str] = None
    seller_order_id: Optional[str] = None
    
    @classmethod
    def from_raw(cls, data: dict) -> "TradeEvent":
        """从原始数据解析"""
        # 支持多种数据源格式
        symbol = data.get("symbol") or data.get("code") or data.get("stock_code")
        price = Decimal(str(data.get("price", 0)))
        quantity = int(data.get("quantity", data.get("volume", 0)))
        
        # 方向判断
        direction = data.get("direction", "unknown")
        if direction not in ("buy", "sell", "neutral"):
            # 尝试其他字段
            bs_flag = data.get("bs_flag", data.get("bs", ""))
            if bs_flag in ("B", "b", "1", "buy", "外盘"):
                direction = "buy"
            elif bs_flag in ("S", "s", "2", "sell", "内盘"):
                direction = "sell"
            else:
                direction = "neutral"
        
        trade_id = data.get("trade_id", data.get("seq", ""))
        timestamp_str = data.get("timestamp", data.get("time", ""))
        
        if timestamp_str:
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except:
                timestamp = datetime.now()
        else:
            timestamp = datetime.now()
        
        return cls(
            symbol=symbol,
            price=price,
            quantity=quantity,
            direction=direction,
            trade_id=trade_id,
            timestamp=timestamp,
        )
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": str(self.price),
            "quantity": self.quantity,
            "direction": self.direction,
            "trade_id": self.trade_id,
            "timestamp": self.timestamp.isoformat(),
        }
    
    def is_buy_initiated(self) -> bool:
        """是否买方主动成交"""
        return self.direction == "buy"
    
    def is_sell_initiated(self) -> bool:
        """是否卖方主动成交"""
        return self.direction == "sell"


@dataclass
class QuoteEvent:
    """盘口快照事件"""
    symbol: str
    timestamp: datetime
    
    # 买盘 10 档
    bids: List[Dict]  # [{"price": Decimal, "quantity": int, "order_count": int}]
    # 卖盘 10 档
    asks: List[Dict]
    
    # 汇总数据
    total_bid_qty: int = 0
    total_ask_qty: int = 0
    
    @classmethod
    def from_raw(cls, data: dict) -> "QuoteEvent":
        """从原始数据解析"""
        symbol = data.get("symbol") or data.get("code")
        timestamp_str = data.get("timestamp", data.get("time", ""))
        
        if timestamp_str:
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except:
                timestamp = datetime.now()
        else:
            timestamp = datetime.now()
        
        # 解析买卖盘
        bids = []
        asks = []
        
        raw_bids = data.get("bids", data.get("buy", []))
        raw_asks = data.get("asks", data.get("sell", []))
        
        for bid in raw_bids:
            if isinstance(bid, list):
                bids.append({
                    "price": Decimal(str(bid[0])),
                    "quantity": int(bid[1]),
                    "order_count": int(bid[2]) if len(bid) > 2 else 0,
                })
            elif isinstance(bid, dict):
                bids.append({
                    "price": Decimal(str(bid.get("price", 0))),
                    "quantity": int(bid.get("quantity", 0)),
                    "order_count": int(bid.get("order_count", 0)),
                })
        
        for ask in raw_asks:
            if isinstance(ask, list):
                asks.append({
                    "price": Decimal(str(ask[0])),
                    "quantity": int(ask[1]),
                    "order_count": int(ask[2]) if len(ask) > 2 else 0,
                })
            elif isinstance(ask, dict):
                asks.append({
                    "price": Decimal(str(ask.get("price", 0))),
                    "quantity": int(ask.get("quantity", 0)),
                    "order_count": int(ask.get("order_count", 0)),
                })
        
        total_bid_qty = sum(b["quantity"] for b in bids)
        total_ask_qty = sum(a["quantity"] for a in asks)
        
        return cls(
            symbol=symbol,
            timestamp=timestamp,
            bids=bids,
            asks=asks,
            total_bid_qty=total_bid_qty,
            total_ask_qty=total_ask_qty,
        )
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "bids": [{"price": str(b["price"]), "quantity": b["quantity"], "order_count": b["order_count"]} for b in self.bids],
            "asks": [{"price": str(a["price"]), "quantity": a["quantity"], "order_count": a["order_count"]} for a in self.asks],
            "total_bid_qty": self.total_bid_qty,
            "total_ask_qty": self.total_ask_qty,
        }
    
    def get_best_bid(self) -> Optional[Decimal]:
        """最优买价"""
        return self.bids[0]["price"] if self.bids else None
    
    def get_best_ask(self) -> Optional[Decimal]:
        """最优卖价"""
        return self.asks[0]["price"] if self.asks else None

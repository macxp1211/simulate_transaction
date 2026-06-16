from decimal import Decimal
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
import uuid


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"       # 创建中
    ACTIVE = "active"         # 已激活
    QUEUED = "queued"         # 排队中
    MATCHING = "matching"     # 撮合中
    PARTIAL = "partial"       # 部分成交
    FILLED = "filled"         # 全部成交
    CANCELLED = "cancelled"   # 已撤销
    REJECTED = "rejected"     # 已拒绝


class OrderType(Enum):
    LIMIT = "limit"           # 限价单
    MARKET = "market"         # 市价单


@dataclass
class QueueInfo:
    """订单队列信息"""
    queue_length_at_enter: int      # 进入队列时的总长度
    queue_position_at_enter: int     # 进入队列时的位置（1-based）
    current_queue_length: int = 0   # 当前队列长度（动态更新）
    current_queue_position: int = 0 # 当前位置（动态更新）
    enter_queue_time: Optional[datetime] = None
    leave_queue_time: Optional[datetime] = None


@dataclass
class Order:
    """委托订单"""
    symbol: str
    side: Side
    price: Decimal
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    
    # 系统自动生成字段
    order_id: str = field(default_factory=lambda: f"ord-{uuid.uuid4().hex[:12]}")
    filled_qty: int = 0
    cancelled_qty: int = 0
    status: OrderStatus = OrderStatus.PENDING
    
    # 队列信息
    queue_info: Optional[QueueInfo] = None

    # 拒绝原因（当被 REJECTED 时填写）
    reject_reason: Optional[str] = None

    # 挂单冻结信息（用于账户解冻/成交结算）
    frozen_total: Optional[Decimal] = None  # 买入时冻结的资金总额
    frozen_position_qty: Optional[int] = None  # 卖出时冻结的仓位数量

    # 模拟行情订单标记：不参与真实账户冻结/解冻
    is_mock: bool = False

    # 时间戳
    create_time: datetime = field(default_factory=datetime.now)
    update_time: datetime = field(default_factory=datetime.now)
    
    # 关联的成交记录
    trades: List['TradeRecord'] = field(default_factory=list)
    
    def __post_init__(self):
        if self.side == Side.BUY and self.order_type == OrderType.MARKET:
            # 市价买入使用极大价格
            self.price = Decimal("999999.99")
        elif self.side == Side.SELL and self.order_type == OrderType.MARKET:
            # 市价卖出使用极小价格
            self.price = Decimal("0.01")
    
    @property
    def remaining_qty(self) -> int:
        """剩余未成交数量（已扣除成交和撤单）"""
        return self.quantity - self.filled_qty - self.cancelled_qty
    
    @property
    def is_filled(self) -> bool:
        """是否全部成交"""
        return self.filled_qty >= self.quantity
    
    @property
    def is_active(self) -> bool:
        """是否仍处于活跃状态（可撮合/排队）"""
        return self.status in (OrderStatus.ACTIVE, OrderStatus.QUEUED, 
                               OrderStatus.MATCHING, OrderStatus.PARTIAL)
    
    @property
    def is_in_queue(self) -> bool:
        """是否在队列中"""
        return self.status in (OrderStatus.QUEUED, OrderStatus.PARTIAL)
    
    def fill(self, qty: int, trade_time: Optional[datetime] = None):
        """成交 qty 数量"""
        if qty <= 0 or qty > self.remaining_qty:
            raise ValueError(f"Invalid fill quantity: {qty}, remaining: {self.remaining_qty}")
        
        self.filled_qty += qty
        self.update_time = trade_time or datetime.now()
        
        if self.filled_qty >= self.quantity:
            self.status = OrderStatus.FILLED
            if self.queue_info:
                self.queue_info.leave_queue_time = self.update_time
        else:
            self.status = OrderStatus.PARTIAL
    
    def cancel(self):
        """撤销订单"""
        if self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            raise ValueError(f"Cannot cancel order with status: {self.status.value}")
        
        self.status = OrderStatus.CANCELLED
        self.update_time = datetime.now()
        if self.queue_info:
            self.queue_info.leave_queue_time = self.update_time
    
    def enter_queue(self, queue_length: int, queue_position: int):
        """进入队列"""
        now = datetime.now()
        self.queue_info = QueueInfo(
            queue_length_at_enter=queue_length,
            queue_position_at_enter=queue_position,
            current_queue_length=queue_length,
            current_queue_position=queue_position,
            enter_queue_time=now
        )
        self.status = OrderStatus.QUEUED
        self.update_time = now
    
    def update_queue_position(self, new_position: int, new_length: int):
        """更新队列位置（当队列被消耗时）"""
        if self.queue_info:
            self.queue_info.current_queue_position = new_position
            self.queue_info.current_queue_length = new_length
    
    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "price": str(self.price),
            "quantity": self.quantity,
            "filled_qty": self.filled_qty,
            "cancelled_qty": self.cancelled_qty,
            "remaining_qty": self.remaining_qty,
            "status": self.status.value,
            "order_type": self.order_type.value,
            "create_time": self.create_time.isoformat(),
            "update_time": self.update_time.isoformat(),
        }

        if self.reject_reason:
            result["reject_reason"] = self.reject_reason
        
        if self.queue_info:
            result["queue_info"] = {
                "queue_length_at_enter": self.queue_info.queue_length_at_enter,
                "queue_position_at_enter": self.queue_info.queue_position_at_enter,
                "current_queue_length": self.queue_info.current_queue_length,
                "current_queue_position": self.queue_info.current_queue_position,
                "enter_queue_time": self.queue_info.enter_queue_time.isoformat() if self.queue_info.enter_queue_time else None,
                "leave_queue_time": self.queue_info.leave_queue_time.isoformat() if self.queue_info.leave_queue_time else None,
                "queue_wait_ms": self._get_queue_wait_ms(),
            }
        
        if self.trades:
            result["trades"] = [t.to_dict() for t in self.trades]
        
        return result
    
    def _get_queue_wait_ms(self) -> Optional[int]:
        """获取队列等待时间（毫秒）"""
        if not self.queue_info or not self.queue_info.enter_queue_time:
            return None
        
        end_time = self.queue_info.leave_queue_time or datetime.now()
        delta = end_time - self.queue_info.enter_queue_time
        return int(delta.total_seconds() * 1000)


@dataclass
class TradeRecord:
    """成交记录"""
    trade_id: str
    order_id: str
    symbol: str
    side: str
    price: Decimal
    quantity: int
    trade_time: datetime
    match_source: str = "trade_event"  # trade_event / order_cross
    trigger_trade_id: Optional[str] = None  # 触发的逐笔成交ID
    fee: Decimal = Decimal("0")  # 该笔成交产生的手续费
    net_amount: Decimal = Decimal("0")  # 净额（买入为负，卖出为正）

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "price": str(self.price),
            "quantity": self.quantity,
            "trade_time": self.trade_time.isoformat(),
            "match_source": self.match_source,
            "trigger_trade_id": self.trigger_trade_id,
            "fee": str(self.fee),
            "net_amount": str(self.net_amount),
        }

"""行情参与者模块 - 多类市场参与者模拟

核心改进：
- 新增参与者类型：算法交易者(TWAP/VWAP)、止损交易者、订单簿不平衡交易者、冰山订单参与者
- 所有参与者增加虚拟账户 P&L 跟踪
- 增加订单簿感知能力（深度、不平衡度、流动性）
- 增加波动率感知
- 参与者之间通过共享市场状态实现策略互动
"""

import random
import math
from abc import ABC, abstractmethod
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Callable


# ─────────── 共享市场状态 ───────────

class SharedMarketState:
    """共享市场状态 - 参与者之间共享的信息"""

    def __init__(self):
        self.last_trades: List[Dict] = []  # 最近成交记录
        self.price_history: List[Decimal] = []  # 价格历史
        self.volatility_ewma: Decimal = Decimal("0.0001")  # EWMA 波动率
        self.order_flow_imbalance: float = 0.0  # 订单流不平衡度 [-1, 1]
        self.trade_volume_buys: int = 0  # 买入成交量
        self.trade_volume_sells: int = 0  # 卖出成交量
        self._max_history = 200

    def on_trade(self, trade: Dict):
        """记录成交，更新市场状态"""
        self.last_trades.append(trade)
        if len(self.last_trades) > self._max_history:
            self.last_trades.pop(0)

        price = Decimal(str(trade.get("price", 0)))
        if price > 0:
            self.price_history.append(price)
            if len(self.price_history) > self._max_history:
                self.price_history.pop(0)
            self._update_volatility()

        qty = trade.get("quantity", 0)
        side = trade.get("side", "")
        if side == "buy":
            self.trade_volume_buys += qty
        else:
            self.trade_volume_sells += qty

    def on_book_update(self, snapshot: Optional[Dict]):
        """更新订单簿状态"""
        if not snapshot:
            return
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        bid_depth = sum(b.get("total_quantity", 0) for b in bids)
        ask_depth = sum(a.get("total_quantity", 0) for a in asks)
        total = bid_depth + ask_depth
        if total > 0:
            self.order_flow_imbalance = (bid_depth - ask_depth) / total
        else:
            self.order_flow_imbalance = 0.0

    def _update_volatility(self):
        """更新 EWMA 波动率"""
        if len(self.price_history) < 2:
            return
        returns = (self.price_history[-1] - self.price_history[-2]) / self.price_history[-2]
        # EWMA: sigma²_t = 0.94 * sigma²_{t-1} + 0.06 * r²_t
        self.volatility_ewma = Decimal("0.94") * self.volatility_ewma + Decimal("0.06") * (returns ** 2)

    @property
    def current_volatility(self) -> Decimal:
        """当前年化波动率（简化）"""
        return self.volatility_ewma.sqrt() if self.volatility_ewma > 0 else Decimal("0.0001")

    @property
    def latest_price(self) -> Optional[Decimal]:
        return self.price_history[-1] if self.price_history else None


# 全局共享市场状态
_shared_market_state = SharedMarketState()


def get_shared_market_state() -> SharedMarketState:
    return _shared_market_state


def reset_shared_market_state():
    global _shared_market_state
    _shared_market_state = SharedMarketState()


# ─────────── 基类 ───────────

class MarketParticipant(ABC):
    """市场参与者基类（增强版）

    新增：
    - 虚拟账户（cash, position, pnl）用于 P&L 跟踪
    - 共享市场状态感知
    - 订单簿感知工具方法
    """

    def __init__(
        self,
        participant_id: str,
        symbol: str = "000001.SZ",
        base_price: float = 10.50,
        target_price: Optional[float] = None,
        order_interval: float = 0.5,
        quantity_range: Tuple[int, int] = (100, 1000),
        active: bool = True,
        initial_cash: float = 1000000.0,
        initial_position: int = 0,
    ):
        self.participant_id = participant_id
        self.symbol = symbol
        self.base_price = Decimal(str(base_price))
        self.target_price = Decimal(str(target_price)) if target_price else self.base_price
        self.order_interval = order_interval
        self.quantity_range = quantity_range
        self.active = active
        self._order_seq = 0
        self._trade_history: List[Dict] = []
        self._current_price = self.base_price
        self._pending_orders: List[Dict] = []

        # 虚拟账户（P&L 跟踪）
        self.cash = Decimal(str(initial_cash))
        self.position = initial_position
        self.frozen_cash = Decimal("0")
        self.frozen_position = 0
        self.total_fees = Decimal("0")
        self.total_trades = 0

    @abstractmethod
    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        pass

    @abstractmethod
    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        pass

    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"{self.participant_id}-{self._order_seq:06d}"

    def _random_quantity(self) -> int:
        q = random.randint(self.quantity_range[0] // 100, self.quantity_range[1] // 100) * 100
        return max(100, q)

    def _create_order(self, side: str, price: Decimal, quantity: int, visible_qty: Optional[int] = None) -> Dict:
        order = {
            "symbol": self.symbol,
            "side": side,
            "price": str(price.quantize(Decimal("0.01"))),
            "quantity": quantity,
            "order_id": self._next_order_id(),
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }
        if visible_qty is not None and visible_qty < quantity:
            order["iceberg"] = True
            order["visible_quantity"] = visible_qty
            order["hidden_quantity"] = quantity - visible_qty
        return order

    def on_order_filled(self, order_id: str, trade_info: Dict):
        self._trade_history.append(trade_info)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order_id]
        self._update_pnl(trade_info)
        # 更新共享市场状态
        get_shared_market_state().on_trade(trade_info)

    def on_order_queued(self, order_dict: Dict):
        self._pending_orders.append(order_dict)

    def _update_pnl(self, trade_info: Dict):
        """更新虚拟账户 P&L"""
        price = Decimal(str(trade_info.get("price", 0)))
        qty = trade_info.get("quantity", 0)
        side = trade_info.get("side", "")
        fee = Decimal(str(trade_info.get("fee", 0)))
        self.total_fees += fee
        self.total_trades += 1
        if side == "buy":
            cost = price * qty + fee
            self.cash -= cost
            self.position += qty
        else:
            revenue = price * qty - fee
            self.cash += revenue
            self.position -= qty

    @property
    def pnl(self) -> Decimal:
        """未实现 P&L = 当前持仓按最新价估值 + 现金 - 初始资金"""
        latest = get_shared_market_state().latest_price or self._current_price
        initial = Decimal(str(self._get_initial_value()))
        current = self.cash + latest * self.position
        return current - initial

    def _get_initial_value(self) -> float:
        # 基类默认，子类可覆盖
        return 1000000.0

    def get_stats(self) -> Dict:
        return {
            "participant_id": self.participant_id,
            "type": self.__class__.__name__,
            "active": self.active,
            "orders_sent": self._order_seq,
            "trades_executed": len(self._trade_history),
            "pending_orders": len(self._pending_orders),
            "cash": float(self.cash),
            "position": self.position,
            "pnl": float(self.pnl),
            "total_fees": float(self.total_fees),
            "total_trades": self.total_trades,
        }

    # ─────────── 订单簿感知工具 ───────────

    def _get_mid_price(self, snapshot: Optional[Dict]) -> Optional[Decimal]:
        if not snapshot:
            return None
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        if bids and asks:
            return (Decimal(str(bids[0]["price"])) + Decimal(str(asks[0]["price"]))) / 2
        elif bids:
            return Decimal(str(bids[0]["price"]))
        elif asks:
            return Decimal(str(asks[0]["price"]))
        return None

    def _get_spread(self, snapshot: Optional[Dict]) -> Optional[Decimal]:
        if not snapshot:
            return None
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        if bids and asks:
            return Decimal(str(asks[0]["price"])) - Decimal(str(bids[0]["price"]))
        return None

    def _get_depth(self, snapshot: Optional[Dict]) -> Tuple[int, int]:
        """返回 (买盘深度, 卖盘深度)"""
        if not snapshot:
            return 0, 0
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        bid_depth = sum(b.get("total_quantity", 0) for b in bids)
        ask_depth = sum(a.get("total_quantity", 0) for a in asks)
        return bid_depth, ask_depth

    def _get_imbalance(self, snapshot: Optional[Dict]) -> float:
        """返回订单簿不平衡度 [-1, 1]"""
        bid_depth, ask_depth = self._get_depth(snapshot)
        total = bid_depth + ask_depth
        if total == 0:
            return 0.0
        return (bid_depth - ask_depth) / total

    def _get_volatility(self) -> Decimal:
        return get_shared_market_state().current_volatility

    def _clamp_price(self, price: Decimal, snapshot: Optional[Dict]) -> Decimal:
        """将价格限制在合理范围内（基于市场规则）"""
        from ..core.market_rules import get_market_rules
        rules = get_market_rules(self.symbol)
        return rules.clamp_to_limit(price)


# ─────────── 现有参与者改进版 ───────────

class MarketMaker(MarketParticipant):
    """做市商（增强版）

    改进：
    - 根据订单簿深度动态调整 spread
    - 根据波动率调整报价激进程度
    - 高波动时扩大 spread，低波动时收窄
    """

    def __init__(self, depth: int = 5, spread: float = 0.02, cancel_prob: float = 0.05, **kwargs):
        super().__init__(**kwargs)
        self.depth = depth
        self.base_spread = Decimal(str(spread))
        self.cancel_prob = cancel_prob
        self._seeded = False
        self._last_quote_time = datetime.now()

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not self._seeded:
            self._seeded = True
            return self._seed_initial_book()

        # 动态调整 spread
        spread = self._adjust_spread(book_snapshot)

        bids = book_snapshot.get("bids", []) if book_snapshot else []
        asks = book_snapshot.get("asks", []) if book_snapshot else []
        best_bid = Decimal(str(bids[0]["price"])) if bids else self._current_price - spread
        best_ask = Decimal(str(asks[0]["price"])) if asks else self._current_price + spread
        self._current_price = (best_bid + best_ask) / 2

        bid_qty = sum(b["total_quantity"] for b in bids) if bids else 0
        ask_qty = sum(a["total_quantity"] for a in asks) if asks else 0

        if not asks or ask_qty < bid_qty * 0.3:
            side = "sell"
            price = best_ask + spread * random.randint(1, 3)
        elif not bids or bid_qty < ask_qty * 0.3:
            side = "buy"
            price = best_bid - spread * random.randint(1, 3)
        else:
            side = "buy" if bid_qty < ask_qty else "sell"
            if side == "buy":
                price = best_bid - spread * random.randint(1, 2)
            else:
                price = best_ask + spread * random.randint(1, 2)

        price = self._clamp_price(price, book_snapshot)
        return self._create_order(side, price, self._random_quantity() * 2)

    def _adjust_spread(self, snapshot: Optional[Dict]) -> Decimal:
        """根据波动率动态调整 spread"""
        vol = self._get_volatility()
        # 波动率越高，spread 越大
        multiplier = Decimal("1") + vol * Decimal("100")
        return self.base_spread * max(multiplier, Decimal("0.5"))

    def _seed_initial_book(self) -> Dict:
        side = random.choice(["buy", "sell"])
        if side == "buy":
            price = self._current_price - self.base_spread * random.randint(1, self.depth)
        else:
            price = self._current_price + self.base_spread * random.randint(1, self.depth)
        price = self._clamp_price(price, None)
        return self._create_order(side, price, self._random_quantity() * 2)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > self.cancel_prob or not self._pending_orders:
            return None
        order = random.choice(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order["order_id"]]
        return {
            "symbol": self.symbol,
            "side": order["side"],
            "price": order["price"],
            "quantity": random.randint(1, 5) * 100,
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }


class TrendFollower(MarketParticipant):
    """趋势跟踪者（增强版）

    改进：
    - 结合共享市场状态的价格历史
    - 根据波动率调整仓位大小
    - 高波动时减少仓位（风险控制）
    """

    def __init__(self, window_size: int = 10, momentum_threshold: float = 0.02, **kwargs):
        super().__init__(**kwargs)
        self.window_size = window_size
        self.momentum_threshold = Decimal(str(momentum_threshold))
        self._price_history: List[Decimal] = []

    def _update_price_history(self, book_snapshot: Optional[Dict]):
        if not book_snapshot:
            return
        bids = book_snapshot.get("bids", [])
        asks = book_snapshot.get("asks", [])
        if bids and asks:
            mid = (Decimal(str(bids[0]["price"])) + Decimal(str(asks[0]["price"]))) / 2
        elif bids or asks:
            mid = Decimal(str((bids or asks)[0]["price"]))
        else:
            mid = self._current_price
        self._price_history.append(mid)
        if len(self._price_history) > self.window_size * 2:
            self._price_history = self._price_history[-self.window_size * 2:]
        self._current_price = mid

    def _calculate_momentum(self) -> Decimal:
        if len(self._price_history) < self.window_size:
            return Decimal("0")
        recent = self._price_history[-1]
        past = self._price_history[-self.window_size]
        if past == 0:
            return Decimal("0")
        return (recent - past) / past

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        self._update_price_history(book_snapshot)
        momentum = self._calculate_momentum()

        if abs(momentum) < self.momentum_threshold:
            return None

        # 根据波动率调整仓位：高波动时减少
        vol = self._get_volatility()
        size_multiplier = max(1, int(3 - float(vol) * 500))  # 波动率越高，倍数越小

        if momentum > 0:
            side = "buy"
            offset = self._current_price * self.momentum_threshold * Decimal(str(random.uniform(2.0, 10.0)))
            price = self._current_price + offset
        else:
            side = "sell"
            offset = self._current_price * self.momentum_threshold * Decimal(str(random.uniform(2.0, 10.0)))
            price = self._current_price - offset

        price = self._clamp_price(price, book_snapshot)
        qty = self._random_quantity() * random.randint(1, max(1, size_multiplier))
        return self._create_order(side, price, qty)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > 0.02 or not self._pending_orders:
            return None
        order = random.choice(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order["order_id"]]
        return {
            "symbol": self.symbol,
            "side": order["side"],
            "price": order["price"],
            "quantity": random.randint(1, 3) * 100,
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }


class MeanReversionTrader(MarketParticipant):
    """均值回归者（增强版）

    改进：
    - 使用更长的历史窗口
    - 根据偏离程度调整仓位大小
    - 订单簿感知：在流动性充足时更激进
    """

    def __init__(self, ma_window: int = 20, deviation_threshold: float = 0.03, **kwargs):
        super().__init__(**kwargs)
        self.ma_window = ma_window
        self.deviation_threshold = Decimal(str(deviation_threshold))
        self._price_history: List[Decimal] = []

    def _update_price_history(self, book_snapshot: Optional[Dict]):
        if not book_snapshot:
            return
        bids = book_snapshot.get("bids", [])
        asks = book_snapshot.get("asks", [])
        if bids and asks:
            mid = (Decimal(str(bids[0]["price"])) + Decimal(str(asks[0]["price"]))) / 2
        elif bids or asks:
            mid = Decimal(str((bids or asks)[0]["price"]))
        else:
            mid = self._current_price
        self._price_history.append(mid)
        if len(self._price_history) > self.ma_window * 3:
            self._price_history = self._price_history[-self.ma_window * 3:]
        self._current_price = mid

    def _calculate_ma(self) -> Decimal:
        if len(self._price_history) < self.ma_window:
            return self._current_price
        recent = self._price_history[-self.ma_window:]
        return sum(recent) / len(recent)

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        self._update_price_history(book_snapshot)
        if len(self._price_history) < self.ma_window:
            return None

        ma = self._calculate_ma()
        deviation = (self._current_price - ma) / ma if ma != 0 else Decimal("0")

        if abs(deviation) < self.deviation_threshold:
            return None

        # 根据偏离程度调整仓位
        size_multiplier = min(5, int(abs(float(deviation)) / float(self.deviation_threshold)))

        # 订单簿感知：流动性充足时更激进（更靠近市场价）
        spread = self._get_spread(book_snapshot)
        liquidity_factor = Decimal("1") if spread and spread < Decimal("0.05") else Decimal("2")

        if deviation > 0:
            side = "sell"
            price = self._current_price - Decimal(str(random.uniform(0.01, 0.05))) / liquidity_factor
        else:
            side = "buy"
            price = self._current_price + Decimal(str(random.uniform(0.01, 0.05))) / liquidity_factor

        price = self._clamp_price(price, book_snapshot)
        qty = self._random_quantity() * max(1, size_multiplier)
        return self._create_order(side, price, qty)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > 0.05 or not self._pending_orders:
            return None
        order = random.choice(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order["order_id"]]
        return {
            "symbol": self.symbol,
            "side": order["side"],
            "price": order["price"],
            "quantity": random.randint(1, 3) * 100,
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }


class NoiseTrader(MarketParticipant):
    """噪声交易者/散户（增强版）

    改进：
    - 增加"追涨杀跌"行为（部分噪声交易者其实是趋势跟随的散户）
    - 高波动时更容易产生非理性价格
    - 增加小额订单比例
    """

    def __init__(self, irrational_prob: float = 0.05, cancel_prob: float = 0.15, **kwargs):
        super().__init__(**kwargs)
        self.irrational_prob = irrational_prob
        self.cancel_prob = cancel_prob

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if book_snapshot:
            bids = book_snapshot.get("bids", [])
            asks = book_snapshot.get("asks", [])
            if bids and asks:
                self._current_price = (Decimal(str(bids[0]["price"])) + Decimal(str(asks[0]["price"]))) / 2
            elif bids or asks:
                self._current_price = Decimal(str((bids or asks)[0]["price"]))

        side = random.choice(["buy", "sell"])

        # 追涨杀跌：有 30% 概率跟随最近趋势
        if random.random() < 0.3 and get_shared_market_state().price_history:
            trend = get_shared_market_state().price_history[-1] - get_shared_market_state().price_history[0] if len(get_shared_market_state().price_history) > 1 else Decimal("0")
            if trend > 0:
                side = "buy" if random.random() < 0.6 else "sell"
            else:
                side = "sell" if random.random() < 0.6 else "buy"

        # 高波动时更容易产生非理性价格
        vol = self._get_volatility()
        irrational_boost = float(vol) * 500  # 波动率越高，非理性概率越高
        effective_irrational = min(0.5, self.irrational_prob + irrational_boost)

        if random.random() < effective_irrational:
            if side == "buy":
                price = self._current_price * Decimal(str(random.uniform(1.03, 1.08)))
            else:
                price = self._current_price * Decimal(str(random.uniform(0.92, 0.97)))
        else:
            price = self._current_price + Decimal(str(random.uniform(-0.10, 0.10)))

        # 散户小额为主，偶尔大单
        low, high = self.quantity_range
        if random.random() < 0.9:
            qty = random.randint(max(100, low), min(500, high)) // 100 * 100
        else:
            qty = random.randint(low, high)
        qty = max(100, qty)

        price = self._clamp_price(price, book_snapshot)
        return self._create_order(side, price, qty)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > self.cancel_prob or not self._pending_orders:
            return None
        order = random.choice(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order["order_id"]]
        return {
            "symbol": self.symbol,
            "side": order["side"],
            "price": order["price"],
            "quantity": random.randint(1, 3) * 100,
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }


class AggressiveTrader(MarketParticipant):
    """激进交易者（增强版）

    改进：
    - 检测订单簿深度，计算价格冲击
    - 在流动性充足时更激进
    - 根据市场波动调整冲击频率
    """

    def __init__(self, burst_prob: float = 0.15, min_depth: int = 2000, **kwargs):
        super().__init__(**kwargs)
        self.burst_prob = burst_prob
        self.min_depth = min_depth
        self._cooldown = 0

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if self._cooldown > 0:
            self._cooldown -= 1
            return None

        if random.random() > self.burst_prob:
            return None

        if not book_snapshot:
            return None

        bids = book_snapshot.get("bids", [])
        asks = book_snapshot.get("asks", [])

        if not bids or not asks:
            return None

        total_bid = sum(b["total_quantity"] for b in bids)
        total_ask = sum(a["total_quantity"] for a in asks)

        # 根据波动率调整冲击深度：高波动时减少冲击
        vol = self._get_volatility()
        depth_factor = max(0.3, 1.0 - float(vol) * 200)
        effective_min_depth = int(self.min_depth * depth_factor)

        if total_bid > total_ask and total_bid > effective_min_depth:
            side = "sell"
            price = Decimal(str(bids[0]["price"])) - Decimal("0.01")
            qty = min(total_bid // 2, random.randint(20, 50) * 100)
        elif total_ask > effective_min_depth:
            side = "buy"
            price = Decimal(str(asks[0]["price"])) + Decimal("0.01")
            qty = min(total_ask // 2, random.randint(20, 50) * 100)
        else:
            return None

        self._cooldown = random.randint(3, 8)
        price = self._clamp_price(price, book_snapshot)
        return self._create_order(side, price, qty)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        return None


# ─────────── 新增参与者 ───────────

class AlgorithmicTrader(MarketParticipant):
    """算法交易者 - TWAP/VWAP 拆单执行

    策略：
    - 收到大单指令后，拆分成多个小单在多个 tick 中执行
    - 隐藏交易意图，避免对市场价格产生过大冲击
    - 根据市场波动调整拆单节奏
    """

    def __init__(self, algo_type: str = "twap", slice_count: int = 10, **kwargs):
        super().__init__(**kwargs)
        self.algo_type = algo_type  # "twap" 或 "vwap"
        self.slice_count = slice_count
        self._target_order: Optional[Dict] = None
        self._slices_remaining = 0
        self._slice_size = 0
        self._slice_side = ""
        self._slice_price = Decimal("0")
        self._tick_count = 0

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        # 随机生成新的大单目标（模拟收到算法交易指令）
        if self._target_order is None and random.random() < 0.05:
            self._start_new_algo_order(book_snapshot)

        if self._target_order is None or self._slices_remaining <= 0:
            return None

        self._tick_count += 1

        # TWAP: 均匀执行
        if self.algo_type == "twap":
            if self._tick_count % max(1, self.slice_count // 3) != 0:
                return None
        # VWAP: 根据成交量分布（简化：前多后少）
        else:
            expected_progress = 1 - (self._slices_remaining / self.slice_count)
            if random.random() > expected_progress * 2:
                return None

        # 根据波动率调整 slice 大小：高波动时减小
        vol = self._get_volatility()
        size_factor = max(0.5, 1.0 - float(vol) * 300)
        actual_qty = max(100, int(self._slice_size * size_factor) // 100 * 100)

        price = self._slice_price
        if book_snapshot:
            if self._slice_side == "buy":
                price = Decimal(str(book_snapshot.get("asks", [{}])[0].get("price", self._slice_price)))
            else:
                price = Decimal(str(book_snapshot.get("bids", [{}])[0].get("price", self._slice_price)))

        self._slices_remaining -= 1
        if self._slices_remaining <= 0:
            self._target_order = None

        price = self._clamp_price(price, book_snapshot)
        return self._create_order(self._slice_side, price, actual_qty)

    def _start_new_algo_order(self, book_snapshot: Optional[Dict]):
        if not book_snapshot:
            return
        mid = self._get_mid_price(book_snapshot)
        if mid is None:
            return

        side = random.choice(["buy", "sell"])
        total_qty = random.randint(50, 200) * 100
        self._slice_size = max(100, total_qty // self.slice_count // 100 * 100)
        self._slices_remaining = self.slice_count
        self._slice_side = side
        self._slice_price = mid
        self._tick_count = 0
        self._target_order = {
            "side": side,
            "total_quantity": total_qty,
            "price": mid,
        }

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        return None


class StopLossTrader(MarketParticipant):
    """止损/止盈交易者 - 条件触发自动交易

    策略：
    - 持有虚拟仓位，设置止损价和止盈价
    - 当价格触及止损/止盈时，立即市价平仓
    - 模拟真实投资者的止损/止盈行为
    """

    def __init__(self, stop_loss_pct: float = 0.03, take_profit_pct: float = 0.05, **kwargs):
        super().__init__(**kwargs)
        self.stop_loss_pct = Decimal(str(stop_loss_pct))
        self.take_profit_pct = Decimal(str(take_profit_pct))
        self._entry_price: Optional[Decimal] = None
        self._stop_price: Optional[Decimal] = None
        self._profit_price: Optional[Decimal] = None
        self._has_position = False

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not book_snapshot:
            return None

        mid = self._get_mid_price(book_snapshot)
        if mid is None:
            return None
        self._current_price = mid

        # 如果没有持仓，随机开仓
        if not self._has_position:
            if random.random() < 0.1:
                side = random.choice(["buy", "sell"])
                self._entry_price = mid
                self._stop_price = mid * (Decimal("1") - self.stop_loss_pct) if side == "buy" else mid * (Decimal("1") + self.stop_loss_pct)
                self._profit_price = mid * (Decimal("1") + self.take_profit_pct) if side == "buy" else mid * (Decimal("1") - self.take_profit_pct)
                self._has_position = True
                price = self._clamp_price(mid, book_snapshot)
                return self._create_order(side, price, self._random_quantity() * 3)
            return None

        # 检查止损/止盈
        # 假设是买入持仓，需要卖出平仓
        if self._entry_price and self._stop_price and self._profit_price:
            # 简单逻辑：假设持仓方向与开仓方向一致
            # 如果价格 <= 止损价 或 >= 止盈价，市价卖出
            if mid <= self._stop_price or mid >= self._profit_price:
                self._has_position = False
                self._entry_price = None
                price = self._clamp_price(mid, book_snapshot)
                return self._create_order("sell", price, self._random_quantity() * 3)

        return None

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        return None


class OrderBookImbalanceTrader(MarketParticipant):
    """订单簿不平衡交易者 - 利用订单簿深度预测短期价格

    策略：
    - 计算订单簿不平衡度 = (买盘深度 - 卖盘深度) / (买盘深度 + 卖盘深度)
    - 不平衡度 > 阈值 → 预期价格上涨 → 买入
    - 不平衡度 < -阈值 → 预期价格下跌 → 卖出
    - 结合订单流不平衡度（共享市场状态）提高预测准确度
    """

    def __init__(self, imbalance_threshold: float = 0.3, **kwargs):
        super().__init__(**kwargs)
        self.imbalance_threshold = imbalance_threshold

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not book_snapshot:
            return None

        # 订单簿不平衡度
        book_imbalance = self._get_imbalance(book_snapshot)
        # 共享市场状态的订单流不平衡度
        flow_imbalance = get_shared_market_state().order_flow_imbalance

        # 综合信号：两者同向时信号更强
        combined = book_imbalance * 0.6 + flow_imbalance * 0.4

        if abs(combined) < self.imbalance_threshold:
            return None

        if combined > 0:
            side = "buy"
            # 不平衡度越高，挂价越激进（更靠近卖一）
            asks = book_snapshot.get("asks", [])
            if asks:
                price = Decimal(str(asks[0]["price"])) + Decimal("0.01")
            else:
                price = self._current_price + Decimal("0.02")
        else:
            side = "sell"
            bids = book_snapshot.get("bids", [])
            if bids:
                price = Decimal(str(bids[0]["price"])) - Decimal("0.01")
            else:
                price = self._current_price - Decimal("0.02")

        price = self._clamp_price(price, book_snapshot)
        qty = self._random_quantity() * random.randint(2, 4)
        return self._create_order(side, price, qty)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > 0.03 or not self._pending_orders:
            return None
        order = random.choice(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order["order_id"]]
        return {
            "symbol": self.symbol,
            "side": order["side"],
            "price": order["price"],
            "quantity": random.randint(1, 3) * 100,
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }


class IcebergParticipant(MarketParticipant):
    """冰山订单参与者 - 隐藏大单真实数量

    策略：
    - 大单只显示小部分数量（如 10%）
    - 成交后自动补充显示数量
    - 模拟机构投资者的冰山订单行为
    """

    def __init__(self, visible_ratio: float = 0.1, **kwargs):
        super().__init__(**kwargs)
        self.visible_ratio = visible_ratio
        self._iceberg_orders: Dict[str, Dict] = {}  # order_id -> 冰山订单信息

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not book_snapshot or random.random() > 0.08:
            return None

        mid = self._get_mid_price(book_snapshot)
        if mid is None:
            return None

        side = random.choice(["buy", "sell"])
        total_qty = random.randint(50, 200) * 100
        visible_qty = max(100, int(total_qty * self.visible_ratio) // 100 * 100)

        if side == "buy":
            price = mid - Decimal(str(random.uniform(0.01, 0.05)))
        else:
            price = mid + Decimal(str(random.uniform(0.01, 0.05)))

        price = self._clamp_price(price, book_snapshot)
        order = self._create_order(side, price, total_qty, visible_qty)
        self._iceberg_orders[order["order_id"]] = {
            "total_qty": total_qty,
            "visible_qty": visible_qty,
            "filled_qty": 0,
        }
        return order

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > 0.02 or not self._pending_orders:
            return None
        order = random.choice(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order["order_id"]]
        self._iceberg_orders.pop(order["order_id"], None)
        return {
            "symbol": self.symbol,
            "side": order["side"],
            "price": order["price"],
            "quantity": random.randint(1, 3) * 100,
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }


# ─────────── 注册表 ───────────

class ParticipantRegistry:
    """参与者注册表（增强版）

    新增：
    - 支持算法交易者、止损交易者、订单簿不平衡交易者、冰山订单参与者
    - 共享市场状态初始化
    """

    def __init__(self, symbol: str = "000001.SZ", base_price: float = 10.50):
        self.symbol = symbol
        self.base_price = base_price
        self._participants: Dict[str, MarketParticipant] = {}
        self._config = {
            "market_maker_count": 2,
            "trend_follower_count": 1,
            "mean_reversion_count": 1,
            "noise_trader_count": 3,
            "aggressive_trader_count": 1,
            "algorithmic_trader_count": 1,
            "stop_loss_trader_count": 1,
            "order_book_imbalance_count": 1,
            "iceberg_participant_count": 1,
            "target_price": base_price,
            "order_interval": 0.2,
        }
        self._build_default_participants()

    def _build_default_participants(self):
        old = self._participants.copy()
        self._participants.clear()
        target = self._config.get("target_price", self.base_price)
        base_interval = self._config.get("order_interval", 0.2)

        def _get_old_attr(pid: str, attr: str, default):
            p = old.get(pid)
            return getattr(p, attr, default) if p else default

        # 做市商
        for i in range(self._config.get("market_maker_count", 2)):
            pid = f"MM-{i+1}"
            p = MarketMaker(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (0.8 + i * 0.2),
                depth=_get_old_attr(pid, "depth", 5),
                spread=_get_old_attr(pid, "base_spread", Decimal("0.02")),
            )
            self._participants[pid] = p

        # 趋势跟踪者
        for i in range(self._config.get("trend_follower_count", 1)):
            pid = f"TF-{i+1}"
            p = TrendFollower(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.5 + i * 0.5),
                window_size=_get_old_attr(pid, "window_size", 10),
                momentum_threshold=_get_old_attr(pid, "momentum_threshold", Decimal("0.02")),
            )
            self._participants[pid] = p

        # 均值回归者
        for i in range(self._config.get("mean_reversion_count", 1)):
            pid = f"MR-{i+1}"
            p = MeanReversionTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.2 + i * 0.3),
                ma_window=_get_old_attr(pid, "ma_window", 20),
                deviation_threshold=_get_old_attr(pid, "deviation_threshold", Decimal("0.03")),
            )
            self._participants[pid] = p

        # 噪声交易者
        for i in range(self._config.get("noise_trader_count", 3)):
            pid = f"NT-{i+1}"
            p = NoiseTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (0.8 + i * 0.2),
                irrational_prob=_get_old_attr(pid, "irrational_prob", 0.05),
                cancel_prob=_get_old_attr(pid, "cancel_prob", 0.15),
            )
            self._participants[pid] = p

        # 激进交易者
        for i in range(self._config.get("aggressive_trader_count", 1)):
            pid = f"AT-{i+1}"
            p = AggressiveTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (2.0 + i * 0.5),
                burst_prob=_get_old_attr(pid, "burst_prob", 0.15),
                min_depth=_get_old_attr(pid, "min_depth", 2000),
            )
            self._participants[pid] = p

        # 算法交易者
        for i in range(self._config.get("algorithmic_trader_count", 1)):
            pid = f"ALGO-{i+1}"
            p = AlgorithmicTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.0 + i * 0.3),
                algo_type="twap" if i % 2 == 0 else "vwap",
                slice_count=_get_old_attr(pid, "slice_count", 10),
            )
            self._participants[pid] = p

        # 止损交易者
        for i in range(self._config.get("stop_loss_trader_count", 1)):
            pid = f"SL-{i+1}"
            p = StopLossTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.5 + i * 0.5),
                stop_loss_pct=_get_old_attr(pid, "stop_loss_pct", 0.03),
                take_profit_pct=_get_old_attr(pid, "take_profit_pct", 0.05),
            )
            self._participants[pid] = p

        # 订单簿不平衡交易者
        for i in range(self._config.get("order_book_imbalance_count", 1)):
            pid = f"OBI-{i+1}"
            p = OrderBookImbalanceTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.0 + i * 0.2),
                imbalance_threshold=_get_old_attr(pid, "imbalance_threshold", 0.3),
            )
            self._participants[pid] = p

        # 冰山订单参与者
        for i in range(self._config.get("iceberg_participant_count", 1)):
            pid = f"ICE-{i+1}"
            p = IcebergParticipant(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.5 + i * 0.3),
                visible_ratio=_get_old_attr(pid, "visible_ratio", 0.1),
            )
            self._participants[pid] = p

    def update_config(self, config: Dict):
        self._config.update(config)
        rebuild = False
        for key in ["market_maker_count", "trend_follower_count", "mean_reversion_count",
                    "noise_trader_count", "aggressive_trader_count", "algorithmic_trader_count",
                    "stop_loss_trader_count", "order_book_imbalance_count", "iceberg_participant_count",
                    "target_price"]:
            if key in config:
                rebuild = True
                break
        if rebuild:
            self._build_default_participants()
        else:
            for p in self._participants.values():
                if "target_price" in config:
                    p.target_price = Decimal(str(config["target_price"]))
                if "active" in config:
                    p.active = config["active"]
                if "order_interval" in config:
                    p.order_interval = config["order_interval"]

    def get_config(self) -> Dict:
        return self._config.copy()

    def get_participants(self) -> List[MarketParticipant]:
        return list(self._participants.values())

    def get_participant(self, participant_id: str) -> Optional[MarketParticipant]:
        return self._participants.get(participant_id)

    def get_all_stats(self) -> List[Dict]:
        return [p.get_stats() for p in self._participants.values()]

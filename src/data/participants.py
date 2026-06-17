"""行情参与者模块 - 多类市场参与者模拟"""

import random
import asyncio
from abc import ABC, abstractmethod
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Callable


class MarketParticipant(ABC):
    """市场参与者基类"""

    def __init__(
        self,
        participant_id: str,
        symbol: str = "000001.SZ",
        base_price: float = 10.50,
        target_price: Optional[float] = None,
        order_interval: float = 0.5,
        quantity_range: Tuple[int, int] = (100, 1000),
        active: bool = True,
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
        self._pending_orders: List[Dict] = []  # 参与者自己维护的订单记录

    @abstractmethod
    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        """生成下一笔委托，返回 None 表示不生成"""
        pass

    @abstractmethod
    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        """生成撤单，返回 None 表示不撤单"""
        pass

    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"{self.participant_id}-{self._order_seq:06d}"

    def _random_quantity(self) -> int:
        """生成随机数量，对齐到 100"""
        q = random.randint(self.quantity_range[0] // 100, self.quantity_range[1] // 100) * 100
        return max(100, q)

    def _create_order(self, side: str, price: Decimal, quantity: int) -> Dict:
        return {
            "symbol": self.symbol,
            "side": side,
            "price": str(price.quantize(Decimal("0.01"))),
            "quantity": quantity,
            "order_id": self._next_order_id(),
            "timestamp": datetime.now().isoformat(),
            "participant_id": self.participant_id,
        }

    def on_order_filled(self, order_id: str, trade_info: Dict):
        """回调：当参与者自己的订单成交时"""
        self._trade_history.append(trade_info)
        # 从 pending 中移除
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order_id]

    def on_order_queued(self, order_dict: Dict):
        """回调：当订单进入队列时"""
        self._pending_orders.append(order_dict)

    def get_stats(self) -> Dict:
        return {
            "participant_id": self.participant_id,
            "type": self.__class__.__name__,
            "active": self.active,
            "orders_sent": self._order_seq,
            "trades_executed": len(self._trade_history),
            "pending_orders": len(self._pending_orders),
        }


class MarketMaker(MarketParticipant):
    """做市商 - 双向挂盘提供流动性，目标维持盘口深度

    策略：
    - 在基准价格两侧各挂 3-5 档，维持盘口深度
    - 当价差过大时主动收窄价差
    - 当一侧深度不足时补充流动性
    - 小概率撤单（模拟真实做市商调整）
    """

    def __init__(self, depth: int = 5, spread: float = 0.02, cancel_prob: float = 0.05, **kwargs):
        super().__init__(**kwargs)
        self.depth = depth
        self.spread = Decimal(str(spread))
        self.cancel_prob = cancel_prob
        self._seeded = False

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not self._seeded:
            # 首次启动时批量注入初始流动性
            self._seeded = True
            return self._seed_initial_book()

        # 获取当前盘口状态
        bids = book_snapshot.get("bids", []) if book_snapshot else []
        asks = book_snapshot.get("asks", []) if book_snapshot else []
        best_bid = Decimal(str(bids[0]["price"])) if bids else self._current_price - self.spread
        best_ask = Decimal(str(asks[0]["price"])) if asks else self._current_price + self.spread
        self._current_price = (best_bid + best_ask) / 2

        # 优先补充缺失的一侧
        bid_qty = sum(b["total_quantity"] for b in bids) if bids else 0
        ask_qty = sum(a["total_quantity"] for a in asks) if asks else 0

        if not asks or ask_qty < bid_qty * 0.3:
            side = "sell"
            price = best_ask + self.spread * random.randint(1, 3)
        elif not bids or bid_qty < ask_qty * 0.3:
            side = "buy"
            price = best_bid - self.spread * random.randint(1, 3)
        else:
            # 两侧都充足时，随机补充较薄弱的一侧
            side = "buy" if bid_qty < ask_qty else "sell"
            if side == "buy":
                price = best_bid - self.spread * random.randint(1, 2)
            else:
                price = best_ask + self.spread * random.randint(1, 2)

        return self._create_order(side, price, self._random_quantity())

    def _seed_initial_book(self) -> Dict:
        """注入初始双边流动性"""
        side = random.choice(["buy", "sell"])
        if side == "buy":
            price = self._current_price - self.spread * random.randint(1, self.depth)
        else:
            price = self._current_price + self.spread * random.randint(1, self.depth)
        return self._create_order(side, price, self._random_quantity() * 2)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > self.cancel_prob or not self._pending_orders:
            return None
        # 撤掉自己的一个随机订单，并立即从 pending 中移除
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
    """趋势跟踪者 - 跟随价格趋势，顺势交易

    策略：
    - 维护价格移动窗口，计算短期趋势方向
    - 趋势向上时，主动买入（挂高价或市价）
    - 趋势向下时，主动卖出（挂低价或市价）
    - 在突破关键价位时增加仓位
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
        elif bids:
            mid = Decimal(str(bids[0]["price"]))
        elif asks:
            mid = Decimal(str(asks[0]["price"]))
        else:
            mid = self._current_price
        self._price_history.append(mid)
        if len(self._price_history) > self.window_size * 2:
            self._price_history = self._price_history[-self.window_size * 2:]
        self._current_price = mid

    def _calculate_momentum(self) -> Decimal:
        """计算价格动量 = (最新 - 窗口前) / 窗口前"""
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
            return None  # 趋势不够明显，不交易

        if momentum > 0:
            # 上涨趋势，买入
            side = "buy"
            # 越强的趋势挂越高的价格（更激进），但偏移控制在价格的 0.5%~3% 以内
            offset = self._current_price * self.momentum_threshold * Decimal(str(random.uniform(2.0, 10.0)))
            price = self._current_price + offset
        else:
            # 下跌趋势，卖出
            side = "sell"
            offset = self._current_price * self.momentum_threshold * Decimal(str(random.uniform(2.0, 10.0)))
            price = self._current_price - offset

        # 大单
        qty = self._random_quantity() * random.randint(2, 5)
        return self._create_order(side, price, qty)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        # 趋势跟踪者很少撤单
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
    """均值回归者 - 价格偏离均线时反向交易

    策略：
    - 维护价格移动平均
    - 当价格高于均线一定比例时，卖出（预期价格回落）
    - 当价格低于均线一定比例时，买入（预期价格反弹）
    - 挂单更激进（逆势），吃盘意愿更强
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

        if deviation > 0:
            # 价格高于均线，卖出（预期回落）
            side = "sell"
            # 价格越高，挂的卖价越低（更想成交）
            price = self._current_price - Decimal(str(random.uniform(0.01, 0.05)))
        else:
            # 价格低于均线，买入（预期反弹）
            side = "buy"
            price = self._current_price + Decimal(str(random.uniform(0.01, 0.05)))

        return self._create_order(side, price, self._random_quantity())

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
    """噪声交易者/散户 - 随机交易，模拟市场噪声

    策略：
    - 随机方向（买卖概率各 50%）
    - 随机价格（在当前价附近随机波动）
    - 随机数量（小额为主，偶尔大单）
    - 高撤单概率（模拟散户频繁改单）
    - 偶尔产生非理性极端价格订单
    """

    def __init__(self, irrational_prob: float = 0.05, cancel_prob: float = 0.15, **kwargs):
        super().__init__(**kwargs)
        self.irrational_prob = irrational_prob
        self.cancel_prob = cancel_prob

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        # 更新当前价格
        if book_snapshot:
            bids = book_snapshot.get("bids", [])
            asks = book_snapshot.get("asks", [])
            if bids and asks:
                self._current_price = (Decimal(str(bids[0]["price"])) + Decimal(str(asks[0]["price"]))) / 2
            elif bids or asks:
                self._current_price = Decimal(str((bids or asks)[0]["price"]))

        side = random.choice(["buy", "sell"])

        # 偶尔产生非理性价格（涨停/跌停附近）
        if random.random() < self.irrational_prob:
            if side == "buy":
                price = self._current_price * Decimal(str(random.uniform(1.03, 1.08)))
            else:
                price = self._current_price * Decimal(str(random.uniform(0.92, 0.97)))
        else:
            # 正常随机波动
            price = self._current_price + Decimal(str(random.uniform(-0.10, 0.10)))

        # 散户小额为主，偶尔大单，但尊重 quantity_range
        low, high = self.quantity_range
        if random.random() < 0.9:
            qty = random.randint(max(100, low), min(500, high)) // 100 * 100
        else:
            qty = random.randint(low, high)
        qty = max(100, qty)

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
    """激进交易者 - 大单主动吃盘，推动价格快速变化

    策略：
    - 检测盘口深度
    - 当一侧深度足够时，生成大单直接吃掉对方最优 N 档
    - 推动价格快速变化，测试系统撮合压力
    - 间歇性交易（不连续下单，模拟大单冲击）
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

        # 选择深度更大的一侧吃掉
        if total_bid > total_ask and total_bid > self.min_depth:
            side = "sell"
            # 卖价低于最优买价，主动成交
            price = Decimal(str(bids[0]["price"])) - Decimal("0.01")
            qty = min(total_bid // 2, random.randint(20, 50) * 100)
        elif total_ask > self.min_depth:
            side = "buy"
            price = Decimal(str(asks[0]["price"])) + Decimal("0.01")
            qty = min(total_ask // 2, random.randint(20, 50) * 100)
        else:
            return None

        self._cooldown = random.randint(3, 8)  # 冷却 N 轮
        return self._create_order(side, price, qty)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        # 激进交易者很少撤单，他们的目标是立即成交
        return None


class ParticipantRegistry:
    """参与者注册表 - 管理和协调多个参与者"""

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
            "target_price": base_price,
            "order_interval": 0.2,
        }
        self._build_default_participants()

    def _build_default_participants(self):
        """根据配置构建默认参与者

        重建时优先复用已有同 ID 参与者的非关键参数，避免用户每次更新配置后
        其他策略参数被随机重置。
        """
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
                participant_id=pid,
                symbol=self.symbol,
                base_price=target,
                target_price=float(target),
                order_interval=base_interval * (0.8 + i * 0.2),
                depth=_get_old_attr(pid, "depth", 5),
                spread=_get_old_attr(pid, "spread", Decimal("0.02")),
            )
            self._participants[pid] = p

        # 趋势跟踪者
        for i in range(self._config.get("trend_follower_count", 1)):
            pid = f"TF-{i+1}"
            p = TrendFollower(
                participant_id=pid,
                symbol=self.symbol,
                base_price=target,
                target_price=float(target),
                order_interval=base_interval * (1.5 + i * 0.5),
                window_size=_get_old_attr(pid, "window_size", 10),
                momentum_threshold=_get_old_attr(pid, "momentum_threshold", Decimal("0.02")),
            )
            self._participants[pid] = p

        # 均值回归者
        for i in range(self._config.get("mean_reversion_count", 1)):
            pid = f"MR-{i+1}"
            p = MeanReversionTrader(
                participant_id=pid,
                symbol=self.symbol,
                base_price=target,
                target_price=float(target),
                order_interval=base_interval * (1.2 + i * 0.3),
                ma_window=_get_old_attr(pid, "ma_window", 20),
                deviation_threshold=_get_old_attr(pid, "deviation_threshold", Decimal("0.03")),
            )
            self._participants[pid] = p

        # 噪声交易者
        for i in range(self._config.get("noise_trader_count", 3)):
            pid = f"NT-{i+1}"
            p = NoiseTrader(
                participant_id=pid,
                symbol=self.symbol,
                base_price=target,
                target_price=float(target),
                order_interval=base_interval * (0.8 + i * 0.2),
                irrational_prob=_get_old_attr(pid, "irrational_prob", 0.05),
                cancel_prob=_get_old_attr(pid, "cancel_prob", 0.15),
            )
            self._participants[pid] = p

        # 激进交易者
        for i in range(self._config.get("aggressive_trader_count", 1)):
            pid = f"AT-{i+1}"
            p = AggressiveTrader(
                participant_id=pid,
                symbol=self.symbol,
                base_price=target,
                target_price=float(target),
                order_interval=base_interval * (2.0 + i * 0.5),
                burst_prob=_get_old_attr(pid, "burst_prob", 0.15),
                min_depth=_get_old_attr(pid, "min_depth", 2000),
            )
            self._participants[pid] = p

    def update_config(self, config: Dict):
        """更新配置并重建参与者"""
        self._config.update(config)
        # 如果关键参数变化，重建参与者
        rebuild = False
        for key in ["market_maker_count", "trend_follower_count", "mean_reversion_count",
                    "noise_trader_count", "aggressive_trader_count", "target_price"]:
            if key in config:
                rebuild = True
                break
        if rebuild:
            self._build_default_participants()
        else:
            # 只更新现有参与者的参数
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

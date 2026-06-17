"""行情参与者模块 - 多类市场参与者模拟

核心设计原则：
1. 做市商必须盈利：双边报价 + inventory 管理，确保买低卖高
2. 参与者需要真正影响行情：有价格引领者（主观方向交易者）、筹码收集者、日内做T者
3. 所有参与者都有合理的虚拟账户管理，盈亏可解释
"""

import random
from abc import ABC, abstractmethod
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, List, Tuple


# ─────────── 共享市场状态 ───────────

class SharedMarketState:
    """共享市场状态 - 参与者之间共享的信息"""

    def __init__(self):
        self.last_trades: List[Dict] = []
        self.price_history: List[Decimal] = []
        self.volatility_ewma: Decimal = Decimal("0.0001")
        self.order_flow_imbalance: float = 0.0
        self.trade_volume_buys: int = 0
        self.trade_volume_sells: int = 0
        self._max_history = 200

    def on_trade(self, trade: Dict):
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
        total_vol = self.trade_volume_buys + self.trade_volume_sells
        if total_vol > 1_000_000:
            self.trade_volume_buys //= 2
            self.trade_volume_sells //= 2

    def on_book_update(self, snapshot: Optional[Dict]):
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
        if len(self.price_history) < 2:
            return
        returns = (self.price_history[-1] - self.price_history[-2]) / self.price_history[-2]
        self.volatility_ewma = Decimal("0.94") * self.volatility_ewma + Decimal("0.06") * (returns ** 2)

    @property
    def current_volatility(self) -> Decimal:
        return self.volatility_ewma.sqrt() if self.volatility_ewma > 0 else Decimal("0.0001")

    @property
    def latest_price(self) -> Optional[Decimal]:
        return self.price_history[-1] if self.price_history else None


_shared_market_states: Dict[str, SharedMarketState] = {}


def get_shared_market_state(symbol: str) -> SharedMarketState:
    if symbol not in _shared_market_states:
        _shared_market_states[symbol] = SharedMarketState()
    return _shared_market_states[symbol]


def reset_shared_market_state(symbol: Optional[str] = None):
    global _shared_market_states
    if symbol is None:
        _shared_market_states = {}
    else:
        _shared_market_states.pop(symbol, None)


# ─────────── 基类 ───────────

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
        self._max_trade_history = 500
        self._current_price = self.base_price
        self._pending_orders: List[Dict] = []
        self._max_pending_orders = 200
        self._pnl_history: List[Decimal] = []

        self.initial_cash = Decimal(str(initial_cash))
        self.initial_position = initial_position
        self.initial_price = self.base_price
        self.cash = self.initial_cash
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
        filled_qty = sum(
            t.get("quantity", 0) for t in self._trade_history if t.get("order_id") == order_id
        )
        pending_order = next((o for o in self._pending_orders if o["order_id"] == order_id), None)
        total_qty = pending_order["quantity"] if pending_order else trade_info.get("quantity", 0)
        if filled_qty >= total_qty:
            self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order_id]
        self._update_pnl(trade_info)
        get_shared_market_state(self.symbol).on_trade(trade_info)
        self._pnl_history.append(self.pnl)
        if len(self._pnl_history) > self._max_trade_history:
            self._pnl_history = self._pnl_history[-self._max_trade_history // 2:]
        if len(self._trade_history) > self._max_trade_history:
            self._trade_history = self._trade_history[-self._max_trade_history // 2:]

    def on_order_queued(self, order_dict: Dict):
        self._pending_orders.append(order_dict)
        if len(self._pending_orders) > self._max_pending_orders:
            self._pending_orders = self._pending_orders[-self._max_pending_orders // 2:]

    def _update_pnl(self, trade_info: Dict):
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
        latest = get_shared_market_state(self.symbol).latest_price or self._current_price
        initial = self._get_initial_value()
        current = self.cash + latest * self.position
        return current - initial

    def _get_initial_value(self) -> Decimal:
        return self.initial_cash + self.initial_price * self.initial_position

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
        if not snapshot:
            return 0, 0
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        bid_depth = sum(b.get("total_quantity", 0) for b in bids)
        ask_depth = sum(a.get("total_quantity", 0) for a in asks)
        return bid_depth, ask_depth

    def _get_imbalance(self, snapshot: Optional[Dict]) -> float:
        bid_depth, ask_depth = self._get_depth(snapshot)
        total = bid_depth + ask_depth
        if total == 0:
            return 0.0
        return (bid_depth - ask_depth) / total

    def _get_volatility(self) -> Decimal:
        return get_shared_market_state(self.symbol).current_volatility

    def _clamp_price(self, price: Decimal, snapshot: Optional[Dict]) -> Decimal:
        from ..core.market_rules import get_market_rules
        rules = get_market_rules(self.symbol)
        price = rules.clamp_to_limit(price)
        if snapshot is not None:
            bids = snapshot.get("bids", [])
            asks = snapshot.get("asks", [])
            benchmark = None
            if bids or asks:
                best_bid = Decimal(str(bids[0]["price"])) if bids else None
                best_ask = Decimal(str(asks[0]["price"])) if asks else None
                if best_bid is not None and best_ask is not None:
                    benchmark = (best_bid + best_ask) / 2
                elif best_bid is not None:
                    benchmark = best_bid
                elif best_ask is not None:
                    benchmark = best_ask
            if benchmark is not None:
                lower, upper = rules.get_price_cage_bounds(benchmark)
                if price > upper:
                    price = upper
                elif price < lower:
                    price = lower
        return price


# ─────────── 1. 做市商（盈利版） ───────────

class MarketMaker(MarketParticipant):
    """做市商 - 双边报价 + inventory 管理，确保买低卖高

    核心策略：
    - 同时在买价和卖价挂单，买价 < 卖价，通过 spread 获利
    - 维护 inventory（净持仓），inventory 为正时降低买价/提高卖价，inventory 为负时提高买价/降低卖价
    - 只在盘口附近报价，不做远离市场的挂单
    - 通过频繁双边报价和成交累积价差利润
    """

    def __init__(self, spread: float = 0.02, inventory_skew: float = 0.5, max_inventory: int = 5000, **kwargs):
        super().__init__(**kwargs)
        self.base_spread = Decimal(str(spread))
        self.inventory_skew = Decimal(str(inventory_skew))
        self.max_inventory = max_inventory
        self._last_side = "buy"  # 交替报价
        self._seeded = False

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not self._seeded:
            self._seeded = True
            return self._seed_initial_book()

        if not book_snapshot:
            return None

        bids = book_snapshot.get("bids", [])
        asks = book_snapshot.get("asks", [])
        if not bids and not asks:
            return None

        best_bid = Decimal(str(bids[0]["price"])) if bids else self._current_price - self.base_spread
        best_ask = Decimal(str(asks[0]["price"])) if asks else self._current_price + self.base_spread
        mid = (best_bid + best_ask) / 2
        self._current_price = mid

        # 根据 inventory 调整报价
        # inventory > 0: 持有多头，需要卖出，降低 bid（减少买入）、降低 ask（更容易卖出）
        # inventory < 0: 持有空头，需要买入，提高 bid（更容易买入）、提高 ask（减少卖出）
        inventory_skew = Decimal(str(self.position)) * self.inventory_skew / Decimal(str(self.max_inventory))
        inventory_skew = max(Decimal("-1"), min(Decimal("1"), inventory_skew))

        # 动态 spread：波动率越高 spread 越大
        vol = self._get_volatility()
        spread = self.base_spread * (Decimal("1") + vol * Decimal("100"))
        spread = max(spread, Decimal("0.02"))

        # 交替生成 buy 和 sell 订单，确保双边都存在
        if self._last_side == "buy":
            side = "sell"
            self._last_side = "sell"
        else:
            side = "buy"
            self._last_side = "buy"

        # 确保 bid < ask，且做市商的买价低于卖价
        if side == "buy":
            # 买入价 = mid - spread/2 - inventory_skew * spread/2
            # inventory 为正时，skew 为负，买入价更低（更不愿意买）
            price = mid - spread / Decimal("2") - inventory_skew * spread / Decimal("2")
            # 确保买入价不超过 best_bid（比当前最高买价略低或相等，确保在盘口）
            price = min(price, best_bid - Decimal("0.01"))
            # 确保买入价低于 mid
            price = min(price, mid - Decimal("0.01"))
        else:
            # 卖出价 = mid + spread/2 - inventory_skew * spread/2
            # inventory 为正时，skew 为负，卖出价更低（更愿意卖）
            price = mid + spread / Decimal("2") - inventory_skew * spread / Decimal("2")
            # 确保卖出价不低于 best_ask（比当前最低卖价略高或相等，确保在盘口）
            price = max(price, best_ask + Decimal("0.01"))
            # 确保卖出价高于 mid
            price = max(price, mid + Decimal("0.01"))

        # 最终检查：确保 buy < sell（相对于自己的报价）
        price = self._clamp_price(price, book_snapshot)

        # 根据 inventory 调整数量：inventory 高时减少买入量、增加卖出量
        if side == "buy":
            if self.position > self.max_inventory:
                return None  # 库存太多，暂停买入
            qty = self._random_quantity()
            if self.position > 0:
                qty = max(100, qty // 2)  # 库存偏多时减少买入
        else:
            if self.position < -self.max_inventory:
                return None  # 空头太多，暂停卖出
            qty = self._random_quantity()
            if self.position < 0:
                qty = max(100, qty // 2)  # 空头偏多时减少卖出

        return self._create_order(side, price, qty)

    def _seed_initial_book(self) -> Dict:
        side = random.choice(["buy", "sell"])
        if side == "buy":
            price = self._current_price - self.base_spread * Decimal(str(random.uniform(0.5, 1.5)))
        else:
            price = self._current_price + self.base_spread * Decimal(str(random.uniform(0.5, 1.5)))
        price = self._clamp_price(price, None)
        return self._create_order(side, price, self._random_quantity() * 2)

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        # 做市商极少撤单，只在订单过多时撤
        if random.random() > 0.02 or not self._pending_orders:
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


# ─────────── 2. 趋势跟踪者 ───────────

class TrendFollower(MarketParticipant):
    """趋势跟踪者"""

    def __init__(self, window_size: int = 10, momentum_threshold: float = 0.02, **kwargs):
        super().__init__(**kwargs)
        self.window_size = window_size
        self.momentum_threshold = Decimal(str(momentum_threshold))
        self._price_history: List[Decimal] = []

    def _update_price_history(self, book_snapshot: Optional[Dict]):
        if not book_snapshot:
            return
        mid = self._get_mid_price(book_snapshot)
        if mid is not None:
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
        vol = self._get_volatility()
        size_multiplier = max(1, int(3 - float(vol) * 500))
        if momentum > 0:
            side = "buy"
            price = self._current_price * (Decimal("1") + momentum * Decimal(str(random.uniform(1.0, 2.0))))
        else:
            side = "sell"
            price = self._current_price * (Decimal("1") + momentum * Decimal(str(random.uniform(1.0, 2.0))))
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


# ─────────── 3. 均值回归者 ───────────

class MeanReversionTrader(MarketParticipant):
    """均值回归者"""

    def __init__(self, ma_window: int = 20, deviation_threshold: float = 0.03, **kwargs):
        super().__init__(**kwargs)
        self.ma_window = ma_window
        self.deviation_threshold = Decimal(str(deviation_threshold))
        self._price_history: List[Decimal] = []

    def _update_price_history(self, book_snapshot: Optional[Dict]):
        if not book_snapshot:
            return
        mid = self._get_mid_price(book_snapshot)
        if mid is not None:
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
        size_multiplier = min(5, int(abs(float(deviation)) / float(self.deviation_threshold)))
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


# ─────────── 4. 噪声交易者 ───────────

class NoiseTrader(MarketParticipant):
    """噪声交易者/散户"""

    def __init__(self, irrational_prob: float = 0.05, cancel_prob: float = 0.15, **kwargs):
        super().__init__(**kwargs)
        self.irrational_prob = irrational_prob
        self.cancel_prob = cancel_prob

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if book_snapshot:
            mid = self._get_mid_price(book_snapshot)
            if mid is not None:
                self._current_price = mid
        side = random.choice(["buy", "sell"])
        sms = get_shared_market_state(self.symbol)
        if random.random() < 0.3 and sms.price_history:
            trend = sms.price_history[-1] - sms.price_history[0] if len(sms.price_history) > 1 else Decimal("0")
            if trend > 0:
                side = "buy" if random.random() < 0.6 else "sell"
            else:
                side = "sell" if random.random() < 0.6 else "buy"
        vol = self._get_volatility()
        irrational_boost = float(vol) * 500
        effective_irrational = min(0.5, self.irrational_prob + irrational_boost)
        if random.random() < effective_irrational:
            if side == "buy":
                price = self._current_price * Decimal(str(random.uniform(1.03, 1.08)))
            else:
                price = self._current_price * Decimal(str(random.uniform(0.92, 0.97)))
        else:
            price = self._current_price + Decimal(str(random.uniform(-0.10, 0.10)))
        low, high = self.quantity_range
        low = max(100, low)
        high = max(low, high)
        if random.random() < 0.9:
            upper = max(low, min(500, high))
            qty = random.randint(low // 100, upper // 100) * 100
            qty = max(100, qty)
        else:
            qty = random.randint(low // 100, high // 100) * 100
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


# ─────────── 5. 激进交易者 ───────────

class AggressiveTrader(MarketParticipant):
    """激进交易者 - 大单吃深度"""

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


# ─────────── 6. 算法交易者 ───────────

class AlgorithmicTrader(MarketParticipant):
    """算法交易者 - TWAP/VWAP 拆单执行"""

    def __init__(self, algo_type: str = "twap", slice_count: int = 10, **kwargs):
        super().__init__(**kwargs)
        self.algo_type = algo_type
        self.slice_count = max(1, slice_count)
        self._target_order: Optional[Dict] = None
        self._slices_remaining = 0
        self._slice_size = 0
        self._slice_side = ""
        self._slice_price = Decimal("0")
        self._tick_count = 0
        self._total_qty = 0

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if self._target_order is None and random.random() < 0.05:
            self._start_new_algo_order(book_snapshot)
        if self._target_order is None or self._slices_remaining <= 0:
            return None
        self._tick_count += 1
        if self.algo_type == "twap":
            if self._tick_count % max(1, self.slice_count // 3) != 0:
                return None
        else:
            expected_progress = 1 - (self._slices_remaining / self.slice_count)
            if random.random() > 2 * (1 - expected_progress):
                return None
        vol = self._get_volatility()
        size_factor = max(0.5, 1.0 - float(vol) * 300)
        actual_qty = max(100, int(self._slice_size * size_factor) // 100 * 100)
        if self._slices_remaining == 1:
            executed = (self.slice_count - self._slices_remaining) * self._slice_size
            actual_qty = max(100, self._total_qty - executed)
            actual_qty = (actual_qty // 100) * 100
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
        self._total_qty = random.randint(50, 200) * 100
        self._slice_size = max(100, self._total_qty // self.slice_count // 100 * 100)
        self._slices_remaining = self.slice_count
        self._slice_side = side
        self._slice_price = mid
        self._tick_count = 0
        self._target_order = {"side": side, "total_quantity": self._total_qty, "price": mid}

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        return None


# ─────────── 7. 止损/止盈交易者 ───────────

class StopLossTrader(MarketParticipant):
    """止损/止盈交易者"""

    def __init__(self, stop_loss_pct: float = 0.03, take_profit_pct: float = 0.05, **kwargs):
        super().__init__(**kwargs)
        self.stop_loss_pct = Decimal(str(stop_loss_pct))
        self.take_profit_pct = Decimal(str(take_profit_pct))
        self._entry_price: Optional[Decimal] = None
        self._stop_price: Optional[Decimal] = None
        self._profit_price: Optional[Decimal] = None
        self._position_side: Optional[str] = None
        self._position_qty: int = 0
        self._closing_order_id: Optional[str] = None

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not book_snapshot:
            return None
        mid = self._get_mid_price(book_snapshot)
        if mid is None:
            return None
        self._current_price = mid
        if self._position_side is None:
            if random.random() < 0.1:
                side = random.choice(["buy", "sell"])
                self._entry_price = mid
                self._position_qty = self._random_quantity() * 3
                if side == "buy":
                    self._stop_price = mid * (Decimal("1") - self.stop_loss_pct)
                    self._profit_price = mid * (Decimal("1") + self.take_profit_pct)
                else:
                    self._stop_price = mid * (Decimal("1") + self.stop_loss_pct)
                    self._profit_price = mid * (Decimal("1") - self.take_profit_pct)
                self._position_side = side
                price = self._clamp_price(mid, book_snapshot)
                return self._create_order(side, price, self._position_qty)
            return None
        if self._closing_order_id is not None:
            return None
        triggered = False
        if self._position_side == "buy":
            if mid <= self._stop_price or mid >= self._profit_price:
                triggered = True
        else:
            if mid >= self._stop_price or mid <= self._profit_price:
                triggered = True
        if triggered:
            close_side = "sell" if self._position_side == "buy" else "buy"
            price = self._clamp_price(mid, book_snapshot)
            order = self._create_order(close_side, price, self._position_qty)
            self._closing_order_id = order["order_id"]
            return order
        return None

    def on_order_filled(self, order_id: str, trade_info: Dict):
        super().on_order_filled(order_id, trade_info)
        if order_id == self._closing_order_id:
            self._position_side = None
            self._position_qty = 0
            self._entry_price = None
            self._stop_price = None
            self._profit_price = None
            self._closing_order_id = None

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        return None


# ─────────── 8. 订单簿不平衡交易者 ───────────

class OrderBookImbalanceTrader(MarketParticipant):
    """订单簿不平衡交易者"""

    def __init__(self, imbalance_threshold: float = 0.3, **kwargs):
        super().__init__(**kwargs)
        self.imbalance_threshold = imbalance_threshold

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not book_snapshot:
            return None
        book_imbalance = self._get_imbalance(book_snapshot)
        flow_imbalance = get_shared_market_state(self.symbol).order_flow_imbalance
        combined = book_imbalance * 0.6 + flow_imbalance * 0.4
        if abs(combined) < self.imbalance_threshold:
            return None
        if combined > 0:
            side = "buy"
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


# ─────────── 9. 冰山订单参与者 ───────────

class IcebergParticipant(MarketParticipant):
    """冰山订单参与者"""

    def __init__(self, visible_ratio: float = 0.1, **kwargs):
        super().__init__(**kwargs)
        self.visible_ratio = visible_ratio
        self._iceberg_orders: Dict[str, Dict] = {}

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
        self._iceberg_orders[order["order_id"]] = {"total_qty": total_qty, "visible_qty": visible_qty, "filled_qty": 0}
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

    def on_order_filled(self, order_id: str, trade_info: Dict):
        super().on_order_filled(order_id, trade_info)
        info = self._iceberg_orders.get(order_id)
        if info is None:
            return
        info["filled_qty"] += trade_info.get("quantity", 0)
        if info["filled_qty"] >= info["total_qty"]:
            self._iceberg_orders.pop(order_id, None)


# ─────────── 10. 主观方向交易者（新增） ───────────

class DirectionalTrader(MarketParticipant):
    """主观方向交易者 - 有明确目标价格，主动推动价格走向目标

    策略：
    - 设定目标价格 target_price
    - 当 current_price < target_price * 0.95（大幅低于目标）时，大量买入
    - 当 current_price > target_price * 1.05（大幅高于目标）时，大量卖出
    - 越接近目标价格，交易越保守
    - 使用大单在订单簿上堆积深度，推动价格
    - 有资金/仓位限制，防止无限交易
    """

    def __init__(self, urgency: float = 0.7, max_position: int = 30000, **kwargs):
        super().__init__(**kwargs)
        self.urgency = Decimal(str(urgency))  # 交易急迫度 0-1
        self.max_position = max_position
        self._last_action = None

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not book_snapshot:
            return None
        mid = self._get_mid_price(book_snapshot)
        if mid is None:
            return None
        self._current_price = mid

        target = self.target_price
        deviation = (mid - target) / target if target != 0 else Decimal("0")

        # 判断是否已接近目标（偏差 < 1%），接近时减少交易频率
        if abs(deviation) < Decimal("0.01"):
            if random.random() > 0.2:
                return None

        # 确定方向和急迫度
        if deviation < Decimal("-0.05"):
            # 大幅低于目标：强烈买入
            side = "buy"
            urgency = self.urgency
            size_multiplier = 5
        elif deviation < Decimal("0"):
            # 低于目标：温和买入
            side = "buy"
            urgency = self.urgency * Decimal("0.5")
            size_multiplier = 3
        elif deviation > Decimal("0.05"):
            # 大幅高于目标：强烈卖出
            side = "sell"
            urgency = self.urgency
            size_multiplier = 5
        else:
            # 高于目标：温和卖出
            side = "sell"
            urgency = self.urgency * Decimal("0.5")
            size_multiplier = 3

        # 仓位检查：避免超过限制
        if side == "buy" and self.position >= self.max_position:
            return None
        if side == "sell" and self.position <= -self.max_position:
            return None

        # 定价：越急迫越靠近对手盘价格（更容易成交）
        bids = book_snapshot.get("bids", [])
        asks = book_snapshot.get("asks", [])
        if side == "buy":
            if asks:
                best_ask = Decimal(str(asks[0]["price"]))
                # 急迫度高时，报价更接近 ask（甚至略高于 ask）
                price = best_ask + Decimal(str(random.uniform(0.0, float(urgency) * 0.05)))
            else:
                price = mid + Decimal(str(random.uniform(0.01, 0.03)))
        else:
            if bids:
                best_bid = Decimal(str(bids[0]["price"]))
                price = best_bid - Decimal(str(random.uniform(0.0, float(urgency) * 0.05)))
            else:
                price = mid - Decimal(str(random.uniform(0.01, 0.03)))

        price = self._clamp_price(price, book_snapshot)
        qty = self._random_quantity() * size_multiplier

        # 在大幅偏离时，可能发送超大单来推动价格
        if abs(deviation) > Decimal("0.08") and random.random() < 0.3:
            qty = qty * 3

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


# ─────────── 11. 筹码收集者（新增） ───────────

class ChipCollector(MarketParticipant):
    """筹码收集者 - 分批次低价建仓

    策略：
    - 目标建仓仓位 target_position（如 20000 股）
    - 分阶段买入：
      - 0-30%：保守，在 bid 附近小单
      - 30-70%：正常，在 bid 附近中单
      - 70-90%：加快，更靠近市场价
      - 90%+：激进，市价附近大单
    - 收集完成后停止买入，可能转为持有或卖出
    - 成本控制：不追高，只在价格低于近期均价时买入
    """

    def __init__(self, target_position: int = 20000, cost_limit: Optional[float] = None, **kwargs):
        super().__init__(**kwargs)
        self.target_position = target_position
        self.cost_limit = Decimal(str(cost_limit)) if cost_limit else None
        self._collection_start_price: Optional[Decimal] = None
        self._price_history: List[Decimal] = []
        self._ma_window = 10

    def _update_history(self, book_snapshot: Optional[Dict]):
        if not book_snapshot:
            return
        mid = self._get_mid_price(book_snapshot)
        if mid is not None:
            self._price_history.append(mid)
            if len(self._price_history) > self._ma_window * 2:
                self._price_history = self._price_history[-self._ma_window * 2:]
            self._current_price = mid
            if self._collection_start_price is None:
                self._collection_start_price = mid

    def _get_ma(self) -> Optional[Decimal]:
        if len(self._price_history) < self._ma_window:
            return None
        return sum(self._price_history[-self._ma_window:]) / self._ma_window

    @property
    def collection_progress(self) -> float:
        if self.target_position <= 0:
            return 1.0
        return min(1.0, max(0.0, self.position / self.target_position))

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        self._update_history(book_snapshot)
        if not book_snapshot:
            return None

        # 已收集完成，停止买入
        if self.position >= self.target_position:
            # 收集完成后，有 10% 概率开始卖出（获利了结）
            if random.random() < 0.1:
                side = "sell"
                qty = min(self._random_quantity() * 2, self.position)
                if qty <= 0:
                    return None
                bids = book_snapshot.get("bids", [])
                if bids:
                    price = Decimal(str(bids[0]["price"])) - Decimal("0.01")
                else:
                    price = self._current_price - Decimal("0.01")
                price = self._clamp_price(price, book_snapshot)
                return self._create_order(side, price, qty)
            return None

        # 成本控制：如果价格高于 MA，暂停买入等待回调
        ma = self._get_ma()
        if ma is not None and self._current_price > ma * Decimal("1.02"):
            if random.random() > 0.1:  # 90% 概率跳过
                return None

        # 分阶段策略
        progress = self.collection_progress
        side = "buy"

        if progress < 0.3:
            # 初期：保守，小单，在 bid 附近
            qty = max(100, self._random_quantity() // 2)
            bids = book_snapshot.get("bids", [])
            if bids:
                price = Decimal(str(bids[0]["price"])) - Decimal(str(random.uniform(0.01, 0.03)))
            else:
                price = self._current_price - Decimal("0.03")
        elif progress < 0.7:
            # 中期：正常，中单，在 bid 附近
            qty = self._random_quantity()
            bids = book_snapshot.get("bids", [])
            if bids:
                price = Decimal(str(bids[0]["price"])) - Decimal(str(random.uniform(0.0, 0.02)))
            else:
                price = self._current_price - Decimal("0.02")
        elif progress < 0.9:
            # 后期：加快，靠近市场价
            qty = self._random_quantity() * 2
            bids = book_snapshot.get("bids", [])
            if bids:
                price = Decimal(str(bids[0]["price"])) + Decimal(str(random.uniform(0.0, 0.01)))
            else:
                price = self._current_price - Decimal("0.01")
        else:
            # 收尾：激进，大单，接近市价
            qty = self._random_quantity() * 3
            asks = book_snapshot.get("asks", [])
            if asks:
                price = Decimal(str(asks[0]["price"])) - Decimal(str(random.uniform(0.0, 0.02)))
            else:
                price = self._current_price

        # 最终检查
        price = self._clamp_price(price, book_snapshot)
        remaining = self.target_position - self.position
        qty = min(qty, remaining)
        if qty <= 0:
            return None
        qty = max(100, (qty // 100) * 100)

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


# ─────────── 12. 日内高抛低吸交易者（新增） ───────────

class DayTrader(MarketParticipant):
    """日内交易者 - 做T，利用 bid-ask spread 和短期波动快速进出

    策略：
    - 只在持仓不超过 max_holding_ticks 个 tick 时持有仓位
    - 利用 bid-ask spread：在 bid 附近买入，在 ask 附近卖出
    - 或者：价格下跌后买入，上涨后卖出
    - 严格止损：单笔亏损不超过 entry_price 的 stop_loss_pct
    - 快速进出，不隔夜持仓
    """

    def __init__(self, max_holding_ticks: int = 15, stop_loss_pct: float = 0.005, profit_target_pct: float = 0.008, **kwargs):
        super().__init__(**kwargs)
        self.max_holding_ticks = max_holding_ticks
        self.stop_loss_pct = Decimal(str(stop_loss_pct))
        self.profit_target_pct = Decimal(str(profit_target_pct))
        self._holdings: List[Dict] = []  # 每笔持仓：{entry_price, qty, side, ticks_held, order_id}
        self._cooldown = 0

    def generate_order(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if not book_snapshot:
            return None
        mid = self._get_mid_price(book_snapshot)
        if mid is None:
            return None
        self._current_price = mid

        # 更新所有持仓的持有时间
        for h in self._holdings:
            h["ticks_held"] += 1

        # 检查是否有持仓需要平仓（止盈/止损/超期）
        for h in self._holdings[:]:
            entry = h["entry_price"]
            side = h["side"]
            qty = h["qty"]
            ticks = h["ticks_held"]

            # 止盈/止损检查
            if side == "buy":  # 买入持仓，需要卖出平仓
                profit_pct = (mid - entry) / entry
                if profit_pct >= self.profit_target_pct or profit_pct <= -self.stop_loss_pct or ticks >= self.max_holding_ticks:
                    # 平仓卖出
                    bids = book_snapshot.get("bids", [])
                    if bids:
                        price = Decimal(str(bids[0]["price"])) - Decimal("0.01")
                    else:
                        price = mid - Decimal("0.01")
                    price = self._clamp_price(price, book_snapshot)
                    self._holdings.remove(h)
                    return self._create_order("sell", price, qty)
            else:  # 卖出持仓（做空），需要买入平仓
                profit_pct = (entry - mid) / entry
                if profit_pct >= self.profit_target_pct or profit_pct <= -self.stop_loss_pct or ticks >= self.max_holding_ticks:
                    # 平仓买入
                    asks = book_snapshot.get("asks", [])
                    if asks:
                        price = Decimal(str(asks[0]["price"])) + Decimal("0.01")
                    else:
                        price = mid + Decimal("0.01")
                    price = self._clamp_price(price, book_snapshot)
                    self._holdings.remove(h)
                    return self._create_order("buy", price, qty)

        # 冷却期检查
        if self._cooldown > 0:
            self._cooldown -= 1
            return None

        # 新建仓：只在有合理 spread 时交易
        if random.random() > 0.4:
            return None

        spread = self._get_spread(book_snapshot)
        if spread is None or spread < Decimal("0.01"):
            return None

        # 利用 spread 做T：在 bid 买入，在 ask 卖出
        side = random.choice(["buy", "sell"])
        bids = book_snapshot.get("bids", [])
        asks = book_snapshot.get("asks", [])

        if side == "buy":
            if bids:
                price = Decimal(str(bids[0]["price"])) - Decimal(str(random.uniform(0.0, 0.01)))
            else:
                price = mid - Decimal("0.01")
        else:
            if asks:
                price = Decimal(str(asks[0]["price"])) + Decimal(str(random.uniform(0.0, 0.01)))
            else:
                price = mid + Decimal("0.01")

        price = self._clamp_price(price, book_snapshot)
        qty = max(100, self._random_quantity() // 2)  # 日内交易者仓位较小

        order = self._create_order(side, price, qty)
        self._holdings.append({
            "entry_price": mid,
            "qty": qty,
            "side": side,
            "ticks_held": 0,
            "order_id": order["order_id"],
        })
        self._cooldown = random.randint(2, 5)
        return order

    def on_order_filled(self, order_id: str, trade_info: Dict):
        super().on_order_filled(order_id, trade_info)
        # 更新持仓的 order_id（首次成交时记录）
        for h in self._holdings:
            if h.get("order_id") == order_id:
                h["entry_price"] = Decimal(str(trade_info.get("price", h["entry_price"])))

    def generate_cancel(self, book_snapshot: Optional[Dict]) -> Optional[Dict]:
        if random.random() > 0.05 or not self._pending_orders:
            return None
        order = random.choice(self._pending_orders)
        self._pending_orders = [o for o in self._pending_orders if o["order_id"] != order["order_id"]]
        # 同时从 holdings 中移除
        self._holdings = [h for h in self._holdings if h.get("order_id") != order["order_id"]]
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
    """参与者注册表"""

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
            "directional_trader_count": 1,       # 新增
            "chip_collector_count": 1,           # 新增
            "day_trader_count": 2,               # 新增
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
                spread=_get_old_attr(pid, "base_spread", Decimal("0.02")),
                inventory_skew=_get_old_attr(pid, "inventory_skew", Decimal("0.5")),
                max_inventory=_get_old_attr(pid, "max_inventory", 5000),
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

        # 主观方向交易者（新增）
        for i in range(self._config.get("directional_trader_count", 1)):
            pid = f"DIR-{i+1}"
            p = DirectionalTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.0 + i * 0.3),
                urgency=_get_old_attr(pid, "urgency", Decimal("0.7")),
                max_position=_get_old_attr(pid, "max_position", 30000),
            )
            self._participants[pid] = p

        # 筹码收集者（新增）
        for i in range(self._config.get("chip_collector_count", 1)):
            pid = f"CHIP-{i+1}"
            p = ChipCollector(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (1.2 + i * 0.3),
                target_position=_get_old_attr(pid, "target_position", 20000),
            )
            self._participants[pid] = p

        # 日内交易者（新增）
        for i in range(self._config.get("day_trader_count", 2)):
            pid = f"DAY-{i+1}"
            p = DayTrader(
                participant_id=pid, symbol=self.symbol, base_price=target,
                target_price=float(target), order_interval=base_interval * (0.6 + i * 0.2),
                max_holding_ticks=_get_old_attr(pid, "max_holding_ticks", 15),
                stop_loss_pct=_get_old_attr(pid, "stop_loss_pct", 0.005),
                profit_target_pct=_get_old_attr(pid, "profit_target_pct", 0.008),
            )
            self._participants[pid] = p

    def update_config(self, config: Dict):
        self._config.update(config)
        rebuild = False
        for key in ["market_maker_count", "trend_follower_count", "mean_reversion_count",
                    "noise_trader_count", "aggressive_trader_count", "algorithmic_trader_count",
                    "stop_loss_trader_count", "order_book_imbalance_count", "iceberg_participant_count",
                    "directional_trader_count", "chip_collector_count", "day_trader_count",
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

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Account:
    """A股账户模型

    支持 T+1 仓位管理与挂单冻结：
    - cash: 可用现金
    - frozen_cash: 买入挂单已冻结的资金
    - available_position: 可用底仓，可立即卖出
    - frozen_position: 卖出挂单已冻结的仓位
    - today_bought_position: 今日买入仓位，日终结算后转入可用底仓
    """

    account_id: str = "default"
    initial_cash: Decimal = field(default_factory=lambda: Decimal("1000000.00"))
    initial_position: int = 0

    cash: Decimal = field(init=False)
    frozen_cash: Decimal = field(init=False)
    available_position: int = field(init=False)
    frozen_position: int = field(init=False)
    today_bought_position: int = field(init=False)
    total_fees: Decimal = field(init=False)
    trade_count: int = field(init=False)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        self.cash = Decimal(str(self.initial_cash)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.frozen_cash = Decimal("0")
        self.available_position = self.initial_position
        self.frozen_position = 0
        self.today_bought_position = 0
        self.total_fees = Decimal("0")
        self.trade_count = 0

    # ─────────── 查询 ───────────

    @property
    def total_position(self) -> int:
        """总持仓 = 可用底仓 + 卖出冻结 + 今日买入"""
        return self.available_position + self.frozen_position + self.today_bought_position

    @property
    def buying_power(self) -> Decimal:
        """购买力 = 可用现金"""
        return self.cash

    def can_buy(self, cost_with_fee: Decimal) -> bool:
        """检查是否有足够现金完成买入（含费用）"""
        return self.cash >= cost_with_fee

    def can_sell(self, qty: int) -> bool:
        """检查是否有足够可用底仓可卖"""
        return qty <= self.available_position

    # ─────────── 挂单冻结 ───────────

    def on_buy_queued(self, qty: int, price: Decimal, fee: Decimal) -> Decimal:
        """买入挂单：冻结资金，返回冻结总额"""
        total = price * Decimal(qty) + fee
        self.cash -= total
        self.cash = self.cash.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.frozen_cash += total
        self.updated_at = datetime.now()
        return total

    def on_buy_unqueued(self, total_frozen: Decimal):
        """买入撤单/未成交退单：解冻资金"""
        self.frozen_cash -= total_frozen
        self.cash += total_frozen
        self.cash = self.cash.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.updated_at = datetime.now()

    def on_sell_queued(self, qty: int):
        """卖出挂单：冻结可用仓位"""
        self.available_position -= qty
        self.frozen_position += qty
        self.updated_at = datetime.now()

    def on_sell_unqueued(self, qty: int):
        """卖出撤单：解冻仓位"""
        self.frozen_position -= qty
        self.available_position += qty
        self.updated_at = datetime.now()

    # ─────────── 成交更新 ───────────

    def on_buy_fill(self, qty: int, price: Decimal, fee: Decimal, total_frozen: Decimal):
        """买入成交：从冻结资金中扣实际成本，余款退回现金，仓位转入今日买入"""
        actual_cost = price * Decimal(qty) + fee
        # 释放冻结资金
        self.frozen_cash -= total_frozen
        # 退回多余现金
        self.cash += total_frozen - actual_cost
        self.cash = self.cash.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # 仓位进入今日买入（T+1 冻结）
        self.today_bought_position += qty
        self.total_fees += fee
        self.trade_count += 1
        self.updated_at = datetime.now()

    def on_sell_fill(self, qty: int, price: Decimal, fee: Decimal):
        """卖出成交：减少卖出冻结仓位，增加现金"""
        revenue = price * Decimal(qty) - fee
        self.frozen_position -= qty
        self.cash += revenue
        self.cash = self.cash.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.total_fees += fee
        self.trade_count += 1
        self.updated_at = datetime.now()

    # ─────────── 日终结算 ───────────

    def settle(self):
        """日终结算：将今日买入仓位转为可用底仓"""
        if self.today_bought_position > 0:
            self.available_position += self.today_bought_position
            self.today_bought_position = 0
            self.updated_at = datetime.now()

    # ─────────── 序列化 ───────────

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "cash": str(self.cash),
            "frozen_cash": str(self.frozen_cash),
            "available_position": self.available_position,
            "frozen_position": self.frozen_position,
            "today_bought_position": self.today_bought_position,
            "total_position": self.total_position,
            "buying_power": str(self.buying_power),
            "total_fees": str(self.total_fees),
            "trade_count": self.trade_count,
            "initial_cash": str(self.initial_cash),
            "initial_position": self.initial_position,
            "updated_at": self.updated_at.isoformat(),
        }

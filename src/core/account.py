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
        self.reset(self.initial_cash, self.initial_position)

    def reset(self, initial_cash: Optional[Decimal] = None, initial_position: Optional[int] = None):
        """重置账户到初始状态"""
        if initial_cash is not None:
            self.initial_cash = Decimal(str(initial_cash)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        if initial_position is not None:
            self.initial_position = int(initial_position)
        self.cash = Decimal(str(self.initial_cash)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.frozen_cash = Decimal("0")
        self.available_position = self.initial_position
        self.frozen_position = 0
        self.today_bought_position = 0
        self.total_fees = Decimal("0")
        self.trade_count = 0
        self.updated_at = datetime.now()

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

    def on_buy_fill(self, qty: int, price: Decimal, fee: Decimal, total_frozen: Optional[Decimal] = None):
        """买入成交

        - 若 total_frozen 提供：从冻结资金中扣实际成本，余款退回现金。
        - 若未提供（立即成交）：直接从可用现金扣实际成本。
        仓位均转入今日买入（T+1 冻结）。
        """
        actual_cost = price * Decimal(qty) + fee
        if total_frozen is not None:
            # 释放冻结资金，退回多余现金
            self.frozen_cash -= total_frozen
            self.cash += total_frozen - actual_cost
        else:
            self.cash -= actual_cost
        self.cash = self.cash.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # 仓位进入今日买入（T+1 冻结）
        self.today_bought_position += qty
        self.total_fees += fee
        self.trade_count += 1
        self.updated_at = datetime.now()

    def on_sell_fill(self, qty: int, price: Decimal, fee: Decimal, from_frozen: bool = False):
        """卖出成交：增加现金，减少仓位

        - from_frozen=True：减少卖出冻结仓位（订单曾排队）。
        - from_frozen=False：减少可用仓位（立即成交）。
        """
        revenue = price * Decimal(qty) - fee
        if from_frozen:
            self.frozen_position -= qty
        else:
            self.available_position -= qty
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

    def restore_from_dict(self, data: dict):
        """从持久化字典恢复账户状态（不重置 initial_cash/initial_position）"""
        self.cash = Decimal(str(data.get("cash", self.initial_cash))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.frozen_cash = Decimal(str(data.get("frozen_cash", "0"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.available_position = int(data.get("available_position", self.initial_position))
        self.frozen_position = int(data.get("frozen_position", 0))
        self.today_bought_position = int(data.get("today_bought_position", 0))
        self.total_fees = Decimal(str(data.get("total_fees", "0"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.trade_count = int(data.get("trade_count", 0))
        if data.get("updated_at"):
            self.updated_at = datetime.fromisoformat(data["updated_at"])
        else:
            self.updated_at = datetime.now()

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

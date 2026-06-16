from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


class FeeCalculator(ABC):
    """费用计算抽象基类"""

    @abstractmethod
    def calculate(self, side: str, price: Decimal, quantity: int) -> Decimal:
        """
        计算单笔成交的费用

        Args:
            side: "buy" 或 "sell"
            price: 成交价格
            quantity: 成交数量

        Returns:
            总费用（已四舍五入到分）
        """
        pass


class AShareFeeCalculator(FeeCalculator):
    """A股费用计算器

    默认费率：
    - 佣金：0.025%（双向），最低 5 元
    - 印花税：0.05%（仅卖出）
    - 过户费：0.001%（双向）
    """

    def __init__(
        self,
        commission_rate: Decimal = Decimal("0.00025"),
        min_commission: Decimal = Decimal("5.0"),
        stamp_tax_rate: Decimal = Decimal("0.0005"),
        transfer_fee_rate: Decimal = Decimal("0.00001"),
    ):
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.stamp_tax_rate = stamp_tax_rate
        self.transfer_fee_rate = transfer_fee_rate

    def calculate(self, side: str, price: Decimal, quantity: int) -> Decimal:
        turnover = price * Decimal(quantity)

        # 佣金（双向，最低5元）
        commission = turnover * self.commission_rate
        if commission < self.min_commission:
            commission = self.min_commission

        # 印花税（仅卖出）
        stamp_tax = Decimal("0")
        if side == "sell":
            stamp_tax = turnover * self.stamp_tax_rate

        # 过户费（双向）
        transfer_fee = turnover * self.transfer_fee_rate

        total = commission + stamp_tax + transfer_fee
        # 四舍五入到分
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def estimate_for_buy(self, price: Decimal, quantity: int) -> Decimal:
        """买入时预估费用（用于下单前资金校验）"""
        return self.calculate("buy", price, quantity)

    def estimate_for_sell(self, price: Decimal, quantity: int) -> Decimal:
        """卖出时预估费用（用于下单前资金校验）"""
        return self.calculate("sell", price, quantity)

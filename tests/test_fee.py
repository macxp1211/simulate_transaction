import pytest
from decimal import Decimal

from src.core.fee import AShareFeeCalculator


class TestAShareFeeCalculator:
    """A股费用计算器测试"""

    @pytest.fixture
    def fee_calc(self):
        return AShareFeeCalculator()

    def test_buy_fee(self, fee_calc):
        """买入费用：佣金 + 过户费（无印花税）"""
        price = Decimal("10.00")
        qty = 1000
        fee = fee_calc.calculate("buy", price, qty)

        turnover = price * qty
        commission = max(turnover * Decimal("0.00025"), Decimal("5.0"))
        transfer = turnover * Decimal("0.00001")
        expected = (commission + transfer).quantize(Decimal("0.01"))

        assert fee == expected
        assert fee > 0

    def test_sell_fee(self, fee_calc):
        """卖出费用：佣金 + 过户费 + 印花税"""
        price = Decimal("10.00")
        qty = 1000
        fee = fee_calc.calculate("sell", price, qty)

        turnover = price * qty
        commission = max(turnover * Decimal("0.00025"), Decimal("5.0"))
        transfer = turnover * Decimal("0.00001")
        stamp_tax = turnover * Decimal("0.0005")
        expected = (commission + transfer + stamp_tax).quantize(Decimal("0.01"))

        assert fee == expected
        assert fee > fee_calc.calculate("buy", price, qty)

    def test_min_commission(self, fee_calc):
        """小额交易触发最低 5 元佣金"""
        price = Decimal("1.00")
        qty = 100
        fee = fee_calc.calculate("buy", price, qty)

        # 佣金 = max(1*100*0.00025=0.025, 5) = 5
        assert fee >= Decimal("5.0")

    def test_large_trade_fee(self, fee_calc):
        """大额交易按比例计算佣金"""
        price = Decimal("100.00")
        qty = 10000
        fee = fee_calc.calculate("sell", price, qty)

        turnover = price * qty
        commission = turnover * Decimal("0.00025")
        assert commission > Decimal("5.0")
        assert fee == (commission + turnover * Decimal("0.00051")).quantize(Decimal("0.01"))

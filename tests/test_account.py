import pytest
from decimal import Decimal

from src.core.account import Account


class TestAccount:
    """账户模型测试"""

    @pytest.fixture
    def account(self):
        return Account(initial_cash="1000000.00", initial_position=10000)

    def test_initial_state(self, account):
        """初始状态"""
        assert account.cash == Decimal("1000000.00")
        assert account.frozen_cash == Decimal("0")
        assert account.available_position == 10000
        assert account.frozen_position == 0
        assert account.today_bought_position == 0
        assert account.total_position == 10000
        assert account.total_fees == Decimal("0")

    def test_can_buy(self, account):
        """买入资金校验"""
        assert account.can_buy(Decimal("500000.00")) is True
        assert account.can_buy(Decimal("1000000.01")) is False

    def test_can_sell(self, account):
        """卖出仓位校验"""
        assert account.can_sell(5000) is True
        assert account.can_sell(10001) is False

    def test_buy_fill(self, account):
        """买入成交后现金减少、仓位进入今日买入"""
        # 先模拟挂单冻结
        frozen_total = account.on_buy_queued(1000, Decimal("10.00"), Decimal("5.25"))
        account.on_buy_fill(1000, Decimal("10.00"), Decimal("5.25"), frozen_total)

        assert account.cash == Decimal("989994.75")
        assert account.frozen_cash == Decimal("0")
        assert account.today_bought_position == 1000
        assert account.available_position == 10000
        assert account.total_position == 11000
        assert account.total_fees == Decimal("5.25")

    def test_sell_fill(self, account):
        """卖出成交后现金增加、卖出冻结仓位减少"""
        account.on_sell_queued(1000)
        account.on_sell_fill(1000, Decimal("10.00"), Decimal("15.25"), from_frozen=True)

        assert account.cash == Decimal("1009984.75")
        assert account.available_position == 9000
        assert account.frozen_position == 0
        assert account.total_position == 9000
        assert account.total_fees == Decimal("15.25")

    def test_sell_fill_immediate(self, account):
        """卖出立即成交，直接扣减可用仓位"""
        account.on_sell_fill(1000, Decimal("10.00"), Decimal("15.25"), from_frozen=False)

        assert account.cash == Decimal("1009984.75")
        assert account.available_position == 9000
        assert account.frozen_position == 0
        assert account.total_position == 9000
        assert account.total_fees == Decimal("15.25")

    def test_settle(self, account):
        """日终结算将今日买入仓位转为可用仓位"""
        frozen_total = account.on_buy_queued(1000, Decimal("10.00"), Decimal("5.0"))
        account.on_buy_fill(1000, Decimal("10.00"), Decimal("5.0"), frozen_total)
        assert account.today_bought_position == 1000
        assert account.available_position == 10000

        account.settle()
        assert account.today_bought_position == 0
        assert account.available_position == 11000
        assert account.total_position == 11000

    def test_to_dict(self, account):
        """序列化"""
        d = account.to_dict()
        assert d["cash"] == "1000000.00"
        assert d["frozen_cash"] == "0"
        assert d["available_position"] == 10000
        assert d["frozen_position"] == 0
        assert d["today_bought_position"] == 0
        assert d["total_position"] == 10000
        assert "total_fees" in d

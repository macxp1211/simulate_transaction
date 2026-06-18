"""A股市场交易规则模块

实现A股真实交易规则：
- 涨跌停限制（主板10%、ST 5%、科创板/创业板20%、北交所30%）
- 价格笼子（买入不得高于基准价102%，卖出不得低于98%）
- 最小价格变动单位（0.01元）
- 每手100股
- 昨收价基准管理
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple
from enum import Enum


class MarketType(Enum):
    """市场类型"""
    MAIN_BOARD = "main_board"        # 沪深主板
    ST_BOARD = "st_board"            # ST股票
    STAR_MARKET = "star_market"      # 科创板
    CHINEXT = "chinext"              # 创业板
    BSE = "bse"                      # 北交所


# 涨跌停比例配置
PRICE_LIMIT_CONFIG = {
    MarketType.MAIN_BOARD: Decimal("0.10"),      # 10%
    MarketType.ST_BOARD: Decimal("0.05"),        # 5%
    MarketType.STAR_MARKET: Decimal("0.20"),     # 20%
    MarketType.CHINEXT: Decimal("0.20"),         # 20%
    MarketType.BSE: Decimal("0.30"),             # 30%
}


class MarketRules:
    """A股市场规则管理器

    每个标的维护：
    - previous_close: 昨收价（所有价格限制基准）
    - market_type: 市场类型（决定涨跌停比例）
    - price_tick: 最小价格变动（默认0.01）
    - lot_size: 每手股数（默认100）
    """

    def __init__(
        self,
        previous_close: Decimal = Decimal("10.00"),
        market_type: MarketType = MarketType.MAIN_BOARD,
        price_tick: Decimal = Decimal("0.01"),
        lot_size: int = 100,
        price_cage_ratio: Decimal = Decimal("0.02"),
    ):
        self.previous_close = Decimal(str(previous_close))
        self.market_type = market_type
        self.price_tick = Decimal(str(price_tick))
        self.lot_size = lot_size
        self.price_cage_ratio = Decimal(str(price_cage_ratio))

    # ─────────── 价格限制计算 ───────────

    @property
    def price_limit_ratio(self) -> Decimal:
        """涨跌停比例"""
        return PRICE_LIMIT_CONFIG.get(self.market_type, Decimal("0.10"))

    @property
    def upper_limit(self) -> Decimal:
        """涨停价 = 昨收 × (1 + 比例)"""
        limit = self.previous_close * (Decimal("1") + self.price_limit_ratio)
        return self._round_to_tick(limit)

    @property
    def lower_limit(self) -> Decimal:
        """跌停价 = 昨收 × (1 - 比例)"""
        limit = self.previous_close * (Decimal("1") - self.price_limit_ratio)
        return self._round_to_tick(limit)

    @property
    def price_cage_upper(self) -> Decimal:
        """价格笼子上限 = 昨收 × (1 + 比例)"""
        return self.previous_close * (Decimal("1") + self.price_cage_ratio)

    @property
    def price_cage_lower(self) -> Decimal:
        """价格笼子下限 = 昨收 × (1 - 比例)"""
        return self.previous_close * (Decimal("1") - self.price_cage_ratio)

    # ─────────── 校验方法 ───────────

    def validate_price(self, price: Decimal, benchmark: Optional[Decimal] = None) -> Tuple[bool, str]:
        """校验价格是否合规

        Args:
            price: 委托价格
            benchmark: 价格笼子基准价格（对手盘最优价或最新成交价）

        Returns:
            (是否通过, 错误信息)
        """
        price = Decimal(str(price))

        # 1. 价格 > 0
        if price <= 0:
            return False, "委托价格必须大于0"

        # 2. 最小价格变动单位
        if not self._is_valid_tick(price):
            return False, f"委托价格必须是 {self.price_tick} 的整数倍，当前 {price}"

        # 3. 涨跌停限制
        if price > self.upper_limit:
            return False, (
                f"委托价格 {price} 超过涨停价 {self.upper_limit} "
                f"(昨收 {self.previous_close}, 限制 ±{self.price_limit_ratio * 100}%)"
            )
        if price < self.lower_limit:
            return False, (
                f"委托价格 {price} 低于跌停价 {self.lower_limit} "
                f"(昨收 {self.previous_close}, 限制 ±{self.price_limit_ratio * 100}%)"
            )

        # 4. 价格笼子（2023年沪深主板新规）
        # 注意：价格笼子只在连续竞价阶段生效，集合竞价阶段不生效
        # 这里简化处理，默认连续竞价阶段
        if benchmark is not None:
            benchmark = Decimal(str(benchmark))
            upper_cage = benchmark * (Decimal("1") + self.price_cage_ratio)
            lower_cage = benchmark * (Decimal("1") - self.price_cage_ratio)
            if price > upper_cage:
                return False, (
                    f"委托价格 {price} 超出价格笼子上限 {upper_cage} "
                    f"(基准价 {benchmark}, 上限 +{self.price_cage_ratio * 100}%)"
                )
            if price < lower_cage:
                return False, (
                    f"委托价格 {price} 低于价格笼子下限 {lower_cage} "
                    f"(基准价 {benchmark}, 下限 -{self.price_cage_ratio * 100}%)"
                )

        return True, ""

    def validate_quantity(self, quantity: int) -> Tuple[bool, str]:
        """校验数量是否合规

        Returns:
            (是否通过, 错误信息)
        """
        if quantity <= 0:
            return False, "委托数量必须大于0"
        if quantity % self.lot_size != 0:
            return False, f"委托数量必须是 {self.lot_size} 的整数倍，当前 {quantity}"
        return True, ""

    def validate_order(self, price: Decimal, quantity: int, benchmark: Optional[Decimal] = None) -> Tuple[bool, str]:
        """综合校验价格和数量

        Returns:
            (是否通过, 错误信息)
        """
        ok, msg = self.validate_quantity(quantity)
        if not ok:
            return False, msg

        ok, msg = self.validate_price(price, benchmark)
        if not ok:
            return False, msg

        return True, ""

    # ─────────── 价格工具 ───────────

    def _round_to_tick(self, price: Decimal) -> Decimal:
        """将价格四舍五入到最小价格变动单位"""
        tick = self.price_tick
        # 先放大到整数，四舍五入，再缩小
        scaled = price / tick
        rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return rounded * tick

    def _is_valid_tick(self, price: Decimal) -> bool:
        """检查价格是否为最小价格变动单位的整数倍"""
        price = Decimal(str(price))
        tick = self.price_tick
        # 使用取模更稳健，避免 Decimal 除法上下文精度问题
        return (price % tick) == Decimal("0")

    def clamp_to_limit(self, price: Decimal) -> Decimal:
        """将价格限制在涨跌停范围内"""
        price = Decimal(str(price))
        if price > self.upper_limit:
            return self.upper_limit
        if price < self.lower_limit:
            return self.lower_limit
        return self._round_to_tick(price)

    def get_price_cage_bounds(self, benchmark: Decimal) -> Tuple[Decimal, Decimal]:
        """获取价格笼子边界

        Returns:
            (下限, 上限)
        """
        benchmark = Decimal(str(benchmark))
        lower = benchmark * (Decimal("1") - self.price_cage_ratio)
        upper = benchmark * (Decimal("1") + self.price_cage_ratio)
        return (self._round_to_tick(lower), self._round_to_tick(upper))

    # ─────────── 序列化 ───────────

    def to_dict(self, benchmark: Optional[Decimal] = None) -> dict:
        """序列化市场规则

        Args:
            benchmark: 价格笼子基准价；提供时返回基于该基准的笼子边界
        """
        cage_lower, cage_upper = None, None
        if benchmark is not None:
            cage_lower, cage_upper = self.get_price_cage_bounds(benchmark)
        return {
            "previous_close": str(self.previous_close),
            "market_type": self.market_type.value,
            "price_limit_ratio": str(self.price_limit_ratio),
            "upper_limit": str(self.upper_limit),
            "lower_limit": str(self.lower_limit),
            "price_cage_ratio": str(self.price_cage_ratio),
            "price_cage_upper": str(cage_upper) if cage_upper is not None else str(self.price_cage_upper),
            "price_cage_lower": str(cage_lower) if cage_lower is not None else str(self.price_cage_lower),
            "price_tick": str(self.price_tick),
            "lot_size": self.lot_size,
        }

    def update_previous_close(self, new_close: Decimal):
        """更新昨收价（日终结算后调用）"""
        self.previous_close = Decimal(str(new_close))

    def update_price_cage_ratio(self, ratio: Decimal):
        """更新价格笼子比例（例如 0.02 表示 ±2%）"""
        self.price_cage_ratio = Decimal(str(ratio))


# 全局市场规则管理器：symbol -> MarketRules
_market_rules: dict = {}


def get_market_rules(symbol: str) -> MarketRules:
    """获取某标的的市场规则，如果不存在则创建默认规则"""
    if symbol not in _market_rules:
        _market_rules[symbol] = MarketRules()
    return _market_rules[symbol]


def set_market_rules(symbol: str, rules: MarketRules):
    """设置某标的的市场规则"""
    _market_rules[symbol] = rules


def update_previous_close(symbol: str, new_close: Decimal):
    """更新某标的的昨收价"""
    rules = get_market_rules(symbol)
    rules.update_previous_close(new_close)


def update_price_cage_ratio(symbol: str, ratio: Decimal):
    """更新某标的的价格笼子比例"""
    rules = get_market_rules(symbol)
    rules.update_price_cage_ratio(ratio)


def clear_market_rules():
    """清除所有市场规则（测试用）"""
    _market_rules.clear()

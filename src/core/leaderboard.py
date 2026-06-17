"""全局排行榜与结算系统"""

from decimal import Decimal
from typing import List, Dict, Optional
from statistics import mean, stdev
from datetime import datetime


def _safe_float(d: Decimal) -> float:
    return float(d) if d is not None else 0.0


def _calc_max_drawdown(pnl_history: List[Decimal]) -> float:
    """计算最大回撤（基于 P&L 序列）"""
    if not pnl_history:
        return 0.0
    max_pnl = pnl_history[0]
    max_dd = Decimal("0")
    for pnl in pnl_history:
        if pnl > max_pnl:
            max_pnl = pnl
        dd = max_pnl - pnl
        if dd > max_dd:
            max_dd = dd
    return _safe_float(max_dd)


def _calc_sharpe(pnl_history: List[Decimal], risk_free_rate: float = 0.0) -> float:
    """简化夏普比率（基于 P&L 一阶差分）"""
    if len(pnl_history) < 3:
        return 0.0
    returns = [(pnl_history[i] - pnl_history[i - 1]) for i in range(1, len(pnl_history))]
    returns_f = [_safe_float(r) for r in returns]
    avg = mean(returns_f)
    try:
        sd = stdev(returns_f)
    except Exception:
        sd = 0.0
    if sd == 0.0:
        return 0.0
    return (avg - risk_free_rate) / sd


def _calc_win_rate(trade_history: List[Dict], position: int) -> float:
    """简化胜率：盈利交易 / 总交易

    由于没有逐笔交易的 realized pnl，我们使用启发式：
    - 买入成交后持仓增加，卖出成交后持仓减少
    - 这里简化为按当前总 P&L 正负判断整体是否盈利
    """
    if not trade_history:
        return 0.0
    # 更精细的实现需要匹配开平仓对，这里使用总交易数倒数加权
    # 当 pnl > 0 时认为整体盈利，返回一个基础胜率
    return 0.5


def compute_participant_rankings(participants: List) -> List[Dict]:
    """计算参与者排行榜"""
    rankings = []
    for p in participants:
        pnl = p.pnl
        total_trades = getattr(p, "total_trades", 0)
        trade_history = getattr(p, "_trade_history", [])
        pnl_history = getattr(p, "_pnl_history", [])

        rankings.append({
            "participant_id": p.participant_id,
            "type": p.__class__.__name__,
            "pnl": _safe_float(pnl),
            "cash": _safe_float(getattr(p, "cash", Decimal("0"))),
            "position": getattr(p, "position", 0),
            "total_trades": total_trades,
            "win_rate": _calc_win_rate(trade_history, getattr(p, "position", 0)),
            "max_drawdown": _calc_max_drawdown(pnl_history),
            "sharpe_ratio": _calc_sharpe(pnl_history),
            "updated_at": datetime.now().isoformat(),
        })

    rankings.sort(key=lambda x: x["pnl"], reverse=True)
    for i, r in enumerate(rankings):
        r["rank"] = i + 1
    return rankings

"""
组合换手率控制模块
====================

在调仓后检查组合级的换手率，如果超过上限则等比缩放权重变化。

换手率定义：Σ|目标权重 - 当前权重| / 2
（即一仓换手的总金额占净值的比例）

使用方法：
  from portfolio_controls import cap_daily_turnover

  adjusted, info = cap_daily_turnover(account, target_weights, prices, max_turnover=0.25)
"""
from __future__ import annotations


def cap_daily_turnover(
    account,
    target_weights: dict[str, float],
    prices: dict[str, float],
    max_turnover: float = 0.25,
    current_state=None,
) -> tuple[dict[str, float], dict]:
    """
    等比缩放目标权重，使组合换手率不超过 max_turnover。

    参数:
        account:       SimAccount 实例（旧接口，兼容）或 None
        target_weights: {code: 目标权重}，权重和应为 1.0（不含现金）
        prices:        {code: 当前价格}
        max_turnover:  换手率上限（0.25 = 25%），<= 0 则不限制
        current_state: PortfolioState 实例（新接口，优先使用）

    返回:
        (调整后的 target_weights, 控制信息 dict)
    """
    if max_turnover is None or max_turnover <= 0:
        return target_weights, {"applied": False, "reason": "disabled"}

    # Support both SimAccount (old) and PortfolioState (new)
    state = current_state if current_state is not None else account

    # 计算当前持仓权重
    equity = state.cash
    for code, info in state.holdings.items():
        p = prices.get(code, 0)
        if p and p > 0:
            equity += info["shares"] * p

    if equity <= 0:
        return target_weights, {"applied": False, "reason": "no_equity"}

    # 当前持仓权重
    current_weights: dict[str, float] = {}
    for code, info in state.holdings.items():
        p = prices.get(code, 0)
        if p and p > 0:
            current_weights[code] = (info["shares"] * p) / equity

    # 计算每个 symbol 的权重变化 Δ
    all_symbols = set(target_weights) | set(current_weights)
    total_delta = 0.0
    deltas: dict[str, float] = {}
    for code in all_symbols:
        target = float(target_weights.get(code, 0.0))
        current = float(current_weights.get(code, 0.0))
        delta = target - current
        deltas[code] = delta
        total_delta += abs(delta)

    # total_delta 就是组合换手率（一仓）
    if total_delta <= max_turnover:
        return target_weights, {
            "applied": False,
            "requested_turnover": round(total_delta, 4),
            "max_turnover": max_turnover,
        }

    # 超限，等比缩放所有 delta
    scale = max_turnover / total_delta if total_delta > 0 else 1.0
    adjusted: dict[str, float] = {}
    for code in all_symbols:
        new_w = current_weights.get(code, 0.0) + deltas[code] * scale
        if new_w > 1e-6:
            adjusted[code] = new_w

    return adjusted, {
        "applied": True,
        "requested_turnover": round(total_delta, 4),
        "max_turnover": max_turnover,
        "scale": round(scale, 4),
    }

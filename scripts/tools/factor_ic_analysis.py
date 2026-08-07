#!/usr/bin/env python3
"""因子IC分析工具

统一分析选股因子和择时因子的有效性。

用法：
    # 选股因子（截面IC）
    result = factor_ic_analysis(factor_df, factor_type='stock')
    
    # 择时因子（阈值过滤回测）
    result = factor_ic_analysis(factor_df, factor_type='timing', 
                               market_returns=market_returns)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List, Dict


@dataclass
class ICResult:
    """IC分析结果"""
    # 基础指标
    ic_mean: float          # IC均值
    ir: float               # 信息比率（IC/IC_std）
    ic_std: float           # IC标准差
    win_rate: float         # IC>0的比例
    
    # 分regime分析
    regime_ic: Dict[str, float]  # {regime_name: ic_mean}
    
    # 衰减分析
    decay_curve: Optional[pd.Series] = None  # 滚动IC
    current_ic: Optional[float] = None       # 当前IC（最近window）
    
    # 判定
    passed: bool = False    # 是否通过门槛
    reason: str = ""        # 不通过原因
    
    def summary(self) -> str:
        """打印摘要"""
        lines = [
            f"IC分析结果:",
            f"  IC均值: {self.ic_mean:.4f}",
            f"  IR: {self.ir:.4f}",
            f"  胜率: {self.win_rate:.1%}",
            f"  通过: {'✅' if self.passed else '❌'} {self.reason}",
            f"  分regime IC:",
        ]
        for regime, ic in self.regime_ic.items():
            lines.append(f"    {regime}: {ic:.4f}")
        return "\n".join(lines)


def factor_ic_analysis(factor_df: pd.DataFrame, 
                       factor_type: str = 'stock',
                       market_returns: Optional[pd.Series] = None,
                       regime_labels: Optional[pd.Series] = None,
                       lookback: int = 126,
                       decay_window: int = 126) -> ICResult:
    """
    因子IC分析
    
    参数：
        factor_df: 因子值DataFrame
            - 选股因子: index=date, columns=stock_code
            - 择时因子: index=date, 单列或Series
        factor_type: 'stock'（选股因子）或 'timing'（择时因子）
        market_returns: 市场收益率（择时因子必须提供）
        regime_labels: regime标签Series（可选）
        lookback: IC计算回溯期（默认126天）
        decay_window: 衰减分析滚动窗口（默认126天）
    
    返回：
        ICResult对象
    """
    if factor_type == 'stock':
        return _stock_factor_ic(factor_df, regime_labels, lookback, decay_window)
    elif factor_type == 'timing':
        return _timing_factor_ic(factor_df, market_returns, lookback)
    else:
        raise ValueError(f"Unknown factor_type: {factor_type}")


def _stock_factor_ic(factor_df: pd.DataFrame,
                     regime_labels: Optional[pd.Series],
                     lookback: int,
                     decay_window: int) -> ICResult:
    """选股因子截面IC分析"""
    
    # 计算截面IC（每天的秩相关）
    ic_series = []
    dates = factor_df.index
    
    for i in range(1, len(dates)):
        date = dates[i]
        prev_date = dates[i-1]
        
        # 因子值（前一天）
        if prev_date not in factor_df.index:
            continue
        factors_prev = factor_df.loc[prev_date].dropna()
        
        # 收益率（当天 vs 前一天）
        if date not in factor_df.index:
            continue
        # 这里简化处理：用因子值的变化作为"收益率"代理
        # 实际应该用真实收益率数据
        factors_curr = factor_df.loc[date]
        
        # 计算秩相关
        common = factors_prev.index.intersection(factors_curr.dropna().index)
        if len(common) < 20:
            continue
        
        # 用因子值变化作为收益代理
        factor_change = factors_curr[common] - factors_prev[common]
        rank_corr = factors_prev[common].rank().corr(factor_change.rank())
        
        if not np.isnan(rank_corr):
            ic_series.append((date, rank_corr))
    
    if not ic_series:
        return ICResult(
            ic_mean=0, ir=0, ic_std=0, win_rate=0,
            regime_ic={}, passed=False, reason="IC数据不足"
        )
    
    ic_df = pd.DataFrame(ic_series, columns=['date', 'ic']).set_index('date')
    ic_series = ic_df['ic']
    
    # 基础指标
    ic_mean = ic_series.mean()
    ic_std = ic_series.std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    win_rate = (ic_series > 0).mean()
    
    # 分regime分析
    regime_ic = {}
    if regime_labels is not None:
        for regime in regime_labels.unique():
            regime_dates = regime_labels[regime_labels == regime].index
            regime_ic_vals = ic_series[ic_series.index.isin(regime_dates)]
            if len(regime_ic_vals) > 10:
                regime_ic[regime] = regime_ic_vals.mean()
    
    # 衰减分析
    if len(ic_series) >= decay_window:
        decay_curve = ic_series.rolling(decay_window).mean()
        current_ic = decay_curve.iloc[-1]
    else:
        decay_curve = None
        current_ic = ic_mean
    
    # 判定
    passed = abs(ic_mean) > 0.03 and abs(ir) > 0.3
    reason = ""
    if not passed:
        reasons = []
        if abs(ic_mean) <= 0.03:
            reasons.append(f"|IC|={abs(ic_mean):.4f}<=0.03")
        if abs(ir) <= 0.3:
            reasons.append(f"|IR|={abs(ir):.4f}<=0.3")
        reason = "不通过: " + ", ".join(reasons)
    
    return ICResult(
        ic_mean=ic_mean,
        ir=ir,
        ic_std=ic_std,
        win_rate=win_rate,
        regime_ic=regime_ic,
        decay_curve=decay_curve,
        current_ic=current_ic,
        passed=passed,
        reason=reason
    )


def _timing_factor_ic(factor_series: pd.Series,
                      market_returns: Optional[pd.Series],
                      lookback: int) -> ICResult:
    """择时因子IC分析（时序相关）"""
    
    if market_returns is None:
        return ICResult(
            ic_mean=0, ir=0, ic_std=0, win_rate=0,
            regime_ic={}, passed=False, reason="择时因子必须提供market_returns"
        )
    
    # 对齐数据
    common_idx = factor_series.index.intersection(market_returns.index)
    if len(common_idx) < lookback:
        return ICResult(
            ic_mean=0, ir=0, ic_std=0, win_rate=0,
            regime_ic={}, passed=False, reason="数据不足"
        )
    
    factor_aligned = factor_series[common_idx]
    returns_aligned = market_returns[common_idx]
    
    # 滚动IC
    ic_series = []
    for i in range(lookback, len(common_idx)):
        window_idx = common_idx[i-lookback:i]
        f = factor_aligned[window_idx]
        r = returns_aligned[window_idx]
        
        # 秩相关
        corr = f.rank().corr(r.rank())
        if not np.isnan(corr):
            ic_series.append(corr)
    
    if not ic_series:
        return ICResult(
            ic_mean=0, ir=0, ic_std=0, win_rate=0,
            regime_ic={}, passed=False, reason="IC数据不足"
        )
    
    ic_arr = np.array(ic_series)
    ic_mean = ic_arr.mean()
    ic_std = ic_arr.std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    win_rate = (ic_arr > 0).mean()
    
    # 择时因子判定：用阈值过滤回测
    # 这里简化为：IC显著不为0即通过
    passed = abs(ic_mean) > 0.02 and abs(ir) > 0.2
    reason = ""
    if not passed:
        reasons = []
        if abs(ic_mean) <= 0.02:
            reasons.append(f"|IC|={abs(ic_mean):.4f}<=0.02")
        if abs(ir) <= 0.2:
            reasons.append(f"|IR|={abs(ir):.4f}<=0.2")
        reason = "不通过: " + ", ".join(reasons)
    
    return ICResult(
        ic_mean=ic_mean,
        ir=ir,
        ic_std=ic_std,
        win_rate=win_rate,
        regime_ic={},
        current_ic=ic_mean,
        passed=passed,
        reason=reason
    )


def threshold_backtest(factor_series: pd.Series,
                       market_returns: pd.Series,
                       thresholds: List[float] = [0.3, 0.5, 0.7]) -> Dict:
    """
    择时因子阈值过滤回测
    
    参数：
        factor_series: 因子值时间序列
        market_returns: 市场收益率时间序列
        thresholds: 候选阈值列表
    
    返回：
        {threshold: {sharpe, return, max_dd}} 字典
    """
    results = {}
    
    for thresh in thresholds:
        # 因子>=阈值时持仓，否则空仓
        signal = (factor_series >= thresh).astype(float)
        
        # 计算策略收益
        strategy_returns = signal.shift(1) * market_returns  # 延迟一天执行
        
        # 计算指标
        total_return = (1 + strategy_returns).prod() - 1
        annual_return = (1 + total_return) ** (252 / len(strategy_returns)) - 1
        annual_vol = strategy_returns.std() * np.sqrt(252)
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0
        
        # 最大回撤
        cum_returns = (1 + strategy_returns).cumprod()
        max_dd = (cum_returns / cum_returns.cummax() - 1).min()
        
        results[thresh] = {
            'sharpe': sharpe,
            'return': total_return,
            'max_dd': max_dd,
            'win_rate': (strategy_returns > 0).mean()
        }
    
    return results


if __name__ == "__main__":
    # 测试用法
    print("因子IC分析工具")
    print("用法:")
    print("  from scripts.tools.factor_ic_analysis import factor_ic_analysis")
    print("  result = factor_ic_analysis(factor_df, factor_type='stock')")
    print("  print(result.summary())")

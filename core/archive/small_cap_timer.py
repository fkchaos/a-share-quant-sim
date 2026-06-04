"""
小市值择时模块
================
判断当前市场是否适合小市值风格，动态调整 small_cap 因子权重。

核心逻辑：
1. 计算"小市值相对强度" = 小市值组合收益 / 大市值组合收益（滚动N日）
2. 计算"小市值溢价" = 小市值因子 IC 的滚动均值
3. 综合得分 = 0.6 × 相对强度分位数 + 0.4 × IC溢价分位数
4. 权重映射：small_cap_weight = base_weight × (1 + sensitivity × score)

参考：聚宽"基于市场存量状态择时的小市值策略"、BigQuant"分时段择优小市值策略"
"""
import numpy as np
import pandas as pd


class SmallCapTimer:
    """小市值择时器：判断当前市场是否适合小市值风格"""

    def __init__(self, lookback=60, sensitivity=1.0):
        """
        lookback : 回看天数，计算滚动相对强度
        sensitivity : 敏感度，>1 更敏感，<1 更保守
        """
        self.lookback = lookback
        self.sensitivity = sensitivity

    def compute_style_strength(self, close_panel, market_cap_panel):
        """
        计算小市值相对强度
        close_panel : DataFrame(dates × stocks) 收盘价
        market_cap_panel : DataFrame(dates × stocks) 市值（close × outstanding_share）
        返回 : Series(dates) 小市值相对强度比值
        """
        n_small = max(5, len(close_panel.columns) // 5)  # 最小20%作为小市值
        n_large = n_small  # 大市值也取同样数量

        # 按市值分组
        cap = market_cap_panel.iloc[-1]  # 最近一天的市值
        cap_sorted = cap.sort_values()
        small_stocks = cap_sorted.index[:n_small]
        large_stocks = cap_sorted.index[-n_large:]

        # 计算滚动收益率
        ret = close_panel.pct_change()
        small_ret = ret[small_stocks].mean(axis=1)  # 小市值等权收益
        large_ret = ret[large_stocks].mean(axis=1)  # 大市值等权收益

        # 滚动累计收益比
        small_cum = (1 + small_ret).rolling(self.lookback).apply(np.prod, raw=True)
        large_cum = (1 + large_ret).rolling(self.lookback).apply(np.prod, raw=True)

        ratio = small_cum / large_cum
        return ratio

    def compute_ic_premium(self, factor_values, forward_returns):
        """
        计算小市值因子 IC 的滚动均值
        factor_values : DataFrame(dates × stocks) 小市值因子值
        forward_returns : DataFrame(dates × stocks) 未来N日收益
        返回 : Series(dates) IC 滚动均值
        """
        ic_series = []
        dates = []

        for i in range(self.lookback, len(factor_values)):
            f = factor_values.iloc[i - 1]  # T-1 日因子
            r = forward_returns.iloc[i]   # T 日收益
            valid = pd.concat([f, r], axis=1).dropna()
            if len(valid) < 20:
                continue
            ic = valid.iloc[:, 0].corr(valid.iloc[:, 1], method='spearman')
            ic_series.append(ic)
            dates.append(factor_values.index[i])

        ic = pd.Series(ic_series, index=dates)
        # 滚动均值
        ic_mean = ic.rolling(self.lookback // 3).mean()
        return ic_mean

    def get_timing_score(self, close_panel, market_cap_panel, forward_ret=5):
        """
        综合择时得分
        返回 : (score, strength_pct, ic_pct)
            score : -1 到 1，>0 表示适合小市值
            strength_pct : 相对强度分位数（0-1）
            ic_pct : IC溢价分位数（0-1）
        """
        # 1. 相对强度
        ratio = self.compute_style_strength(close_panel, market_cap_panel)
        if ratio.isna().all():
            return 0.0, 0.5, 0.5

        # 相对强度的历史分位数（当前值在历史中的位置）
        current_ratio = ratio.iloc[-1]
        hist_ratio = ratio.dropna()
        if len(hist_ratio) < 20:
            strength_pct = 0.5
        else:
            strength_pct = (hist_ratio < current_ratio).mean()

        # 2. IC 溢价（简化：用市值与未来收益的相关性）
        # 这里用最近 lookback 天的截面 IC 均值
        cap = market_cap_panel.iloc[-1]
        cap_sorted = cap.sort_values()
        small_stocks = cap_sorted.index[:max(5, len(cap_sorted) // 5)]

        ic_recent = []
        for i in range(max(1, forward_ret), min(self.lookback, len(close_panel))):
            idx = len(close_panel) - 1 - i
            if idx < 1:
                break
            f = market_cap_panel.iloc[idx]  # 市值（负值 = 小市值）
            r = close_panel.iloc[idx + forward_ret] / close_panel.iloc[idx] - 1 if idx + forward_ret < len(close_panel) else np.nan
            valid = pd.concat([f, r], axis=1).dropna()
            if len(valid) < 10:
                continue
            ic_val = valid.iloc[:, 0].corr(valid.iloc[:, 1], method='spearman')
            ic_recent.append(ic_val)

        if len(ic_recent) < 5:
            ic_pct = 0.5
        else:
            ic_mean = np.nanmean(ic_recent)
            # IC > 0 表示小市值有效
            ic_pct = 0.5 + 0.5 * np.clip(ic_mean * 10, -1, 1)  # 映射到 0-1

        # 3. 综合得分
        score = 0.6 * (strength_pct - 0.5) + 0.4 * (ic_pct - 0.5)
        score = np.clip(score * self.sensitivity, -1, 1)

        return score, strength_pct, ic_pct

    def get_adjusted_weight(self, base_weight, score):
        """
        根据择时得分调整 small_cap 权重
        score : -1 到 1
        返回 : 调整后的权重
        """
        # score > 0 → 加大权重，score < 0 → 减小权重
        adjusted = base_weight * (1 + score)
        return max(0, adjusted)  # 最小为0（完全关闭）


def compute_market_cap_panel(close_panel, outstanding_share_dict):
    """
    从收盘价和流通股本计算市值面板
    close_panel : DataFrame(dates × stocks)
    outstanding_share_dict : dict {code: outstanding_share} 最新流通股本
    返回 : DataFrame(dates × stocks) 市值
    """
    cap = pd.DataFrame(index=close_panel.index, columns=close_panel.columns)
    for code in close_panel.columns:
        if code in outstanding_share_dict:
            cap[code] = close_panel[code] * outstanding_share_dict[code]
    return cap.astype(float)

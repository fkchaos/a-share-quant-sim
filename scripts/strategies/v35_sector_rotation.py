#!/usr/bin/env python3
"""
scripts/strategies/v35_sector_rotation.py — v35 行业轮动叠加选股
====================================================
行业轮动不需要外部行业分类数据，使用市值分组作为行业代理：
- 大盘组（市值前20%）：蓝筹股动量
- 中盘组（市值20-50%）：成长股动量
- 小盘组（市值后50%）：小市值动量

核心逻辑：
1. 按成交额/市值将股票分为 3 组（大/中/小盘）
2. 计算各组的 5/20/60 日收益率（行业动量）
3. 行业动量 = 加权合成（0.4/0.3/0.3）
4. 选股时：优先选行业动量高的分组中的股票
5. 最终评分 = 个股评分 + 行业动量评分 × 权重

与 v27 集成方式：
- v35 作为独立策略运行，不依赖 v27
- 选股逻辑：动量过滤 + 行业动量加权

参考：中银量化行业轮动模型（2025年跑赢基准6-19%超额）
"""
import pandas as pd
import numpy as np

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.02,
    "TAKE_PROFIT": 0.05,
    "MAX_HOLDINGS": 8,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_MIN": 1,
    "HOLD_DAYS_EXTEND": 7,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    # 行业动量参数
    "SECTOR_MOM_WEIGHT": 0.30,     # 行业动量在综合评分中的权重
    "SECTOR_SHORT": 5,             # 短期动量窗口（日）
    "SECTOR_MID": 20,              # 中期动量窗口（日）
    "SECTOR_LONG": 60,             # 长期动量窗口（日）
    "SECTOR_W_SHORT": 0.4,         # 短期权重
    "SECTOR_W_MID": 0.3,          # 中期权重
    "SECTOR_W_LONG": 0.3,         # 长期权重
    "MOM_THRESHOLD": 0.01,         # 个股动量阈值
    # 市场状态
    "REGIME_ENABLED": True,
    "REGIME_MA_PERIOD": 20,
    "REGIME_SLOPE_DAYS": 5,
    "REGIME_BULL_ALLOC": 1.0,
    "REGIME_SIDEWAYS_ALLOC": 0.7,
    "REGIME_BEAR_ALLOC": 0.3,
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel,
                 open_panel=None, params=None):
    """
    计算 v35 行业轮动因子

    返回 dict:
        sector_momentum: 各股票所属行业分组动量
        stock_momentum: 个股动量（mom_5）
        size_group: 市值分组（large/mid/small）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    eps = 1e-10

    returns = close_panel.pct_change()
    mom_5 = close_panel.pct_change(5)

    # ── 市值分组（用成交额代理）──
    avg_amount = amount_panel.rolling(20).mean()
    # 截面排名：每天将所有股票按成交额分为 3 组
    # rank(pct=True) 返回 0~1 的百分位
    rank = avg_amount.rank(axis=1, pct=True)

    # 分组：large(>0.8), mid(0.5~0.8), small(<0.5)
    # 用更均衡的分组：large(>0.67), mid(0.33~0.67), small(<0.33)
    is_large = rank > 0.67
    is_mid = (rank > 0.33) & (rank <= 0.67)
    is_small = rank <= 0.33

    # ── 各组收益率（行业代理收益率）──
    # 大盘组收益
    large_ret = returns.where(is_large).mean(axis=1)
    # 中盘组收益
    mid_ret = returns.where(is_mid).mean(axis=1)
    # 小盘组收益
    small_ret = returns.where(is_small).mean(axis=1)

    # ── 行业动量（各组的多周期加权收益率）──
    def series_momentum(series, short, mid, long, w_s, w_m, w_l):
        """计算一个序列的多周期动量"""
        mom_s = series.rolling(short).mean()
        mom_m = series.rolling(mid).mean()
        mom_l = series.rolling(long).mean()
        return w_s * mom_s + w_m * mom_m + w_l * mom_l

    large_mom = series_momentum(large_ret, p["SECTOR_SHORT"], p["SECTOR_MID"], p["SECTOR_LONG"],
                                p["SECTOR_W_SHORT"], p["SECTOR_W_MID"], p["SECTOR_W_LONG"])
    mid_mom = series_momentum(mid_ret, p["SECTOR_SHORT"], p["SECTOR_MID"], p["SECTOR_LONG"],
                             p["SECTOR_W_SHORT"], p["SECTOR_W_MID"], p["SECTOR_W_LONG"])
    small_mom = series_momentum(small_ret, p["SECTOR_SHORT"], p["SECTOR_MID"], p["SECTOR_LONG"],
                               p["SECTOR_W_SHORT"], p["SECTOR_W_MID"], p["SECTOR_W_LONG"])

    # ── 为每只股票分配其所属分组动量 ──
    sector_momentum = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    sector_momentum = sector_momentum.mask(is_large, large_mom, axis=0)
    sector_momentum = sector_momentum.mask(is_mid, mid_mom, axis=0)
    sector_momentum = sector_momentum.mask(is_small, small_mom, axis=0)
    sector_momentum = sector_momentum.astype(float)

    # 分组标签（用于分析）
    size_group = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=object)
    size_group = size_group.mask(is_large, 'large', axis=0)
    size_group = size_group.mask(is_mid, 'mid', axis=0)
    size_group = size_group.mask(is_small, 'small', axis=0)

    return {
        'sector_momentum': sector_momentum,
        'stock_momentum': mom_5,
        'size_group': size_group,
    }


def select_stocks_v35(factors, date, current_holdings=None, params=None):
    """
    v35 选股：动量 + 行业动量加权

    逻辑：
    1. 个股动量 > 阈值
    2. 行业动量作为加分项
    3. 综合评分 = 个股动量 × (1 + 行业动量 × 权重)
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['sector_momentum'].index:
        return []

    sm = factors['sector_momentum'].loc[date].dropna()
    m5 = factors['stock_momentum'].loc[date].dropna()

    cands = []
    for code in sm.index:
        sector_mom = sm[code]
        if pd.isna(sector_mom):
            continue

        # 个股动量
        if code in m5.index:
            stock_mom = m5[code]
            if pd.isna(stock_mom) or stock_mom <= p["MOM_THRESHOLD"]:
                continue
        else:
            continue

        # 综合评分：个股动量 + 行业动量加权
        # 行业动量标准化到可比较范围
        score = stock_mom * 100 + sector_mom * 100 * p["SECTOR_MOM_WEIGHT"]
        cands.append((code, score))

    cands.sort(key=lambda x: x[1], reverse=True)

    if current_holdings:
        cands = [(c, s) for c, s in cands if c not in current_holdings]

    return cands

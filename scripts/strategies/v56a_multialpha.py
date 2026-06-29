#!/usr/bin/env python3
"""
scripts/strategies/v56a_multialpha.py — v56a 多空Alpha策略
====================================================
核心逻辑：同时追踪聪明钱（主力）和散户行为偏差，构建双向alpha信号。

因子体系（7个因子）：
 正向（聪明钱追踪）：
    - smart_q：聪明钱情绪因子（分钟价量识别机构参与度）
    - volflow_resid：大单残差资金流（剔除反转效应后的主力信号）
    - chip_score：筹码得分（集中度+底部锁定）
 反向（散户偏差利用）：
    - retail_resid：小单残差因子（散户过度追随的反向信号）
    - herding_score：散户羊群效应得分
    - reversal_score：连板后反转概率（连板数越多越不稳定）
 基础过滤：
    - quality_score：质量底子（ROE+低波+规模）

与 v39g（当前标杆，Sharpe 1.297）的区别：
1. v39g 用价量因子（mom_5/pv_corr/turnover/illiq/size）
2. v56a 新增微观结构/筹码/行为因子，旨在捕获 v39g 未覆盖的 alpha 源
3. 同时做正向+反向，理论上在 v39g 基础上增厚收益、降低回撤

版本历史：
- v56a: 初始版，8因子多空Alpha → 证伪（8因子中仅mom_5有效）
- v56b: 简化版，仅保留IC/WF验证有效的 mom_5 + quality_score
"""
import pandas as pd
import numpy as np
from core.db import get_float_shares_map

DEFAULT_PARAMS = {
    # ── 风控参数（与标杆条件一致）──
    "STOP_LOSS": -0.015,
    "TAKE_PROFIT": 0.03,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 8,
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 2,

    # ── 选股池参数 ──
    "EXCLUDE_ST": True,
    "EXCLUDE_NEW": True,
    "EXCLUDE_LIMIT_UP": True,
    "EXCLUDE_SUSPENDED": True,
    "MIN_AMOUNT_DAYS": 5000000,  # 最低日均成交额 500万

    # ── 评分权重（v56b: 仅保留有效因子）──
    # IC/WF验证有效：mom_5(IR=+0.05, WF_Sharpe=1.09), quality_score(作为过滤)
    # 剔除：reversal_score(幸存者偏差-连板封板买不进), smart_q/retail/herding/chip/volflow(IC≈0或反向)
    "W_MOM5": 0.55,              # 5日动量（确认有效）
    "W_QUALITY": 0.45,           # 低波质量过滤

    # ── 因子参数 ──
    "SMART_Q_MINUTES": 30,        # 聪明钱识别的分钟窗口
    "VOLFLOW_LOOKBACK": 5,       # 资金流回看天数
    "CHIP_HISTORY": 252,         # 筹码历史窗口（约1年）
    "RETAIL_SENSITIVITY": 20,   # 散户敏感度窗口
    "HERDING_WINDOW": 5,         # 羊群效应窗口
    "REVERSAL_MAX_STREAK": 5,     # 连板惩罚上限
}


def calc_factors(close_panel, volume_panel, amount_panel,
                 high_panel, low_panel, open_panel=None, params=None, extra_data=None):
    """
    v56a 因子计算：多空Alpha双引擎
    """
    p = params or {}
    factors = {}
    float_shares_map = extra_data.get('float_shares_map', {}) if extra_data else {}
    if not float_shares_map:
        float_shares_map = get_float_shares_map()

    # ========== 因子1: mom_5（5日动量，v39g核心因子保留）==========
    if close_panel is not None and len(close_panel) > 0:
        factors['mom_5'] = close_panel.pct_change(periods=5)

    # ========== 因子2: smart_q（聪明钱情绪因子）==========
    # 逻辑：当收盘价在当日高位且成交量大 → 聪明钱在资金推动
    # smart_q = (close - low) / (high - low) 在放量日的加权平均
    if close_panel is not None and volume_panel is not None and len(close_panel) > 0:
        price_position = (close_panel - low_panel) / (high_panel - low_panel).replace(0, np.nan)
        # 聪明钱情绪 = 价格位置 × 成交量权重（5日均量做归一化）
        vol_norm = volume_panel / volume_panel.rolling(5, min_periods=1).mean()
        smart_raw = price_position * vol_norm
        # 取5日平均聪明钱情绪
        factors['smart_q'] = smart_raw.rolling(5, min_periods=3).mean()

    # ========== 因子3: volflow_resid（残差资金流，剔除反转效应）==========
    # 逻辑：大单资金流中的"非价格解释"部分才是纯主力信号
    # 简化实现：对每只股票，5日收益与5日成交量变化的相关性即为资金流强度
    # 进一步剔除市场beta（即与市场整体资金流的相关性）
    if close_panel is not None and volume_panel is not None and len(close_panel) > 0:
        # 5日动量 × 5日量变 = 原始资金流
        vol_change = volume_panel.pct_change(periods=p.get('VOLFLOW_LOOKBACK', 5))
        mom_5 = close_panel.pct_change(periods=5)
        raw_flow = mom_5 * vol_change.clip(lower=-3, upper=3)

        # 截面去均值（近似截距去除）
        # 这就是动量-量价共振在截面上的残差
        flow_demean = raw_flow.sub(raw_flow.mean(axis=1), axis=0)
        mom_demean = mom_5.sub(mom_5.mean(axis=1), axis=0)

        # 截面 beta = cov(demean_flow, demean_mom) / var(demean_mom)
        beta_numerator = (flow_demean * mom_demean).sum(axis=1)
        beta_denominator = (mom_demean * mom_demean).sum(axis=1).replace(0, 1)
        beta = (beta_numerator / beta_denominator).fillna(0)

        # 残差 = raw_flow - beta * mom_5 (逐列独立计算)
        # 关键修复：beta 是 Series，需要用 mul 的 axis=0 对齐
        beta_mom = beta.values.reshape(-1, 1) * mom_5.values  # (1551,) x (1551,1800)
        beta_mom_df = pd.DataFrame(beta_mom, index=mom_5.index, columns=mom_5.columns)
        factors['volflow_resid'] = (raw_flow - beta_mom_df).clip(lower=-3, upper=3)

    # ========== 因子4: chip_score（筹码得分，基于可获取的价量数据估算）==========
    # 注：本框架无逐笔筹码数据，用价量分布特征估算"类筹码"信号
    # 方式：成交量在低价区集中（吸筹迹象）+ 换手率递减（锁仓迹象）
    if close_panel is not None and volume_panel is not None and len(close_panel) > 0:
        n_days = min(p.get('CHIP_HISTORY', 252), len(close_panel))

        # 子指标1：量价偏离度（上涨但量缩 = 筹码锁定）
        ret_chg = close_panel.pct_change(periods=5)
        vol_chg = volume_panel.pct_change(periods=5)
        # 量价背离 = 收益率上涨但成交量下降，得分高
        price_up_mask = ret_chg > 0
        vol_shrink_mask = vol_chg < 0
        vp_divergence = ret_chg.clip(lower=0) * (-vol_chg).clip(lower=0)
        vp_divergence = vp_divergence * (price_up_mask & vol_shrink_mask).astype(float)
        vol_expand = ret_chg.clip(lower=0) * vol_chg.clip(lower=0)
        vp_divergence = vp_divergence + vol_expand * 0.5  # 量价同向也加分
        factors['chip_score'] = vp_divergence.rolling(5, min_periods=3).mean()

        # 子指标2：换手率稳定性（换手率方差低 = 筹码稳定）
        if float_shares_map:
            float_series = pd.Series(float_shares_map).reindex(volume_panel.columns).fillna(0)
            turnover = volume_panel / float_series.replace(0, np.nan)
        elif amount_panel is not None:
            turnover = volume_panel * close_panel / amount_panel
        else:
            turnover = volume_panel / volume_panel.rolling(20, min_periods=5).mean()

        # 换手率稳定性 = 20日换手率标准差的倒数（越低越稳定）
        turnover_std = turnover.rolling(20, min_periods=10).std()
        factors['chip_stability'] = 1.0 / turnover_std.replace(0, np.nan)

        # 综合筹码得分（取两者的截面 z-score 加权平均）
        chip1_z = _zscore(vp_divergence)
        chip2_z = _zscore(turnover_std)
        factors['chip_score'] = chip1_z * 0.6 + (1 - chip2_z) * 0.4

    # ========== 因子5: retail_resid（小单残差因子 - 散户行为反向）==========
    # 逻辑：当个股5日内涨幅高且同时换手率暴涨 → 散户大量涌入 → 负向信号
    if close_panel is not None and volume_panel is not None and len(close_panel) > 0:
        momentum = close_panel.pct_change(periods=p.get('RETAIL_SENSITIVITY', 20))

        # 换手率相对变化（近似小单活跃度）
        if float_shares_map:
            float_series = pd.Series(float_shares_map).reindex(volume_panel.columns).fillna(0)
            turnover = volume_panel / float_series.replace(0, np.nan)
        else:
            turnover = volume_panel / volume_panel.rolling(20, min_periods=5).mean()

        # 散户热度 = 动量 × 换手率 （散户追涨强度）
        retail_heat = momentum * turnover

        # 散户残差 = 散户热度 - 可由市盈率/行业解释的部分（简化：截面去均值）
        factors['retail_resid'] = retail_heat.sub(retail_heat.mean(axis=1), axis=0)

    # ========== 因子6: herding_score（羊群效应得分）==========
    # 逻辑：追涨的股票中，如果涨跌幅与换手率高度同步 → 羊群效应强
    if close_panel is not None and volume_panel is not None and len(close_panel) > 0:
        moment = close_panel.pct_change(periods=p.get('HERDING_WINDOW', 5))
        # 换手率变化率（方向与动量一致 = 追涨）
        vol_norm = volume_panel / volume_panel.rolling(20, min_periods=5).mean()

        # 羊群得分 = 正向动量 × 相对成交量（追涨一致性得分）
        # 如果是追涨 → 得分高 → 作为反向因子使用（高分看跌）
        pos_mom = moment.clip(lower=0)
        high_vol = vol_norm.clip(lower=1.0)  # 放量才计数
        factors['herding_score'] = (pos_mom * high_vol).rolling(3, min_periods=2).mean()

    # ========== 因子7: reversal_score（连板惩罚得分）==========
    # 逻辑：连续涨停后的反转概率快速上升，用连板计数做负向打分
    if close_panel is not None and len(close_panel) > 0:
        # 辨识涨停（涨幅≥9.9%）
        daily_ret = close_panel.pct_change(periods=1)
        is_limit_up = (daily_ret >= 0.099).astype(int)
        # 连续涨停天数
        streak = is_limit_up.copy()
        for i in range(1, len(is_limit_up)):
            streak.iloc[i] = (streak.iloc[i-1] + 1) * is_limit_up.iloc[i]
        factors['reversal_score'] = streak / p.get('REVERSAL_MAX_STREAK', 5)

    # ========== 因子8: quality_score（质量过滤得分）==========
    if close_panel is not None and len(close_panel) > 0:
        daily_ret = close_panel.pct_change(periods=1)
        # 低波动率得分（20日波动率越低得分越高）
        vol20 = daily_ret.rolling(20, min_periods=10).std()
        factors['quality_score'] = 1.0 / vol20.replace(0, np.nan)

    return factors


def select_stocks_v56a(factors, date, current_holdings=None, params=None,
                       sold_recently=None, extra_data=None):
    """
    v56b 选股：仅使用单因子 WF 验证有效的因子
    保留：mom_5 + quality_score
    剔除：reversal_score(幸存者偏差)、smart_q/retail/herding/chip/volflow(IC≈0或反向)
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if 'mom_5' not in factors or date not in factors['mom_5'].index:
        return []

    mom = factors['mom_5'].loc[date].dropna()
    candidates = list(mom.index)

    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    scores = pd.Series(0.0, index=candidates)

    # mom_5 (正向，确认有效)
    if p.get("W_MOM5", 0) > 0 and 'mom_5' in factors:
        s = _score_column(factors, date, 'mom_5')
        scores += s.reindex(candidates).fillna(0) * p["W_MOM5"]

    # quality_score (正向)
    if p.get("W_QUALITY", 0) > 0 and 'quality_score' in factors:
        s = _score_column(factors, date, 'quality_score')
        scores += s.reindex(candidates).fillna(0) * p["W_QUALITY"]

    scores = scores.sort_values(ascending=False)
    n = min(p["MAX_DAILY_BUY"], len(scores))
    selected = scores.index[:n]

    return [(code, scores[code]) for code in selected]


# ══════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════

def _score_column(factors, date, col):
    """将因子值归一化到 [0, 1]（percentile 排名）"""
    if col not in factors or date not in factors[col].index:
        return pd.Series(dtype=float)
    s = factors[col].loc[date].dropna()
    if len(s) <= 1:
        return pd.Series(0.5, index=s.index)
    return s.rank(pct=True, method='average')


def _zscore(panel):
    """截面 z-score 标准化（逐日处理）"""
    mean = panel.mean(axis=1)
    std = panel.std(axis=1).replace(0, np.nan)
    return (panel.sub(mean, axis=0)).div(std, axis=0)

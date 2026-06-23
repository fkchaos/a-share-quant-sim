#!/usr/bin/env python3
"""
scripts/strategies/v40_factor_exit.py — v40 因子恶化卖出 + 延迟止盈止损
====================================================================
目的：在 v39c 多因子评分体系基础上，增加"因子维度"的卖出判断

设计：
- 复用 v39c 的 calc_factors + 评分逻辑
- 每日对持仓股重新评分，评分低于 SELL_THRESHOLD → 卖出候选
- 卖出候选中，若评分 > BUY_BACK_THRESHOLD（说明因子又回来了）→ 延迟止盈止损
- 三层卖出优先级：硬风控 > 因子恶化 > 正常持有

新增参数：
- SELL_THRESHOLD: 持仓评分低于此值触发卖出（默认 0.35）
- BUY_BACK_THRESHOLD: 卖出候选评分高于此值延迟卖出（默认 0.65）
- SELL_PENALTY_N: 连续N天低于阈值才卖出（默认 3，防单日噪声）
- VERSION: 策略版本号
"""
import pandas as pd
import numpy as np

VERSION = "v40"

DEFAULT_PARAMS = {
    # ── 风控参数（与 v39c 一致）──
    "STOP_LOSS": -0.015,
    "TAKE_PROFIT": 0.03,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 8,
    "COOLDOWN_DAYS": 0,

    # ── 选股门槛（与 v39c 一致）──
    "MOM_THRESHOLD": 0.03,
    "PV_CORR_10_MIN": -0.5,
    "PV_CORR_20_MIN": 0.0,
    "BOLL_W_MIN": 0.0,

    # ── 评分权重（与 v39c 完全一致，sum=1.0）──
    "W_MOM": 0.20,
    "W_PV_CORR": 0.05,
    "W_TURNOVER": 0.10,
    "W_SIZE": 0.10,
    "W_FUND_FLOW": 0.15,
    "W_GAP": 0.10,
    "W_ILLIQ": 0.10,

    # ── v40 新增：因子恶化卖出参数 ──
    # 绝对阈值法问题：v39c 选出的好股票正常波动也会跌到阈值以下（如 0.18）
    # momentum 模式：跟踪每只股票持仓期间的最高评分，从高点回落才触发
    # MOMENTUM_DROP_PCT=0.15：评分从高点回落超15%→因子确实恶化（非正常波动）
    "SELL_THRESHOLD": 0.20,        # threshold模式阈值（备用）
    "BUY_BACK_THRESHOLD": 0.30,    # 延迟卖出阈值（threshold模式）
    "SELL_PENALTY_N": 1,           # threshold模式的连续天数
    "SELL_MODE": "momentum",       # 默认使用 momentum 模式（更精准捕获真实恶化）
    "MOMENTUM_DROP_PCT": 0.20,     # 评分从持仓高点回落超20% → 因子恶化确认
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None):
    """
    计算因子面板（完全复用 v39c 逻辑）

    返回 dict: {factor_name: DataFrame(index=date, columns=code)}
    """
    eps = 1e-10
    returns = close_panel.pct_change()
    mom_5 = close_panel.pct_change(5)

    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps) if open_panel is not None else returns * 0

    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)

    vol_5 = volume_panel.rolling(5).mean()
    vr = vol_5 / (volume_panel.rolling(20).mean() + eps)

    def _pcorr(window):
        rm = returns.rolling(window).mean()
        vrm = vr.rolling(window).mean()
        cov = ((returns - rm) * (vr - vrm)).rolling(window).mean()
        return cov / (returns.rolling(window).std() * vr.rolling(window).std() + eps)

    pv_corr_10 = _pcorr(10)
    pv_corr_20 = _pcorr(20)

    price_level = close_panel.rolling(20).mean()
    price_trend = close_panel.pct_change(20)
    vol_shrink = vol_5 / (volume_panel.rolling(20).mean() + eps)
    vol_current = returns.rolling(5).std()
    vol_hist = returns.rolling(60).std()
    vol_abnormal = vol_current / (vol_hist + eps)

    def _zscore(df):
        m = df.mean(axis=1)
        s = df.std(axis=1)
        return (df.sub(m, axis=0)).div(s + eps, axis=0)

    delist_risk = (-_zscore(price_level) + -_zscore(price_trend) +
                   -_zscore(vol_shrink) + _zscore(vol_abnormal)) / 4.0
    dr_threshold = delist_risk.quantile(0.9, axis=1)

    amount_5d = amount_panel.rolling(5).mean()

    # 换手率
    turnover = volume_panel / (amount_panel / (close_panel + eps) + eps)
    turnover_avg = turnover.rolling(5).mean()

    # 市值弹性（小市值代理）
    est_market_cap = amount_panel.rolling(20).mean() * 20
    size_factor = 1.0 / (np.log(est_market_cap / 1e8 + 1) / 10 + eps)

    # 非流动性
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e8 + eps)

    # 动量质量
    path_vol_5 = returns.rolling(5).std()
    mom_quality = mom_5 / (path_vol_5 * np.sqrt(5) + eps)

    # 资金流强度
    up_days = returns.copy()
    down_days = returns.copy()
    up_days[returns <= 0] = np.nan
    down_days[returns >= 0] = np.nan
    up_vol = up_days * volume_panel
    down_vol = down_days.abs() * volume_panel
    up_vol_sum = up_vol.rolling(10).sum()
    down_vol_sum = down_vol.rolling(10).sum()
    fund_flow = up_vol_sum / (down_vol_sum + eps)

    return {
        'mom_5': mom_5, 'gap_ratio': gap_ratio,
        'boll_w': boll_w, 'pv_corr_10': pv_corr_10, 'pv_corr_20': pv_corr_20,
        'delist_risk': delist_risk, 'dr_threshold': dr_threshold,
        'amount_5d': amount_5d, 'turnover_avg': turnover_avg,
        'size_factor': size_factor, 'illiq': illiq,
        'mom_quality': mom_quality, 'fund_flow': fund_flow,
    }


def score_stocks(factors, date, params, codes=None):
    """
    对指定日期的股票进行多因子评分（0~1 归一化）

    注意：归一化始终基于全市场（与 v39c 一致），codes 参数只用于返回子集。
    这确保持仓股评分和选股候选评分在同一尺度上可比。

    Parameters
    ----------
    factors : dict — calc_factors 返回的因子面板
    date : 评分日期
    params : DEFAULT_PARAMS
    codes : list, optional — 只返回这些代码的分数。None=返回全市场

    Returns
    -------
    Series: index=code, value=score (0~1)
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['mom_5'].index:
        return pd.Series(dtype=float)

    # 全市场评分（归一化范围与 v39c 一致）
    all_scores = pd.Series(0.0, index=factors['mom_5'].columns)

    # ① 动量评分
    mom_scores = _score_column(factors, date, 'mom_5')
    all_scores += mom_scores.reindex(all_scores.index).fillna(0) * p["W_MOM"]

    # ② 量价共振评分
    pv_scores = _score_column(factors, date, 'pv_corr_20')
    all_scores += pv_scores.reindex(all_scores.index).fillna(0) * p["W_PV_CORR"]

    # ③ 换手率评分
    to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
    all_scores += to_scores.reindex(all_scores.index).fillna(0) * p["W_TURNOVER"]

    # ④ 市值弹性评分
    sf_scores = _score_column(factors, date, 'size_factor')
    all_scores += sf_scores.reindex(all_scores.index).fillna(0) * p["W_SIZE"]

    # ⑤ 资金流强度评分
    ff_scores = _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0)
    all_scores += ff_scores.reindex(all_scores.index).fillna(0) * p["W_FUND_FLOW"]

    # ⑥ 跳空评分
    gap_scores = _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05)
    all_scores += gap_scores.reindex(all_scores.index).fillna(0) * p["W_GAP"]

    # ⑦ 非流动性评分
    illiq_scores = _score_column(factors, date, 'illiq')
    all_scores += illiq_scores.reindex(all_scores.index).fillna(0) * p["W_ILLIQ"]

    # 去掉全零行（未参与评分的股票）
    all_scores = all_scores[all_scores > 0]

    if codes is not None:
        # 只返回指定代码的分数
        valid_codes = [c for c in codes if c in all_scores.index]
        return all_scores.loc[valid_codes]
    return all_scores


def _score_column(factors, date, col, clip_min=None, clip_max=None):
    """横截面 zscore 归一化到 [0, 1]"""
    if date not in factors[col].index:
        return pd.Series(dtype=float)
    s = factors[col].loc[date].dropna()
    if clip_min is not None:
        s = s.clip(lower=clip_min)
    if clip_max is not None:
        s = s.clip(upper=clip_max)
    if s.max() == s.min():
        return pd.Series(0.5, index=s.index)
    return (s - s.min()) / (s.max() - s.min())


def select_stocks_v40(factors, date, current_holdings=None, params=None,
                      sold_recently=None):
    """
    v40 选股：v39c 选股逻辑 + 因子恶化卖出判断

    与 v39c 的区别：
    - 对持仓股单独评分，评分 < SELL_THRESHOLD → 加入卖出候选
    - 卖出候选中评分 > BUY_BACK_THRESHOLD → 移入 hold_plan（延迟止盈止损）
    - 正常选股 top N → buy_plan

    Parameters
    ----------
    factors : dict — calc_factors 返回
    date : 评分日期
    current_holdings : dict — 当前持仓 {code: {shares, cost_price, ...}}
    params : DEFAULT_PARAMS
    sold_recently : set — 近期卖出的代码（cooldown）

    Returns
    -------
    list[(code, score)] — 买入候选（已排除卖出候选和已持有）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['mom_5'].index:
        return []

    # ── 1. 持仓股评分（因子恶化检查）──
    hold_scores = pd.Series(dtype=float)
    if current_holdings:
        hold_codes = list(current_holdings.keys())
        hold_scores = score_stocks(factors, date, p, codes=hold_codes)

    # ── 2. 筛选因子恶化卖出候选 ──
    sell_threshold = p.get("SELL_THRESHOLD", 0.35)
    buyback_threshold = p.get("BUY_BACK_THRESHOLD", 0.65)

    factor_sell_candidates = []
    for code, score in hold_scores.items():
        if score < sell_threshold:
            factor_sell_candidates.append((code, score))

    # ── 3. 正常选股（排除已持有 + 卖出候选）──
    hold_set = set(current_holdings.keys()) if current_holdings else set()
    sell_cand_set = set(c for c, _ in factor_sell_candidates)
    exclude_set = hold_set | sell_cand_set
    if sold_recently:
        exclude_set |= sold_recently

    # 门槛过滤
    cands = _apply_gates(factors, date, p)
    cands = [c for c in cands if c not in exclude_set]

    if not cands:
        return []

    # 评分排序
    all_scores = score_stocks(factors, date, p)
    cands_scores = all_scores.reindex(cands).dropna()
    cands_scores = cands_scores.sort_values(ascending=False)

    max_daily_buy = p["MAX_DAILY_BUY"]
    selected = cands_scores.index[:max_daily_buy]

    return [(code, cands_scores[code]) for code in selected]


def _apply_gates(factors, date, params):
    """选股门槛过滤（与 v39c 一致）"""
    if date not in factors['mom_5'].index:
        return []

    mom_t = factors['mom_5'].loc[date]
    threshold = params.get("MOM_THRESHOLD", 0.03)
    candidates = [c for c in mom_t.index if mom_t[c] > threshold]

    # PV_CORR_10 门槛
    if 'pv_corr_10' in factors and date in factors['pv_corr_10'].index:
        pv_t = factors['pv_corr_10'].loc[date]
        pv_min = params.get("PV_CORR_10_MIN", -0.5)
        candidates = [c for c in candidates if c in pv_t.index and pv_t[c] > pv_min]

    # 退市风险
    if 'dr_threshold' in factors and date in factors['dr_threshold'].index:
        dr_t = factors['dr_threshold'].loc[date]
        candidates = [c for c in candidates
                      if c not in factors['delist_risk'].columns
                      or factors['delist_risk'].loc[date, c] <= dr_t]

    return candidates


def check_factor_exit(factors, date, current_holdings, params, sell_penalty_tracker):
    """
    因子恶化卖出判断（供 account_runner 调用）

    支持两种卖出触发模式（通过 SELL_MODE 参数切换）：
    - "threshold": 评分 < SELL_THRESHOLD 连续 SELL_PENALTY_N 天 → 卖出
    - "momentum": 评分从高点回落 > MOMENTUM_DROP_PCT → 卖出（实验B）

    Parameters
    ----------
    factors : dict — 因子面板
    date : 当前日期
    current_holdings : dict — 当前持仓
    params : 策略参数
    sell_penalty_tracker : dict — {code: {"count": n, "high_score": s, ...}}

    Returns
    -------
    tuple: (to_sell, to_defer, tracker)
        to_sell: list[(code, score)] — 确认卖出
        to_defer: list[(code, score)] — 延迟卖出（买回）
        tracker: dict — 更新后的 tracker
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    sell_threshold = p.get("SELL_THRESHOLD", 0.35)
    buyback_threshold = p.get("BUY_BACK_THRESHOLD", 0.65)
    penalty_n = p.get("SELL_PENALTY_N", 1)
    sell_mode = p.get("SELL_MODE", "threshold")  # "threshold" or "momentum"
    momentum_drop_pct = p.get("MOMENTUM_DROP_PCT", 0.30)  # 评分从高点回落30%

    to_sell = []
    to_defer = []

    if not current_holdings or date not in factors['mom_5'].index:
        return to_sell, to_defer, sell_penalty_tracker

    hold_codes = list(current_holdings.keys())
    hold_scores = score_stocks(factors, date, p, codes=hold_codes)

    for code, score in hold_scores.items():
        tracker = sell_penalty_tracker.get(code, {})

        if sell_mode == "momentum":
            # ── 模式B：评分动量（从高点回落）──
            high_score = tracker.get("high_score", score)
            if score > high_score:
                high_score = score
            tracker["high_score"] = high_score

            # 计算从高点的回落幅度
            if high_score > 0:
                drop_pct = (high_score - score) / high_score
            else:
                drop_pct = 0

            if drop_pct >= momentum_drop_pct:
                to_sell.append((code, score))
                tracker = {}  # 重置
            elif score >= buyback_threshold:
                to_defer.append((code, score))

        else:
            # ── 模式A：阈值法（默认）──
            if score >= sell_threshold:
                tracker = {}  # 重置
            else:
                tracker["count"] = tracker.get("count", 0) + 1

                if tracker["count"] >= penalty_n:
                    to_sell.append((code, score))
                    tracker = {}  # 重置
                elif score >= buyback_threshold:
                    to_defer.append((code, score))

        sell_penalty_tracker[code] = tracker

    # 清理已不在持仓的 tracker
    stale = [c for c in sell_penalty_tracker if c not in current_holdings]
    for c in stale:
        del sell_penalty_tracker[c]

    return to_sell, to_defer, sell_penalty_tracker


# ── v40b: 纯轮动逻辑（每日卖最低4只 + 买最高4只，无硬风控）──

DEFAULT_PARAMS_V40B = {
    **DEFAULT_PARAMS,
    "STRATEGY_NAME": "v40b",
    "SELL_COUNT": 4,          # 每日卖出持仓中评分最低的 N 只
    "BUY_COUNT": 4,           # 每日买入全市场评分最高的 N 只
    "NO_HARD_RISK": True,     # 标记：跳过硬风控检查
}


def select_stocks_v40b(factors, date, current_holdings=None, params=None,
                       sold_recently=None):
    """
    v40b 纯轮动选股：每日无条件卖出持仓评分最低4只 + 买入全市场评分最高4只

    与 v40 的区别：
    - 无条件轮动：每天必须卖 SELL_COUNT 只 + 买 BUY_COUNT 只
    - 无硬风控：不检查止损/止盈/持有期
    - 持仓可能 < SELL_COUNT（轻仓时不强制卖）

    Returns
    -------
    tuple: (sell_list, buy_list)
        sell_list: list[(code, score)] — 卖出候选（按评分升序，最低分在前）
        buy_list: list[(code, score)] — 买入候选（按评分降序，最高分在前）
    """
    p = {**DEFAULT_PARAMS_V40B, **(params or {})}
    sell_count = p.get("SELL_COUNT", 4)
    buy_count = p.get("BUY_COUNT", 4)

    if date not in factors['mom_5'].index:
        return [], []

    # ── 1. 持仓股评分 → 卖出最低分（空仓时不卖）──
    if current_holdings:
        hold_codes = list(current_holdings.keys())
        hold_scores = score_stocks(factors, date, p, codes=hold_codes)
        hold_scores = hold_scores.sort_values(ascending=True)  # 最低分在前
        sell_list = [(code, score) for code, score in hold_scores.head(sell_count).items()]
    else:
        sell_list = []

    # ── 2. 全市场评分 → 买入最高分（排除已持有）──
    hold_set = set(current_holdings.keys()) if current_holdings else set()
    all_scores = score_stocks(factors, date, p)
    all_scores = all_scores.drop(labels=list(hold_set), errors='ignore')
    all_scores = all_scores.sort_values(ascending=False)  # 最高分在前
    buy_list = [(code, score) for code, score in all_scores.head(buy_count).items()]

    return sell_list, buy_list

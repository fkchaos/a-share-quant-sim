#!/usr/bin/env python3
"""
scripts/v28_kronos.py — v28 Kronos AI 增强选股函数
===================================================
基于 v27 价量共振初筛 + Kronos 预测因子二次排序。

架构：
  1. v27 初筛：mom_5 > 2% + 价量共振，选出 Top N 候选
  2. Kronos 预测：对候选股跑预测，提取 kronos_ret / kronos_conf / kronos_trend
  3. 综合评分：final_score = v27_score + alpha * kronos_ret * kronos_conf

函数签名（与 v27 兼容）：
    calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel) -> dict
    select_stocks(factors, date, current_holdings=None) -> list[(code, score)]
"""
import sys
import os
import numpy as np
import pandas as pd

# 确保能 import v27 和 core 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from scripts.strategies.v27_select import calc_factors as v27_calc_factors
from scripts.strategies.v27_select import select_stocks_v27, DEFAULT_PARAMS as V27_DEFAULT_PARAMS

# ── v28 参数 ──
DEFAULT_PARAMS = {
    **V27_DEFAULT_PARAMS,
    # Kronos 相关
    "KRONOS_ENABLED": True,           # 是否启用 Kronos 增强
    "KRONOS_ALPHA": 0.5,              # kronos_ret 在综合评分中的权重
    "KRONOS_CANDIDATE_N": 50,         # v27 初筛后取 Top N 跑 Kronos
    "KRONOS_PRED_LEN": 5,             # 预测未来 N 天
    "KRONOS_LOOKBACK": 200,           # 回看 K 线数
    "KRONOS_T": 0.8,                  # 采样温度
    "KRONOS_TOP_P": 0.85,             # nucleus sampling
    "KRONOS_SAMPLE_COUNT": 5,         # 蒙特卡洛采样次数
    "KRONOS_DEVICE": "cpu",           # cpu / cuda:0
    "KRONOS_MODEL": "small",          # small / base
    "KRONOS_CONF_THRESHOLD": 0.3,     # kronos_conf 低于此值降权
}


def _load_kronos_model(params):
    """
    懒加载 Kronos 模型（全局缓存，避免重复加载）
    返回 (predictor, loaded) — loaded=False 表示模型不可用
    """
    cache = _load_kronos_model.__dict__
    if "predictor" in cache:
        return cache["predictor"], True

    try:
        from model import Kronos, KronosTokenizer, KronosPredictor
    except ImportError:
        print("[v28] Kronos 模型代码不可用，跳过 AI 增强")
        cache["predictor"] = None
        return None, False

    model_name = params.get("KRONOS_MODEL", "small")
    device = params.get("KRONOS_DEVICE", "cpu")

    if model_name == "small":
        tokenizer_name = "NeoQuasar/Kronos-Tokenizer-base"
        model_hf_name = "NeoQuasar/Kronos-small"
    else:
        tokenizer_name = "NeoQuasar/Kronos-Tokenizer-base"
        model_hf_name = "NeoQuasar/Kronos-base"

    try:
        print(f"[v28] 加载 Kronos-{model_name} 模型...")
        tokenizer = KronosTokenizer.from_pretrained(tokenizer_name)
        model = Kronos.from_pretrained(model_hf_name)
        model.to(device)
        model.eval()
        predictor = KronosPredictor(model, tokenizer, max_context=512)
        cache["predictor"] = predictor
        print(f"[v28] Kronos-{model_name} 模型加载完成")
        return predictor, True
    except Exception as e:
        print(f"[v28] Kronos 模型加载失败: {e}")
        cache["predictor"] = None
        return None, False


def _get_kline_from_db(code, lookback, date_str):
    """
    从 DB 读取单只股票的日K线数据。
    返回 DataFrame (columns: open, high, low, close, volume, amount, date) 或 None
    """
    try:
        from core.db import get_kline
        rows = get_kline(code, limit=lookback + 10)
        if not rows or len(rows) < lookback * 0.5:
            return None
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        # 只取 date_str 之前的数据
        cutoff = pd.Timestamp(date_str)
        df = df[df['date'] < cutoff].tail(lookback)
        if len(df) < 20:
            return None
        return df
    except Exception:
        return None


def _predict_single(predictor, df_hist, pred_len, params):
    """
    对单只股票跑 Kronos 预测。
    返回 dict: {kronos_ret, kronos_conf, kronos_trend, kronos_vol} 或 None
    """
    try:
        T = params.get("KRONOS_T", 0.8)
        top_p = params.get("KRONOS_TOP_P", 0.85)
        sample_count = params.get("KRONOS_SAMPLE_COUNT", 5)

        # 准备输入
        x_timestamp = df_hist['date']
        # 生成未来时间戳（按交易日近似 = 自然日 * 5/7）
        last_date = df_hist['date'].iloc[-1]
        future_dates = pd.date_range(
            start=last_date + pd.Timedelta(days=1),
            periods=pred_len,
            freq='B'  # 工作日
        )
        y_timestamp = future_dates[:pred_len]

        # 调用预测
        pred_df = predictor.predict(
            df=df_hist,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_len,
            T=T,
            top_p=top_p,
            sample_count=sample_count,
            verbose=False,
        )

        if pred_df is None or len(pred_df) == 0:
            return None

        # 提取因子
        last_close = df_hist['close'].iloc[-1]
        pred_close = pred_df['close'].values
        pred_volume = pred_df['volume'].values if 'volume' in pred_df.columns else np.zeros(pred_len)

        # 预测收益率
        kronos_ret = (pred_close[-1] - last_close) / last_close

        # 预测置信度（多次采样的标准差 → 这里用预测序列的波动率代理）
        kronos_conf = 1.0 / (np.std(pred_close / last_close - 1) + 0.01)
        kronos_conf = min(kronos_conf, 5.0)  # 截断

        # 趋势强度（线性回归斜率）
        x = np.arange(len(pred_close))
        if len(pred_close) > 1:
            slope = np.polyfit(x, pred_close, 1)[0]
            kronos_trend = slope / last_close  # 归一化
        else:
            kronos_trend = 0.0

        # 预测波动率
        if len(pred_close) > 1:
            kronos_vol = np.std(np.diff(pred_close) / pred_close[:-1])
        else:
            kronos_vol = 0.0

        return {
            'kronos_ret': kronos_ret,
            'kronos_conf': kronos_conf,
            'kronos_trend': kronos_trend,
            'kronos_vol': kronos_vol,
        }
    except Exception as e:
        return None


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None):
    """
    v28 因子计算 = v27 因子 + Kronos 预测因子（可选）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # 先算 v27 因子
    factors = v27_calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, p)

    # Kronos 因子在 select 阶段计算（需要逐只预测，不适合面板模式）
    # 这里只做标记，实际预测在 select_stocks 里做
    factors['_kronos_enabled'] = p.get("KRONOS_ENABLED", True)
    factors['_kronos_params'] = p

    return factors


def select_stocks_v28(factors, date, current_holdings=None, params=None):
    """
    v28 选股：v27 初筛 + Kronos 增强排序

    参数:
        factors: dict — calc_factors() 返回
        date: Timestamp — 选股日期
        current_holdings: dict — 当前持仓（可选）
        params: dict — 覆盖默认参数

    返回:
        list[(code, score)] — 按评分降序排列
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── Step 1: v27 初筛 ──
    v27_cands = select_stocks_v27(factors, date, current_holdings, p)

    if not v27_cands:
        return []

    # ── Step 2: Kronos 增强（可选）──
    kronos_enabled = p.get("KRONOS_ENABLED", True) and factors.get('_kronos_enabled', True)

    if not kronos_enabled:
        return v27_cands

    # 取 Top N 候选跑 Kronos
    candidate_n = p.get("KRONOS_CANDIDATE_N", 50)
    top_cands = v27_cands[:candidate_n]
    candidate_codes = [c for c, s in top_cands]
    v27_scores = {c: s for c, s in top_cands}

    # 加载 Kronos 模型
    predictor, loaded = _load_kronos_model(p)
    if not loaded:
        return v27_cands  # 模型不可用，退回 v27

    # 逐只跑预测
    date_str = pd.Timestamp(date) if not isinstance(date, str) else date
    if isinstance(date_str, pd.Timestamp):
        date_str = date_str.strftime('%Y-%m-%d')

    lookback = p.get("KRONOS_LOOKBACK", 200)
    pred_len = p.get("KRONOS_PRED_LEN", 5)
    alpha = p.get("KRONOS_ALPHA", 0.5)
    conf_threshold = p.get("KRONOS_CONF_THRESHOLD", 0.3)

    kronos_results = {}
    for i, code in enumerate(candidate_codes):
        df_hist = _get_kline_from_db(code, lookback, date_str)
        if df_hist is None:
            continue
        result = _predict_single(predictor, df_hist, pred_len, p)
        if result is not None:
            kronos_results[code] = result

    if not kronos_results:
        return v27_cands  # 全部预测失败，退回 v27

    # ── Step 3: 综合评分 ──
    final_cands = []
    for code, v27_score in top_cands:
        if code in kronos_results:
            kr = kronos_results[code]
            kronos_ret = kr['kronos_ret']
            kronos_conf = kr['kronos_conf']

            # 置信度加权：低置信度降权
            conf_weight = min(kronos_conf / conf_threshold, 1.0)

            # Kronos 增强分
            kronos_enhance = alpha * kronos_ret * conf_weight

            final_score = v27_score + kronos_enhance
        else:
            final_score = v27_score  # 无 Kronos 结果，保持 v27 分

        final_cands.append((code, final_score))

    final_cands.sort(key=lambda x: x[1], reverse=True)

    # 排除已持有
    if current_holdings:
        final_cands = [(c, s) for c, s in final_cands if c not in current_holdings]

    return final_cands


# ── 兼容入口（与 strategy_map 对接）──
def select_stocks(factors, date, current_holdings=None, params=None):
    """兼容 v27 的函数签名"""
    return select_stocks_v28(factors, date, current_holdings, params)

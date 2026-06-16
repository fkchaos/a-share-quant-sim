#!/usr/bin/env python3
"""
core/strategy_map.py — 策略映射表
===================================
统一管理所有策略的选股逻辑映射。

设计理念：
- 回测和模拟盘共享同一套选股逻辑
- 通过 strategy name 映射到具体的选股函数
- 新增策略只需在此表注册，不需要改账户脚本

使用方式：
    from core.strategy_map import STRATEGY_MAP, load_strategy
    strategy = load_strategy("v27")
    factors = strategy["calc_factors"](close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
    cands = strategy["select_stocks"](factors, date)
"""
import importlib

def _load_func(dotted_path):
    """动态加载函数：module.path.func_name"""
    parts = dotted_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(f"无效路径: {dotted_path}")
    mod = importlib.import_module(parts[0])
    return getattr(mod, parts[1])


def load_strategy(name):
    """加载策略配置"""
    s = STRATEGY_MAP.get(name)
    if s is None:
        raise ValueError(f"未知策略: {name}，可用: {list(STRATEGY_MAP.keys())}")
    # 动态加载函数（如需）
    result = dict(s)
    if "select_fn" in result:
        result["select_stocks"] = _load_func(result["select_fn"])
    if "calc_factors_fn" in result:
        result["calc_factors"] = _load_func(result["calc_factors_fn"])
    return result


STRATEGY_MAP = {
    # ── v11b: Ensemble 截面因子（账户1）──
    "v11b": {
        "mode": "custom",
        "description": "Ensemble 截面因子选股（Momentum+Volatility+Reversal 3组并集）",
        "account_id": 1,
        "timing": "intraday",
        "select_fn": "scripts.strategies.v11b_select.select_stocks",
        "calc_factors_fn": "scripts.strategies.v11b_select.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "MAX_HOLDINGS": 12,
            "MAX_DAILY_BUY": 5,
            "MAX_POSITION": 0.30,
            "HOLD_DAYS_MAX": 5,
            "REBAL_FREQ": 5,
            "TOP_N": 8,
        },
    },

    # ── v27: 价量共振动量（账户2）──
    "v27": {
        "mode": "custom",
        "description": "价量共振动量（mom_5>2% + pv_corr_20 + gap/illiq/boll）",
        "account_id": 2,
        "timing": "intraday",
        "select_fn": "scripts.strategies.v27_select.select_stocks_v27",
        "calc_factors_fn": "scripts.strategies.v27_select.calc_factors",
        "params": {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.10,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 6,
            "MAX_POSITION": 0.20,
            "HOLD_DAYS_MAX": 8,
            "HOLD_DAYS_MIN": 2,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MOM_THRESHOLD": 0.02,
        },
    },

    # ── v28: Kronos AI 增强（v27 + 预测因子）──
    "v28": {
        "mode": "custom",
        "description": "Kronos AI 增强选股（v27初筛 + kronos_ret/conf/trend二次排序）",
        "account_id": 4,
        "timing": "intraday",
        "select_fn": "scripts.strategies.v28_kronos.select_stocks_v28",
        "calc_factors_fn": "scripts.strategies.v28_kronos.calc_factors",
        "params": {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "MAX_HOLDINGS": 12,
            "MAX_DAILY_BUY": 5,
            "MAX_POSITION": 0.25,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_MIN": 2,
            "MOM_THRESHOLD": 0.02,
            "KRONOS_ENABLED": True,
            "KRONOS_ALPHA": 0.5,
            "KRONOS_CANDIDATE_N": 50,
            "KRONOS_PRED_LEN": 5,
            "KRONOS_LOOKBACK": 200,
            "KRONOS_T": 0.8,
            "KRONOS_TOP_P": 0.85,
            "KRONOS_SAMPLE_COUNT": 5,
            "KRONOS_DEVICE": "cpu",
            "KRONOS_MODEL": "small",
            "KRONOS_CONF_THRESHOLD": 0.3,
        },
    },

    # ── v20c: 尾盘缩量企稳（账户3）──
    "v20c": {
        "mode": "custom",
        "description": "尾盘缩量企稳（软约束评分排序）",
        "account_id": 3,
        "timing": "tail",  # 14:40信号 → 14:55执行
        "select_fn": "scripts.strategies.v20_tail_pick.select_stocks_tail_pick",
        "calc_factors_fn": "scripts.strategies.v20_tail_pick.calc_tail_pick_factors",
        "params": {
            "STOP_LOSS": -0.03,
            "TAKE_PROFIT": 0.15,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_MIN": 1,
        },
    },
}

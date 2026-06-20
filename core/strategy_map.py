#!/usr/bin/env python3
"""
core/strategy_map.py — 策略映射表
===================================
统一管理所有策略的选股逻辑映射。

设计理念：
- 回测和模拟盘共享同一套选股逻辑
- 通过 strategy name 映射到具体的选股函数
- 新增策略只需在此表注册，不需要改账户脚本
- 策略与账户解耦：账户在 DB 中绑定策略，不在代码里硬编码

使用方式:
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


def list_strategy_names():
    """列出所有可用策略名"""
    return list(STRATEGY_MAP.keys())


STRATEGY_MAP = {
    # ── v11b: Ensemble 截面因子 ──
    "v11b": {
        "mode": "custom",
        "description": "Ensemble 截面因子选股（Momentum+Volatility+Reversal 3组并集）",
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

    # ── v27: 价量共振动量 ──
    "v27": {
        "mode": "custom",
        "description": "价量共振动量（mom_5>2% + pv_corr_20 + gap/illiq/boll）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v27_select.select_stocks_v27",
        "calc_factors_fn": "scripts.strategies.v27_select.calc_factors",
        "params": {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "HOLD_DAYS_MAX": 7,
            "HOLD_DAYS_MIN": 1,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MOM_THRESHOLD": 0.05,
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        },
    },

    # ── v28: Kronos AI 增强（v27 + 预测因子）──
    "v28": {
        "mode": "custom",
        "description": "Kronos AI 增强选股（v27初筛 + kronos_ret/conf/trend二次排序）",
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
            "MOM_THRESHOLD": 0.05,
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

    # ── v20c: 尾盘缩量企稳（已退役）──
    # 面板顺序 bug 修复后策略失效（WF 5/16，全量 -67%，核心因子 IC≈0）
    # 代码保留在 scripts/strategies/v20_tail_pick.py，不参与活跃交易
    "v20c": {
        "mode": "custom",
        "description": "尾盘缩量企稳（软约束评分排序）— 已退役",
        "timing": "tail",
        "select_fn": "scripts.strategies.v20_tail_pick.select_stocks_tail_pick",
        "calc_factors_fn": "scripts.strategies.v20_tail_pick.calc_tail_pick_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.15,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 8,
            "MAX_POSITION": 0.30,
            "HOLD_DAYS_MAX": 2,
            "HOLD_DAYS_MIN": 1,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        },
    },

    # ── v33: 残差动量 ──
    "v33": {
        "mode": "custom",
        "description": "残差动量（剥离市场Beta后的个股alpha信号）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v33_residual_momentum.select_stocks_v33",
        "calc_factors_fn": "scripts.strategies.v33_residual_momentum.calc_factors",
        "params": {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.05,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_MIN": 1,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "RESID_WINDOW": 12,
            "RESID_LOOKBACK": 6,
            "RESID_SKIP": 1,
            "MIN_OBS": 6,
            "MOM_THRESHOLD": 0.0,
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        },
    },

    # ── v35: 行业轮动（市值分组代理行业）──
    "v35": {
        "mode": "custom",
        "description": "行业轮动选股（大盘/中/小盘分组动量 + 个股动量加权）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v35_sector_rotation.select_stocks_v35",
        "calc_factors_fn": "scripts.strategies.v35_sector_rotation.calc_factors",
        "params": {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.05,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_MIN": 1,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "SECTOR_MOM_WEIGHT": 0.30,
            "SECTOR_SHORT": 5,
            "SECTOR_MID": 20,
            "SECTOR_LONG": 60,
            "SECTOR_W_SHORT": 0.4,
            "SECTOR_W_MID": 0.3,
            "SECTOR_W_LONG": 0.3,
            "MOM_THRESHOLD": 0.01,
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        },
    },

    # ── v32: 分析师预期因子 ──
    "v32": {
        "mode": "custom",
        "description": "分析师预期因子（SUE代理+盈利预测上调+异常覆盖+综合因子）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v32_analyst_expectation.select_stocks_v32",
        "calc_factors_fn": "scripts.strategies.v32_analyst_expectation.calc_factors",
        "params": {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.05,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_MIN": 1,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "ANALYST_WEIGHT": 0.30,
            "SUE_THRESHOLD": 0.0,
            "FORECAST_UP_THRESHOLD": 0.10,
            "ANALYST_COVERAGE_MIN": 5,
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        },
    },

    # ── v31: 价量共振+动量增强（已归档，与 v27 高度重复）──
    # mom_20/mom_40 与 mom_5 相关性 0.3-0.5，非独立信息，预期选股重叠度 >70%
    # 代码保留在 scripts/strategies/v29_select.py，不参与活跃交易
    "v31": {
        "mode": "custom",
        "description": "价量共振+动量增强（v27核心85% + mom_20/mom_40动量15%）— 已归档，与v27高度重复",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v29_select.select_stocks_v29",
        "calc_factors_fn": "scripts.strategies.v29_select.calc_factors",
        "archived": True,
        "archive_reason": "与 v27 因子高度重叠（mom_20/mom_40 与 mom_5 相关性 0.3-0.5），无独立回测价值",
        "params": {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_MIN": 1,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MOM_THRESHOLD": 0.05,
            "MOM_20_WEIGHT": 0.15,
            "MOM_40_WEIGHT": 0.15,
        },
    },
}

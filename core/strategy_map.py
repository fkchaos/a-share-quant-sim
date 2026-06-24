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
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_MIN": 1,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MOM_THRESHOLD": 0.05,
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
    # ── v39c: 多因子评分版（v27 选股门槛 + 7因子加权评分）──
    "v39c": {
        "mode": "custom",
        "description": "多因子评分（v27选股门槛 + 7因子加权评分，W_MOM=0.20 W_PV_CORR=0.05）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v39c_pv_resonance.select_stocks_v39c",
        "calc_factors_fn": "scripts.strategies.v39c_pv_resonance.calc_factors",
        "params": {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.20,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.10,
            "W_SIZE": 0.10,
            "W_FUND_FLOW": 0.15,
            "W_GAP": 0.10,
            "W_ILLIQ": 0.10,
        },
    },

    # ── v40: 因子恶化卖出 + 延迟止盈止损（v39c 评分体系 + 持仓重评分）──
    "v40": {
        "mode": "custom",
        "description": "因子恶化卖出（持仓评分<SELL_THRESHOLD卖出，>BUY_BACK_THRESHOLD延迟止盈止损）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v40_factor_exit.select_stocks_v40",
        "calc_factors_fn": "scripts.strategies.v40_factor_exit.calc_factors",
        "factor_exit_fn": "scripts.strategies.v40_factor_exit.check_factor_exit",
        "params": {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.20,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.10,
            "W_SIZE": 0.10,
            "W_FUND_FLOW": 0.15,
            "W_GAP": 0.10,
            "W_ILLIQ": 0.10,
            # v40 新增
            "SELL_THRESHOLD": 0.35,
            "BUY_BACK_THRESHOLD": 0.65,
            "SELL_PENALTY_N": 1,
            "SELL_MODE": "momentum",
            "MOMENTUM_DROP_PCT": 0.20,
        },
    },

    # ── v40b: 纯轮动（每日卖最低4只+买最高4只，无硬风控）──
    "v40b": {
        "mode": "custom",
        "description": "纯轮动（每日卖出持仓评分最低4只+买入全市场评分最高4只，无硬风控）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v40_factor_exit.select_stocks_v40b",
        "calc_factors_fn": "scripts.strategies.v40_factor_exit.calc_factors",
        "params": {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.20,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.10,
            "W_SIZE": 0.10,
            "W_FUND_FLOW": 0.15,
            "W_GAP": 0.10,
            "W_ILLIQ": 0.10,
            # v40b 新增
            "SELL_COUNT": 4,
            "BUY_COUNT": 4,
            "NO_HARD_RISK": True,
        },
    },

    # ── v41: VWAP 偏离 + 净支撑量因子（v39c 评分体系 + 2个新量价因子）──
    "v41": {
        "mode": "custom",
        "description": "VWAP偏离+净支撑量因子（v39c 7因子 + VWAP_DEV 0.15 + NET_SUPPORT 0.10）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v41_vwap_deviation.select_stocks_v41",
        "calc_factors_fn": "scripts.strategies.v41_vwap_deviation.calc_factors_v41",
        "params": {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 8,
            # v39c 原有权重
            "W_MOM": 0.20,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.10,
            "W_SIZE": 0.10,
            "W_FUND_FLOW": 0.15,
            "W_GAP": 0.10,
            "W_ILLIQ": 0.10,
            # 新增因子权重
            "W_VWAP_DEV": 0.15,
            "W_NET_SUPPORT": 0.10,
        },
    },

    # ── v39d: 参数优化版（基于 IC 分析调整权重 + 风控参数）──
    "v39d": {
        "mode": "custom",
        "description": "v39c 参数优化（IC 驱动权重调整 + 松风控 + 低换手）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v39d_optimized.select_stocks_v39d",
        "calc_factors_fn": "scripts.strategies.v39c_pv_resonance.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.125,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.15,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.30,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        },
    },

    # ── v39e: 基于 v39d 交易行为分析进一步优化 ──
    "v39e": {
        "mode": "custom",
        "description": "v39d 交易行为优化（收紧止损 + 缩短持有期 + 降仓位 + size_factor 40%）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v39e_optimized.select_stocks_v39e",
        "calc_factors_fn": "scripts.strategies.v39c_pv_resonance.calc_factors",
        "params": {
            "STOP_LOSS": -0.03,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 3,
            "HOLD_DAYS_EXTEND_PNL": 0.05,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.15,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.10,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.40,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        },
    },

    # ── v39f: 修正 v39e 错误（保持 -5% 止损 + 降低止盈到 5%）──
    "v39f": {
        "mode": "custom",
        "description": "v39e 修正（保持松止损 + 低止盈 + 高换手）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v39f_optimized.select_stocks_v39f",
        "calc_factors_fn": "scripts.strategies.v39c_pv_resonance.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.05,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.10,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.40,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        },
    },

    # ── v39g: 风控参数优化（短持有期 + 低止盈 + 高换手）──
    "v39g": {
        "mode": "custom",
        "description": "v39g 风控优化（HOLD_DAYS_MAX=3 + TAKE_PROFIT=5% + MAX_DAILY_BUY=4）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v39g_optimized.select_stocks_v39g",
        "calc_factors_fn": "scripts.strategies.v39c_pv_resonance.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.05,
            "HOLD_DAYS_MAX": 3,
            "HOLD_DAYS_EXTEND": 3,
            "HOLD_DAYS_EXTEND_PNL": 0.08,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.10,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.40,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        },
    },

    # ── v39h: 动态 MOM_THRESHOLD（熊市自适应减仓）──
    "v39h": {
        "mode": "custom",
        "description": "v39h 动态门槛（熊市 MOM_THRESHOLD=0.10 自然减仓 + v39d 风控参数）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v39h_optimized.select_stocks_v39h",
        "calc_factors_fn": "scripts.strategies.v39c_pv_resonance.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.125,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.03,
            "MOM_THRESHOLD_BEAR": 0.10,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.15,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.30,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        },
    },

    # ── v39i: 最优阈值（夏普 1.199 / 回撤 16.69%）──
    "v39i": {
        "mode": "custom",
        "description": "v39i 最优阈值（BULL=0.05/BEAR=0.08，夏普1.199/回撤16.69%）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v39i_optimized.select_stocks_v39i",
        "calc_factors_fn": "scripts.strategies.v39c_pv_resonance.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.125,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.05,
            "MOM_THRESHOLD_BEAR": 0.08,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.15,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.30,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        },
    },

    # ── v42: 换手率因子研究（v39i + turnover_rate）──
    "v42": {
        "mode": "custom",
        "description": "换手率因子研究（真实换手率 vs 量比，W_TURNOVER_RATE=0.05）",
        "timing": "intraday",
        "select_fn": "scripts.strategies.v42_turnover_research.select_stocks_v42",
        "calc_factors_fn": "scripts.strategies.v42_turnover_research.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.125,
            "MAX_HOLDINGS": 8,
            "COOLDOWN_DAYS": 0,
            "MOM_THRESHOLD": 0.05,
            "MOM_THRESHOLD_BEAR": 0.08,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "W_MOM": 0.15,
            "W_PV_CORR": 0.05,
            "W_TURNOVER_RATE": 0.05,
            "W_TURNOVER_AVG": 0.05,
            "W_SIZE": 0.30,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        },
    },
}


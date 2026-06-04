"""Core configuration — loads config.yaml with typed defaults."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


# ── 可调配置（改这里即可，无需动代码） ──────────────────────────────
CONFIG = dict(
    # ── 交易成本 ──
    initial_capital   = 200_000,   # 初始资金
    commission_rate   = 0.0003,    # 佣金率（万3）
    stamp_tax_rate    = 0.001,     # 印花税（千1，卖出收）
    slippage_rate     = 0.001,     # 滑点（千1）

    # ── 风控参数 ──
    stop_loss         = 0.20,      # 止损比例（20%）
    stop_loss_atr_k   = 6.0,       # ATR 动态止损 K 值
    top_n             = 12,        # 持仓数量
    rebalance_freq    = 20,        # 调仓频率（交易日）
    max_single_weight = 0.15,      # 单只最大仓位占比
    max_daily_turnover= 0.30,      # 单日最大换手率
    min_rebalance_interval = 3,    # 最小调仓间隔（交易日）

    # ── 选股参数 ──
    max_position      = 0.10,      # 单只最大仓位占比（策略级）
    max_industry_weight = 0.0,     # 行业仓位上限（0=不限制）

    # ── 波动率缩放 ──
    vol_target        = 0.20,      # 目标年化波动率
)


# ── Default values (fallback when config.yaml is absent) ──────────────

DEFAULT_FACTOR_WEIGHTS = {
    'mom_5': 0.05, 'mom_10': 0.10, 'mom_20': 0.10, 'mom_60': 0.08, 'mom_120': 0.05,
    'rev_3': 0.05, 'rev_5': 0.08, 'rev_10': 0.05,
    'vol_10': -0.03, 'vol_20': -0.05, 'vol_60': -0.05,
    'vol_change': 0.03,
    'vol_ratio_5': 0.05, 'vol_ratio_20': 0.05, 'amount_ratio': 0.05,
    'rsi_6': 0.03, 'rsi_14': 0.05, 'rsi_28': 0.02,
    'macd_12_26': 0.08, 'macd_5_35': 0.04,
    'boll_pos_10': 0.03, 'boll_pos_20': 0.03, 'boll_width_20': 0.03,
    'atr_14': -0.03,
    'skew_20': 0.02, 'kurt_20': -0.02,
    'vwap_mom': 0.03,
    'rel_strength_20': 0.05, 'rel_strength_60': 0.03,
}


@dataclass
class TradingCosts:
    initial_capital: float = field(default_factory=lambda: CONFIG["initial_capital"])
    commission_rate: float = field(default_factory=lambda: CONFIG["commission_rate"])
    stamp_tax_rate: float = field(default_factory=lambda: CONFIG["stamp_tax_rate"])
    slippage_rate: float = field(default_factory=lambda: CONFIG["slippage_rate"])


@dataclass
class MarketFilter:
    """股票范围过滤配置。

    include_prefixes : 允许的股票代码前缀列表
        '6'  = 沪市主板 (600/601/603/605...)
        '0'  = 深市主板 (000/001/002/003...)
        '3'  = 创业板 (300/301...)
        留空 []  = 不过滤（全部纳入）
    exclude_prefixes : 强制排除的前缀（优先级高于 include）
        '688' = 科创板
        '8'   = 北交所
        '4'   = 老三板
        '2'   = B股
    exclude_delisted : bool — 自动排除退市/长期停牌股
        判断标准：最后交易日期距今超过 delist_max_gap 天
    delist_max_gap   : int — 超过多少天无数据判定为退市/停牌（默认 30 天）
    """
    include_prefixes: tuple = ('6', '0', '3')
    exclude_prefixes: tuple = ('688', '8', '4', '2')
    exclude_delisted: bool = True
    delist_max_gap: int = 30


@dataclass
class RiskLimits:
    stop_loss: float = field(default_factory=lambda: CONFIG["stop_loss"])
    stop_loss_atr_k: float = field(default_factory=lambda: CONFIG["stop_loss_atr_k"])
    top_n: int = field(default_factory=lambda: CONFIG["top_n"])
    rebalance_freq: int = field(default_factory=lambda: CONFIG["rebalance_freq"])
    max_single_weight: float = field(default_factory=lambda: CONFIG["max_single_weight"])
    max_daily_turnover: float = field(default_factory=lambda: CONFIG["max_daily_turnover"])
    min_rebalance_interval: int = field(default_factory=lambda: CONFIG["min_rebalance_interval"])


@dataclass
class StrategyConfig:
    """策略参数 — 所有策略的唯一参数来源。run_backtest 和 sim_daily 都从这里读。"""
    label: str = "default"

    # ── 选股参数 ──────────────────────────────────────────────
    weight_method: str = "equal"          # equal | ic_ir | markowitz
    top_n: int = field(default_factory=lambda: CONFIG["top_n"])
    rebalance_freq: int = field(default_factory=lambda: CONFIG["rebalance_freq"])
    factor_weights: Optional[Dict[str, float]] = None  # None = 用 DEFAULT_FACTOR_WEIGHTS

    # ── 风控参数 ──────────────────────────────────────────────
    stop_loss: float = field(default_factory=lambda: CONFIG["stop_loss"])
    max_position: float = field(default_factory=lambda: CONFIG["max_position"])
    max_industry_weight: float = field(default_factory=lambda: CONFIG["max_industry_weight"])
    max_daily_turnover: float = 0          # 0 = 不限制（策略级覆盖）

    # ── 波动率缩放 ────────────────────────────────────────────
    use_vol_scaling: bool = True
    vol_target: float = field(default_factory=lambda: CONFIG["vol_target"])

    # ── 止盈 ──────────────────────────────────────────────────
    use_take_profit: bool = False
    tp_tiers: Optional[list] = None        # e.g. [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)]

    # ── 持有期衰减 ────────────────────────────────────────────
    use_holding_decay: bool = False

    # ── ATR 止损 ──────────────────────────────────────────────
    use_atr_stop: bool = False
    atr_k: float = field(default_factory=lambda: CONFIG["stop_loss_atr_k"])

    # ── 优化用 ────────────────────────────────────────────────
    risk_aversion: float = 1.0

    # ── 多组 Ensemble 策略 ────────────────────────────────────
    ensemble_groups: Optional[Dict[str, Dict[str, float]]] = None
    ensemble_group_top_n: int = 4


# ============================================================
# 预定义策略 Profiles
# ============================================================
# 新增策略：在此添加常量 + 在 STRATEGY_PROFILES dict 里注册

PROFILE_V4_BASELINE = StrategyConfig(
    label="v4_baseline",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0,
)

PROFILE_V4_INDUSTRY_CAP = StrategyConfig(
    label="v4_industry_cap",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
)

PROFILE_V5_TP_DECAY = StrategyConfig(
    label="v5_tp_decay",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
)

# ── v6 系列：因子优化 ──────────────────────────────────────────

PROFILE_V6A_12F_ICIR = StrategyConfig(
    label="v6a_12f_icir",
    weight_method="ic_ir",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'mom_60': 0.2236, 'macd_12_26': 0.1979, 'mom_120': 0.1902,
        'rsi_28': 0.1510, 'vol_10': 0.1426, 'atr_14': 0.1392,
        'vol_20': 0.1375, 'vol_60': 0.1321, 'mom_20': 0.0985,
        'vol_ratio_20': 0.0957, 'skew_20': 0.0945, 'boll_width_20': 0.0897,
    },
)

PROFILE_V6B_8F_POS_IC = StrategyConfig(
    label="v6b_8f_pos_ic",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'vol_ratio_20': 0.20, 'amount_ratio': 0.15, 'rsi_6': 0.15,
        'vol_ratio_5': 0.12, 'boll_pos_10': 0.12, 'mom_5': 0.10,
        'rev_10': 0.08, 'boll_pos_20': 0.08,
    },
)

# ── v6b + high_low_range 变体 ──────────────────────────────────

PROFILE_V6B_HLR = StrategyConfig(
    label="v6b_hlr",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'vol_ratio_20': 0.18, 'amount_ratio': 0.14, 'rsi_6': 0.14,
        'vol_ratio_5': 0.11, 'boll_pos_10': 0.11, 'mom_5': 0.09,
        'rev_10': 0.07, 'boll_pos_20': 0.07,
        'high_low_range': 0.09,  # 新增：日内振幅
    },
)

# ── v7 系列：放开行业限制 ──────────────────────────────────────

PROFILE_V7A_8F_IND40 = StrategyConfig(
    label="v7a_8f_ind40",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.40,             # 放开到 40%
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'vol_ratio_20': 0.20, 'amount_ratio': 0.15, 'rsi_6': 0.15,
        'vol_ratio_5': 0.12, 'boll_pos_10': 0.12, 'mom_5': 0.10,
        'rev_10': 0.08, 'boll_pos_20': 0.08,
    },
)

PROFILE_V7B_8F_IND50 = StrategyConfig(
    label="v7b_8f_ind50",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.50,             # 放开到 50%
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'vol_ratio_20': 0.20, 'amount_ratio': 0.15, 'rsi_6': 0.15,
        'vol_ratio_5': 0.12, 'boll_pos_10': 0.12, 'mom_5': 0.10,
        'rev_10': 0.08, 'boll_pos_20': 0.08,
    },
)

PROFILE_V7C_8F_NO_IND = StrategyConfig(
    label="v7c_8f_no_ind",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0,                # 完全不限制
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'vol_ratio_20': 0.20, 'amount_ratio': 0.15, 'rsi_6': 0.15,
        'vol_ratio_5': 0.12, 'boll_pos_10': 0.12, 'mom_5': 0.10,
        'rev_10': 0.08, 'boll_pos_20': 0.08,
    },
)

STRATEGY_PROFILES = {
    "v4_baseline": PROFILE_V4_BASELINE,
    "v4_industry_cap": PROFILE_V4_INDUSTRY_CAP,
    "v5_tp_decay": PROFILE_V5_TP_DECAY,
    "v6a_12f_icir": PROFILE_V6A_12F_ICIR,
    "v6b_8f_pos_ic": PROFILE_V6B_8F_POS_IC,
    "v7a_8f_ind40": PROFILE_V7A_8F_IND40,
    "v7b_8f_ind50": PROFILE_V7B_8F_IND50,
    "v7c_8f_no_ind": PROFILE_V7C_8F_NO_IND,
}

# ── v8 系列：去冗余 + IC_IR 加权（新默认）──────────────────────────

PROFILE_V8_ALL_ICIR = StrategyConfig(
    label="v8_all_icir",
    weight_method="ic_ir",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        # 去冗余后的 18 个因子，按 |IC_IR| 归一化，负 IC 因子取负权重
        'illiquidity':    +0.1806,
        'boll_width_20':  +0.1113,
        'amplitude':      +0.0749,
        'turnover_skew':  -0.0715,
        'mom_120':        -0.0666,
        'vol_20':         +0.0647,
        'turnover_change':+0.0575,
        'vol_ratio_20':   +0.0536,
        'rev_3':          -0.0522,
        'boll_pos_20':    +0.0459,
        'amount_ratio':   +0.0395,
        'price_impact':   +0.0384,
        'macd_12_26':     -0.0294,
        'mom_20':         +0.0290,
        'pv_corr':        -0.0259,
        'chip_kurt':      -0.0205,
        'obv_slope':      -0.0199,
        'kurt_20':        -0.0184,
    },
)

# ── v9 系列：短线策略（RETIRED — 回测验证失败）─────────────────────
# 5 天调仓频率下交易成本过高，全历史回测年化 -7.68%，Sharpe -0.34
# 失败原因：freq=5 调仓太频繁，A股 T+1+摩擦成本吃掉短线 alpha
# 保留因子计算代码（high_low_range 仍有价值），但不作为独立策略

# PROFILE_V9_SHORT_TERM = StrategyConfig(
#     label="v9_short_term",
#     weight_method="equal",
#     top_n=12, rebalance_freq=5,
#     stop_loss=0.15, max_position=0.15,
#     ...
# )

STRATEGY_PROFILES = {
    "v4_baseline": PROFILE_V4_BASELINE,
    "v4_industry_cap": PROFILE_V4_INDUSTRY_CAP,
    "v5_tp_decay": PROFILE_V5_TP_DECAY,
    "v6a_12f_icir": PROFILE_V6A_12F_ICIR,
    "v6b_8f_pos_ic": PROFILE_V6B_8F_POS_IC,
    "v7a_8f_ind40": PROFILE_V7A_8F_IND40,
    "v7b_8f_ind50": PROFILE_V7B_8F_IND50,
    "v7c_8f_no_ind": PROFILE_V7C_8F_NO_IND,
    "v8_all_icir": PROFILE_V8_ALL_ICIR,
    "v6b_hlr": PROFILE_V6B_HLR,
    # v9_short_term: RETIRED (freq=5 too costly for A-shares)
}

# ── v10 系列：小市值因子 ──────────────────────────────────────────

PROFILE_V10_SMALL_CAP = StrategyConfig(
    label="v10_small_cap",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        # 精简版：小市值 + 跳空比 + 5个强因子
        'small_cap':       0.20,   # 小市值因子（2025年最强）
        'gap_ratio':       0.18,   # 跳空比
        'rsi_6':           0.15,   # 短期RSI
        'boll_pos_10':     0.15,   # 布林位置
        'amount_ratio':    0.12,   # 成交额比
        'mom_5':           0.10,   # 5日动量
        'rsi_14':          0.10,   # 中期RSI
    },
)

# v10b：小市值 + 动量 + 反转（测试反转因子在小市值股票上是否有效）
PROFILE_V10B_SMALL_MOM = StrategyConfig(
    label="v10b_small_mom",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'small_cap':       0.25,   # 小市值
        'gap_ratio':       0.15,   # 跳空
        'mom_5':           0.15,   # 短期动量
        'mom_10':          0.10,   # 中期动量
        'rsi_6':           0.10,   # RSI
        'amount_ratio':    0.10,   # 成交额
        'rev_5':           0.08,   # 短期反转（小市值上可能有效）
        'boll_pos_10':     0.07,   # 布林
    },
)

# ── v10 系列：中证800 IC 最优因子 ──────────────────────────────────

PROFILE_V10_ZZ800_TOP_IR = StrategyConfig(
    label="v10_zz800_top_ir",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        # 按中证800 IC分析 IR 排序，选取 avg|IR| > 0.07 的因子
        # high_low_range IR=0.168 → 最高权重
        # vol_60 IR=0.128 → 第二（v6b系列没有，新增）
        # vol_20 IR=0.083 → 第三
        # mom_10/rev_10 IR=0.099 → 第四（配对）
        # mom_20 IR=0.102 → 第五（三期限最稳定）
        # mom_5/rev_5 IR=0.084 → 第六（配对）
        # rsi_6 IR=0.071 → 第七
        # rsi_14 IR=0.071 → 第八
        # vol_10 IR=0.067 → 第九
        # boll_pos_10 IR=0.053 → 第十
        # boll_pos_20 IR=0.058 → 第十一
        # 去掉 vol_ratio_20(0.045), amount_ratio(0.042), vol_ratio_5(0.027)
        'high_low_range': 0.16,
        'vol_60':         0.13,
        'mom_20':         0.10,
        'mom_10':         0.10,
        'rev_10':         0.10,
        'vol_20':         0.08,
        'mom_5':          0.08,
        'rev_5':          0.08,
        'rsi_6':          0.07,
        'rsi_14':         0.05,
        'vol_10':         0.03,
        'boll_pos_10':    0.01,
        'boll_pos_20':    0.01,
    },
)

# v10b: 纯高IR因子（去掉低IR的boll/vol_10，集中权重）
PROFILE_V10B_ZZ800_CORE = StrategyConfig(
    label="v10b_zz800_core",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        # 只保留 avg|IR| > 0.08 的核心因子
        'high_low_range': 0.20,
        'vol_60':         0.16,
        'mom_20':         0.13,
        'mom_10':         0.13,
        'rev_10':         0.13,
        'vol_20':         0.10,
        'mom_5':          0.08,
        'rev_5':          0.07,
    },
)

# v10c: 降低vol_60权重（与hlr相关性0.43），增加反转因子
PROFILE_V10C_ZZ800_BALANCED = StrategyConfig(
    label="v10c_zz800_balanced",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        # vol_60权重从0.13降到0.08（与hlr冗余）
        # 释放的权重加到反转因子（负IC但低相关性）
        'high_low_range': 0.18,
        'mom_20':         0.12,
        'mom_10':         0.12,
        'rev_10':         0.12,
        'vol_60':         0.08,
        'vol_20':         0.08,
        'mom_5':          0.08,
        'rev_5':          0.08,
        'rsi_6':          0.06,
        'rsi_14':         0.04,
        'vol_10':         0.02,
        'boll_pos_10':    0.01,
        'boll_pos_20':    0.01,
    },
)

# v10d: 纯动量+波动率（去掉反转因子，测试方向性）
PROFILE_V10D_ZZ800_MOM = StrategyConfig(
    label="v10d_zz800_mom",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        # 纯动量+波动率方向（中证800上IC>0的因子）
        'high_low_range': 0.20,
        'vol_60':         0.15,
        'mom_20':         0.15,
        'mom_10':         0.12,
        'vol_20':         0.10,
        'mom_5':          0.10,
        'rsi_6':          0.08,
        'rsi_14':         0.05,
        'vol_10':         0.03,
        'boll_pos_10':    0.01,
        'boll_pos_20':    0.01,
    },
)

STRATEGY_PROFILES["v10_zz800_top_ir"] = PROFILE_V10_ZZ800_TOP_IR
STRATEGY_PROFILES["v10b_zz800_core"] = PROFILE_V10B_ZZ800_CORE
# v10e: 降低vol_60权重(熊市放大亏损), 提高反转因子权重
PROFILE_V10E_ZZ800_DEF = StrategyConfig(
    label="v10e_zz800_def",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        # vol_60从0.08降到0.04(高波动因子在熊市放大亏损)
        # 释放的权重加到反转因子(熊市反转效应更强)
        'high_low_range': 0.16,
        'rev_10':         0.14,  # 提高反转权重
        'rev_5':          0.12,  # 提高反转权重
        'mom_20':         0.12,
        'mom_10':         0.10,
        'vol_20':         0.08,
        'mom_5':          0.08,
        'rsi_6':          0.06,
        'vol_60':         0.04,  # 降低高波动因子
        'rsi_14':         0.04,
        'vol_10':         0.02,
        'boll_pos_10':    0.02,
        'boll_pos_20':    0.02,
    },
)

STRATEGY_PROFILES["v10c_zz800_balanced"] = PROFILE_V10C_ZZ800_BALANCED
# v10f: v10c + 换手率控制(熊市频繁调仓增加成本)
PROFILE_V10F_ZZ800_TURNOVER = StrategyConfig(
    label="v10f_zz800_turnover",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    max_daily_turnover=0.20,  # 限制单日换手率≤20%
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights={
        'high_low_range': 0.18,
        'mom_20':         0.12,
        'mom_10':         0.12,
        'rev_10':         0.12,
        'vol_20':         0.08,
        'mom_5':          0.08,
        'rev_5':          0.08,
        'rsi_6':          0.06,
        'vol_60':         0.08,
        'rsi_14':         0.04,
        'vol_10':         0.02,
        'boll_pos_10':    0.01,
        'boll_pos_20':    0.01,
    },
)

STRATEGY_PROFILES["v10e_zz800_def"] = PROFILE_V10E_ZZ800_DEF
STRATEGY_PROFILES["v10f_zz800_turnover"] = PROFILE_V10F_ZZ800_TURNOVER
STRATEGY_PROFILES["v10d_zz800_mom"] = PROFILE_V10D_ZZ800_MOM







# ── v11b: 多组 Ensemble 策略 ─────────────────────────────────
PROFILE_V11B_ZZ800_UNION = StrategyConfig(
    label="v11b_zz800_union",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
    use_holding_decay=True,
    factor_weights=None,  # 不使用单一权重，用 ensemble_groups
    ensemble_groups={
        'momentum': {
            'mom_20': 0.30,
            'mom_10': 0.25,
            'rsi_14': 0.25,
            'high_low_range': 0.20,
        },
        'volatility': {
            'vol_60': 0.30,
            'vol_20': 0.25,
            'vol_10': 0.25,
            'boll_width_20': 0.20,
        },
        'reversal': {
            'rev_10': 0.30,
            'rev_5': 0.25,
            'rsi_6': 0.25,
            'boll_pos_10': 0.20,
        },
    },
    ensemble_group_top_n=5,  # 每组选5只，WF 验证最优
)

STRATEGY_PROFILES["v11b_zz800_union"] = PROFILE_V11B_ZZ800_UNION

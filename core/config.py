"""Core configuration — loads config.yaml with typed defaults."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


# ── 可调配置（改这里即可，无需动代码） ──────────────────────────────
CONFIG = dict(
    # ── 交易成本 ──
    initial_capital   = 200000,    # 初始资金
    commission_rate   = 0.0003,    # 佣金率（万3）
    stamp_tax_rate    = 0.001,     # 印花税（千1，卖出收）
    slippage_rate     = 0.001,     # 滑点（千1）

    # ── 风控参数（v11b 最优值）──
    stop_loss         = 0.20,      # 止损比例（20%）
    stop_loss_atr_k   = 6.0,       # ATR 动态止损 K 值
    top_n             = 12,        # 持仓数量
    rebalance_freq    = 20,        # 调仓频率（交易日）
    max_single_weight = 0.15,      # 单只最大仓位占比
    max_daily_turnover= 0.30,      # 单日最大换手率
    min_rebalance_interval = 3,    # 最小调仓间隔（交易日）

    # ── 选股参数（v11b 最优值）──
    max_position      = 0.10,      # 单只最大仓位占比
    max_industry_weight = 0.25,    # 行业仓位上限（25%，v11b WF 最优）

    # ── 波动率缩放（v11b 最优值）──
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
    exclude_prefixes: tuple = ('688', '689', '8', '4', '2')
    exclude_delisted: bool = True
    delist_max_gap: int = 30
    min_price: float = 0.0
    """最低价格过滤（元）。0=不过滤。
    排除最新收盘价低于此阈值的股票，用于过滤退市末日轮等异常低价股。
    建议值：2.0（排除2元以下股票，保留正常低价股）。
    """


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

    # ── 市场择时 filter（v11b熊市保护，opt-6）──────────────────────
    use_market_filter: bool = False
    market_filter_method: str = "ma_crossover"  # ma_crossover: MA20<MA60 时空仓
    market_ma_short: int = 20
    market_ma_long: int = 60

    # ── HMM 仓位管理 ────────────────────────────────────────────
    use_hmm_position: bool = False  # True 时根据 HMM 状态动态调整仓位

    # ── 多组 Ensemble 策略 ────────────────────────────────────
    ensemble_groups: Optional[Dict[str, Dict[str, float]]] = None
    ensemble_group_top_n: int = 4
    ensemble_min_groups: int = 1   # 最少需要被多少组选中（1=union, 2=intersection）
    crowd_threshold: float = 0.0   # 拥挤度过滤阈值（0=不过滤，0.9=排除拥挤度>90%的股票）

    # ── 多策略并行 ────────────────────────────────────────────
    multi_strategy: Optional[Dict] = None
    # 格式: {"strategies": [{"profile": "v11b_zz800_union", "mode": "ensemble", "weight": 0.5}, ...]}
    # 最终评分 = Σ weight_i × zscore(strategy_i_scores)


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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
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
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
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

# ── v11b 变体: 去掉 Volatility 组 ──────────────────────────────
PROFILE_V11B_ZZ800_NOVOL = StrategyConfig(
    label="v11b_zz800_novol",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {
            'mom_20': 0.30,
            'mom_10': 0.25,
            'rsi_14': 0.25,
            'high_low_range': 0.20,
        },
        'reversal': {
            'rev_10': 0.30,
            'rev_5': 0.25,
            'rsi_6': 0.25,
            'boll_pos_10': 0.20,
        },
    },
    ensemble_group_top_n=5,
)

# ── opt-3 结论：TP-incremental 止盈参数（采纳为默认）──────────
# 原 [(0.10,0.30),(0.20,0.30),(0.30,1.00)] → 新 [(0.10,0.20),(0.20,0.30),(0.30,0.50)]
# 全量回测: 30.43%/1.16/27.49% vs 26.03%/1.04/26.05%（收益+4.4pp，夏普+0.12）
STRATEGY_PROFILES["v11b_zz800_union"] = PROFILE_V11B_ZZ800_UNION
STRATEGY_PROFILES["v11b_zz800_novol"] = PROFILE_V11B_ZZ800_NOVOL

# ── v11b_intersection: opt-5 分层 intersection（min_groups=2）─
# 只保留被 2+ 组同时选中的股票，持仓更集中，质量更高
PROFILE_V11B_INTERSECTION = StrategyConfig(
    label="v11b_intersection",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
    ensemble_min_groups=2,  # intersection: 至少被2组选中
)
STRATEGY_PROFILES["v11b_intersection"] = PROFILE_V11B_INTERSECTION

# ── v11b_bear: v11b + 熊市保护（MA20<MA60 禁止买入）──────────
# opt-6: 在熊市 fold 里不买入（保持现金），减少熊市亏损
PROFILE_V11B_BEAR = StrategyConfig(
    label="v11b_bear",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
    use_market_filter=True,
    market_filter_method="ma_crossover",
    market_ma_short=20,
    market_ma_long=60,
)
STRATEGY_PROFILES["v11b_bear"] = PROFILE_V11B_BEAR

# ── v11b_crowd: v11b + 拥挤度过滤（排除综合拥挤度>80%的股票）──
PROFILE_V11B_CROWD = StrategyConfig(
    label="v11b_crowd",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
    crowd_threshold=0.80,  # 排除综合拥挤度>80%的股票
)
STRATEGY_PROFILES["v11b_crowd"] = PROFILE_V11B_CROWD

# ── v11b_hmm: v11b + HMM 仓位管理 ────────────────────────────
# 用 HMM 识别市场状态（趋势上涨/震荡/趋势下跌），动态调整仓位
# 趋势下跌时 25% 仓位，震荡时 60% 仓位，趋势上涨时满仓
PROFILE_V11B_HMM = StrategyConfig(
    label="v11b_hmm",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
    use_hmm_position=True,  # ← 核心差异：HMM 仓位管理
)
STRATEGY_PROFILES["v11b_hmm"] = PROFILE_V11B_HMM

# ── v14_resid: 残差动量策略 ──────────────────────────────────────
# 华泰金工残差动量因子：截面回归剥离风格暴露后取残差动量
# 与 v11b ensemble 低相关（残差动量是纯 Alpha，无风格暴露）
# 选股：残差动量 + v11b ensemble 混合评分
PROFILE_V14_RESID = StrategyConfig(
    label="v14_resid",
    weight_method="equal",
    top_n=10, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights={
        'resid_mom': 0.50,   # 残差动量（主因子）
        'mom_20': 0.15,      # 趋势动量（辅助）
        'rev_10': 0.15,      # 反转因子（辅助）
        'vol_20': 0.10,      # 波动率过滤
        'amount_ratio': 0.10, # 流动性
    },
)
STRATEGY_PROFILES["v14_resid"] = PROFILE_V14_RESID

# ── v15_quality: 基本面质量因子策略 ──────────────────────────────
# 用 ROE/营收增速/净利增速/净利率/资产负债率构建质量评分
# 与量价因子低相关，提供差异化 Alpha 来源
# 质量因子预期：ROE 高 + 营收增速高 + 负债率低 = 高质量
PROFILE_V15_QUALITY = StrategyConfig(
    label="v15_quality",
    weight_method="equal",
    top_n=10, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights={
        'roe': 0.25,          # ROE（净资产收益率）
        'profit_yoy': 0.20,   # 净利润增速
        'revenue_yoy': 0.15,  # 营收增速
        'gross_margin': 0.15, # 销售净利率
        'debt_asset': -0.10,  # 资产负债率（负向：负债率低更好）
        'resid_mom': 0.15,    # 残差动量（辅助）
    },
)
STRATEGY_PROFILES["v15_quality"] = PROFILE_V15_QUALITY
PROFILE_V11B_LOWVOL = StrategyConfig(
    label="v11b_lowvol",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.15, 'vol_20': 0.15, 'vol_10': 0.15, 'boll_width_20': 0.10},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
)
STRATEGY_PROFILES["v11b_lowvol"] = PROFILE_V11B_LOWVOL

# 基准: [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)]

# TP-v1: 更激进（早止盈，锁定利润更快）
PROFILE_V11B_TP_AGGRESSIVE = StrategyConfig(
    label="v11b_tp_aggressive",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.08, 0.40), (0.15, 0.30), (0.25, 1.00)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
)
STRATEGY_PROFILES["v11b_tp_aggressive"] = PROFILE_V11B_TP_AGGRESSIVE

# TP-v2: 更宽松（让利润奔跑）
PROFILE_V11B_TP_RELAXED = StrategyConfig(
    label="v11b_tp_relaxed",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.15, 0.25), (0.25, 0.35), (0.40, 1.00)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
)
STRATEGY_PROFILES["v11b_tp_relaxed"] = PROFILE_V11B_TP_RELAXED

# TP-v3: 递增比例（越涨越减）
PROFILE_V11B_TP_INCREMENTAL = StrategyConfig(
    label="v11b_tp_incremental",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    ensemble_groups={
        'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
        'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
        'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
    },
    ensemble_group_top_n=5,
)
STRATEGY_PROFILES["v11b_tp_incremental"] = PROFILE_V11B_TP_INCREMENTAL

# ── v11b_style: v11b + 价格路径分布因子组 ──────────────────────
# opt-1: 在v11b 3组基础上添加第4组 distribution
# 因子：amplitude/vwap_mom/skew_20/kurt_20（收益分布+日内特征，与现有3组正交）
# IC诊断（2025-06~2026-06）：amplitude=+0.0314/+0.223, vwap_mom=+0.0249/+0.144,
#   skew_20=+0.0187/+0.285, kurt_20=-0.0111/-0.175
# 注意：流动性因子(illiquidity/vol_ratio/amount_ratio)在当前窗口 IC≈0，已弃用
PROFILE_V11B_ZZ800_UNION_STYLE = StrategyConfig(
    label="v11b_zz800_union_style",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
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

STRATEGY_PROFILES["v11b_zz800_union_style"] = PROFILE_V11B_ZZ800_UNION_STYLE

# ── v12: 多策略并行 ──────────────────────────────────────────
# v11b (ensemble) + v10c (因子) + v6b_hlr (稳定) 三策略混合
# 权重：v11b=0.5, v10c=0.3, v6b=0.2
PROFILE_V12_MULTI = StrategyConfig(
    label="v12_multi",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights=None,
    multi_strategy={
        "strategies": [
            {"profile": "v11b_zz800_union", "mode": "ensemble", "weight": 0.5},
            {"profile": "v10c_zz800_balanced", "mode": "factor", "weight": 0.3},
            {"profile": "v6b_hlr", "mode": "factor", "weight": 0.2},
        ]
    },
)

STRATEGY_PROFILES["v12_multi"] = PROFILE_V12_MULTI

# ── v13: 小市值中短线（⚠️ 此 profile 是旧版参考，不代表真实 v13）──────────────────
# 真实 v13 使用评分排序选股 + 流动性过滤，回测脚本：scripts/v13_small_mid_short.py
# 全量：49.87%/2.484/-13.46%，WF：14.9%/1.05/94%
# 此 profile 用 run_backtest.py 跑的结果与真实 v13 不一致，仅作对比基准
PROFILE_V13_SMALL_MID_SHORT = StrategyConfig(
    label="v13_small_mid_short",
    weight_method="equal",
    top_n=8, rebalance_freq=5,
    stop_loss=0.05, max_position=0.20,
    use_vol_scaling=False, vol_target=0.20,
    max_industry_weight=0,
    use_take_profit=False,
    use_holding_decay=False,
    factor_weights={
        'rev_3': 0.20, 'rev_5': 0.15, 'vol_10': 0.15,
        'rsi_6': 0.15, 'amount_ratio': 0.15, 'mom_5': 0.10,
        'boll_pos_10': 0.10,
    },
)
STRATEGY_PROFILES["v13_small_mid_short"] = PROFILE_V13_SMALL_MID_SHORT

# ── v14: 残差动量 ──────────────────────────────────────────────
# 残差动量 = 个股收益 - 市场收益回归的残差
# 独立于市场方向的纯 alpha 动量
PROFILE_V14_RESID_MOM = StrategyConfig(
    label="v14_resid_mom",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights={
        'resid_mom': 0.30, 'mom_20': 0.15, 'rev_5': 0.15,
        'vol_20': 0.10, 'rsi_14': 0.10, 'amount_ratio': 0.10,
        'boll_pos_10': 0.10,
    },
)
STRATEGY_PROFILES["v14_resid_mom"] = PROFILE_V14_RESID_MOM

# ── v15: 质量因子 ──────────────────────────────────────────────
# ROE/营收增速/毛利率/负债率 + 残差动量混合
PROFILE_V15_QUALITY = StrategyConfig(
    label="v15_quality",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights={
        'quality_roe': 0.20, 'quality_rev_growth': 0.15,
        'quality_gross_margin': 0.10, 'quality_leverage': -0.10,
        'resid_mom': 0.20, 'mom_20': 0.15, 'vol_20': 0.10,
        'rsi_14': 0.10, 'amount_ratio': 0.10,
    },
)
STRATEGY_PROFILES["v15_quality"] = PROFILE_V15_QUALITY

# ── v16: 动量+反转混合策略 ──────────────────────────────────────
# 短期反转（3-5日）+ 中期动量（20-60日）+ 长期趋势（120日）
# 三周期复合评分，与 v13（纯反转）和 v11b（纯动量）都不同
PROFILE_V16_MOM_REV_HYBRID = StrategyConfig(
    label="v16_mom_rev_hybrid",
    weight_method="equal",
    top_n=12, rebalance_freq=20,
    stop_loss=0.20, max_position=0.10,
    use_vol_scaling=True, vol_target=0.20,
    max_industry_weight=0.25,
    use_take_profit=True,
    tp_tiers=[(0.10, 0.20), (0.20, 0.30), (0.30, 0.50)],
    use_holding_decay=True,
    factor_weights={
        # 反转因子（短期）
        'rev_3': 0.15, 'rev_5': 0.12,
        # 动量因子（中期）
        'mom_20': 0.12, 'mom_60': 0.10,
        # 趋势因子（长期）
        'mom_120': 0.08,
        # 量价因子
        'vol_20': 0.08, 'rsi_14': 0.08, 'amount_ratio': 0.07,
        # 短线因子
        'high_low_range': 0.05, 'gap_ratio': 0.05,
        # 波动率变化
        'vol_change': 0.05, 'boll_pos_10': 0.05,
    },
)
STRATEGY_PROFILES["v16_mom_rev_hybrid"] = PROFILE_V16_MOM_REV_HYBRID

# ── v17: 价量张力因子 ──────────────────────────────────────────
# 基于国联民生证券研报(2026.05): 价格偏离度 × 量能变化率
# 周频调仓，小市值有效，与 v13 互补
PROFILE_V17_PRICE_VOLUME_TENSION = StrategyConfig(
    label="v17_price_volume_tension",
    weight_method="equal",
    top_n=12, rebalance_freq=5,
    stop_loss=0.05, max_position=0.10,
    use_vol_scaling=False,
    max_industry_weight=0.25,
    use_take_profit=False,
    use_holding_decay=False,
    factor_weights={
        'price_volume_tension': 0.30,
        'vol_accel': -0.15,
        'amount_ratio': 0.15,
        'high_low_range': 0.20,
        'rev_5': 0.15,
        'delist_risk': -0.15,
    },
)

STRATEGY_PROFILES["v17_price_volume_tension"] = PROFILE_V17_PRICE_VOLUME_TENSION

# ── v18: 波动率的波动率因子 ──────────────────────────────────────
# 基于方正金工(2022.08): 波动率的波动率刻画市场模糊性
# 周频调仓，与 v13 互补
PROFILE_V18_VOL_OF_VOL = StrategyConfig(
    label="v18_vol_of_vol",
    weight_method="equal",
    top_n=12, rebalance_freq=5,
    stop_loss=0.05, max_position=0.10,
    use_vol_scaling=False,
    max_industry_weight=0.25,
    use_take_profit=False,
    use_holding_decay=False,
    factor_weights={
        'vol_of_vol': 0.25,
        'vol_change': 0.15,
        'atr_14': 0.15,
        'boll_width_20': 0.15,
        'kurt_20': -0.10,
        'delist_risk': -0.20,
    },
)

STRATEGY_PROFILES["v18_vol_of_vol"] = PROFILE_V18_VOL_OF_VOL

# ── v19: 球队硬币因子（动量效应识别）──────────────────────────────
# 基于方正金工: 个股动量效应识别
# 与 v13 互补，动量在特定市场状态下有效
PROFILE_V19_TEAM_COIN = StrategyConfig(
    label="v19_team_coin",
    weight_method="equal",
    top_n=10, rebalance_freq=5,
    stop_loss=0.05, max_position=0.10,
    use_vol_scaling=False,
    max_industry_weight=0.25,
    use_take_profit=False,
    use_holding_decay=False,
    factor_weights={
        'mom_5': 0.3,
        'mom_10': 0.25,
        'mom_20': 0.2,
        'rsi_14': 0.15,
        'amount_ratio': 0.1,
    },
)

STRATEGY_PROFILES["v19_team_coin"] = PROFILE_V19_TEAM_COIN

"""Core configuration — loads config.yaml with typed defaults."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import os


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
    initial_capital: float = 200_000
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.001


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
    stop_loss: float = 0.20
    stop_loss_atr_k: float = 6.0        # K for ATR-based dynamic stop-loss (close-to-close ATR typically ~3-5%)
    top_n: int = 12
    rebalance_freq: int = 20
    max_single_weight: float = 0.15
    max_daily_turnover: float = 0.30
    min_rebalance_interval: int = 3


@dataclass
class StrategyConfig:
    """策略参数 — 所有策略的唯一参数来源。run_backtest 和 sim_daily 都从这里读。"""
    label: str = "default"

    # ── 选股参数 ──────────────────────────────────────────────
    weight_method: str = "equal"          # equal | ic_ir | markowitz
    top_n: int = 12
    rebalance_freq: int = 20
    factor_weights: Optional[Dict[str, float]] = None  # None = 用 config.factor_weights

    # ── 风控参数 ──────────────────────────────────────────────
    stop_loss: float = 0.20
    max_position: float = 0.10             # 单只最大仓位占比
    max_industry_weight: float = 0.0       # 0 = 不限制
    max_daily_turnover: float = 0          # 0 = 不限制

    # ── 波动率缩放 ────────────────────────────────────────────
    use_vol_scaling: bool = True
    vol_target: float = 0.20

    # ── 止盈 ──────────────────────────────────────────────────
    use_take_profit: bool = False
    tp_tiers: Optional[list] = None        # e.g. [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)]

    # ── 持有期衰减 ────────────────────────────────────────────
    use_holding_decay: bool = False

    # ── ATR 止损 ──────────────────────────────────────────────
    use_atr_stop: bool = False
    atr_k: float = 6.0

    # ── 优化用 ────────────────────────────────────────────────
    risk_aversion: float = 1.0


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

STRATEGY_PROFILES["v10_small_cap"] = PROFILE_V10_SMALL_CAP
STRATEGY_PROFILES["v10b_small_mom"] = PROFILE_V10B_SMALL_MOM


@dataclass
class Config:
    """Root configuration object.  Loaded from config.yaml or defaults."""

    data_dir: str = "data"
    daily_dir: str = "data/daily"
    signal_dir: str = "data/signals"
    portfolio_dir: str = "data/portfolio"
    output_dir: str = "data/backtest_results"
    start_date: str = "2021-01-01"
    end_date: str = ""                    # empty = today

    costs: TradingCosts = field(default_factory=TradingCosts)
    risk: RiskLimits = field(default_factory=RiskLimits)
    market: MarketFilter = field(default_factory=MarketFilter)
    factor_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FACTOR_WEIGHTS))
    strategies: Dict[str, StrategyConfig] = field(default_factory=dict)

    @property
    def resolved_end_date(self) -> str:
        if self.end_date:
            return self.end_date
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d")


def load_config(path: Optional[str] = None) -> Config:
    """Load config from YAML.  Missing file / missing keys → defaults."""
    if path is None:
        candidates = [
            Path("config.yaml"),
            Path(__file__).parent.parent / "config.yaml",
            Path.home() / ".a-share-backtest" / "config.yaml",
        ]
        for p in candidates:
            if p.exists():
                path = str(p)
                break

    if path is None or not os.path.exists(path):
        return Config()

    try:
        import yaml
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return Config()

    costs = TradingCosts(**raw.get("costs", {}))
    risk = RiskLimits(**raw.get("risk", {}))
    fw = raw.get("factor_weights", {})
    factor_weights = dict(DEFAULT_FACTOR_WEIGHTS)
    if fw:
        factor_weights.update(fw)

    strategies = {}
    for name, s in raw.get("strategies", {}).items():
        strategies[name] = StrategyConfig(
            label=name,
            weight_method=s.get("weight_method", "equal"),
            top_n=s.get("top_n", risk.top_n),
            rebalance_freq=s.get("rebalance_freq", risk.rebalance_freq),
            stop_loss=s.get("stop_loss", risk.stop_loss),
            max_position=s.get("max_position", 0.10),
            use_vol_scaling=s.get("use_vol_scaling", True),
            vol_target=s.get("vol_target", 0.20),
            max_industry_weight=s.get("max_industry_weight", 0.25),
            max_daily_turnover=s.get("max_daily_turnover", 0),
            risk_aversion=s.get("risk_aversion", 1.0),
            factor_weights=factor_weights if s.get("weight_method") != "markowitz" else None,
            use_take_profit=s.get("use_take_profit", False),
            tp_tiers=s.get("tp_tiers", None),
            use_holding_decay=s.get("use_holding_decay", False),
            use_atr_stop=s.get("use_atr_stop", False),
            atr_k=s.get("atr_k", 6.0),
        )

    return Config(
        data_dir=raw.get("data", {}).get("data_dir", "data"),
        daily_dir=raw.get("data", {}).get("daily_dir", "data/daily"),
        signal_dir=raw.get("data", {}).get("signal_dir", "data/signals"),
        portfolio_dir=raw.get("data", {}).get("portfolio_dir", "data/portfolio"),
        output_dir=raw.get("data", {}).get("output_dir", "data/backtest_results"),
        start_date=raw.get("backtest", {}).get("start_date", "2021-01-01"),
        end_date=raw.get("backtest", {}).get("end_date", ""),
        costs=costs,
        risk=risk,
        factor_weights=factor_weights,
        strategies=strategies,
    )


# ── Module-level singleton (eager load) ──────────────────────────────
config = load_config()

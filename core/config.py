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
    'boll_pos_10': 0.03, 'boll_pos_20': 0.03, 'boll_width_20': -0.02,
    'atr_14': -0.03,
    'skew_20': 0.02, 'kurt_20': -0.02,
    'vwap_mom': 0.03,
    'rel_strength_20': 0.05, 'rel_strength_60': 0.03,
}


@dataclass
class TradingCosts:
    initial_capital: float = 1_000_000
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.001


@dataclass
class RiskLimits:
    stop_loss: float = 0.20
    top_n: int = 10
    rebalance_freq: int = 20
    max_single_weight: float = 0.15
    max_daily_turnover: float = 0.30
    min_rebalance_interval: int = 3


@dataclass
class StrategyConfig:
    label: str = "default"
    weight_method: str = "equal"          # equal | ic_ir | markowitz
    top_n: int = 10
    rebalance_freq: int = 20
    stop_loss: float = 0.20
    max_position: float = 0.10
    use_vol_scaling: bool = False
    vol_target: float = 0.20
    risk_aversion: float = 1.0
    factor_weights: Optional[Dict[str, float]] = None


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
            use_vol_scaling=s.get("use_vol_scaling", False),
            vol_target=s.get("vol_target", 0.20),
            risk_aversion=s.get("risk_aversion", 1.0),
            factor_weights=factor_weights if s.get("weight_method") != "markowitz" else None,
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

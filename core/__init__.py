"""
Core trading engine — shared by live simulation (sim_daily.py) and backtest (run_backtest.py).

Modules:
  config   — typed configuration (StrategyConfig dataclass + STRATEGY_PROFILES)
  factors  — factor calculation (single-stock + panel modes)
  account  — PortfolioState + buy/sell/check_stop_loss
  scoring  — factor standardization + composite score + ensemble
  strategy — StrategyEngine: unified scoring entry (factor/ml/hybrid/ensemble)
"""
from core.config import StrategyConfig, STRATEGY_PROFILES, DEFAULT_FACTOR_WEIGHTS, TradingCosts, MarketFilter
from core.factors import calc_factors_single, calc_factors_panel
from core.account import (
    PortfolioState, buy, sell, check_stop_loss,
    portfolio_value, status_report,
)
from core.scoring import (
    composite_score, composite_score_equal, standardize,
    score_all_stocks, rel_strength_adjust, factor_correlation,
    ensemble_union_score, ensemble_union_score_single,
)
from core.strategy import StrategyEngine
from core.position import Position, holdings_to_dict, holdings_from_dict, copy_holdings

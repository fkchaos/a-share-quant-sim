"""
Core trading engine — shared by live simulation and backtest.

Modules:
  config   — typed configuration (StrategyConfig dataclass + STRATEGY_PROFILES)
  factors  — factor calculation (single-stock + panel modes)
  account  — PortfolioState + buy/sell/check_stop_loss
  position — Position data model
  db       — SQLite dual-database layer
  strategy_map — strategy registry (dynamic loading)
"""
from core.config import StrategyConfig, STRATEGY_PROFILES, DEFAULT_FACTOR_WEIGHTS, TradingCosts, MarketFilter
from core.factors import calc_factors_single, calc_factors_panel
from core.account import (
    PortfolioState, buy, sell, check_stop_loss,
    portfolio_value, status_report,
)
from core.position import Position, holdings_to_dict, holdings_from_dict, copy_holdings

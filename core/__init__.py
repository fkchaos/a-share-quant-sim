"""
Core trading engine — shared by live simulation (sim_daily.py) and backtest (run_backtest.py).

Modules:
  config   — typed configuration from config.yaml
  factors  — factor calculation (single-stock + panel modes)
  account  — PortfolioState + buy/sell/check_stop_loss
  scoring  — factor standardization + composite score
"""
from core.config import Config, config, load_config
from core.factors import calc_factors_single, calc_factors_panel
from core.account import (
    PortfolioState, buy, sell, check_stop_loss,
    portfolio_value, status_report,
)
from core.scoring import composite_score, composite_score_equal, standardize, score_all_stocks, rel_strength_adjust, factor_correlation
from core.position import Position, holdings_to_dict, holdings_from_dict, copy_holdings

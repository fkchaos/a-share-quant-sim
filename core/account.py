"""
Core trading account — pure data class + transaction functions.

This module defines ONLY:
  - PortfolioState: immutable-ish snapshot of cash + holdings
  - buy() / sell() / partial_sell() / check_stop_loss() / check_take_profit(): transaction functions
  - portfolio_value() / status_report(): read operations

NO file I/O, NO strategy logic, NO factor calculation.
Both sim_daily.py (live) and run_backtest.py (backtest) call these functions.
"""

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.config import TradingCosts, RiskLimits


# ── PortfolioState ──────────────────────────────────────────────────

@dataclass
class PortfolioState:
    """Immutable-ish portfolio state.  Return a new copy after each transaction."""
    cash: float = 1_000_000
    initial_capital: float = 1_000_000
    holdings: Dict[str, dict] = field(default_factory=dict)
    trade_log: List[dict] = field(default_factory=list)
    nav_history: List[dict] = field(default_factory=list)

    def copy(self) -> 'PortfolioState':
        return copy.deepcopy(self)


# ── Transaction functions ───────────────────────────────────────────

def compute_buy_shares(
    state: PortfolioState,
    code: str,
    price: float,
    target_value: float = None,
    max_position_weight: float = 0.12,
) -> Tuple[int, float, float]:
    """Compute how many shares to buy, the cost, and commission.

    Parameters
    ----------
    target_value : float, optional — target market value for this stock.
                   If None, uses state.cash (backward-compatible).
    max_position_weight : float — max position as fraction of target_value cap.

    Returns: (shares, cost, commission)
    """
    costs = TradingCosts()

    if target_value is not None:
        # New mode: allocate based on provided target (from portfolio-level planning)
        pass
    else:
        # Legacy mode: split remaining cash evenly
        max_shares_by_count = max(1, len(state.holdings | {code: 1}))
        target_value = state.cash / max_shares_by_count

    # Cap by max_position
    # If target_value already represents a single-stock target, don't cap again
    # (capping is done at the caller level)

    adj_price = price * (1 + costs.slippage_rate)
    shares = int(target_value / adj_price / 100) * 100

    if shares <= 0:
        return 0, 0.0, 0.0

    cost = shares * adj_price
    commission = cost * costs.commission_rate

    # Final cash check: if not enough cash, reduce shares but keep at least 100 shares
    if state.cash < cost + commission:
        # Reduce shares to fit available cash (keep at least 1 lot = 100 shares)
        _max_affordable = int((state.cash * 0.98) / adj_price / 100) * 100
        if _max_affordable >= 100:
            shares = _max_affordable
            cost = shares * adj_price
            commission = cost * costs.commission_rate
        else:
            # Can't afford minimum 100 shares — skip this stock
            return 0, 0.0, 0.0

    return shares, cost, commission


def buy(
    state: PortfolioState,
    code: str,
    price: float,
    date,
    shares: int = None,
    target_value: float = None,
) -> PortfolioState:
    """Execute a buy order. Returns a NEW state.

    Parameters
    ----------
    shares : int, explicit share count (bypasses auto-compute).
    target_value : float, target market value for auto-compute.
                   If None, falls back to legacy cash-splitting logic.
    """
    costs = TradingCosts()
    new_state = state.copy()

    adj_price = price * (1 + costs.slippage_rate)

    if shares is not None:
        # Explicit share count — 强制 100 股整数倍
        shares = int(shares / 100) * 100
        if shares <= 0:
            return new_state  # no-op
        cost = shares * adj_price
        commission = cost * costs.commission_rate
        if new_state.cash < cost + commission:
            return new_state  # no-op
    else:
        # Auto-compute shares
        shares, cost, commission = compute_buy_shares(
            new_state, code, price, target_value=target_value,
        )
        if shares <= 0:
            return new_state  # no-op

    new_state.cash -= (cost + commission)

    # Update with weighted average cost if adding to existing position
    if code in new_state.holdings:
        old = new_state.holdings[code]
        total_shares = old['shares'] + shares
        total_cost = old['shares'] * old['cost_price'] + shares * price
        new_state.holdings[code] = {
            'shares': total_shares,
            'cost_price': total_cost / total_shares,
            'entry_date': old['entry_date'],
            'tp_taken': old.get('tp_taken', []),
            'highest_profit': old.get('highest_profit', 0.0),
        }
    else:
        new_state.holdings[code] = {
            'shares': shares,
            'cost_price': price,
            'entry_date': str(date),
            'tp_taken': [],
            'highest_profit': 0.0,
        }

    new_state.trade_log.append({
        'date': str(date),
        'code': code,
        'action': 'BUY',
        'shares': shares,
        'price': price,
        'cost': cost,
        'commission': commission,
        'reason': 'AUTO' if target_value is None else 'TARGET',
    })

    return new_state


def sell(
    state: PortfolioState,
    code: str,
    price: float,
    date,
    reason: str = 'SELL',
) -> PortfolioState:
    """Execute a sell order.  Returns a NEW state."""
    costs = TradingCosts()
    new_state = state.copy()

    if code not in new_state.holdings:
        return new_state

    info = new_state.holdings[code]
    adj_price = price * (1 - costs.slippage_rate)
    revenue = info['shares'] * adj_price
    commission = revenue * costs.commission_rate
    stamp_tax = revenue * costs.stamp_tax_rate

    new_state.cash += (revenue - commission - stamp_tax)

    pnl = (price - info['cost_price']) / info['cost_price']

    new_state.trade_log.append({
        'date': str(date), 'code': code, 'action': reason,
        'shares': info['shares'], 'price': round(price, 4),
        'cost': round(commission + stamp_tax, 2),
        'pnl': round(pnl, 4),
    })

    del new_state.holdings[code]
    return new_state


def partial_sell(
    state: PortfolioState,
    code: str,
    price: float,
    date,
    sell_fraction: float,
    reason: str = 'TAKE_PROFIT',
) -> PortfolioState:
    """Sell a fraction of a position. Returns NEW state.

    sell_fraction: 0.0~1.0, fraction of current shares to sell.
    E.g. sell_fraction=0.3 means sell 30% of holdings, keep 70%.
    """
    new_state = state.copy()

    if code not in new_state.holdings:
        return new_state

    info = new_state.holdings[code]
    cur_shares = info['shares']
    sell_shares = int(cur_shares * sell_fraction / 100) * 100

    if sell_shares <= 0:
        return new_state

    remaining = cur_shares - sell_shares
    if remaining < 100:
        # Sell everything if remainder would be < 1 lot
        return sell(new_state, code, price, date, reason=reason)

    costs = TradingCosts()
    adj_price = price * (1 - costs.slippage_rate)
    revenue = sell_shares * adj_price
    commission = revenue * costs.commission_rate
    stamp_tax = revenue * costs.stamp_tax_rate

    new_state.cash += (revenue - commission - stamp_tax)
    new_state.holdings[code] = {
        'shares': remaining,
        'cost_price': info['cost_price'],  # cost basis unchanged for remainder
        'entry_date': info['entry_date'],
        'tp_taken': info.get('tp_taken', []),
    }

    pnl = (price - info['cost_price']) / info['cost_price']
    new_state.trade_log.append({
        'date': str(date), 'code': code, 'action': reason,
        'shares': sell_shares, 'price': round(price, 4),
        'cost': round(commission + stamp_tax, 2),
        'pnl': round(pnl, 4),
    })

    return new_state


# ── Stop-loss ────────────────────────────────────────────────────────

def check_stop_loss(
    state: PortfolioState,
    date,
    price_data,
    atr_data=None,
) -> PortfolioState:
    """Check all holdings for stop-loss triggers. Returns NEW state with liquidated positions.

    Two modes:
      - fixed (default): stop when loss >= risk.stop_loss (e.g. 20%)
      - atr-based:      stop when loss >= K * ATR(14) / price
        Requires atr_data: Series of ATR values indexed by stock code.
        K defaults to 6.0 (RiskLimits.stop_loss_atr_k).
    """
    risk = RiskLimits()
    new_state = state.copy()
    use_atr = atr_data is not None
    atr_k = getattr(risk, 'stop_loss_atr_k', 6.0)

    for code in sorted(new_state.holdings.keys()):
        if code in price_data.index:
            p = price_data[code]
            if not pd.isna(p) and p > 0:
                if use_atr and code in atr_data.index:
                    atr_val = atr_data[code]
                    if not pd.isna(atr_val) and atr_val > 0:
                        dynamic_threshold = atr_k * atr_val / p
                        dynamic_threshold = max(0.03, min(0.50, dynamic_threshold))
                        loss = (new_state.holdings[code]['cost_price'] - p) / new_state.holdings[code]['cost_price']
                        if loss >= dynamic_threshold:
                            new_state = sell(new_state, code, p, date, reason='STOP_LOSS')
                        continue
                # Fixed stop-loss (default)
                loss = (new_state.holdings[code]['cost_price'] - p) / new_state.holdings[code]['cost_price']
                if loss >= risk.stop_loss:
                    new_state = sell(new_state, code, p, date, reason='STOP_LOSS')
    return new_state


# ── Take-profit (tiered partial sell) ───────────────────────────────

def check_take_profit(
    state: PortfolioState,
    date,
    price_data,
    tiers=None,
) -> PortfolioState:
    """Tiered take-profit: partially sell when profit crosses thresholds.

    tiers: list of (profit_threshold, sell_fraction) applied sequentially.
        Default: [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)]
        At +10% → sell 30% of position (lock in some gain)
        At +20% → sell another 30%
        At +30% → sell remaining (full exit)

    Each tier triggers only once per position (tracked via holdings[code]['tp_taken']).
    Triggered in order; only one tier per day.
    Returns NEW state.
    """
    if tiers is None:
        tiers = [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)]

    new_state = state.copy()

    for code in sorted(new_state.holdings.keys()):
        if code not in price_data.index:
            continue
        p = price_data[code]
        if pd.isna(p) or p <= 0:
            continue

        info = new_state.holdings[code]
        cost = info['cost_price']
        profit = (p - cost) / cost
        tp_taken = set(info.get('tp_taken', []))
        # Bug 3 修复：记录历史最高 profit，按"曾到达"判断 tier
        highest = max(profit, info.get('highest_profit', 0.0))
        info['highest_profit'] = highest

        for threshold, sell_frac in tiers:
            if highest >= threshold and threshold not in tp_taken:
                new_state = partial_sell(new_state, code, p, date,
                                         sell_fraction=sell_frac,
                                         reason='TAKE_PROFIT')
                # Update tp_taken on the remaining position (if any)
                if code in new_state.holdings:
                    new_tp = sorted(tp_taken | {threshold})
                    new_state.holdings[code]['tp_taken'] = new_tp
                # If fully sold (code no longer in holdings), nothing more to track
                break  # Only trigger one tier per day

    return new_state


# ── Holding-period decay ────────────────────────────────────────────

def apply_holding_decay(
    state: PortfolioState,
    date,
    price_data,
    rebalance_freq: int = 20,
) -> PortfolioState:
    """Reduce position size for stocks held longer than rebalance_freq days.

    Decay schedule:
      - held <= rebalance_freq days:  no reduction (full weight)
      - held > rebalance_freq days:   reduce to 70% of current shares
      - held > 2 * rebalance_freq:    reduce to 40% of current shares

    This frees up capital for new high-score stocks and reduces concentration
    risk in stale positions.

    Returns NEW state.
    """
    from datetime import datetime, timedelta

    new_state = state.copy()
    threshold_1x = rebalance_freq
    threshold_2x = 2 * rebalance_freq

    for code in sorted(new_state.holdings.keys()):
        if code not in price_data.index:
            continue
        p = price_data[code]
        if pd.isna(p) or p <= 0:
            continue

        info = new_state.holdings[code]
        try:
            entry = pd.Timestamp(info['entry_date'])
            if hasattr(date, 'date'):  # Timestamp
                today = pd.Timestamp(date)
            else:
                today = pd.Timestamp(str(date))
            held_days = (today - entry).days
        except Exception:
            continue

        if held_days <= threshold_1x:
            continue  # No decay

        cur_shares = info['shares']

        if held_days > threshold_2x:
            target_frac = 0.40  # Keep 40%
        elif held_days > threshold_1x:
            target_frac = 0.70  # Keep 70%
        else:
            continue

        target_shares = int(cur_shares * target_frac / 100) * 100
        sell_shares = cur_shares - target_shares

        if sell_shares >= 100:
            new_state = partial_sell(new_state, code, p, date,
                                     sell_fraction=sell_shares / cur_shares,
                                     reason='HOLDING_DECAY')

    return new_state


# ── Risk Parity ──────────────────────────────────────────────────────

def _risk_parity_weights(cov, max_iter=100, tol=1e-6, max_position=0.10):
    """Compute risk parity weights from covariance matrix.

    Each stock contributes equally to portfolio volatility.
    Uses iterative algorithm (spin/scenario approach).

    Args:
        cov: DataFrame (annualized covariance matrix), index=columns=stock codes
        max_iter: maximum iterations
        tol: convergence tolerance
        max_position: cap per-stock weight

    Returns:
        Dict {stock_code: weight} summing to 1.0
    """
    stocks = list(cov.index)
    n = len(stocks)
    if n == 0:
        return {}
    if n == 1:
        return {stocks[0]: 1.0}

    w = np.ones(n) / n
    cov_arr = cov.values.astype(float)

    for _ in range(max_iter):
        port_vol = np.sqrt(w @ cov_arr @ w)
        if port_vol < 1e-10:
            break
        # Marginal risk contribution
        marginal = (cov_arr @ w) / port_vol
        # Risk contribution per stock
        rc = w * marginal
        # Target: equal risk contribution
        target = port_vol / n
        # Update weights proportional to target/RC ratio
        ratio = target / np.maximum(rc, 1e-12)
        w = w * ratio
        w = np.maximum(w, 1e-8)
        w /= w.sum()
        # Check convergence
        rc_new = w * (cov_arr @ w) / np.sqrt(w @ cov_arr @ w)
        if np.std(rc_new) < tol:
            break

    # Apply max_position cap
    capped = np.minimum(w, max_position)
    # Redistribute excess to uncapped stocks
    for _ in range(10):
        excess = w.sum() - capped.sum()
        if excess <= 0:
            break
        uncapped = capped < max_position - 1e-8
        if not uncapped.any():
            break
        add_per = excess / uncapped.sum()
        capped[uncapped] += add_per
        capped = np.minimum(capped, max_position)

    capped /= capped.sum()
    return {stocks[i]: float(capped[i]) for i in range(n)}


# ── Position sizing / weight allocation ─────────────────────────────

def allocate_weights(
    top_stocks: List[str],
    price_data,            # Series (price per stock) for the current day
    method: str = 'equal',
    vol_target: float = 0.20,
    close_panel=None,      # DataFrame (dates × stocks) for volatility calc; used by vol_inverse
    vol_series=None,       # Series of per-stock volatility; alternative to close_panel
    max_position: float = 0.10,
) -> Dict[str, float]:
    """Compute position weights for a list of target stocks.

    Methods:
        'equal'         : 1/N each
        'vol_inverse'   : inverse-volatility weighted (lower vol → higher weight)
        'risk_parity'   : risk parity (equal risk contribution per stock)
        'markowitz'     : placeholder (caller should use markowitz_optimize directly)

    vol_series: pre-computed per-stock volatility (e.g. 20-day std of returns).
        If provided, takes precedence over close_panel for vol_inverse method.

    Returns:
        {stock_code: weight} dict summing to <= 1.0
    """
    if not top_stocks:
        return {}

    n = len(top_stocks)

    if method == 'equal':
        w = {c: 1.0 / n for c in top_stocks}

    elif method == 'vol_inverse':
        vol = None
        if vol_series is not None:
            vol = vol_series.reindex(top_stocks).dropna()
        elif close_panel is not None:
            available = [c for c in top_stocks if c in close_panel.columns]
            sub = close_panel[available] if available else None
            if sub is not None and len(sub) >= 20:
                ret = sub.pct_change()
                vol = ret.tail(20).std()

        if vol is not None and len(vol) > 0:
            vol = vol.reindex(top_stocks).fillna(vol.median())
            vol = vol.clip(lower=1e-6)
            inv_vol = 1.0 / vol
            total = inv_vol.sum()
            raw_w = (inv_vol / total).to_dict()
            # Clamp each weight to max_position
            clamped = {c: min(v, max_position) for c, v in raw_w.items()}
            # Re-normalise to sum to 1.0
            s = sum(clamped.values())
            w = {c: v / s for c, v in clamped.items()} if s > 0 else {c: 1.0 / n for c in top_stocks}
        else:
            w = {c: 1.0 / n for c in top_stocks}

    elif method == 'risk_parity':
        # Risk parity: each stock contributes equally to portfolio volatility
        # Requires close_panel for covariance estimation
        if close_panel is not None and len(close_panel) >= 20:
            available = [c for c in top_stocks if c in close_panel.columns]
            if len(available) >= 2:
                sub = close_panel[available]
                ret = sub.pct_change().dropna()
                if len(ret) >= 10:
                    cov = ret.cov() * 252  # annualized covariance
                    w = _risk_parity_weights(cov, max_position=max_position)
                    # Map back to all top_stocks (missing ones get 0)
                    w_full = {c: w.get(c, 0.0) for c in top_stocks}
                    # Normalize
                    s = sum(w_full.values())
                    w = {c: v / s for c, v in w_full.items()} if s > 0 else {c: 1.0 / n for c in top_stocks}
                else:
                    w = {c: 1.0 / n for c in top_stocks}
            else:
                w = {c: 1.0 / n for c in top_stocks}
        else:
            w = {c: 1.0 / n for c in top_stocks}

    elif method == 'markowitz':
        w = {c: 1.0 / n for c in top_stocks}

    else:
        w = {c: 1.0 / n for c in top_stocks}

    return w


# ── Read-only operations ────────────────────────────────────────────

def portfolio_value(state: PortfolioState, date, price_data) -> float:
    """Total portfolio value = cash + holdings market value."""
    total = state.cash
    for code, info in state.holdings.items():
        if code in price_data.index:
            p = price_data[code]
            if not pd.isna(p) and p > 0:
                total += info['shares'] * p
    return total


def status_report(state: PortfolioState, date, price_data) -> dict:
    """Generate a full status report."""
    total_value = portfolio_value(state, date, price_data)

    holdings_report = []
    for code, info in state.holdings.items():
        if code in price_data.index:
            p = price_data[code]
            if not pd.isna(p) and p > 0:
                mv = info['shares'] * p
                holdings_report.append({
                    'code': code,
                    'shares': info['shares'],
                    'cost_price': info['cost_price'],
                    'current_price': p,
                    'market_value': mv,
                    'weight': mv / total_value if total_value > 0 else 0,
                    'pnl': (p - info['cost_price']) / info['cost_price'],
                    'entry_date': info['entry_date'],
                    'tp_taken': info.get('tp_taken', []),
                })

    return {
        'date': str(date),
        'cash': state.cash,
        'portfolio_value': total_value,
        'total_return': (total_value / state.initial_capital) - 1,
        'holdings_count': len(state.holdings),
        'holdings': holdings_report,
        'total_trades': len(state.trade_log),
    }

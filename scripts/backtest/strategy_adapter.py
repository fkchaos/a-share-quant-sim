#!/usr/bin/env python3
"""
scripts/backtest/strategy_adapter.py — 统一策略适配器
=====================================================
所有策略（v27/v20c/v11b/内置）的统一接口层。

职责：
1. 策略注册表：策略名 → 选股函数 + 风控参数 + 市场状态参数
2. 统一选股接口：select_stocks(strategy_name, factors, date, holdings, params)
3. 统一风控接口：check_risk(strategy_name, state, date, price_data, params)
4. 统一市场状态接口：calc_regime(strategy_name, close_panel, date, params)

回测和模拟盘都通过这个适配器调用策略逻辑，确保代码路径一致。

使用方式：
    from scripts.backtest.strategy_adapter import StrategyAdapter

    adapter = StrategyAdapter()
    cands = adapter.select("v27", factors, date, holdings, params)
    to_sell = adapter.risk_check("v27", state, date, price_data, params, prev_close=prev)
    regime, mult = adapter.regime("v27", close_panel, date, params)
"""
import sys
import os
import pandas as pd

# 确保项目根目录在 path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.db import get_kline


class StrategyAdapter:
    """统一策略适配器：选股 + 风控 + 市场状态"""

    def __init__(self):
        self._select_fns = {}
        self._risk_params = {}
        self._regime_params = {}
        self._register_builtin_strategies()

    def _register_builtin_strategies(self):
        """注册所有活跃策略"""
        # ── v27: 价量共振 ──
        self._select_fns["v27"] = self._v27_select
        self._risk_params["v27"] = {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.05,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
        }
        self._regime_params["v27"] = {
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        }

        # ── v20c: 尾盘缩量 ──
        self._select_fns["v20c"] = self._v20c_select
        self._risk_params["v20c"] = {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.15,
            "HOLD_DAYS_MAX": 2,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 8,
            "MAX_POSITION": 0.30,
            "INITIAL_CAPITAL": 200000,
        }
        self._regime_params["v20c"] = {
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        }

    # ── 统一选股接口 ──────────────────────────────────────────────

    def select(self, strategy_name, factors, date, close_panel=None,
               volume_panel=None, amount_panel=None, high_panel=None,
               low_panel=None, open_panel=None, current_holdings=None,
               params=None):
        """
        统一选股接口。

        参数:
            strategy_name: 策略名 ("v27" / "v20c")
            factors: dict — calc_factors() 返回的因子面板
            date: Timestamp — 选股日期
            close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel:
                DataFrame — 价格面板（v20c 需要）
            current_holdings: dict — 当前持仓（用于排除已持有）
            params: dict — 策略参数覆盖

        返回:
            list[(code, score)] — 按评分降序排列
        """
        fn = self._select_fns.get(strategy_name)
        if fn is None:
            raise ValueError(f"未知策略: {strategy_name}，可用: {list(self._select_fns.keys())}")
        return fn(factors, date, close_panel, volume_panel, amount_panel,
                   high_panel, low_panel, open_panel, current_holdings, params)

    def _v27_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params):
        """v27 选股 — 委托给 v27_select.py"""
        from scripts.strategies.v27_select import calc_factors, select_stocks_v27

        # 如果 factors 是 None 或原始面板，先计算因子
        if factors is None or "mom_5" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v27"])
        if params:
            merged_params.update(params)
        return select_stocks_v27(factors, date, current_holdings, merged_params)

    def _v20c_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params):
        """v20c 选股 — 委托给 v20_tail_pick.py，统一返回 list[(code, score)]"""
        from scripts.strategies.v20_tail_pick import calc_tail_pick_factors, select_stocks_tail_pick

        # 如果 factors 是 None 或原始面板，先计算因子
        if factors is None or "vol_ratio" not in factors:
            factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel,
                                             high_panel, low_panel)

        codes = select_stocks_tail_pick(factors, date, close_panel, volume_panel,
                                        amount_panel, high_panel, low_panel,
                                        current_holdings)
        # select_stocks_tail_pick 返回 list[str]，统一转为 list[(code, score)]
        # 注意：板块过滤由调用方（account_runner/wf_runner）根据策略配置决定
        return [(c, 1.0) for c in codes]

    # ── 统一风控接口 ──────────────────────────────────────────────

    def risk_check(self, strategy_name, state, date, price_data, params=None,
                   prev_close=None, sell1_vol=None):
        """
        统一风控检查接口。

        参数:
            strategy_name: 策略名
            state: PortfolioState
            date: 当前日期
            price_data: Series — 当日收盘价
            params: dict — 策略参数
            prev_close: Series — 前日收盘价（涨停判断用）
            sell1_vol: Series — 卖一量（涨停判断用）

        返回:
            list[(code, reason, pnl)] — 需要卖出的股票
        """
        merged = dict(self._risk_params.get(strategy_name, {}))
        if params:
            merged.update(params)
        return self._check_risk_impl(state, date, price_data, merged, prev_close, sell1_vol)

    def _check_risk_impl(self, state, date, price_data, params, prev_close, sell1_vol):
        """风控实现 — 与 account_runner.py 的 check_risk() 逻辑一致"""
        to_sell = []
        hold_max = params["HOLD_DAYS_MAX"]
        hold_ext = params.get("HOLD_DAYS_EXTEND", hold_max)
        hold_ext_pnl = params.get("HOLD_DAYS_EXTEND_PNL", 0.03)

        def _limit_threshold(code):
            if code.startswith('300') or code.startswith('688'):
                return 0.199
            return 0.099

        for code, h in list(state.holdings.items()):
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost_price']) / h['cost_price']

            # 封板判断
            is_limit_up = False
            if sell1_vol is not None and code in sell1_vol.index:
                sv = sell1_vol[code]
                if not pd.isna(sv) and sv == 0:
                    is_limit_up = True
            if not is_limit_up and prev_close is not None and code in prev_close.index:
                prev = prev_close[code]
                if not pd.isna(prev) and prev > 0:
                    chg = (cp - prev) / prev
                    if chg >= _limit_threshold(code):
                        is_limit_up = True

            if pnl <= params["STOP_LOSS"]:
                to_sell.append((code, 'stop_loss', pnl))
            elif pnl >= params["TAKE_PROFIT"]:
                if is_limit_up:
                    continue
                to_sell.append((code, 'take_profit', pnl))
            else:
                hd = h.get('hold_days', 0)
                limit = hold_ext if pnl >= hold_ext_pnl else hold_max
                if hd >= limit:
                    to_sell.append((code, 'timeout', pnl))
        return to_sell

    # ── 统一市场状态接口 ──────────────────────────────────────────

    def calc_regime(self, strategy_name, close_panel, date, params=None):
        """
        市场状态识别 → 仓位乘数。

        返回:
            (regime_label, multiplier) — 如 ("牛市", 1.0)
        """
        merged = dict(self._regime_params.get(strategy_name, {}))
        if params:
            merged.update(params)

        if not merged.get("REGIME_ENABLED", False):
            return ("未启用", 1.0)

        INDEX_CODE = "sh000001"
        kl = get_kline(INDEX_CODE)
        if not kl:
            return ("指数数据缺失", 1.0)

        idx_df = pd.DataFrame(kl)
        idx_df["date"] = pd.to_datetime(idx_df["date"])
        idx_df = idx_df.set_index("date").sort_index()
        idx_df = idx_df[idx_df["volume"] > 0]

        if date not in idx_df.index:
            return ("指数日期缺失", 1.0)

        ma_period = merged.get("REGIME_MA_PERIOD", 20)
        slope_days = merged.get("REGIME_SLOPE_DAYS", 5)

        pos = idx_df.index.get_loc(date)
        if isinstance(pos, slice):
            pos = pos.start
        if pos < ma_period + slope_days:
            return ("数据不足", 1.0)

        close_series = idx_df["close"]
        ma20_now = close_series.iloc[pos - ma_period + 1:pos + 1].mean()
        ma20_prev = close_series.iloc[pos - ma_period - slope_days + 1:pos - slope_days + 1].mean()
        slope = (ma20_now - ma20_prev) / ma20_prev if ma20_prev > 0 else 0

        if pos >= 59:
            ma60 = close_series.iloc[pos - 59:pos + 1].mean()
        else:
            ma60 = close_series.iloc[:pos + 1].mean()

        price_now = close_series.iloc[pos]
        bull_alloc = merged.get("REGIME_BULL_ALLOC", 1.0)
        bear_alloc = merged.get("REGIME_BEAR_ALLOC", 0.3)
        sideways_alloc = merged.get("REGIME_SIDEWAYS_ALLOC", 0.7)

        if slope > 0 and price_now > ma60:
            return ("牛市", bull_alloc)
        elif slope < 0 and price_now < ma60:
            return ("熊市", bear_alloc)
        else:
            return ("震荡", sideways_alloc)

    # ── 策略参数查询 ──────────────────────────────────────────────

    def get_risk_params(self, strategy_name):
        """获取策略的风控参数"""
        return dict(self._risk_params.get(strategy_name, {}))

    def get_regime_params(self, strategy_name):
        """获取策略的市场状态参数"""
        return dict(self._regime_params.get(strategy_name, {}))

    def list_strategies(self):
        """列出所有已注册策略"""
        return list(self._select_fns.keys())


# ── 模块级便捷函数 ────────────────────────────────────────────────

_adapter = None

def get_adapter():
    """获取全局 StrategyAdapter 实例（懒加载）"""
    global _adapter
    if _adapter is None:
        _adapter = StrategyAdapter()
    return _adapter


def select_stocks(strategy_name, factors, date, **kwargs):
    """模块级便捷函数"""
    return get_adapter().select(strategy_name, factors, date, **kwargs)


def check_risk(strategy_name, state, date, price_data, **kwargs):
    """模块级便捷函数"""
    return get_adapter().risk_check(strategy_name, state, date, price_data, **kwargs)


def calc_regime(strategy_name, close_panel, date, **kwargs):
    """模块级便捷函数"""
    return get_adapter().calc_regime(strategy_name, close_panel, date, **kwargs)

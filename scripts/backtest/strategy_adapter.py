#!/usr/bin/env python3
"""
scripts/backtest/strategy_adapter.py — 统一策略适配器
=====================================================
所有策略（v27/v11b/内置）的统一接口层。v20c 已退役。

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
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.05,
        }
        self._regime_params["v27"] = {}

        # ── v20c: 已退役（2026-07-24）──
        # 面板顺序 bug 修复后策略失效（WF 5/16，全量 -67%，核心因子 IC≈0）
        # 代码保留在 scripts/strategies/v20_tail_pick.py，不注册到适配器

        # ── v31: 已归档（2026-07-25）──
        # 与 v27 高度重复（mom_20/mom_40 与 mom_5 相关性 0.3-0.5），无独立回测价值
        # 代码保留在 scripts/strategies/v29_select.py，不注册到适配器

        # ── v32: 分析师预期因子 ──
        self._select_fns["v32"] = self._v32_select
        self._risk_params["v32"] = {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.05,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
        }
        self._regime_params["v32"] = {
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        }

        # ── v35: 行业轮动 ──
        self._select_fns["v35"] = self._v35_select
        self._risk_params["v35"] = {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.05,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
        }
        self._regime_params["v35"] = {
            "REGIME_ENABLED": True,
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        }

        # ── v38: 价量共振（v27 改进版）──
        self._select_fns["v38"] = self._v38_select
        self._risk_params["v38"] = {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.06,
            "PV_CORR_20_MIN": 0.10,
            "PV_CORR_10_MIN": -0.2,
            "BOLL_W_MIN": 0.8,
            "MIN_AMOUNT_DAYS": 30000000,
            "COOLDOWN_DAYS": 3,
            "MAX_SAME_PREFIX": 3,
        }
        self._regime_params["v38"] = {}

        # ── v39: 价量共振高频版（v38 放宽门槛）──
        self._select_fns["v39"] = self._v39_select
        self._risk_params["v39"] = {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 10,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_20_MIN": 0.05,
            "TURNOVER_MIN": 0.003,
            "BOLL_W_MIN": 0.15,
            "MIN_AMOUNT_DAYS": 3000000,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 10,
        }
        self._regime_params["v39"] = {}

        # ── v39b: 价量共振平衡版（v38 门槛 + 收盘价买入 + 提高日买入上限）──
        self._select_fns["v39b"] = self._v39b_select
        self._risk_params["v39b"] = {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 10,
            "MAX_POSITION": 0.15,
            "MOM_THRESHOLD": 0.05,
            "PV_CORR_20_MIN": 0.10,
            "TURNOVER_MIN": 0.005,
            "BOLL_W_MIN": 0.2,
            "MIN_AMOUNT_DAYS": 5000000,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 10,
        }
        self._regime_params["v39b"] = {}

        # ── v39c: v27 门槛 + v39 多因子评分（验证评分逻辑是否有效）──
        self._select_fns["v39c"] = self._v39c_select
        self._risk_params["v39c"] = {
            "STOP_LOSS": -0.015,
            "TAKE_PROFIT": 0.03,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.05,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 8,
        }
        self._regime_params["v39c"] = {}

        # ── v40: 因子恶化卖出 + 延迟止盈止损（v39c 评分体系 + 持仓重评分）──
        self._select_fns["v40"] = self._v40_select
        self._risk_params["v40"] = {
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
            "SELL_THRESHOLD": 0.35,
            "BUY_BACK_THRESHOLD": 0.65,
            "SELL_PENALTY_N": 1,
            "SELL_MODE": "threshold",
            "MOMENTUM_DROP_PCT": 0.30,
        }
        self._regime_params["v40"] = {}

        # ── v40b: 纯轮动（每日卖最低4只+买最高4只，无硬风控）──
        self._select_fns["v40b"] = self._v40b_select
        self._risk_params["v40b"] = {
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
            "SELL_COUNT": 4,
            "BUY_COUNT": 4,
            "NO_HARD_RISK": True,
        }
        self._regime_params["v40b"] = {}

        # ── v41: VWAP 偏离 + 净支撑量因子（v39c 评分体系 + 2个新量价因子）──
        self._select_fns["v41"] = self._v41_select
        self._risk_params["v41"] = {
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
        }
        self._regime_params["v41"] = {}

        # ── v39d: v39c 参数优化（IC 驱动权重调整 + 松风控 + 低换手）──
        self._select_fns["v39d"] = self._v39d_select
        self._risk_params["v39d"] = {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 8,
            "W_MOM": 0.15,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.30,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        }
        self._regime_params["v39d"] = {}

        # ── v39e: 基于 v39d 交易行为分析进一步优化 ──
        self._select_fns["v39e"] = self._v39e_select
        self._risk_params["v39e"] = {
            "STOP_LOSS": -0.03,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 3,
            "HOLD_DAYS_EXTEND_PNL": 0.05,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.15,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 8,
            "W_MOM": 0.10,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.40,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        }
        self._regime_params["v39e"] = {}

        # ── v39f: 修正 v39e 错误（保持 -5% 止损 + 降低止盈到 5%）──
        self._select_fns["v39f"] = self._v39f_select
        self._risk_params["v39f"] = {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.05,
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
            "W_MOM": 0.10,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.40,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        }
        self._regime_params["v39f"] = {}

        # ── v39g: 风控参数优化（短持有期 + 低止盈 + 高换手）──
        self._select_fns["v39g"] = self._v39g_select
        self._risk_params["v39g"] = {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.05,
            "HOLD_DAYS_MAX": 3,
            "HOLD_DAYS_EXTEND": 3,
            "HOLD_DAYS_EXTEND_PNL": 0.08,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.03,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 8,
            "W_MOM": 0.10,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.40,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        }
        self._regime_params["v39g"] = {}

        # ── v39h: 动态 MOM_THRESHOLD（熊市自适应减仓）──
        self._select_fns["v39h"] = self._v39h_select
        self._risk_params["v39h"] = {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.03,
            "MOM_THRESHOLD_BEAR": 0.10,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 8,
            "W_MOM": 0.15,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.30,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        }
        self._regime_params["v39h"] = {}

        # ── v39i: 最优阈值（BULL=0.05/BEAR=0.08，夏普1.199/回撤16.69%）──
        self._select_fns["v39i"] = self._v39i_select
        self._risk_params["v39i"] = {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.10,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 5,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 3,
            "MAX_POSITION": 0.20,
            "MOM_THRESHOLD": 0.05,
            "MOM_THRESHOLD_BEAR": 0.08,
            "PV_CORR_10_MIN": -0.5,
            "PV_CORR_20_MIN": 0.0,
            "BOLL_W_MIN": 0.0,
            "COOLDOWN_DAYS": 0,
            "MAX_HOLDINGS": 8,
            "W_MOM": 0.15,
            "W_PV_CORR": 0.05,
            "W_TURNOVER": 0.05,
            "W_SIZE": 0.30,
            "W_FUND_FLOW": 0.05,
            "W_GAP": 0.05,
            "W_ILLIQ": 0.20,
        }
        self._regime_params["v39i"] = {}

        # ── v33: 残差动量 ──
        self._select_fns["v33"] = self._v33_select
        self._risk_params["v33"] = {
            "STOP_LOSS": -0.02,
            "TAKE_PROFIT": 0.05,
            "HOLD_DAYS_MAX": 5,
            "HOLD_DAYS_EXTEND": 7,
            "HOLD_DAYS_EXTEND_PNL": 0.03,
            "MAX_DAILY_BUY": 4,
            "MAX_POSITION": 0.20,
        }
        self._regime_params["v33"] = {
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
               params=None, sold_recently=None):
        """
        统一选股接口。

        参数:
            strategy_name: 策略名 ("v27")
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
                   high_panel, low_panel, open_panel, current_holdings, params,
                   sold_recently=sold_recently)

    def _v27_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
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

    # v20c 已退役，_v20c_select 方法移除
    # v31 已归档，_v29_select 方法移除

    def _v38_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v38 选股 — 委托给 v38_pv_resonance.py"""
        from scripts.strategies.v38_pv_resonance import calc_factors, select_stocks_v38

        if factors is None or "mom_5" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v38"])
        if params:
            merged_params.update(params)
        return select_stocks_v38(factors, date, current_holdings, merged_params,
                                  sold_recently=sold_recently)

    def _v39_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v39 选股 — 委托给 v39_pv_resonance.py"""
        from scripts.strategies.v39_pv_resonance import calc_factors, select_stocks_v39

        if factors is None or "mom_5" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39"])
        if params:
            merged_params.update(params)
        return select_stocks_v39(factors, date, current_holdings, merged_params,
                                  sold_recently=sold_recently)

    def _v39b_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39b 选股 — 委托给 v39b_pv_resonance.py"""
        from scripts.strategies.v39b_pv_resonance import calc_factors, select_stocks_v39b

        if factors is None or "mom_5" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39b"])
        if params:
            merged_params.update(params)
        return select_stocks_v39b(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v39c_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39c 选股 — v27 门槛 + v39 多因子评分"""
        from scripts.strategies.v39c_pv_resonance import calc_factors, select_stocks_v39c

        if factors is None or "mom_5" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39c"])
        if params:
            merged_params.update(params)
        return select_stocks_v39c(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v40b_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v40b 选股 — 纯轮动（卖最低4只+买最高4只）"""
        from scripts.strategies.v40_factor_exit import calc_factors, select_stocks_v40b

        if factors is None or "mom_5" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v40b"])
        if params:
            merged_params.update(params)
        return select_stocks_v40b(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v39d_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39d 选股 — v39c 参数优化（IC 驱动权重调整 + 松风控 + 低换手）"""
        from scripts.strategies.v39d_optimized import select_stocks_v39d

        if factors is None or "mom_5" not in factors:
            from scripts.strategies.v39c_pv_resonance import calc_factors
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39d"])
        if params:
            merged_params.update(params)
        return select_stocks_v39d(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v39e_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39e 选股 — 基于 v39d 交易行为分析进一步优化"""
        from scripts.strategies.v39e_optimized import select_stocks_v39e

        if factors is None or "mom_5" not in factors:
            from scripts.strategies.v39c_pv_resonance import calc_factors
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39e"])
        if params:
            merged_params.update(params)
        return select_stocks_v39e(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v39f_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39f 选股 — 修正 v39e 错误"""
        from scripts.strategies.v39f_optimized import select_stocks_v39f

        if factors is None or "mom_5" not in factors:
            from scripts.strategies.v39c_pv_resonance import calc_factors
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39f"])
        if params:
            merged_params.update(params)
        return select_stocks_v39f(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v39g_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39g 选股"""
        from scripts.strategies.v39g_optimized import select_stocks_v39g
        if factors is None or "mom_5" not in factors:
            from scripts.strategies.v39c_pv_resonance import calc_factors
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)
        merged_params = dict(self._risk_params["v39g"])
        if params:
            merged_params.update(params)
        return select_stocks_v39g(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v39h_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39h 选股 — 动态 MOM_THRESHOLD（熊市自适应减仓）"""
        from scripts.strategies.v39h_optimized import select_stocks_v39h

        if factors is None or "mom_5" not in factors:
            from scripts.strategies.v39c_pv_resonance import calc_factors
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39h"])
        if params:
            merged_params.update(params)
        return select_stocks_v39h(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v39i_select(self, factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel, current_holdings, params,
                     sold_recently=None):
        """v39i 选股 — 最优动态阈值（BULL=0.05/BEAR=0.08）"""
        from scripts.strategies.v39i_optimized import select_stocks_v39i

        if factors is None or "mom_5" not in factors:
            from scripts.strategies.v39c_pv_resonance import calc_factors
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v39i"])
        if params:
            merged_params.update(params)
        return select_stocks_v39i(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v41_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v41 选股 — VWAP 偏离 + 净支撑量因子"""
        from scripts.strategies.v41_vwap_deviation import calc_factors_v41, select_stocks_v41

        if factors is None or "mom_5" not in factors:
            factors = calc_factors_v41(close_panel, volume_panel, amount_panel,
                                        high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v41"])
        if params:
            merged_params.update(params)
        return select_stocks_v41(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v40_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v40 选股 — 因子恶化卖出 + 延迟止盈止损"""
        from scripts.strategies.v40_factor_exit import calc_factors, select_stocks_v40

        if factors is None or "mom_5" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v40"])
        if params:
            merged_params.update(params)
        return select_stocks_v40(factors, date, current_holdings, merged_params,
                                   sold_recently=sold_recently)

    def _v35_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v35 选股 — 委托给 v35_sector_rotation.py"""
        from scripts.strategies.v35_sector_rotation import (
            calc_factors, select_stocks_v35
        )

        if factors is None or "sector_momentum" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v35"])
        if params:
            merged_params.update(params)

        # 环境变量覆盖（用于参数扫描）
        import os
        for key in ['SECTOR_MOM_WEIGHT', 'SECTOR_W_SHORT', 'SECTOR_W_MID', 'SECTOR_W_LONG']:
            if key in os.environ:
                merged_params[key] = float(os.environ[key])

        return select_stocks_v35(factors, date, current_holdings, merged_params)

    def _v33_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v33 选股 — 委托给 v33_residual_momentum.py"""
        from scripts.strategies.v33_residual_momentum import (
            calc_factors, select_stocks_v33
        )

        if factors is None or "resid_mom" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v33"])
        if params:
            merged_params.update(params)
        return select_stocks_v33(factors, date, current_holdings, merged_params)

    def _v32_select(self, factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, open_panel, current_holdings, params,
                    sold_recently=None):
        """v32 选股 — 委托给 v32_analyst_expectation.py"""
        from scripts.strategies.v32_analyst_expectation import (
            calc_factors, select_stocks_v32
        )

        # 如果 factors 是 None 或缺少 v32 特有因子，重新计算
        if factors is None or "analyst_composite" not in factors:
            factors = calc_factors(close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel, params)

        merged_params = dict(self._risk_params["v32"])
        if params:
            merged_params.update(params)
        return select_stocks_v32(factors, date, current_holdings, merged_params)

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
            # T+1：当天买入的股票不检查（hold_days < 1）
            if h.get('hold_days', 0) < 1:
                continue
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

        支持两种模式（通过 REGIME_MODE 参数切换）:
          "3class"  — 三档：牛市/震荡/熊市（原始逻辑 + 斜率阈值）
          "linear"  — 连续映射：slope 线性映射到 [bear_alloc, bull_alloc]
          "vol"     — 波动率过滤：极端波动期强制降仓

        返回:
            (regime_label, multiplier) — 如 ("牛市", 1.0)
        """
        merged = dict(self._regime_params.get(strategy_name, {}))
        if params:
            merged.update(params)

        if not merged.get("REGIME_ENABLED", False):
            return ("未启用", 1.0)

        from core.db import get_index_kline
        INDEX_CODE = merged.get("REGIME_INDEX", "sh000001")
        kl = get_index_kline(INDEX_CODE)
        if not kl:
            return ("指数数据缺失", 1.0)

        idx_df = pd.DataFrame([dict(r) for r in kl])
        idx_df["date"] = pd.to_datetime(idx_df["date"])
        idx_df = idx_df.set_index("date").sort_index()
        idx_df = idx_df[idx_df["volume"] > 0]

        if date not in idx_df.index:
            return ("指数日期缺失", 1.0)

        ma_period = merged.get("REGIME_MA_PERIOD", 20)
        slope_days = merged.get("REGIME_SLOPE_DAYS", 5)
        slope_threshold = merged.get("SLOPE_THRESHOLD", 0.0)

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

        regime_mode = merged.get("REGIME_MODE", "3class")

        # ── 方案D：波动率过滤 ──
        if regime_mode == "vol" or merged.get("REGIME_VOL_FILTER", False):
            vol_window = merged.get("REGIME_VOL_WINDOW", 20)
            vol_threshold = merged.get("REGIME_VOL_THRESHOLD", 1.5)
            if pos >= vol_window * 2:
                vol_recent = close_series.iloc[pos - vol_window + 1:pos + 1].pct_change().std()
                vol_hist = close_series.iloc[pos - vol_window * 2 + 1:pos + 1].pct_change().std()
                if vol_hist > 0 and vol_recent / vol_hist > vol_threshold:
                    return ("高波动", bear_alloc)

        # ── 方案B：连续映射 ──
        if regime_mode == "linear":
            slope_cap = merged.get("REGIME_SLOPE_CAP", 0.01)
            # 将 slope 线性映射到 [bear_alloc, bull_alloc]
            # slope = -slope_cap → bear_alloc, slope = +slope_cap → bull_alloc
            normalized = slope / slope_cap  # [-1, 1] 范围
            normalized = max(-1.0, min(1.0, normalized))
            mult = bull_alloc + (bear_alloc - bull_alloc) * (1 - normalized) / 2
            if normalized > 0.5:
                label = "强牛市"
            elif normalized > 0:
                label = "弱牛市"
            elif normalized > -0.5:
                label = "弱熊市"
            else:
                label = "强熊市"
            return (label, mult)

        # ── 方案C：多指数（通过 REGIME_INDEX 切换，逻辑不变） ──
        # 斜率阈值过滤：|slope| < threshold 视为无趋势，归为震荡
        if slope > slope_threshold and price_now > ma60:
            return ("牛市", bull_alloc)
        elif slope < -slope_threshold and price_now < ma60:
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

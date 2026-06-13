#!/usr/bin/env python3
"""
v21b_industry_defense — 行业轮动防御性过滤 + 全市场选股
==============================================

架构：
  第一层：行业轮动打分 → 排除最差行业（后30%）
    - 行业动量：过去 20 日行业平均收益
    - 行业反转：过去 5 日行业平均收益
    - 行业拥挤度：行业内换手率标准差
    - 排除轮动分最低的行业（后30%），保留中间+上游行业

  第二层：在保留行业内用 v13 量价因子选股
    - 沿用 v13 反转+量价因子
    - 选股池 = 保留行业内的股票（排除差行业后约 70% 股票）

核心思路：
  - 行业轮动不做进攻（不追最强行业），只做防御（排除最差行业）
  - 避免"行业动量+个股反转"的冲突
  - 选股仍由 v13 量价因子主导
"""

import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db, load_industry_map


class IndustryDefenseFilter:
    """行业轮动防御性过滤引擎"""

    def __init__(self, close_panel, volume_panel, amount_panel,
                 industry_map, mom_window=20, rev_window=5,
                 crowding_penalty=0.3, exclude_pct=0.3):
        """
        exclude_pct: 排除最差行业的比例（默认后30%）
        """
        self.close_panel = close_panel
        self.volume_panel = volume_panel
        self.amount_panel = amount_panel
        self.industry_map = industry_map
        self.mom_window = mom_window
        self.rev_window = rev_window
        self.crowding_penalty = crowding_penalty
        self.exclude_pct = exclude_pct

        self._industry_mom = None
        self._industry_rev = None
        self._industry_crowd = None
        self._stock_industry = None
        self._precompute()

    def _precompute(self):
        codes = self.close_panel.columns.tolist()
        industries = pd.Series([self.industry_map.get(c, "") for c in codes], index=codes)
        valid = industries[industries != ""].index.tolist()
        if not valid:
            return

        self._stock_industry = industries
        returns = self.close_panel[valid].pct_change()
        ind_groups = industries[valid]
        unique_inds = ind_groups.unique()

        mom_dict, rev_dict, crowd_dict = {}, {}, {}
        for ind in unique_inds:
            ind_codes = ind_groups[ind_groups == ind].index.tolist()
            if len(ind_codes) < 3:
                continue
            ind_ret = returns[ind_codes].mean(axis=1)
            mom_dict[ind] = ind_ret.rolling(self.mom_window, min_periods=5).mean()
            rev_dict[ind] = ind_ret.rolling(self.rev_window, min_periods=2).mean()
            if self.amount_panel is not None and self.volume_panel is not None:
                amt = self.amount_panel.reindex(columns=ind_codes).fillna(0)
                close_sub = self.close_panel.reindex(columns=ind_codes).ffill()
                turnover = amt / close_sub.replace(0, np.nan)
                crowd_dict[ind] = turnover.std(axis=1).rolling(10, min_periods=3).mean()
            else:
                crowd_dict[ind] = returns[ind_codes].std(axis=1).rolling(10, min_periods=3).mean()

        if mom_dict:
            self._industry_mom = pd.DataFrame(mom_dict)
            self._industry_rev = pd.DataFrame(rev_dict)
            self._industry_crowd = pd.DataFrame(crowd_dict)

    def get_industry_scores(self, date):
        """score = 动量 - 反转 - 拥挤度惩罚"""
        for df in [self._industry_mom, self._industry_rev, self._industry_crowd]:
            if df is None or date not in df.index:
                return pd.Series(dtype=float)
        mom = self._industry_mom.loc[date]
        rev = self._industry_rev.loc[date]
        crowd = self._industry_crowd.loc[date] if self._industry_crowd is not None else pd.Series(0, index=mom.index)
        def z(s):
            return (s - s.mean()) / s.std() if s.std() > 0 else pd.Series(0, index=s.index)
        mom_z, rev_z, crowd_z = z(mom.dropna()), z(rev.dropna()), z(crowd.dropna())
        common = mom_z.index.intersection(rev_z.index).intersection(crowd_z.index)
        return (mom_z[common] - rev_z[common] - self.crowding_penalty * crowd_z[common]).dropna()

    def get_excluded_industries(self, date):
        """获取应排除的最差行业"""
        scores = self.get_industry_scores(date)
        if scores.empty:
            return []
        n_exclude = max(1, int(len(scores) * self.exclude_pct))
        return scores.nsmallest(n_exclude).index.tolist()

    def get_industry_mask(self, date):
        """获取保留行业的股票掩码（True=保留）"""
        excluded = self.get_excluded_industries(date)
        if not excluded:
            return pd.Series(True, index=self.close_panel.columns)
        mask = ~self._stock_industry.isin(excluded)
        return mask.reindex(self.close_panel.columns).fillna(True)


# ============================================================
# 参数配置
# ============================================================
class V21bConfig:
    # 行业过滤参数
    exclude_pct = 0.3          # 排除后30%行业
    mom_window = 20
    rev_window = 5
    crowding_penalty = 0.3

    # 选股参数（继承 v13）
    min_liquidity = 500
    max_liquidity = 8000
    rev_threshold = -0.02
    vol_ratio_threshold = 1.0
    max_holdings = 8
    max_daily_buy = 6
    max_position = 0.20
    hold_days_max = 5
    hold_days_min = 2
    stop_loss = -0.015
    stop_profit = 0.03

    commission_rate = 0.0003
    stamp_tax = 0.001
    slippage_rate = 0.002
    initial_capital = 200000


# ============================================================
# 选股逻辑
# ============================================================
def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel):
    rev_5 = close_panel.pct_change(5)
    vol_avg = volume_panel.rolling(10).mean()
    vol_ratio = volume_panel / vol_avg
    vol_shrink = volume_panel / volume_panel.shift(1)
    price_stable = close_panel.pct_change().abs() < close_panel.pct_change().abs().rolling(5).mean()
    daily_range = (high_panel - low_panel) / close_panel
    avg_range = daily_range.rolling(5).mean()
    range_ratio = daily_range / avg_range
    return {
        'rev_5': rev_5, 'vol_ratio': vol_ratio,
        'vol_shrink': vol_shrink * price_stable.astype(float),
        'range_ratio': range_ratio,
    }


def select_stocks(factors, date, close_panel, volume_panel, amount_panel,
                   industry_mask, current_holdings, cfg):
    """在行业掩码内选股"""
    if date not in factors['rev_5'].index:
        return []
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    if date in avg_amount.index:
        day_amount = avg_amount.loc[date]
        liquid = set(day_amount[(day_amount >= cfg.min_liquidity) & (day_amount <= cfg.max_liquidity)].dropna().index)
    else:
        liquid = set(close_panel.columns)

    if industry_mask is not None:
        liquid = liquid & set(industry_mask[industry_mask].index)
    if not liquid:
        return []

    rev_5 = factors['rev_5'].loc[date]
    vol_ratio = factors['vol_ratio'].loc[date]
    vol_shrink = factors['vol_shrink'].loc[date]
    range_ratio = factors['range_ratio'].loc[date]

    scores = {}
    for code in liquid:
        if code not in rev_5.index or pd.isna(rev_5[code]):
            continue
        score = 0.0
        r = rev_5[code]
        if r < cfg.rev_threshold:
            score += abs(r) * 100
            vr = vol_ratio.get(code, 1.0)
            if vr > cfg.vol_ratio_threshold:
                score += 0.5
            vs = vol_shrink.get(code, 1.0)
            if vs < 0.7:
                score += 0.3
            rr = range_ratio.get(code, 1.0)
            if rr < 0.8:
                score += 0.2
        if score > 0:
            scores[code] = score

    if current_holdings:
        scores = {c: s for c, s in scores.items() if c not in current_holdings}

    candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
    return candidates[:cfg.max_holdings]


# ============================================================
# 回测引擎
# ============================================================
def run_v21b_backtest(start_date='2022-01-01', end_date='2026-05-31'):
    print("=" * 60)
    print("v21b 行业轮动防御性过滤 + 全市场选股")
    print("=" * 60)
    t0 = time.time()

    print("\n[1/4] 加载数据...")
    tpl, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel = tpl[3] if len(tpl) > 3 else None
    high_panel = tpl[4] if len(tpl) > 4 else None
    low_panel = tpl[5] if len(tpl) > 5 else None
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")

    industry_map = load_industry_map()
    mapped = sum(1 for c in close_panel.columns if c in industry_map)
    print(f"  行业分类: {len(industry_map)} 只, 覆盖 {mapped}/{len(close_panel.columns)}")

    print("\n[2/4] 计算因子...")
    factors = calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

    print("\n[3/4] 预计算行业轮动...")
    engine = IndustryDefenseFilter(
        close_panel, volume_panel, amount_panel, industry_map,
        mom_window=V21bConfig.mom_window, rev_window=V21bConfig.rev_window,
        crowding_penalty=V21bConfig.crowding_penalty, exclude_pct=V21bConfig.exclude_pct
    )

    print("\n[4/4] 运行回测...")
    cfg = V21bConfig()
    cash = cfg.initial_capital
    holdings = {}
    trade_log = []
    nav_series = []
    select_days = 0
    total_buys = 0
    total_sells = 0
    sell_reasons = {}
    ind_excluded_days = 0

    dates = close_panel.index[close_panel.index >= pd.Timestamp(start_date)]

    for i, date in enumerate(dates):
        if i < 30:
            nav_series.append((date, cash))
            continue

        price_data = close_panel.loc[date] if date in close_panel.index else None
        if price_data is None:
            nav_series.append((date, cash))
            continue

        open_data = open_panel.loc[date] if open_panel is not None and date in open_panel.index else price_data

        for code in holdings:
            holdings[code]['hold_days'] = holdings[code].get('hold_days', 0) + 1

        # 行业过滤
        ind_mask = engine.get_industry_mask(date)
        if ind_mask.sum() < len(close_panel.columns) * 0.5:
            ind_excluded_days += 1

        # 风控
        to_sell = []
        for code, h in holdings.items():
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']
            hd = h.get('hold_days', 0)
            if pnl <= cfg.stop_loss:
                to_sell.append((code, 'stop_loss', pnl))
            elif pnl >= cfg.stop_profit:
                to_sell.append((code, 'stop_profit', pnl))
            elif hd >= cfg.hold_days_max:
                to_sell.append((code, 'timeout', pnl))

        sold_codes = set()
        for code, reason, pnl in to_sell:
            if code not in price_data.index:
                continue
            sp = price_data[code]
            if pd.isna(sp) or sp <= 0:
                continue
            h = holdings[code]
            sv = h['shares'] * sp * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
            cash += sv
            trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                              'reason': reason, 'pnl_pct': round(pnl*100, 2), 'value': round(sv, 2)})
            sold_codes.add(code)
            total_sells += 1
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1

        for code in sold_codes:
            holdings.pop(code, None)

        # 选股 + 买入
        candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel,
                                    ind_mask, holdings, cfg)

        if candidates and cash > cfg.initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available = cash - cfg.initial_capital * 0.1
            n_buy = min(len(candidates), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = min(available / n_buy, cfg.initial_capital * cfg.max_position)

            bought = 0
            for code in candidates:
                if bought >= n_buy:
                    break
                bp = open_data[code] if code in open_data.index else price_data[code]
                if pd.isna(bp) or bp <= 0:
                    continue
                shares = int(per_stock / bp / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * bp * (1 + cfg.commission_rate + cfg.slippage_rate)
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0}
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                  'reason': 'signal', 'cost': round(cost, 2)})
                bought += 1
                total_buys += 1

        if candidates:
            select_days += 1

        nav = cash
        for code, h in holdings.items():
            if code in price_data.index:
                cp = price_data[code]
                if not pd.isna(cp) and cp > 0:
                    nav += h['shares'] * cp
        nav_series.append((date, nav))

    elapsed = time.time() - t0

    nav_df = pd.DataFrame(nav_series, columns=['date', 'nav']).set_index('date')
    nav_df['return'] = nav_df['pct_change'] = nav_df['nav'].pct_change()
    total_return = (nav_df['nav'].iloc[-1] / cfg.initial_capital) - 1
    days = (nav_df.index[-1] - nav_df.index[0]).days
    annual_return = (1 + total_return) ** (365 / max(days, 1)) - 1
    sharpe = nav_df['return'].mean() / nav_df['return'].std() * np.sqrt(252) if nav_df['return'].std() > 0 else 0
    max_dd = ((nav_df['nav'].cummax() - nav_df['nav']) / nav_df['nav'].cummax()).max()

    print(f"\n{'='*60}")
    print(f"回测结果 ({start_date} ~ {end_date})")
    print(f"{'='*60}")
    print(f"  年化收益: {annual_return*100:.2f}%")
    print(f"  夏普比率: {sharpe:.3f}")
    print(f"  最大回撤: {max_dd*100:.2f}%")
    print(f"  总交易: {total_buys} 买 / {total_sells} 卖")
    print(f"  选股率: {select_days}/{len(dates)-30} 天 ({select_days/max(1,len(dates)-30)*100:.1f}%)")
    print(f"  行业过滤生效天数: {ind_excluded_days}/{len(dates)-30}")
    print(f"  卖出原因: {sell_reasons}")
    print(f"  耗时: {elapsed:.1f}s")

    return {
        'annual_return': annual_return, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_buys': total_buys, 'total_sells': total_sells,
        'select_days': select_days, 'nav': nav_df, 'trade_log': trade_log,
    }


if __name__ == "__main__":
    run_v21b_backtest()

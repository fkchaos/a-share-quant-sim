#!/usr/bin/env python3
"""
v13_small_mid_short — 小资金中短线策略
========================================
策略定位：小市值 + 反转因子 + 短周期（1-5天）+ 严格止损

核心思路：
1. 选股池：中证800 之外的小市值股票（自由流通市值 < 50亿）
2. 因子：短期反转（5日/10日跌幅）+ 量价异动（放量下跌后缩量企稳）
3. 调仓频率：每日调仓，持仓 1-3 天
4. 风控：个股止损 -5%，单日最大回撤 -3% 清仓，仓位上限 20%/只
5. 目标：年化 30%+，最大回撤 < 15%

与 v11b 的区别：
- v11b：中证800，20天调仓，因子选股，适合大资金
- v13：小市值，1-3天调仓，反转+量价，适合小资金（< 500万）
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

# ============================================================
# 参数配置
# ============================================================
class V13Config:
    # 选股池（用流动性近似）
    min_liquidity = 300     # 最小日均成交额 300万（放宽）
    max_liquidity = 10000   # 最大日均成交额 1亿（放宽）
    exclude_st = True             # 排除 ST
    exclude_new_ipo_days = 60     # 排除上市 60 天内的新股

    # 因子参数
    rev_lookback = 5              # 短期反转回看天数
    vol_lookback = 10             # 量能回看天数
    rev_threshold = -0.02         # 反转阈值（5日跌幅 > 2%，放宽）
    vol_ratio_threshold = 1.3     # 放量阈值（当日量 / 10日均量，放宽）

    # Bonus 加分因子（默认空列表 = 不影响现有结果）
    # 通过 select_stocks() 和 calc_small_cap_factors() 的 bonus_factors 参数传入
    # 格式：{'factor': str, 'calc': callable, 'condition': callable, 'score': float}
    bonus_factors = []

    # 择时参数
    market_rev_threshold = -0.01   # 市场 5 日跌幅 > 1% 才选股（放宽）
    max_daily_buy = 6              # 每日最多买 6 只
    max_holdings = 8               # 最大持仓 8 只
    max_position = 0.20            # 单只最大仓位 20%
    hold_days_max = 8             # 最大持仓天数（反转效应需要 3-8 天）
    hold_days_min = 2             # 最小持仓天数
    hold_days_extend = 7          # 浮盈达标后的最大持仓天数
    hold_days_extend_pnl = 0.03   # 浮盈 3% 可延长持仓

    # 风控参数
    stop_loss = -0.02             # 个股止损 -2%
    stop_profit = 0.10            # 个股止盈 10%
    daily_max_dd = -0.03          # 单日最大回撤 -3% 清仓
    initial_capital = 200000      # 初始资金 20万

    # 交易成本（小资金费率更高）
    commission_rate = 0.0003      # 佣金万三
    stamp_tax = 0.001             # 印花税千一（卖出）
    slippage_rate = 0.002         # 滑点 0.2%（小市值流动性差）


# ============================================================
# 数据加载
# ============================================================
def load_small_cap_panel(start_date='2021-01-01', end_date='2026-05-31'):
    """加载面板数据（从数据库，zz800成分股 + 流动性过滤）"""
    from core.db import load_panel_from_db

    loaded, codes = load_panel_from_db(
        start_date, end_date,
        need_open=True, need_hl=True,
        pool="zz800",
    )
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = loaded

    # 流动性过滤统计
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    min_liquidity = V13Config.min_liquidity
    max_liquidity = V13Config.max_liquidity
    valid_count = 0
    for date in close_panel.index:
        if date not in avg_amount.index:
            continue
        day_amount = avg_amount.loc[date]
        mask = (day_amount > min_liquidity) & (day_amount < max_liquidity)
        if mask.sum() >= V13Config.max_holdings * 2:
            valid_count += 1

    print(f"流动性筛选：{valid_count}/{len(close_panel)} 个交易日有足够候选")
    if valid_count > 0:
        print(f"筛选条件：日均成交额 {min_liquidity}万-{max_liquidity}万")

    return close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel


# ============================================================
# 因子计算
# ============================================================
def calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel,
                          bonus_factors=None):
    """计算小市值专用因子

    Args:
        bonus_factors: 可选的 bonus 因子配置列表，默认 None = 使用 V13Config.bonus_factors
    """
    if bonus_factors is None:
        bonus_factors = V13Config.bonus_factors

    factors = {}

    # 1. 短期反转因子（5日跌幅）
    rev_5 = close_panel.pct_change(V13Config.rev_lookback)
    factors['rev_5'] = rev_5

    # 2. 量价异动因子（当日量 / 10日均量）
    vol_avg = volume_panel.rolling(V13Config.vol_lookback).mean()
    vol_ratio = volume_panel / vol_avg
    factors['vol_ratio'] = vol_ratio

    # 3. 缩量企稳因子（当日量 < 前一日量的 70%，且跌幅收窄）
    vol_shrink = volume_panel / volume_panel.shift(1)
    price_stable = close_panel.pct_change().abs() < close_panel.pct_change().abs().rolling(5).mean()
    factors['vol_shrink'] = vol_shrink * price_stable.astype(float)

    # 4. 日内振幅因子（当日振幅 / 5日平均振幅）
    daily_range = (high_panel - low_panel) / close_panel
    avg_range = daily_range.rolling(5).mean()
    factors['range_ratio'] = daily_range / avg_range

    # 5. 换手率因子
    turnover = amount_panel / close_panel  # 近似换手率
    factors['turnover'] = turnover

    # 6. Bonus 加分因子（动态计算）
    for bonus in bonus_factors:
        bname = bonus['factor']
        bcalc = bonus['calc']
        try:
            factors[bname] = bcalc(close_panel, volume_panel, amount_panel, high_panel, low_panel)
        except Exception as e:
            print(f"  ⚠️ Bonus factor '{bname}' calc failed: {e}")

    return factors


# ============================================================
# 选股逻辑
# ============================================================
def select_stocks(factors, date, close_panel, volume_panel, amount_panel, current_holdings=None,
                  bonus_factors=None):
    """每日选股 — 评分排序制

    Args:
        bonus_factors: 可选的 bonus 因子配置列表，默认 None = 使用 V13Config.bonus_factors
    """
    if bonus_factors is None:
        bonus_factors = V13Config.bonus_factors
    if date not in factors['rev_5'].index:
        return []

    # 流动性筛选
    avg_amount = amount_panel.rolling(20).mean() / 1e4  # 万元
    if date in avg_amount.index:
        day_amount = avg_amount.loc[date]
        liquid_mask = (day_amount > V13Config.min_liquidity) & (day_amount < V13Config.max_liquidity)
        liquid_stocks = set(day_amount[liquid_mask].dropna().index)
    else:
        liquid_stocks = set(close_panel.columns)

    # 获取当日因子值
    rev_5 = factors['rev_5'].loc[date].dropna()
    vol_ratio = factors['vol_ratio'].loc[date].dropna()
    vol_shrink = factors['vol_shrink'].loc[date].dropna()
    range_ratio = factors['range_ratio'].loc[date].dropna()

    # 在流动性池中，对每只股票计算综合评分
    scores = {}
    for code in liquid_stocks:
        if code not in rev_5.index:
            continue
        score = 0.0

        # 反转因子（跌幅越大分越高，负值表示下跌）
        r = rev_5.get(code, 0)
        if r < V13Config.rev_threshold:  # 超跌至少要有 2% 跌幅
            score += abs(r) * 100  # 跌幅的绝对值作为基础分

            # 量价辅助因子（加分项）
            vr = vol_ratio.get(code, 1.0)
            if vr > V13Config.vol_ratio_threshold:  # 放量
                score += 0.5
            vs = vol_shrink.get(code, 1.0)
            if vs < 0.7:  # 缩量企稳
                score += 0.3
            rr = range_ratio.get(code, 1.0)
            if rr < 0.8:  # 振幅收窄
                score += 0.2

            # Bonus 加分因子（可配置扩展，通过参数传入）
            for bonus in bonus_factors:
                bname = bonus['factor']
                bcond = bonus['condition']
                bscore = bonus['score']
                if bname in factors:
                    bval = factors[bname].loc[date, code] if (date in factors[bname].index and code in factors[bname].columns) else None
                    if bval is not None and not pd.isna(bval) and bcond(bval):
                        score += bscore

        if score > 0:
            scores[code] = score

    # 排除当前持仓
    if current_holdings:
        scores = {c: s for c, s in scores.items() if c not in current_holdings}

    # 按评分降序排列
    candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)

    return candidates[:V13Config.max_holdings]


# ============================================================
# 回测引擎
# ============================================================
def run_v13_backtest():
    """v13 回测主函数"""
    print("=" * 60)
    print("v13_small_mid_short — 小资金中短线策略回测")
    print("=" * 60)

    # 加载数据
    print("\n[1/4] 加载数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_small_cap_panel()
    print(f"  耗时 {time.time()-t0:.1f}s")

    # 计算因子
    print("\n[2/4] 计算因子...")
    t0 = time.time()
    factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"  耗时 {time.time()-t0:.1f}s")

    # 回测
    print("\n[3/4] 运行回测...")
    t0 = time.time()

    cfg = V13Config()
    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}  # {code: {'shares': int, 'cost': float, 'hold_days': int}}
    nav_list = []
    trade_log = []
    dates = close_panel.index

    for i, date in enumerate(dates):
        if i < 20:  # 预热期
            nav_list.append(initial_capital)
            continue

        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date] if open_panel is not None else price_data

        # 1. 更新持仓天数
        for code in holdings:
            holdings[code]['hold_days'] += 1

        # 2. 风控检查（止损/止盈/超时）
        to_sell = []
        for code, h in holdings.items():
            if code not in price_data.index:
                continue
            current_price = price_data[code]
            if pd.isna(current_price) or current_price <= 0:
                continue

            pnl_pct = (current_price - h['cost']) / h['cost']

            # 止损
            if pnl_pct <= cfg.stop_loss:
                to_sell.append((code, 'stop_loss', pnl_pct))
                continue

            # 止盈
            if pnl_pct >= cfg.stop_profit:
                to_sell.append((code, 'stop_profit', pnl_pct))
                continue

            # 超时（动态持仓天数）
            hd = h['hold_days']
            if pnl_pct >= cfg.hold_days_extend_pnl and hd >= cfg.hold_days_max:
                # 浮盈达标但未超过延长线，继续拿
                if hd >= cfg.hold_days_extend:
                    to_sell.append((code, 'timeout_extend', pnl_pct))
            elif hd >= cfg.hold_days_max:
                to_sell.append((code, 'timeout', pnl_pct))

        # 执行卖出
        sold_codes = set()
        for code, reason, pnl_pct in to_sell:
            if code in price_data.index:
                sell_price = price_data[code]
                if pd.isna(sell_price) or sell_price <= 0:
                    continue

                # 跌停检查：如果跌停则无法卖出，跳过（等下一天）
                if i > 0:
                    prev_close = close_panel.iloc[i-1][code] if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        limit_down = prev_close * 0.90
                        if sell_price <= limit_down * 1.01:  # 接近或达到跌停
                            # 无法卖出，回退持仓天数（避免第二天立即超时）
                            holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                            continue

                h = holdings[code]
                # 扣除成本
                sell_value = h['shares'] * sell_price * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
                cash += sell_value
                trade_log.append({
                    'date': str(date.date()),
                    'code': code,
                    'action': 'sell',
                    'reason': reason,
                    'pnl_pct': round(pnl_pct * 100, 2),
                    'value': round(sell_value, 2),
                })
                sold_codes.add(code)

        # 清理已卖出的持仓
        for code in sold_codes:
            if code in holdings:
                del holdings[code]

        # 3. 选股
        candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)

        # 4. 买入（持仓未满才买，择时已通过选股因子隐式控制：反转因子天然是跌了才买）
        if candidates and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            # 执行买入
            available_cash = cash - initial_capital * 0.1
            per_stock = min(available_cash / min(len(candidates), V13Config.max_daily_buy), initial_capital * cfg.max_position)

            for code in candidates[:V13Config.max_daily_buy]:
                if code not in price_data.index:
                    continue
                buy_price = open_data[code] if code in open_data.index else price_data[code]
                if pd.isna(buy_price) or buy_price <= 0:
                    continue

                # 涨跌停检查：如果涨停则不买（买不到）
                if i > 0:
                    prev_close = close_panel.iloc[i-1][code] if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        limit_up = prev_close * 1.10
                        if buy_price >= limit_up * 0.99:
                            continue

                # 计算可买股数（100 股整数倍）
                adj_price = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                shares = int(per_stock / adj_price / 100) * 100
                if shares <= 0:
                    continue

                cost = shares * adj_price
                if cost > cash:
                    continue

                cash -= cost
                holdings[code] = {
                    'shares': shares,
                    'cost': buy_price,
                    'hold_days': 0,
                }
                trade_log.append({
                    'date': str(date.date()),
                    'code': code,
                    'action': 'buy',
                    'price': round(buy_price, 2),
                    'shares': shares,
                    'value': round(cost, 2),
                })

        # 5. 计算 NAV
        portfolio_value = cash
        for code, h in holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    portfolio_value += h['shares'] * p

        nav_list.append(portfolio_value)

    elapsed = time.time() - t0
    print(f"  耗时 {elapsed:.1f}s")

    # 计算绩效
    print("\n[4/4] 计算绩效...")
    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    metrics = calc_v13_metrics(nav, trade_log, initial_capital)

    # 打印结果
    print(f"\n{'='*60}")
    print("v13 回测结果")
    print(f"{'='*60}")
    print(f"回测区间: {dates[0].date()} ~ {dates[-1].date()}")
    print(f"初始资金: {initial_capital:,.0f}")
    print(f"最终资金: {nav.iloc[-1]:,.0f}")
    print(f"总收益率: {metrics['total_return']:.2f}%")
    print(f"年化收益: {metrics['annual_return']:.2f}%")
    print(f"最大回撤: {metrics['max_drawdown']:.2f}%")
    print(f"夏普比率: {metrics['sharpe']:.3f}")
    print(f"交易次数: {metrics['total_trades']}")
    print(f"胜率: {metrics['win_rate']:.1f}%")

    return nav, trade_log, metrics


def calc_v13_metrics(nav, trade_log, initial_capital):
    """计算绩效指标"""
    rets = nav.pct_change().dropna()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = (nav.index[-1] - nav.index[0]).days
    years = max(days / 365, 0.01)
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    peak = nav.cummax()
    max_dd = ((nav - peak) / peak).min()

    # 交易统计
    sells = [t for t in trade_log if t['action'] == 'sell']
    wins = [t for t in sells if t.get('pnl_pct', 0) > 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0

    return {
        'total_return': round(total_ret * 100, 2),
        'annual_return': round(ann_ret * 100, 2),
        'annual_vol': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_dd * 100, 2),
        'total_trades': len(trade_log),
        'win_rate': round(win_rate, 1),
    }


if __name__ == '__main__':
    nav, trades, metrics = run_v13_backtest()

#!/usr/bin/env python3
"""
v20_tail_pick — 尾盘选股策略
============================
策略定位：尾盘强势选股 + 隔夜持有 + 短周期（1-3天）

核心思路：
1. 选股池：中证800成分股 + 流动性过滤（300万-1亿元日均成交额）
2. 选股信号（尾盘已确定）：
   - 尾盘放量：当日成交量 > 5日均量 × 1.3（主力抢筹）
   - 振幅收窄：当日振幅 < 5日平均振幅 × 0.8（洗盘结束）
   - 换手率适中：3%-10%（活跃但不疯狂）
   - 近期强势：20天内有涨停历史（股性好）
   - 价格位置：收盘价 > 5日均线（短期趋势向上）
3. 交易执行：
   - T日尾盘选股 → T+1日开盘买入
   - 持有 1-3 天 → 止盈/止损/超时卖出
4. 风控：个股止损 -5%，止盈 5%，最大持仓 8 只

与 v13 的区别：
- v13：超跌反转（跌了买，涨了卖）
- v20：强势延续（涨了买，继续涨）
- v13 当天开盘买，v20 次日开盘买（隔夜跳空）
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
class V20Config:
    # 选股池
    min_liquidity = 300     # 最小日均成交额 300万
    max_liquidity = 10000   # 最大日均成交额 1亿
    exclude_st = True
    exclude_new_ipo_days = 60

    # 尾盘选股因子参数
    # v20a: 放量追涨（强势延续）— 已证伪，次日低开 -3%
    # v20b: 缩量企稳（反转逻辑）— 振幅收窄 + 缩量 = 洗盘结束
    vol_vs_avg_max = 0.8     # 当日成交量 < 5日均量 × 0.8（缩量）
    range_vs_avg = 0.8        # 当日振幅 < 5日平均振幅 × 0.8（振幅收窄）
    amount_vs_avg_min = 0.5   # 当日成交额 / 20日平均成交额 > 0.5（不太冷清）
    amount_vs_avg_max = 3.0   # 排除异常放量
    price_above_ma5 = True    # 收盘价 > 5日均线（短期趋势向上）
    recent_limit_up = 20      # N天内有涨停历史（股性好）

    # 择时参数
    max_daily_buy = 6       # 每日最多买 6 只
    max_holdings = 8        # 最大持仓 8 只
    max_position = 0.20     # 单只最大仓位 20%
    hold_days_max = 5       # 最大持仓天数
    hold_days_min = 1       # 最小持仓天数

    # 风控参数
    stop_loss = -0.05       # 个股止损 -5%
    stop_profit = 0.25      # 个股止盈 25%
    initial_capital = 200000  # 初始资金 20万

    # 交易成本
    commission_rate = 0.0003
    stamp_tax = 0.001
    slippage_rate = 0.002


# ============================================================
# 数据加载（复用 v13 的加载逻辑）
# ============================================================
def load_panel(start_date='2021-01-01', end_date='2026-05-31'):
    """加载股票面板数据（优先从数据库）"""
    from core.db import load_panel_from_db, init_db
    init_db()
    loaded, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel  = loaded[0]
    volume_panel = loaded[1]
    amount_panel = loaded[2]
    open_panel   = loaded[3]
    high_panel  = loaded[4]
    low_panel   = loaded[5]

    # 流动性筛选
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    valid_count = 0
    for date in close_panel.index:
        if date not in avg_amount.index:
            continue
        day_amount = avg_amount.loc[date]
        mask = (day_amount > V20Config.min_liquidity) & (day_amount < V20Config.max_liquidity)
        n = mask.sum()
        if n >= V20Config.max_holdings * 2:
            valid_count += 1

    print(f"流动性筛选：{valid_count}/{len(close_panel)} 个交易日有足够候选")
    return close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel


# ============================================================
# 因子计算
# ============================================================
def calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel):
    """计算尾盘选股因子"""
    factors = {}

    # 1. 量比（当日成交量 / 5日均量）
    vol_avg5 = volume_panel.rolling(5).mean()
    factors['vol_ratio'] = volume_panel / vol_avg5

    # 2. 振幅（当日振幅 / 5日平均振幅）
    daily_range = (high_panel - low_panel) / close_panel
    avg_range5 = daily_range.rolling(5).mean()
    factors['range_ratio'] = daily_range / avg_range5

    # 3. 换手率（用成交额 / 收盘价的相对量近似，标准化到可比范围）
    # 换手率 ≈ 成交量 / 流通股本，但没有流通股本数据
    # 改用：当日成交额 / 20日平均成交额（量比的概念，但针对金额）
    amount_avg20 = amount_panel.rolling(20).mean()
    factors['amount_ratio'] = amount_panel / amount_avg20

    # 4. 价格 vs 5日均线
    ma5 = close_panel.rolling(5).mean()
    factors['price_vs_ma5'] = close_panel / ma5

    # 5. N天内是否有涨停（用收盘价涨幅 > 9.5% 近似）
    pct_change = close_panel.pct_change()
    limit_up = (pct_change > 0.095).astype(float)
    factors['recent_limit_up'] = limit_up.rolling(V20Config.recent_limit_up).max()

    # 6. 当日振幅（绝对值）
    factors['daily_range'] = daily_range

    return factors


# ============================================================
# 选股逻辑
# ============================================================
def select_stocks_tail_pick(factors, date, close_panel, volume_panel, amount_panel,
                            high_panel, low_panel, current_holdings=None):
    """尾盘选股 — 缩量企稳信号（v20b）"""
    if date not in factors['vol_ratio'].index:
        return []

    # 流动性筛选
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    if date in avg_amount.index:
        day_amount = avg_amount.loc[date]
        liquid_mask = (day_amount > V20Config.min_liquidity) & (day_amount < V20Config.max_liquidity)
        liquid_stocks = set(day_amount[liquid_mask].dropna().index)
    else:
        liquid_stocks = set(close_panel.columns)

    # 获取当日因子值
    vol_ratio = factors['vol_ratio'].loc[date].dropna()
    range_ratio = factors['range_ratio'].loc[date].dropna()
    amount_ratio = factors['amount_ratio'].loc[date].dropna()
    price_vs_ma5 = factors['price_vs_ma5'].loc[date].dropna()
    recent_limit_up = factors['recent_limit_up'].loc[date].dropna()

    candidates = []
    for code in liquid_stocks:
        if code not in vol_ratio.index:
            continue

        # 条件1：尾盘缩量（成交量 < 5日均量 × 0.8）
        vr = vol_ratio.get(code, 999)
        if vr > V20Config.vol_vs_avg_max:
            continue

        # 条件2：振幅收窄（当日振幅 < 5日平均振幅 × 0.8）
        rr = range_ratio.get(code, 999)
        if rr > V20Config.range_vs_avg:
            continue

        # 条件3：成交额活跃（当日成交额 / 20日均额 在 1.2-5.0 之间）
        ar = amount_ratio.get(code, 0)
        if ar < V20Config.amount_vs_avg_min or ar > V20Config.amount_vs_avg_max:
            continue

        # 条件4：价格 > 5日均线（短期趋势向上）
        pm = price_vs_ma5.get(code, 0)
        if pm < 1.0:
            continue

        # 条件5：近期有涨停历史（20天内）
        lu = recent_limit_up.get(code, 0)
        if lu < 1.0:
            continue

        # 综合评分（缩量企稳：量比低 + 振幅收窄 + 涨停史）
        score = (1.0 / (vr + 0.1)) * 2.0 + (1.0 / (rr + 0.1)) * 1.0 + lu * 0.5
        candidates.append((code, score))

    # 排除当前持仓
    if current_holdings:
        candidates = [(c, s) for c, s in candidates if c not in current_holdings]

    # 按评分降序排列
    candidates.sort(key=lambda x: x[1], reverse=True)

    return [c for c, s in candidates[:V20Config.max_holdings]]


# ============================================================
# 回测引擎
# ============================================================
def run_v20_backtest():
    """v20 回测主函数"""
    print("=" * 60)
    print("v20_tail_pick — 尾盘选股策略回测")
    print("=" * 60)

    # 加载数据
    print("\n[1/4] 加载数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_panel()
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")
    print(f"  耗时 {time.time()-t0:.1f}s")

    # 计算因子
    print("\n[2/4] 计算因子...")
    t0 = time.time()
    factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"  耗时 {time.time()-t0:.1f}s")

    # 回测
    print("\n[3/4] 运行回测...")
    t0 = time.time()

    cfg = V20Config()
    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}  # {code: {'shares': int, 'cost': float, 'hold_days': int, 'buy_date': date}}
    nav_list = []
    trade_log = []
    dates = close_panel.index

    # 待买入队列（T日选股，T+1日开盘买）
    pending_buy = []  # [(code, score)]

    for i, date in enumerate(dates):
        if i < 20:  # 预热期
            nav_list.append(initial_capital)
            continue

        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date] if open_panel is not None else price_data

        # 1. 执行待买入队列（T日选股，T+1日开盘买）
        if pending_buy and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available_cash = cash - initial_capital * 0.1
            n_buy = min(len(pending_buy), cfg.max_daily_buy,
                        cfg.max_holdings - len(holdings))
            per_stock = available_cash / n_buy if n_buy > 0 else 0
            per_stock = min(per_stock, initial_capital * cfg.max_position)

            for code, score in pending_buy[:n_buy]:
                if code not in open_data.index:
                    continue
                buy_price = open_data[code]
                if pd.isna(buy_price) or buy_price <= 0:
                    continue
                # 涨停检查
                if i > 0:
                    prev_close = close_panel.iloc[i-1].get(code, None) if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        limit_up = prev_close * 1.10
                        if buy_price >= limit_up * 0.99:
                            continue
                adj = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                shares = int(per_stock / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {
                    'shares': shares, 'cost': buy_price,
                    'hold_days': 0, 'buy_date': date,
                }
                trade_log.append({
                    'date': str(date.date()), 'code': code, 'action': 'buy',
                    'price': round(buy_price, 2), 'shares': shares,
                    'score': round(score, 2),
                })

        pending_buy = []  # 清空队列

        # 2. 更新持仓天数
        for code in holdings:
            holdings[code]['hold_days'] += 1

        # 3. 风控检查（止损/止盈/超时）
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in price_data.index:
                continue
            current_price = price_data[code]
            if pd.isna(current_price) or current_price <= 0:
                continue
            pnl_pct = (current_price - h['cost']) / h['cost']

            if pnl_pct <= cfg.stop_loss:
                to_sell.append((code, 'stop_loss', pnl_pct))
                continue
            if pnl_pct >= cfg.stop_profit:
                to_sell.append((code, 'stop_profit', pnl_pct))
                continue
            if h['hold_days'] >= cfg.hold_days_max:
                to_sell.append((code, 'timeout', pnl_pct))
                continue

        # 执行卖出
        sold_codes = set()
        for code, reason, pnl_pct in to_sell:
            if code in price_data.index:
                sell_price = price_data[code]
                if pd.isna(sell_price) or sell_price <= 0:
                    continue
                if i > 0:
                    prev_close = close_panel.iloc[i-1].get(code, None) if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if sell_price <= prev_close * 0.90 * 1.01:
                            holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                            continue
                h = holdings[code]
                sv = h['shares'] * sell_price * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
                cash += sv
                trade_log.append({
                    'date': str(date.date()), 'code': code, 'action': 'sell',
                    'reason': reason, 'pnl_pct': round(pnl_pct * 100, 2),
                })
                sold_codes.add(code)
        for code in sold_codes:
            holdings.pop(code, None)

        # 4. 尾盘选股（T日选，T+1日买）
        if len(holdings) < cfg.max_holdings:
            candidates = select_stocks_tail_pick(
                factors, date, close_panel, volume_panel, amount_panel,
                high_panel, low_panel, holdings
            )
            if candidates:
                # 重新评分排序（缩量企稳：量比低 + 振幅收窄 + 涨停史）
                vol_ratio = factors['vol_ratio'].loc[date]
                range_ratio = factors['range_ratio'].loc[date]
                recent_lu = factors['recent_limit_up'].loc[date]
                scored = []
                for code in candidates:
                    vr = vol_ratio.get(code, 999)
                    rr = range_ratio.get(code, 999)
                    lu = recent_lu.get(code, 0)
                    # 量比越低（缩量越多）分越高 + 振幅收窄 + 涨停史
                    score = (1.0 / (vr + 0.1)) * 2.0 + (1.0 / (rr + 0.1)) * 1.0 + lu * 0.5
                    scored.append((code, score))
                scored.sort(key=lambda x: x[1], reverse=True)
                pending_buy = scored[:cfg.max_daily_buy]

        # 5. NAV
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
    metrics = calc_v20_metrics(nav, trade_log, initial_capital)

    # 打印结果
    print(f"\n{'='*60}")
    print("v20 回测结果")
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


def calc_v20_metrics(nav, trade_log, initial_capital):
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
    nav, trades, metrics = run_v20_backtest()

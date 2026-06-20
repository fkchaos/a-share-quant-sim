#!/usr/bin/env python3
"""
nav_compare.py — 模拟盘 vs 回测 NAV 对比

功能：
1. 读取模拟盘账户的每日 NAV（从 trade_log 和 holdings 计算）
2. 跑最近 N 个交易日的回测
3. 对比两者偏差，超过阈值则告警

用法：
  python scripts/tools/nav_compare.py                    # 默认 20 个交易日
  python scripts/tools/nav_compare.py --days 10          # 指定交易日数
  python scripts/tools/nav_compare.py --threshold 0.05   # 偏差阈值（默认 5%）
  python scripts/tools/nav_compare.py --quiet            # 静默模式（不发消息）
"""
import os, sys, argparse
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(PROJECT_ROOT, "data"))

import pandas as pd
import numpy as np


def get_sim_nav(account_id, days=20):
    """读取模拟盘账户最近 N 个交易日的 NAV"""
    from core.db import get_account, get_holdings, get_kline_latest, get_latest_date

    acct = get_account(account_id)
    if not acct:
        print(f"❌ 账户 {account_id} 不存在")
        return None

    holdings = get_holdings(account_id)
    cash = acct["cash"]

    # 获取最近 N 个交易日日期
    latest = get_latest_date()
    if not latest:
        return None

    # 简化：用最新持仓 + 最新价格估算当前 NAV
    # 注意：这只是当前快照，不是历史 NAV 序列
    total_value = cash
    for code, h in holdings.items():
        kl = get_kline_latest(code)
        if kl and kl.get("close", 0) > 0:
            total_value += h.get("shares", 0) * kl["close"]

    # 返回当前 NAV 和初始资金
    return {
        "current_nav": total_value,
        "initial_capital": acct["initial_capital"],
        "cash": cash,
        "holding_count": len(holdings),
    }


def get_backtest_nav(strategy, days=20):
    """跑最近 N 个交易日的回测，返回最终 NAV"""
    from core.db import get_latest_date, load_panel_from_db, get_kline_df
    from core.factors import calc_all_factors
    from core.strategy import StrategyEngine
    from core.account import PortfolioState, portfolio_value
    from core.config import STRATEGY_PROFILES

    latest = get_latest_date()
    if not latest:
        return None

    # 计算起始日期（多取一些用于 warmup）
    start_date = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=days * 3)).strftime("%Y-%m-%d")

    # 加载面板数据
    tpl, codes = load_panel_from_db(start_date, latest, need_open=True, need_hl=True)
    if tpl is None or len(tpl) < 1:
        return None

    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel = tpl[3] if len(tpl) > 3 else None
    high_panel = tpl[4] if len(tpl) > 4 else None
    low_panel = tpl[5] if len(tpl) > 5 else None

    # 获取策略配置
    if strategy not in STRATEGY_PROFILES:
        print(f"❌ 未知策略: {strategy}")
        return None

    profile = STRATEGY_PROFILES[strategy]

    # 计算因子
    factors = calc_all_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)

    # 初始化策略引擎
    engine = StrategyEngine(profile)

    # 初始化账户
    state = PortfolioState(
        cash=profile.initial_capital or 100000,
        initial_capital=profile.initial_capital or 100000,
    )

    # 回测循环
    dates = close_panel.index.tolist()
    nav_history = []

    for i, date in enumerate(dates):
        if i < 120:  # warmup
            continue

        price_data = close_panel.loc[date]

        # 风控检查
        from core.account import check_stop_loss, check_take_profit
        state = check_stop_loss(state, date, price_data)
        if profile.use_take_profit and profile.tp_tiers:
            state = check_take_profit(state, date, price_data, tiers=profile.tp_tiers)

        # 选股
        score = engine.score_panel(factors, date)
        if score is not None and len(score) > 0:
            top_stocks = score.nlargest(profile.top_n).index.tolist()

            # 卖出不在目标中的
            for code in list(state.holdings.keys()):
                if code not in top_stocks and code in price_data.index:
                    from core.account import sell
                    state = sell(state, code, price_data[code], date, 'REBALANCE')

            # 买入
            if top_stocks:
                from core.account import buy
                available_cash = state.cash
                per_stock = available_cash / len(top_stocks) if top_stocks else 0
                for code in top_stocks:
                    if code not in state.holdings and code in price_data.index:
                        p = price_data[code]
                        if p > 0:
                            shares = int(per_stock / p / 100) * 100
                            if shares > 0:
                                state = buy(state, code, p, date, shares=shares)

        # 记录 NAV
        nav = portfolio_value(state, date, price_data)
        nav_history.append({"date": str(date), "nav": nav})

    if nav_history:
        return {
            "current_nav": nav_history[-1]["nav"],
            "initial_capital": profile.initial_capital or 100000,
            "nav_history": nav_history,
        }
    return None


def compare_and_report(sim_result, bt_result, threshold=0.05):
    """对比模拟盘和回测 NAV，返回报告"""
    if sim_result is None or bt_result is None:
        return "❌ 数据不足，无法对比", False

    sim_nav = sim_result["current_nav"]
    bt_nav = bt_result["current_nav"]
    sim_initial = sim_result["initial_capital"]
    bt_initial = bt_result["initial_capital"]

    # 计算收益率
    sim_return = (sim_nav - sim_initial) / sim_initial
    bt_return = (bt_nav - bt_initial) / bt_initial

    # 计算偏差（收益率差异）
    deviation = sim_return - bt_return

    report_lines = [
        f"📊 模拟盘 vs 回测 NAV 对比报告",
        f"",
        f"模拟盘:",
        f"  初始资金: ¥{sim_initial:,.0f}",
        f"  当前 NAV: ¥{sim_nav:,.0f}",
        f"  收益率: {sim_return:+.2%}",
        f"  持仓: {sim_result['holding_count']} 只",
        f"",
        f"回测 ({bt_result.get('strategy', 'unknown')}):",
        f"  初始资金: ¥{bt_initial:,.0f}",
        f"  当前 NAV: ¥{bt_nav:,.0f}",
        f"  收益率: {bt_return:+.2%}",
        f"",
        f"偏差: {deviation:+.2%}",
        f"阈值: {threshold:.1%}",
    ]

    is_alert = abs(deviation) > threshold
    if is_alert:
        report_lines.append(f"⚠️ 偏差超过阈值！需要关注")
    else:
        report_lines.append(f"✅ 偏差在正常范围内")

    return "\n".join(report_lines), is_alert


def send_message(message):
    """通过 hermes CLI 发送消息"""
    import subprocess
    cmd = ["hermes", "send", "-t", "qqbot:95A0E5858A112E7A030CAE1094512D2F", message]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="模拟盘 vs 回测 NAV 对比")
    parser.add_argument("--days", type=int, default=20, help="对比交易日数（默认: 20）")
    parser.add_argument("--threshold", type=float, default=0.05, help="偏差阈值（默认: 0.05 = 5%）")
    parser.add_argument("--account-id", type=int, default=2, help="模拟盘账户ID（默认: 2）")
    parser.add_argument("--strategy", type=str, default="v27", help="回测策略（默认: v27）")
    parser.add_argument("--quiet", action="store_true", help="静默模式（不发消息）")
    args = parser.parse_args()

    print(f"📊 模拟盘 vs 回测 NAV 对比")
    print(f"账户: {args.account_id}  策略: {args.strategy}  交易日: {args.days}  阈值: {args.threshold:.1%}")
    print()

    # 读取模拟盘 NAV
    print("读取模拟盘数据...")
    sim_result = get_sim_nav(args.account_id, args.days)
    if sim_result:
        print(f"  模拟盘 NAV: ¥{sim_result['current_nav']:,.0f} ({sim_result['holding_count']} 只持仓)")

    # 跑回测
    print("跑回测...")
    bt_result = get_backtest_nav(args.strategy, args.days)
    if bt_result:
        print(f"  回测 NAV: ¥{bt_result['current_nav']:,.0f}")

    # 对比
    print()
    report, is_alert = compare_and_report(sim_result, bt_result, args.threshold)
    print(report)

    if is_alert and not args.quiet:
        send_message(report)
        print("\n⚠️ 已发送告警消息")


if __name__ == "__main__":
    main()

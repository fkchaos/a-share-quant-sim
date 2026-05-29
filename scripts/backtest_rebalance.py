"""
回测验证：强制调仓日对比 v3 vs v4
====================================
把 trade_count 设为 19（下一个交易日就是调仓日），
对比 v3 和 v4 在调仓日的实际行为差异。
"""
import sys, os, pandas as pd, numpy as np, json, shutil
from datetime import datetime

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/a-share-quant-sim/scripts")

from sim_account import SimAccount, generate_scores, load_hs300_names, DAILY_DIR
from constraints import build_trade_context
from data_quality import DataQualityAuditor
from portfolio_controls import cap_daily_turnover

REBAL_FREQ = 20
STOP_LOSS = 0.20
TOP_N = 10
SLIPPAGE_RATE = 0.001
MAX_DAILY_TURNOVER = 0.30
INITIAL_CAPITAL = 1_000_000

DATA_DIR = "data"
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")


def run_rebalance_day(label: str, v4_mode: bool, trade_count_override: int = None):
    """
    强制在今天跑一次调仓（设置 trade_count = REBAL_FREQ - 1，下一天就是调仓日）。
    实际上今天就是调仓日（trade_count % REBAL_FREQ == 0）。
    """
    bt_dir = os.path.join(DATA_DIR, f"bt_rebal_{label}")
    bt_portfolio = os.path.join(bt_dir, "portfolio")
    os.makedirs(bt_portfolio, exist_ok=True)

    # 备份并恢复原始 account
    backup = os.path.join(PORTFOLIO_DIR, "account_v3_backup_2.json")
    src = os.path.join(PORTFOLIO_DIR, "account.json")
    if not os.path.exists(backup):
        shutil.copy2(src, backup)
    shutil.copy2(backup, os.path.join(bt_portfolio, "account.json"))
    shutil.copy2(backup, src)  # 也恢复原始

    # 加载
    account = SimAccount()
    with open(backup) as f:
        data = json.load(f)
    account.cash = data['cash']
    account.holdings = data['holdings']
    account.trade_log = data.get('trade_log', [])
    account.nav_history = data.get('nav_history', [])
    for code in account.holdings:
        account.holdings[code]['shares'] = int(account.holdings[code]['shares'])

    # 调仓计数
    trade_count = trade_count_override if trade_count_override is not None else (
        REBAL_FREQ - 1  # 下一天就是调仓日
    )

    # 加载所有股票数据
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    code_dfs = {}
    all_dates = set()
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if len(df) > 120:
            code_dfs[code] = df
            all_dates.update(df.index)

    all_dates = sorted(all_dates)
    latest_date = all_dates[-1]
    names = load_hs300_names()

    # 构建价格
    price_data = {}
    for code, df in code_dfs.items():
        if latest_date in df.index:
            row = df.loc[latest_date]
            cv = row.get('close', None)
            if cv is not None and not pd.isna(cv) and float(cv) > 0:
                price_data[code] = float(cv)

    # 期初净值
    pv_before = account.cash
    for code, info in account.holdings.items():
        if code in price_data:
            pv_before += info['shares'] * price_data[code]

    print(f"\n{'='*60}")
    print(f"调仓日回测: {label} (v4={v4_mode})")
    print(f"日期: {latest_date.date()}")
    print(f"期初净值: ¥{pv_before:,.0f}")
    print(f"期初持仓: {len(account.holdings)} 只")
    print(f"调仓计数: {trade_count} → {'调仓日' if trade_count % REBAL_FREQ == 0 else '非调仓日'}")

    # 止损
    for code in list(account.holdings.keys()):
        if code in price_data:
            p = price_data[code]
            info = account.holdings[code]
            loss = (info['cost_price'] - p) / info['cost_price']
            if loss >= STOP_LOSS:
                if v4_mode and code in code_dfs:
                    ctx = build_trade_context(code, code_dfs[code], latest_date)
                    if ctx and ctx.is_sell_blocked()[0]:
                        print(f"  ⏭️  止损跳过 {names.get(code,code)}: {ctx.is_sell_blocked()[1]}")
                        continue
                account.sell(code, p, latest_date, 'STOP_LOSS')

    # 强制调仓
    scores = generate_scores()
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_stocks = [code for code, _ in sorted_scores[:TOP_N]]

    print(f"\n  目标 Top{TOP_N}:")
    for i, code in enumerate(top_stocks):
        name = names.get(code, '—')
        s = scores.get(code, 0)
        p = price_data.get(code, 0)
        print(f"    {i+1}. {code} {name:<10} 评分={s:.3f} 价格={p:.2f}")

    # 卖出
    sell_blocked = []
    for code in list(account.holdings.keys()):
        if code not in top_stocks and code in price_data:
            if v4_mode and code in code_dfs:
                ctx = build_trade_context(code, code_dfs[code], latest_date)
                if ctx and ctx.is_sell_blocked()[0]:
                    reason = ctx.is_sell_blocked()[1]
                    print(f"  ⏭️  卖出跳过 {names.get(code,code)}: {reason}")
                    sell_blocked.append(code)
                    continue
            account.sell(code, price_data[code], latest_date, 'SELL')

    # 换手率控制
    current_pv = account.cash
    for code, info in account.holdings.items():
        if code in price_data:
            current_pv += info['shares'] * price_data[code]

    weight_per = 1.0 / TOP_N
    target_weights = {}
    for code in top_stocks:
        if code in price_data:
            target_weights[code] = weight_per

    turnover_info = None
    if v4_mode and target_weights:
        price_dict = {c: price_data[c] for c in target_weights if c in price_data}
        target_weights, turnover_info = cap_daily_turnover(
            account, target_weights, price_dict, MAX_DAILY_TURNOVER
        )
        if turnover_info["applied"]:
            print(f"\n  📊 换手率控制: {turnover_info['requested_turnover']:.1%}→{turnover_info['max_turnover']:.1%} ×{turnover_info['scale']}")

    # 买入
    buy_blocked = []
    for code in top_stocks:
        if code not in account.holdings and code in price_data:
            if v4_mode and code in code_dfs:
                ctx = build_trade_context(code, code_dfs[code], latest_date)
                if ctx and ctx.is_buy_blocked()[0]:
                    reason = ctx.is_buy_blocked()[1]
                    print(f"  ⏭️  买入跳过 {names.get(code,code)}: {reason}")
                    buy_blocked.append(code)
                    continue
            account.buy(code, price_data[code], latest_date)

    # 期末净值
    pv_after = account.cash
    for code, info in account.holdings.items():
        if code in price_data:
            pv_after += info['shares'] * price_data[code]

    total_ret = (pv_after / account.initial_capital) - 1

    print(f"\n  期末净值: ¥{pv_after:,.0f}")
    print(f"  总收益率: {total_ret:+.2%}")
    print(f"  持仓: {len(account.holdings)} 只")
    print(f"  交易次数: {len(account.trade_log)}")
    if sell_blocked:
        print(f"  卖出被阻塞: {len(sell_blocked)} 只")
    if buy_blocked:
        print(f"  买入被阻塞: {len(buy_blocked)} 只")
    print(f"{'='*60}")

    return {
        'label': label,
        'v4': v4_mode,
        'pv_before': pv_before,
        'pv_after': pv_after,
        'total_return': total_ret,
        'holdings': len(account.holdings),
        'trades': len(account.trade_log),
        'sell_blocked': len(sell_blocked),
        'buy_blocked': len(buy_blocked),
        'turnover_applied': turnover_info.get("applied", False) if turnover_info else False,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("调仓日回测：v3 vs v4 对比")
    print("=" * 60)

    r_v3 = run_rebalance_day("v3", v4_mode=False)
    r_v4 = run_rebalance_day("v4", v4_mode=True)

    if r_v3 and r_v4:
        print(f"\n{'='*60}")
        print("对比:")
        print(f"{'指标':<18} {'v3':>14} {'v4':>14}")
        print("-" * 46)
        for k in ['pv_before', 'pv_after', 'total_return', 'holdings', 'trades']:
            print(f"{k:<18} {r_v3[k]:>+13.4f} {r_v4[k]:>+13.4f}" if isinstance(r_v3[k], float) else f"{k:<18} {str(r_v3[k]):>14} {str(r_v4[k]):>14}")
        print(f"{'sell_blocked':<18} {r_v3['sell_blocked']:>14} {r_v4['sell_blocked']:>14}")
        print(f"{'buy_blocked':<18} {r_v3['buy_blocked']:>14} {r_v4['buy_blocked']:>14}")
        print(f"{'turnover_applied':<18} {str(r_v3['turnover_applied']):>14} {str(r_v4['turnover_applied']):>14}")

        print()
        # v4 的净值可能略低于 v3（因为有些股票被阻塞无法买卖），
        # 但差异应该在合理范围内（< 1%）
        diff_pct = (r_v4['pv_after'] - r_v3['pv_after']) / r_v3['pv_after'] * 100
        print(f"净值差异: {diff_pct:+.2f}%")
        if abs(diff_pct) < 1.0:
            print("✅ v4 与 v4 净值差异 < 1%，通过验证")
        elif r_v4['pv_after'] >= r_v3['pv_after']:
            print("✅ v4 净值不低于 v3，通过验证")
        else:
            print(f"⚠️  v4 净值低于 v3 {abs(diff_pct):.2f}%，需要检查是否合理")
            print("   （v4 有交易约束，部分股票无法买卖导致差异是正常的）")

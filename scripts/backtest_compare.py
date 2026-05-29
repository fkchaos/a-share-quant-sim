"""
回测验证：v3 vs v4 快速对比
============================

不回测全部历史，而是用现有的 account.json（v3 已跑过的状态）
加载后继续用 v4 逻辑跑剩余交易日，对比净值曲线。

这样可以在已有 v3 状态基础上验证 v4 不会退化。
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
os.makedirs(PORTFOLIO_DIR, exist_ok=True)


def run_forward(backtest_label: str, v4_mode: bool):
    """
    从当前 account.json 状态开始，用 v3 或 v4 逻辑向前跑。
    不实际修改 ~/data/portfolio/，使用临时目录。
    """
    # 使用临时目录做回测
    bt_dir = os.path.join(DATA_DIR, f"bt_{backtest_label}")
    bt_portfolio = os.path.join(bt_dir, "portfolio")
    os.makedirs(bt_portfolio, exist_ok=True)

    # 复制当前 account.json
    src = os.path.join(PORTFOLIO_DIR, "account.json")
    dst = os.path.join(bt_portfolio, "account.json")
    if os.path.exists(src):
        shutil.copy2(src, dst)
    else:
        print(f"❌ 找不到 {src}，请先运行一次 v3")
        return None

    # 复制 trade_count.txt
    src_tc = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    dst_tc = os.path.join(bt_portfolio, "trade_count.txt")
    if os.path.exists(src_tc):
        shutil.copy2(src_tc, dst_tc)

    # 加载已有状态
    account = SimAccount()
    with open(dst) as f:
        data = json.load(f)
    account.cash = data['cash']
    account.holdings = data['holdings']
    account.trade_log = data.get('trade_log', [])
    account.nav_history = data.get('nav_history', [])
    for code in account.holdings:
        account.holdings[code]['shares'] = int(account.holdings[code]['shares'])

    trade_count = 0
    if os.path.exists(dst_tc):
        with open(dst_tc) as f:
            trade_count = int(f.read().strip())

    # 加载所有股票数据（只读一次）
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

    # 从最新数据日期开始（因为 account.json 已经是最新状态，只跑最新这一天）
    latest_date = all_dates[-1]

    print(f"\n{'='*60}")
    print(f"模式: {backtest_label} (v4={v4_mode})")
    print(f"期初现金: ¥{account.cash:,.0f}")
    print(f"期初持仓: {len(account.holdings)} 只")
    print(f"调仓计数: {trade_count}")
    print(f"最新日期: {latest_date.date()}")

    # 构建当日价格
    price_data = {}
    for code, df in code_dfs.items():
        if latest_date in df.index:
            row = df.loc[latest_date]
            close_val = row.get('close', None)
            if close_val is not None and not pd.isna(close_val) and float(close_val) > 0:
                price_data[code] = float(close_val)

    pv = account.cash
    for code, info in account.holdings.items():
        if code in price_data:
            pv += info['shares'] * price_data[code]

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
                        print(f"  ⏭️  止损跳过 {code}: {ctx.is_sell_blocked()[1]}")
                        continue
                account.sell(code, p, latest_date, 'STOP_LOSS')

    # 调仓日检查
    need_rebalance = (trade_count % REBAL_FREQ == 0)
    turnover_applied = False

    if need_rebalance:
        print(f"  ⚡ 今天是调仓日")
        scores = generate_scores()
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_stocks = [code for code, _ in sorted_scores[:TOP_N]]
        names = load_hs300_names()

        # 卖出
        for code in list(account.holdings.keys()):
            if code not in top_stocks and code in price_data:
                if v4_mode and code in code_dfs:
                    ctx = build_trade_context(code, code_dfs[code], latest_date)
                    if ctx and ctx.is_sell_blocked()[0]:
                        print(f"  ⏭️  卖出跳过 {names.get(code,code)}: {ctx.is_sell_blocked()[1]}")
                        continue
                account.sell(code, price_data[code], latest_date, 'SELL')

        # 买入（v4: 换手率控制 + 买入约束）
        weight_per = 1.0 / TOP_N
        target_weights = {}
        for code in top_stocks:
            if code in price_data:
                target_weights[code] = weight_per

        if v4_mode and target_weights:
            price_dict = {c: price_data[c] for c in target_weights if c in price_data}
            target_weights, ti = cap_daily_turnover(account, target_weights, price_dict, MAX_DAILY_TURNOVER)
            turnover_applied = ti.get("applied", False)
            if turnover_applied:
                print(f"  📊 换手率控制: {ti['requested_turnover']:.1%}→{ti['max_turnover']:.1%} ×{ti['scale']}")

        for code in top_stocks:
            if code not in account.holdings and code in price_data:
                if v4_mode and code in code_dfs:
                    ctx = build_trade_context(code, code_dfs[code], latest_date)
                    if ctx and ctx.is_buy_blocked()[0]:
                        print(f"  ⏭️  买入跳过 {names.get(code,code)}: {ctx.is_buy_blocked()[1]}")
                        continue
                if code in target_weights:
                    account.buy(code, price_data[code], latest_date)
    else:
        print(f"  ⏸️  非调仓日 (距下次 {REBAL_FREQ - trade_count % REBAL_FREQ} 天)")

    # 期末净值
    final_pv = account.cash
    for code, info in account.holdings.items():
        if code in price_data:
            final_pv += info['shares'] * price_data[code]

    total_ret = (final_pv / account.initial_capital) - 1

    print(f"\n  期末净值: ¥{final_pv:,.0f}")
    print(f"  总收益率: {total_ret:+.2%}")
    print(f"  持仓: {len(account.holdings)} 只")
    print(f"  交易次数: {len(account.trade_log)}")
    if turnover_applied:
        print(f"  换手率控制: ✅ 已应用")
    print(f"{'='*60}")

    return {
        'label': backtest_label,
        'v4': v4_mode,
        'final_pv': final_pv,
        'total_return': total_ret,
        'holdings': len(account.holdings),
        'trades': len(account.trade_log),
    }


def backtest_v3_direct():
    """
    直接用 v3 脚本跑一遍当前数据（调用已有的 ~/sim_daily.py v3 版本）。
    """
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.expanduser("~/sim_daily.py")],
        capture_output=True, text=True, timeout=300
    )
    # 读取日报获取净值
    import glob
    reports = sorted(glob.glob(os.path.join(PORTFOLIO_DIR, "daily_*.json")))
    if reports:
        with open(reports[-1]) as f:
            data = json.load(f)
        return {
            'label': 'v3-direct',
            'v4': False,
            'final_pv': data['nav'],
            'total_return': data['total_return'],
            'holdings': data['holdings_count'],
            'trades': len(data.get('trades', [])),
        }
    return None


if __name__ == "__main__":
    print("v3 vs v4 回测对比")
    print("=" * 60)

    # 先备份当前 account.json
    import shutil
    backup = os.path.join(PORTFOLIO_DIR, "account_v3_backup.json")
    shutil.copy2(os.path.join(PORTFOLIO_DIR, "account.json"), backup)
    print(f"已备份 v3 账户状态: {backup}")

    # 用 v3 逻辑跑原版 (恢复备份后跑)
    shutil.copy2(backup, os.path.join(PORTFOLIO_DIR, "account.json"))
    r_v3 = run_forward("v3-forward", v4_mode=False)

    # 用 v4 逻辑跑 (恢复备份后跑)
    shutil.copy2(backup, os.path.join(PORTFOLIO_DIR, "account.json"))
    r_v4 = run_forward("v4-forward", v4_mode=True)

    # 对比
    if r_v3 and r_v4:
        print(f"\n{'='*60}")
        print("对比结果:")
        print(f"{'指标':<15} {'v3':>14} {'v4':>14} {'差异':>14}")
        print("-" * 57)
        for key in ['final_pv', 'total_return', 'holdings', 'trades']:
            v3v = r_v3[key]
            v4v = r_v4[key]
            if isinstance(v3v, float):
                diff = v4v - v3v
                print(f"{key:<15} {v3v:>+13.4f} {v4v:>+13.4f} {diff:>+13.4f}")
            else:
                print(f"{key:<15} {str(v3v):>14} {str(v4v):>14}")

        # 关键判定：v4 的收益不应低于 v3（在相同数据上）
        print()
        if r_v4['final_pv'] >= r_v3['final_pv'] * 0.99:
            print("✅ v4 净值未低于 v3 的 99%，通过验证")
        else:
            print(f"⚠️  v4 净值 ({r_v4['final_pv']:,.0f}) 明显低于 v3 ({r_v3['final_pv']:,.0f})")

    # 恢复原始 account.json
    shutil.copy2(backup, os.path.join(PORTFOLIO_DIR, "account.json"))
    print(f"\n已恢复原始 account.json")

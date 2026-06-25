#!/usr/bin/env python3
"""
cmd.py — 数据库 & 模拟盘手动操作 CLI

替代已废弃的 scripts/tools/cli.py。所有数据库操作通过本脚本完成，
不需要写 SQL，直接命令行操作。

用法（直接复制粘贴即可）:
  python cmd.py                                                       # 帮助
  python cmd.py status                                               # 全局状态一眼览
  python cmd.py account                                              # 查看账户概览
  python cmd.py holdings                                            # 查看持仓
  python cmd.py trades --limit 20                                   # 交易记录
  python cmd.py buy --code 600519 --shares 100 --price 1500          # 买入
  python cmd.py sell --code 600519 --shares 100 --price 1600         # 卖出
  python cmd.py set-cash --amount 200000                            # 调整现金
  python cmd.py switch --strategy v44                               # 切换策略
  python cmd.py strategies                                          # 策略列表
  python cmd.py signals                                             # 最近信号
  python cmd.py kline 600519                                        # K线
  python cmd.py stats                                               # 数据库统计

所有写操作有确认提示，不会误操作。默认操作 main 账户。
"""
import os
import sys
import json
import glob

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.db import (
    get_account, list_accounts, get_holdings, get_trades,
    get_kline, db_stats, get_stock_name_map, upsert_account,
    update_cash, upsert_holding, delete_holding, clear_holdings,
    add_trade, get_conn, get_stock_pool_full,
)
from core.strategy_map import STRATEGY_MAP


# ── 格式化 ──────────────────────────────────────────────────

def fmt(v, decimals=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{decimals}f}"
    return str(v)


def confirm(prompt):
    """确认提示"""
    ans = input(f"  ⚠️ {prompt} [yes/no]: ").strip().lower()
    return ans in ("yes", "y")


def get_account_by_name(name):
    """通过 name 查不到则尝试 id"""
    accounts = list_accounts()
    if name.isdigit():
        acct = get_account(int(name))
        return acct, acct['id'] if acct else None
    acct = next((a for a in accounts if a['name'] == name), None)
    return acct, acct['id'] if acct else None


def find_latest_plan(name):
    """找账户最新的 trade_plan 文件"""
    plan_dir = os.path.join(_PROJECT_ROOT, 'data', 'portfolio')
    files = sorted(glob.glob(os.path.join(plan_dir, f'trade_plan_{name}*.json')))
    if files:
        with open(files[-1]) as f:
            return json.load(f), files[-1]
    return None, None


# ── 命令实现 ────────────────────────────────────────────────

def cmd_status(args):
    """全局状态一眼览: status [--account <name>]"""
    acct, aid = get_account_by_name(args[0] if args else "main")
    if not acct:
        return

    holdings = get_holdings(aid)
    # 计算持仓市值 + 盈亏
    total_mv = 0
    total_cost = 0
    rows = []
    name_map = get_stock_name_map()
    for code, h in holdings.items():
        kl = get_kline(code, limit=1)
        price = kl[0]['close'] if kl else 0
        mv = h['shares'] * price
        cost = h['shares'] * h['cost_price']
        total_mv += mv
        total_cost += cost
        rows.append((code, h.get('name', name_map.get(code, '')), h['shares'], h['cost_price'], price, mv))

    total = acct['cash'] + total_mv
    ret_pct = (total - acct['initial_capital']) / acct['initial_capital'] * 100 if acct['initial_capital'] else 0
    pnl_pct = (total_mv - total_cost) / total_cost * 100 if total_cost else 0

    print(f"┌─────────────────────────────────────────────┐")
    print(f"│ 账户 {aid}: {acct['name']:<14} 策略: {acct.get('strategy',''):>8} │")
    print(f"├─────────────────────────────────────────────┤")
    print(f"│ 💰 现金     ¥{fmt(acct['cash']):>14}              │")
    print(f"│ 📊 市值     ¥{fmt(total_mv):>14}              │")
    print(f"│ 📈 总资产   ¥{fmt(total):>14}              │")
    print(f"│ 📉 收益率   {ret_pct:>+13.2f}%              │")
    print(f"│ 💰 盈亏     {pnl_pct:>+13.2f}%              │")
    print(f"└─────────────────────────────────────────────┘")

    if rows:
        print(f"\n{'代码':<8} {'名称':<10} {'数量':>6} {'成本':>8} {'现价':>8} {'市值':>10}")
        print("-" * 55)
        for r in sorted(rows, key=lambda x: -x[5]):
            print(f"{r[0]:<8} {r[1]:<10} {r[2]:>6} {r[3]:>8.2f} {r[4]:>8.2f} ¥{r[5]:>9,.0f}")

    # 最新信号
    plan, plan_path = find_latest_plan(acct['name'])
    if plan:
        date = plan.get('date', '?')
        buys = plan.get('buys', [])
        sells = plan.get('sells', [])
        print(f"\n📡 最近信号 ({date}): 买{len(buys)} 卖{len(sells)}")
        for b in buys[:5]:
            print(f"  📈 {b.get('code','')} {b.get('shares',0)}股 @ {b.get('price',0):.2f}  {b.get('reason','')}")
        for s in sells[:5]:
            print(f"  📉 {s.get('code','')} {s.get('shares',0)}股 @ {s.get('price',0):.2f}  {s.get('reason','')}")
    else:
        print(f"\n📡 无交易信号文件")


def cmd_account(args):
    """查看账户: account [name|id]"""
    if not args:
        accounts = list_accounts()
        print(f"{'ID':<4} {'名称':<10} {'策略':<10} {'现金':>12} {'初始资金':>12}")
        print("-" * 52)
        for a in accounts:
            print(f"{a['id']:<4} {a['name']:<10} {a.get('strategy',''):<10} ¥{fmt(a['cash']):>10} ¥{fmt(a['initial_capital']):>10}")
        return

    acct, aid = get_account_by_name(args[0])
    if not acct:
        print(f"账户 '{args[0]}' 不存在")
        return

    holdings = get_holdings(aid)
    total_mv = sum(h['shares'] * get_kline(code, limit=1)[0]['close']
                   for code, h in holdings.items() if get_kline(code, limit=1))
    total = acct['cash'] + total_mv
    ret = (total - acct['initial_capital']) / acct['initial_capital'] * 100 if acct['initial_capital'] else 0

    print(f"账户 {aid}: {acct['name']}")
    print(f"  策略: {acct.get('strategy','(未绑定)')}")
    print(f"  现金: ¥{fmt(acct['cash'])}")
    print(f"  市值: ¥{fmt(total_mv)}")
    print(f"  总产: ¥{fmt(total)}")
    print(f"  收益: {ret:+.2f}%")
    print(f"  持仓: {len(holdings)} 只")


def cmd_holdings(args):
    """查看持仓: holdings [--account <name>]"""
    acct, aid = get_account_by_name(args[0] if args else "main")
    if not acct:
        return

    holdings = get_holdings(aid)
    if not holdings:
        print("空仓")
        return

    name_map = get_stock_name_map()
    total_mv = 0
    total_cost = 0
    rows = []
    for code, h in holdings.items():
        kl = get_kline(code, limit=1)
        price = kl[0]['close'] if kl else 0
        mv = h['shares'] * price
        cost = h['shares'] * h['cost_price']
        total_mv += mv
        total_cost += cost
        rows.append((code, h.get('name', name_map.get(code, '')), h['shares'], h['cost_price'], price, mv))

    pnl_pct = (total_mv - total_cost) / total_cost * 100 if total_cost else 0
    print(f"{'代码':<8} {'名称':<10} {'数量':>6} {'成本':>8} {'现价':>8} {'市值':>10} {'盈亏':>8}")
    print("-" * 65)
    for r in sorted(rows, key=lambda x: -x[5]):
        pnl = (r[4] - r[3]) / r[3] * 100 if r[3] > 0 else 0
        print(f"{r[0]:<8} {r[1]:<10} {r[2]:>6} {r[3]:>8.2f} {r[4]:>8.2f} ¥{r[5]:>9,.0f} {pnl:>+7.2f}%")
    print("-" * 65)
    print(f"市值合计: ¥{fmt(total_mv)}  | 盈亏: {pnl_pct:+.2f}%")


def cmd_trades(args):
    """交易记录: trades [--account <name>] [--limit N] [--code 600519] [--action BUY]"""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--account', default='main')
    p.add_argument('--limit', type=int, default=30)
    p.add_argument('--code', default=None)
    p.add_argument('--action', default=None)
    p.add_argument('--date-from', default=None)
    p = p.parse_args(args)

    acct, aid = get_account_by_name(p.account)
    if not acct:
        return

    trades = get_trades(aid, limit=p.limit)
    if p.code:
        trades = [t for t in trades if t['code'] == p.code.zfill(6)]
    if p.action:
        trades = [t for t in trades if t['action'] == p.action.upper()]
    if p.date_from:
        trades = [t for t in trades if t['created_at'] >= p.date_from]
    if not trades:
        print("无交易记录")
        return

    print(f"{'时间':<17} {'代码':<8} {'名称':<9} {'操作':<5} {'数量':>6} {'价格':>8} {'原因'}")
    print("-" * 65)
    for t in trades:
        print(f"{t['created_at'][:17]:<17} {t['code']:<8} {t['name']:<9} {t['action']:<5} "
              f"{t['shares']:>6} {t['price']:>8.2f} {t['reason']}")


def cmd_set_cash(args):
    """调整现金: set-cash --amount 200000 [--account <name>]"""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--amount', type=float, required=True)
    p.add_argument('--account', default='main')
    p = p.parse_args(args)

    acct, aid = get_account_by_name(p.account)
    if not acct:
        return

    old = acct['cash']
    delta = p.amount - old
    print(f"账户 {aid} ({acct['name']})")
    print(f"  当前: ¥{fmt(old)} → 目标: ¥{fmt(p.amount)} (Δ{fmt(delta):+})")
    if not confirm("确认?"):
        print("已取消"); return
    update_cash(aid, delta=delta)
    print(f"✅ 现金已调整为 ¥{fmt(p.amount)}")


def cmd_buy(args):
    """买入: buy --code 600519 --shares 100 --price 1500 [--account main] [--reason 手动买入]"""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--code', required=True)
    p.add_argument('--shares', type=int, required=True)
    p.add_argument('--price', type=float, required=True)
    p.add_argument('--account', default='main')
    p.add_argument('--reason', default='手动买入')
    p = p.parse_args(args)

    acct, aid = get_account_by_name(p.account)
    if not acct:
        return

    code = p.code.zfill(6)
    amount = p.shares * p.price
    if acct['cash'] < amount:
        print(f"❌ 现金不足: 需 ¥{fmt(amount)}, 有 ¥{fmt(acct['cash'])}")
        return

    print(f"  买入 {code} {p.shares}股 @ {p.price:.2f} = ¥{fmt(amount)}")
    print(f"  现金: ¥{fmt(acct['cash'])} → ¥{fmt(acct['cash'] - amount)}")
    if not confirm("确认买入?"):
        print("已取消"); return

    holdings = get_holdings(aid)
    name_map = get_stock_name_map()
    if code in holdings:
        h = holdings[code]
        new_shares = h['shares'] + p.shares
        new_cost = (h['cost_price'] * h['shares'] + p.price * p.shares) / new_shares
        upsert_holding(aid, code, h.get('name', code), new_shares, round(new_cost, 4))
    else:
        upsert_holding(aid, code, name_map.get(code, code), p.shares, p.price)
    update_cash(aid, delta=-amount)
    add_trade(aid, code, name_map.get(code, code), "BUY", p.shares, p.price, amount, p.reason)
    print(f"✅ 买入成功")


def cmd_sell(args):
    """卖出: sell --code 600519 --shares 100 --price 1600 [--account main] [--reason 手动卖出]"""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--code', required=True)
    p.add_argument('--shares', type=int, required=True)
    p.add_argument('--price', type=float, required=True)
    p.add_argument('--account', default='main')
    p.add_argument('--reason', default='手动卖出')
    p = p.parse_args(args)

    acct, aid = get_account_by_name(p.account)
    if not acct:
        return

    code = p.code.zfill(6)
    holdings = get_holdings(aid)
    if code not in holdings:
        print(f"❌ 未持有 {code}")
        return
    h = holdings[code]
    if h['shares'] < p.shares:
        print(f"❌ 持仓不足: 有{h['shares']}股, 欲卖{p.shares}股")
        return

    amount = p.shares * p.price
    remaining = h['shares'] - p.shares
    pnl = (p.price - h['cost_price']) / h['cost_price'] * 100

    print(f"  卖出 {code} {p.shares}股 @ {p.price:.2f} = ¥{fmt(amount)}")
    print(f"  成本: {h['cost_price']:.2f}  盈亏: {pnl:+.2f}%  剩余: {remaining}股")
    if not confirm("确认卖出?"):
        print("已取消"); return

    if remaining == 0:
        delete_holding(aid, code)
    else:
        upsert_holding(aid, code, h.get('name', code), remaining, h['cost_price'])
    update_cash(aid, delta=amount)
    add_trade(aid, code, h.get('name', code), "SELL", p.shares, p.price, amount, p.reason)
    print(f"✅ 卖出成功 (盈亏 {pnl:+.2f}%)")


def cmd_switch(args):
    """切换策略: switch --strategy v44 [--account <name>]"""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--strategy', required=True)
    p.add_argument('--account', default='main')
    p = p.parse_args(args)

    strategy = p.strategy
    if strategy not in STRATEGY_MAP:
        print(f"❌ 未知策略: {strategy}")
        print(f"可用策略: {', '.join(sorted(STRATEGY_MAP.keys()))}")
        return

    acct, aid = get_account_by_name(p.account)
    if not acct:
        return

    old_strategy = acct.get('strategy', '(未绑定)')
    desc = STRATEGY_MAP[strategy].get('description', '')

    print(f"账户 {aid} ({acct['name']})")
    print(f"  当前策略: {old_strategy}")
    print(f"  目标策略: {strategy}  —  {desc}")
    if not confirm("确认切换?"):
        print("已取消"); return

    with get_conn("accounts") as conn:
        conn.execute("UPDATE account SET strategy=?, updated_at=datetime('now') WHERE id=?",
                     (strategy, aid))
    print(f"✅ 策略已切换为 {strategy}")
    print(f"  注: 下次信号生成时生效")


def cmd_strategies(args):
    """策略列表: strategies"""
    accounts = list_accounts()
    bound = {a['id']: a.get('strategy','') for a in accounts}

    print(f"{'策略':<8} {'状态':<6} {'描述'}")
    print("-" * 65)
    for name, info in sorted(STRATEGY_MAP.items()):
        status = "✅" if name in bound.values() else "  "
        print(f"{status} {name:<6}  {info.get('description','')}")

    print(f"\n当前绑定:")
    for aid, strat in bound.items():
        print(f"  账户{aid}: {strat or '(未绑定)'}")


def cmd_signals(args):
    """最近信号: signals [--account <name>]"""
    acct, aid = get_account_by_name(args[0] if args else "main")
    if not acct:
        return

    plan, path = find_latest_plan(acct['name'])
    if not plan:
        print(f"无交易信号文件 (data/portfolio/trade_plan_{acct['name']}*.json)")
        return

    import datetime
    date = plan.get('date', '?')
    today = datetime.date.today().isoformat()
    is_today = "📅 今日" if date == today else f"📅 {date}"

    buys = plan.get('buys', [])
    sells = plan.get('sells', [])
    hold = plan.get('hold', [])

    print(f"{is_today}  |  买 {len(buys)} 卖 {len(sells)} 持有 {len(hold)}")
    if buys:
        print(f"\n  📈 买入:")
        for b in buys:
            print(f"    {b.get('code','')} {b.get('shares',0)}股 @ {b.get('price',0):.2f}  {b.get('reason','')}")
    if sells:
        print(f"\n  📉 卖出:")
        for s in sells:
            print(f"    {s.get('code','')} {s.get('shares',0)}股 @ {s.get('price',0):.2f}  {s.get('reason','')}")
    if hold:
        print(f"\n  ⏸️ 持有不动: {', '.join(h.get('code','') for h in hold)}")


def cmd_kline(args):
    """K线: kline <code> [limit] [--account main]"""
    if not args:
        print("用法: cmd.py kline <code> [limit]")
        return
    code = args[0].zfill(6)
    limit = int(args[1]) if len(args) > 1 else 20

    name_map = get_stock_name_map()
    name = name_map.get(code, '?')
    rows = get_kline(code, limit=limit)
    if not rows:
        print(f"无 {code} ({name}) 数据")
        return

    print(f"{code} ({name}) 最近 {len(rows)} 日K线")
    print(f"{'日期':<12} {'开':>7} {'高':>7} {'低':>7} {'收':>7} {'量':>10} {'换手%':>6}")
    print("-" * 55)
    for r in rows:
        print(f"{r['date']:<12} {r['open']:>7.2f} {r['high']:>7.2f} {r['low']:>7.2f} "
              f"{r['close']:>7.2f} {r['volume']:>10,.0f} {'—':>6}")


def cmd_stats(args):
    """数据库统计: stats"""
    stats = db_stats()
    print("=== 数据库统计 ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


# ── 入口 ────────────────────────────────────────────────────

COMMANDS = {
    "status": cmd_status,
    "account": cmd_account,
    "holdings": cmd_holdings,
    "trades": cmd_trades,
    "set-cash": cmd_set_cash,
    "buy": cmd_buy,
    "sell": cmd_sell,
    "switch": cmd_switch,
    "strategies": cmd_strategies,
    "signals": cmd_signals,
    "kline": cmd_kline,
    "stats": cmd_stats,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])

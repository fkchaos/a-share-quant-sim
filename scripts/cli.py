"""
手动操作 CLI — 账户管理
用法:
  python3 cli.py init                    # 初始化数据库
  python3 cli.py migrate                 # CSV → 数据库迁移
  python3 cli.py account                 # 查看账户概览
  python3 cli.py buy 000001 1000 10.5    # 买入
  python3 cli.py sell 000001 500 11.0    # 卖出
  python3 cli.py holdings                # 查看持仓
  python3 cli.py trades                  # 查看交易记录
  python3 cli.py kline 000001            # 查看K线
  python3 cli.py stats                   # 数据库统计
"""
import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKTEST_DATA_DIR", "/root/data")

from core.db import (
    init_db, db_stats, get_account, upsert_account, update_cash,
    get_holdings, upsert_holding, delete_holding, clear_holdings,
    add_trade, get_trades, get_kline, get_latest_date,
    get_stock_name_map, upsert_kline_batch, get_all_codes,
)


def fmt(v, decimals=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{decimals}f}"
    return str(v)


def cmd_init(args):
    init_db()
    print("✅ 数据库已初始化")


def cmd_migrate(args):
    """CSV → 数据库迁移"""
    import glob
    daily_dir = os.environ.get("BACKTEST_DATA_DIR", "/root/data") + "/daily"
    csv_files = glob.glob(os.path.join(daily_dir, "*.csv"))
    print(f"找到 {len(csv_files)} 只股票 CSV 文件")

    init_db()
    name_map = {}
    total = 0
    t0 = time.time()

    for i, fpath in enumerate(csv_files):
        code = os.path.basename(fpath).replace(".csv", "")
        try:
            import pandas as pd
            df = pd.read_csv(fpath)
            if df.empty:
                continue
            # 标准化列名
            df.columns = [c.strip().lower() for c in df.columns]
            # 日期列
            date_col = "date" if "date" in df.columns else df.columns[0]
            records = []
            for _, row in df.iterrows():
                d = str(row[date_col])[:10]
                o = float(row.get("open", 0) or 0)
                h = float(row.get("high", 0) or 0)
                l = float(row.get("low", 0) or 0)
                c = float(row.get("close", 0) or 0)
                v = float(row.get("volume", 0) or 0)
                a = float(row.get("amount", 0) or 0)
                records.append((code, d, o, h, l, c, v, a))
            upsert_kline_batch(records)
            total += len(records)
            # 从 CSV 第一行读名字（如果有）
            if "name" in df.columns:
                name_map[code] = str(df["name"].iloc[0])
        except Exception as e:
            pass
        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(csv_files)} 只, {total} 条K线, {time.time()-t0:.1f}s")

    # 写股票池
    from core.db import upsert_stock
    for code, name in name_map.items():
        upsert_stock(code, name=name)
    # 没名字的从 zz800 补
    zz800_path = os.path.join(os.environ.get("BACKTEST_DATA_DIR", "/root/data"), "zz800_constituents.csv")
    if os.path.exists(zz800_path):
        import pandas as pd
        zz = pd.read_csv(zz800_path)
        for _, row in zz.iterrows():
            c = str(row["code"]).zfill(6)
            n = str(row.get("name", ""))
            b = str(row.get("board", ""))
            if c not in name_map:
                upsert_stock(c, name=n, board=b)

    # 初始化账户
    for aid, name, cash in [(1, "v11b", 200000), (2, "v13", 100000), (3, "v20", 100000)]:
        upsert_account(aid, name=name, cash=cash, initial_capital=cash,
                       strategy=name)

    elapsed = time.time() - t0
    print(f"\n✅ 迁移完成: {len(csv_files)} 只股票, {total} 条K线, {elapsed:.1f}s")
    print(f"   数据库: {db_stats()}")


def cmd_account(args):
    aid = int(args[0]) if args else 1
    acct = get_account(aid)
    if not acct:
        print(f"账户 {aid} 不存在")
        return
    holdings = get_holdings(aid)
    # 算持仓市值
    total_mv = 0
    for code, h in holdings.items():
        kl = get_kline(code, limit=1)
        if kl:
            mv = h["shares"] * kl[0]["close"]
            total_mv += mv
    total = acct["cash"] + total_mv
    print(f"=== 账户 {aid}: {acct['name']} ===")
    print(f"  现金:     ¥{fmt(acct['cash'])}")
    print(f"  持仓市值: ¥{fmt(total_mv)}")
    print(f"  总资产:   ¥{fmt(total)}")
    print(f"  初始资金: ¥{fmt(acct['initial_capital'])}")
    ret = (total - acct["initial_capital"]) / acct["initial_capital"] * 100
    print(f"  收益率:   {ret:+.2f}%")
    print(f"  持仓数:   {len(holdings)} 只")
    print(f"  策略:     {acct['strategy']}")


def cmd_holdings(args):
    aid = int(args[0]) if args else 1
    holdings = get_holdings(aid)
    if not holdings:
        print("空仓")
        return
    acct = get_account(aid)
    total_mv = 0
    rows = []
    for code, h in holdings.items():
        kl = get_kline(code, limit=1)
        price = kl[0]["close"] if kl else 0
        mv = h["shares"] * price
        total_mv += mv
        pnl = (price - h["cost_price"]) / h["cost_price"] * 100 if h["cost_price"] > 0 else 0
        rows.append((code, h.get("name", ""), h["shares"], h["cost_price"], price, mv, pnl))
    total = acct["cash"] + total_mv if acct else total_mv
    print(f"{'代码':<8} {'名称':<10} {'持仓':>6} {'成本':>8} {'现价':>8} {'市值':>10} {'盈亏':>8}")
    print("-" * 65)
    for r in sorted(rows, key=lambda x: -x[5]):
        print(f"{r[0]:<8} {r[1]:<10} {r[2]:>6} {r[3]:>8.2f} {r[4]:>8.2f} ¥{r[5]:>9,.0f} {r[6]:>+7.2f}%")
    print("-" * 65)
    print(f"{'合计':<26} {'':>8} {'':>8} ¥{total_mv:>9,.0f}")
    print(f"现金: ¥{fmt(acct['cash'])}  总资产: ¥{fmt(total)}")


def cmd_buy(args):
    if len(args) < 3:
        print("用法: cli.py buy <code> <shares> <price> [account_id] [reason]")
        return
    code = args[0].zfill(6)
    shares = int(args[1])
    price = float(args[2])
    aid = int(args[3]) if len(args) > 3 else 1
    reason = args[4] if len(args) > 4 else "手动买入"
    amount = shares * price
    acct = get_account(aid)
    if not acct:
        print(f"账户 {aid} 不存在")
        return
    if acct["cash"] < amount:
        print(f"现金不足: 需要 ¥{fmt(amount)}, 可用 ¥{fmt(acct['cash'])}")
        return
    # 更新持仓
    holdings = get_holdings(aid)
    if code in holdings:
        h = holdings[code]
        new_shares = h["shares"] + shares
        new_cost = (h["cost_price"] * h["shares"] + price * shares) / new_shares
        upsert_holding(aid, code, h.get("name", code), new_shares, round(new_cost, 4))
    else:
        name_map = get_stock_name_map()
        name = name_map.get(code, code)
        upsert_holding(aid, code, name, shares, price)
    # 扣现金
    update_cash(aid, delta=-amount)
    # 记录交易
    add_trade(aid, code, name_map.get(code, code), "BUY", shares, price, amount, reason)
    print(f"✅ 买入 {code} {shares}股 @ {price:.2f} = ¥{fmt(amount)} (账户{aid})")


def cmd_sell(args):
    if len(args) < 3:
        print("用法: cli.py sell <code> <shares> <price> [account_id] [reason]")
        return
    code = args[0].zfill(6)
    shares = int(args[1])
    price = float(args[2])
    aid = int(args[3]) if len(args) > 3 else 1
    reason = args[4] if len(args) > 4 else "手动卖出"
    holdings = get_holdings(aid)
    if code not in holdings:
        print(f"未持有 {code}")
        return
    h = holdings[code]
    if h["shares"] < shares:
        print(f"持仓不足: 持有 {h['shares']} 股, 尝试卖出 {shares} 股")
        return
    amount = shares * price
    # 更新持仓
    remaining = h["shares"] - shares
    if remaining == 0:
        delete_holding(aid, code)
    else:
        upsert_holding(aid, code, h.get("name", code), remaining, h["cost_price"])
    # 加现金
    update_cash(aid, delta=amount)
    # 记录交易
    add_trade(aid, code, h.get("name", code), "SELL", shares, price, amount, reason)
    pnl = (price - h["cost_price"]) / h["cost_price"] * 100
    print(f"✅ 卖出 {code} {shares}股 @ {price:.2f} = ¥{fmt(amount)} (盈亏 {pnl:+.2f}%) (账户{aid})")


def cmd_trades(args):
    aid = int(args[0]) if args else 1
    trades = get_trades(aid, limit=30)
    if not trades:
        print("无交易记录")
        return
    print(f"{'时间':<20} {'代码':<8} {'名称':<10} {'操作':<6} {'数量':>6} {'价格':>8} {'金额':>10} {'原因'}")
    print("-" * 85)
    for t in trades:
        print(f"{t['created_at']:<20} {t['code']:<8} {t['name']:<10} {t['action']:<6} "
              f"{t['shares']:>6} {t['price']:>8.2f} ¥{t['amount']:>9,.0f} {t['reason']}")


def cmd_kline(args):
    if not args:
        print("用法: cli.py kline <code> [limit]")
        return
    code = args[0].zfill(6)
    limit = int(args[1]) if len(args) > 1 else 20
    rows = get_kline(code, limit=limit)
    if not rows:
        print(f"无 {code} 数据")
        return
    print(f"{'日期':<12} {'开':>8} {'高':>8} {'低':>8} {'收':>8} {'量':>12} {'额':>12}")
    print("-" * 70)
    for r in rows:
        print(f"{r['date']:<12} {r['open']:>8.2f} {r['high']:>8.2f} {r['low']:>8.2f} "
              f"{r['close']:>8.2f} {r['volume']:>12,.0f} {r['amount']:>12,.0f}")


def cmd_stats(args):
    stats = db_stats()
    print("=== 数据库统计 ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


COMMANDS = {
    "init": cmd_init,
    "migrate": cmd_migrate,
    "account": cmd_account,
    "holdings": cmd_holdings,
    "buy": cmd_buy,
    "sell": cmd_sell,
    "trades": cmd_trades,
    "kline": cmd_kline,
    "stats": cmd_stats,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    COMMANDS[cmd](sys.argv[2:])

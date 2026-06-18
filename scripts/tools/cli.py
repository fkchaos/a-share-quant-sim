"""
手动操作 CLI — 账户管理 / 持仓管理 / 买卖操作

用法（直接复制粘贴即可）:
  python scripts/tools/cli.py                                                                  # 帮助
  python scripts/tools/cli.py init                                                            # 初始化数据库
  python scripts/tools/cli.py account                                                           # 查看账户概览
  python scripts/tools/cli.py account 2                                                        # 查看账户2
  python scripts/tools/cli.py holdings                                                          # 查看持仓
  python scripts/tools/cli.py buy    600519 100 1500.0                                        # 买入
  python scripts/tools/cli.py sell   600519 100 1600.0                                        # 卖出
  python scripts tools/cli.py adjust  --account 2 --cash 50000                                # 调现金
  python scripts tools/cli.py adjust  --account 2 --add-stock 600519 100 1500.0               # 增仓
  python scripts/tools/cli.py adjust  --account 2 --del-stock 600519                          # 清仓某只
  python scripts/tools/cli.py new-account --id 4 --name "v28" --cash 100000 --strategy v28   # 新建账户
  python scripts tools/cli.py del-account --id 4                                                # 删除账户
  python scripts tools/cli.py trades  1                                                         # 最近30条交易记录
  python scripts/tools/cli.py clear-holdings --account 2                                       # 全部清仓
  python scripts tools/cli.py kline   600519 20                                                # 最近20日K线
  python scripts/tools/cli.py stats                                                            # 数据库统计

所有命令都可以加 --help 看帮助。
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


# ── 账户管理 ──

def cmd_init(args):
    """初始化数据库 + 股票池 + 账户（完整初始化）"""
    init_db()
    print("✅ 数据库已初始化")
    print()
    print("下一步: 运行 python scripts/tools/init_project.py 完成完整初始化")


def cmd_account(args):
    """查看账户概览: cli.py account [account_id]"""
    aid = int(args[0]) if args else 1
    acct = get_account(aid)
    if not acct:
        print(f"账户 {aid} 不存在")
        # 显示所有账户
        print("\n所有账户:")
        for aid_check in [1, 2, 3, 4, 5]:
            a = get_account(aid_check)
            if a:
                print(f"  id={aid_check} name={a['name']} cash={fmt(a['cash'])} strategy={a.get('strategy','')}")
        return
    holdings = get_holdings(aid)
    total_mv = 0
    for code, h in holdings.items():
        kl = get_kline(code, limit=1)
        if kl:
            mv = h["shares"] * kl[0]["close"]
            total_mv += mv
    total = acct["cash"] + total_mv
    ret = (total - acct["initial_capital"]) / acct["initial_capital"] * 100
    print(f"=== 账户 {aid}: {acct['name']} ===")
    print(f"  现金:     ¥{fmt(acct['cash'])}")
    print(f"  持仓市值: ¥{fmt(total_mv)}")
    print(f"  总资产:   ¥{fmt(total)}")
    print(f"  初始资金: ¥{fmt(acct['initial_capital'])}")
    print(f"  收益率:   {ret:+.2f}%")
    print(f"  持仓数:   {len(holdings)} 只")
    print(f"  策略:     {acct.get('strategy','')}")


def cmd_new_account(args):
    """新建账户: cli.py new-account --id 4 --name v28 --cash 100000 --strategy v28"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cash", type=float, required=True)
    parser.add_argument("--strategy", default="")
    p = parser.parse_args(args)
    existing = get_account(p.id)
    if existing:
        print(f"❌ 账户 {p.id} 已存在 (name={existing['name']})")
        return
    upsert_account(p.id, name=p.name, cash=p.cash, initial_capital=p.cash,
                   strategy=p.strategy)
    print(f"✅ 新账户: id={p.id} name={p.name} cash={fmt(p.cash)} strategy={p.strategy}")


def cmd_del_account(args):
    """删除账户: cli.py del-account --id 4"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, required=True)
    p = parser.parse_args(args)
    acct = get_account(p.id)
    if not acct:
        print(f"❌ 账户 {p.id} 不存在")
        return
    # 检查是否有持仓
    holdings = get_holdings(p.id)
    if holdings:
        print(f"⚠️ 账户 {p.id} 还有 {len(holdings)} 只持仓，先执行 clear-holdings --account {p.id}")
        return
    import sqlite3
    db_path = os.path.join(os.environ.get("BACKTEST_DATA_DIR", "/root/data"), "quant.db")
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM account WHERE id=?", (p.id,))
    conn.commit()
    conn.close()
    print(f"✅ 已删除账户 {p.id} ({acct['name']})")


# ── 持仓管理 ──

def cmd_holdings(args):
    """查看持仓: cli.py holdings [account_id]"""
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
    if acct:
        print(f"现金: ¥{fmt(acct['cash'])}  总资产: ¥{fmt(total)}")


def cmd_adjust(args):
    """
    调整账户: cli.py adjust [options]

    选项:
      --account ID          账户ID (默认1)
      --cash AMOUNT         设置现金余额（直接覆盖）
      --add-stock CODE SHARES PRICE   增加持仓（不检查现金，直接写入）
      --del-stock CODE      清除某只持仓（不清算盈亏，直接从持仓删除）

    示例:
      cli.py adjust --account 2 --cash 50000            # 把账户2现金设为5万
      cli.py adjust --account 2 --add-stock 600519 100 1500  # 账户2加100股茅台，成本1500
      cli.py adjust --account 2 --del-stock 600519       # 账户2清掉茅台
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", type=int, default=1)
    parser.add_argument("--cash", type=float, default=None)
    parser.add_argument("--add-stock", nargs=3, metavar=("CODE", "SHARES", "PRICE"), default=None)
    parser.add_argument("--del-stock", type=str, default=None)
    p = parser.parse_args(args)

    acct = get_account(p.account)
    if not acct:
        print(f"❌ 账户 {p.account} 不存在")
        return

    if p.cash is not None:
        old_cash = acct["cash"]
        delta = p.cash - old_cash
        update_cash(p.account, delta=delta)
        print(f"  现金: ¥{fmt(old_cash)} → ¥{fmt(p.cash)} (Δ{delta:+,.0f})")

    if p.add_stock:
        code = p.add_stock[0].zfill(6)
        shares = int(p.add_stock[1])
        price = float(p.add_stock[2])
        name_map = get_stock_name_map()
        name = name_map.get(code, code)
        holdings = get_holdings(p.account)
        if code in holdings:
            h = holdings[code]
            new_shares = h["shares"] + shares
            new_cost = (h["cost_price"] * h["shares"] + price * shares) / new_shares
            upsert_holding(p.account, code, name, new_shares, round(new_cost, 4))
        else:
            upsert_holding(p.account, code, name, shares, price)
        add_trade(p.account, code, name, "BUY", shares, price, shares * price, "手动调仓")
        print(f"  加仓: {code} {name} +{shares}股 @ {price:.2f}")

    if p.del_stock:
        code = p.del_stock.zfill(6)
        holdings = get_holdings(p.account)
        if code in holdings:
            h = holdings[code]
            add_trade(p.account, code, h.get("name", code), "SELL", h["shares"], 0, 0, "手动清仓")
            delete_holding(p.account, code)
            print(f"  清仓: {code} {h.get('name', code)} ({h['shares']}股)")
        else:
            print(f"  未持有 {code}")


def cmd_clear_holdings(args):
    """全部清仓: cli.py clear-holdings --account 2"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", type=int, default=1)
    p = parser.parse_args(args)
    holdings = get_holdings(p.account)
    if not holdings:
        print("已经是空仓")
        return
    for code, h in holdings.items():
        add_trade(p.account, code, h.get("name", code), "SELL", h["shares"], 0, 0, "手动清仓")
    clear_holdings(p.account)
    print(f"✅ 已清仓 {len(holdings)} 只股票（账户 {p.account}）")


# ── 买卖 ──

def cmd_buy(args):
    """
    手动买入: cli.py buy <code> <shares> <price> [account_id] [reason]

    示例:
      cli.py buy 600519 100 1500.0          # 账户1买入茅台100股@1500
      cli.py buy 600519 100 1500.0 2        # 账户2买入
    """
    if len(args) < 3:
        print("用法: cli.py buy <code> <shares> <price> [account_id] [reason]")
        print("示例: cli.py buy 600519 100 1500.0")
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
    holdings = get_holdings(aid)
    name_map = get_stock_name_map()
    if code in holdings:
        h = holdings[code]
        new_shares = h["shares"] + shares
        new_cost = (h["cost_price"] * h["shares"] + price * shares) / new_shares
        upsert_holding(aid, code, h.get("name", code), new_shares, round(new_cost, 4))
    else:
        name = name_map.get(code, code)
        upsert_holding(aid, code, name, shares, price)
    update_cash(aid, delta=-amount)
    add_trade(aid, code, name_map.get(code, code), "BUY", shares, price, amount, reason)
    print(f"✅ 买入 {code} {shares}股 @ {price:.2f} = ¥{fmt(amount)} (账户{aid})")


def cmd_sell(args):
    """
    手动卖出: cli.py sell <code> <shares> <price> [account_id] [reason]

    示例:
      cli.py sell 600519 100 1600.0          # 账户1卖出茅台100股@1600
      cli.py sell 600519 50 1600.0 2         # 账户2卖出50股
    """
    if len(args) < 3:
        print("用法: cli.py sell <code> <shares> <price> [account_id] [reason]")
        print("示例: cli.py sell 600519 100 1600.0")
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
    remaining = h["shares"] - shares
    if remaining == 0:
        delete_holding(aid, code)
    else:
        upsert_holding(aid, code, h.get("name", code), remaining, h["cost_price"])
    update_cash(aid, delta=amount)
    add_trade(aid, code, h.get("name", code), "SELL", shares, price, amount, reason)
    pnl = (price - h["cost_price"]) / h["cost_price"] * 100
    print(f"✅ 卖出 {code} {shares}股 @ {price:.2f} = ¥{fmt(amount)} (盈亏 {pnl:+.2f}%) (账户{aid})")


# ── 交易记录 ──

def cmd_trades(args):
    """查看交易记录: cli.py trades [account_id] [limit]"""
    aid = int(args[0]) if args else 1
    limit = int(args[1]) if len(args) > 1 else 30
    trades = get_trades(aid, limit=limit)
    if not trades:
        print("无交易记录")
        return
    print(f"{'时间':<20} {'代码':<8} {'名称':<10} {'操作':<6} {'数量':>6} {'价格':>8} {'金额':>10} {'原因'}")
    print("-" * 85)
    for t in trades:
        print(f"{t['created_at']:<20} {t['code']:<8} {t['name']:<10} {t['action']:<6} "
              f"{t['shares']:>6} {t['price']:>8.2f} ¥{t['amount']:>9,.0f} {t['reason']}")


# ── K线 ──

def cmd_kline(args):
    """查看K线: cli.py kline <code> [limit]"""
    if not args:
        print("用法: cli.py kline <code> [limit]")
        return
    code = args[0].zfill(6)
    limit = int(args[1]) if len(args) > 1 else 20
    rows = get_kline(code, limit=limit)
    if not rows:
        print(f"无 {code} 数据")
        return
    print(f"{'日期':<12} {'开':>8} {'高':>8} {'低':<8} {'收':>8} {'量':>12} {'额':>12}")
    print("-" * 70)
    for r in rows:
        print(f"{r['date']:<12} {r['open']:>8.2f} {r['high']:>8.2f} {r['low']:>8.2f} "
              f"{r['close']:>8.2f} {r['volume']:>12,.0f} {r['amount']:>12,.0f}")


# ── 统计 ──

def cmd_stats(args):
    """数据库统计: cli.py stats"""
    stats = db_stats()
    print("=== 数据库统计 ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


# ── 迁移 ──

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
            df.columns = [c.strip().lower() for c in df.columns]
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
            if "name" in df.columns:
                name_map[code] = str(df["name"].iloc[0])
        except Exception as e:
            pass
        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(csv_files)} 只, {total} 条K线, {time.time()-t0:.1f}s")
    from core.db import upsert_stock
    for code, name in name_map.items():
        upsert_stock(code, name=name)
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
    for aid, name, cash in [(1, "v11b", 200000), (2, "v13", 100000), (3, "v20", 100000)]:
        upsert_account(aid, name=name, cash=cash, initial_capital=cash, strategy=name)
    elapsed = time.time() - t0
    print(f"\n✅ 迁移完成: {len(csv_files)} 只股票, {total} 条K线, {elapsed:.1f}s")
    print(f"   数据库: {db_stats()}")


COMMANDS = {
    "init": cmd_init,
    "migrate": cmd_migrate,
    "account": cmd_account,
    "new-account": cmd_new_account,
    "del-account": cmd_del_account,
    "holdings": cmd_holdings,
    "adjust": cmd_adjust,
    "clear-holdings": cmd_clear_holdings,
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

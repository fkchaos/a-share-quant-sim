"""
数据库层 — SQLite
- daily_kline: 股票日K线
- stock_pool: 股票池（中证800等）
- account: 账户（现金、初始资金）
- holdings: 持仓
- trade_log: 交易记录
- indicators: 技术指标（按需计算存储）
"""
import sqlite3
import os
import json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime

DB_PATH = os.environ.get("QUANT_DB", "/root/data/quant.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """建表（幂等）"""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_pool (
                code        TEXT PRIMARY KEY,
                name        TEXT NOT NULL DEFAULT '',
                board       TEXT NOT NULL DEFAULT '',
                pool        TEXT NOT NULL DEFAULT 'zz800',
                is_active   INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS daily_kline (
                code    TEXT NOT NULL,
                date    TEXT NOT NULL,
                open    REAL,
                high    REAL,
                low     REAL,
                close   REAL,
                volume  REAL,
                amount  REAL,
                PRIMARY KEY (code, date)
            ) WITHOUT ROWID;

            CREATE INDEX IF NOT EXISTS idx_kline_date ON daily_kline(date);
            CREATE INDEX IF NOT EXISTS idx_kline_code ON daily_kline(code);

            CREATE TABLE IF NOT EXISTS account (
                id              INTEGER PRIMARY KEY,
                name            TEXT NOT NULL DEFAULT 'main',
                cash            REAL NOT NULL DEFAULT 0,
                initial_capital REAL NOT NULL DEFAULT 200000,
                strategy        TEXT NOT NULL DEFAULT '',
                params_json     TEXT NOT NULL DEFAULT '{}',
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS holdings (
                account_id  INTEGER NOT NULL DEFAULT 1,
                code        TEXT NOT NULL,
                name        TEXT NOT NULL DEFAULT '',
                shares      INTEGER NOT NULL DEFAULT 0,
                cost_price  REAL NOT NULL DEFAULT 0,
                tp_taken    TEXT NOT NULL DEFAULT '[]',
                added_at    TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (account_id, code),
                FOREIGN KEY (account_id) REFERENCES account(id)
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS trade_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  INTEGER NOT NULL DEFAULT 1,
                code        TEXT NOT NULL,
                name        TEXT NOT NULL DEFAULT '',
                action      TEXT NOT NULL,  -- BUY / SELL
                shares      INTEGER NOT NULL,
                price       REAL NOT NULL,
                amount      REAL NOT NULL,
                reason      TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trade_account ON trade_log(account_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_trade_code ON trade_log(code);

            CREATE TABLE IF NOT EXISTS indicators (
                code        TEXT NOT NULL,
                date        TEXT NOT NULL,
                ind_name    TEXT NOT NULL,
                value       REAL,
                PRIMARY KEY (code, date, ind_name)
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS industry_map (
                code        TEXT PRIMARY KEY,
                industry    TEXT NOT NULL DEFAULT '',
                industry_m  TEXT NOT NULL DEFAULT '',
                industry_s  TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            ) WITHOUT ROWID;

            CREATE INDEX IF NOT EXISTS idx_ind_name ON indicators(name, date);
        """)
    print(f"✅ 数据库初始化完成: {DB_PATH}")


# ── 股票池 ─────────────────────────────────────────────────

def upsert_stock(code, name="", board="", pool="zz800"):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stock_pool(code,name,board,pool) VALUES(?,?,?,?)",
            (code, name, board, pool),
        )


def get_stock_pool(pool="zz800", active_only=True):
    with get_conn() as conn:
        sql = "SELECT code, name, board FROM stock_pool WHERE pool=?"
        params = [pool]
        if active_only:
            sql += " AND is_active=1"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_stock_name_map(pool="zz800"):
    """返回 {code: name} 映射"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT code, name FROM stock_pool WHERE pool=?", (pool,)
        ).fetchall()
        return {r["code"]: r["name"] for r in rows}


# ── 日K线 ──────────────────────────────────────────────────

def upsert_kline(code, date, open_, high, low, close, volume, amount):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume,amount)
               VALUES(?,?,?,?,?,?,?,?)""",
            (code, date, open_, high, low, close, volume, amount),
        )


def upsert_kline_batch(records):
    """批量写入 [(code,date,open,high,low,close,volume,amount), ...]"""
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume,amount)
               VALUES(?,?,?,?,?,?,?,?)""",
            records,
        )


def get_kline(code, limit=None, start_date=None, end_date=None):
    """返回某只股票的日K线，按日期升序"""
    with get_conn() as conn:
        sql = "SELECT * FROM daily_kline WHERE code=?"
        params = [code]
        if start_date:
            sql += " AND date>=?"
            params.append(start_date)
        if end_date:
            sql += " AND date<=?"
            params.append(end_date)
        if limit:
            sql += " ORDER BY date DESC LIMIT ?"
            params.append(int(limit))
        else:
            sql += " ORDER BY date ASC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_kline_latest(code):
    """返回最新一天的数据"""
    rows = get_kline(code, limit=1)
    return rows[0] if rows else None


def get_all_codes():
    """返回所有有K线的股票代码"""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT code FROM daily_kline ORDER BY code").fetchall()
        return [r["code"] for r in rows]


def get_latest_date():
    """返回数据库中最新的交易日"""
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(date) as d FROM daily_kline").fetchone()
        return row["d"] if row else None


def get_kline_df(codes=None, start_date=None):
    """返回 DataFrame 格式，兼容现有代码"""
    import pandas as pd
    with get_conn() as conn:
        sql = "SELECT * FROM daily_kline WHERE 1=1"
        params = []
        if codes:
            placeholders = ",".join("?" * len(codes))
            sql += f" AND code IN ({placeholders})"
            params.extend(codes)
        if start_date:
            sql += " AND date>=?"
            params.append(start_date)
        sql += " ORDER BY code, date"
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        return df


# ── 账户 ───────────────────────────────────────────────────

def get_account(account_id=1):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM account WHERE id=?", (account_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json", "{}"))
        return d


def upsert_account(account_id=1, name="main", cash=200000, initial_capital=200000,
                   strategy="", params=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO account(id,name,cash,initial_capital,strategy,params_json,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (account_id, name, cash, initial_capital, strategy,
             json.dumps(params or {}),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def update_cash(account_id=1, cash=None, delta=None):
    """更新现金：直接设值 或 增减"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        if cash is not None:
            conn.execute("UPDATE account SET cash=?, updated_at=? WHERE id=?",
                         (cash, now_str, account_id))
        elif delta is not None:
            conn.execute("UPDATE account SET cash=cash+?, updated_at=? WHERE id=?",
                         (delta, now_str, account_id))


# ── 持仓 ───────────────────────────────────────────────────

def get_holdings(account_id=1):
    """返回 {code: {name, shares, cost_price, ...}}"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE account_id=?", (account_id,)
        ).fetchall()
        return {r["code"]: dict(r) for r in rows}


def upsert_holding(account_id, code, name, shares, cost_price):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO holdings(account_id,code,name,shares,cost_price,added_at)
               VALUES(?,?,?,?,?,COALESCE((SELECT added_at FROM holdings WHERE account_id=? AND code=?), ?))""",
            (account_id, code, name, shares, cost_price, account_id, code,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def delete_holding(account_id, code):
    with get_conn() as conn:
        conn.execute("DELETE FROM holdings WHERE account_id=? AND code=?",
                     (account_id, code))


def clear_holdings(account_id=1):
    with get_conn() as conn:
        conn.execute("DELETE FROM holdings WHERE account_id=?", (account_id,))


# ── 交易记录 ───────────────────────────────────────────────

def add_trade(account_id, code, name, action, shares, price, amount, reason=""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trade_log(account_id,code,name,action,shares,price,amount,reason,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (account_id, code, name, action, shares, price, amount, reason,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def get_trades(account_id=1, code=None, limit=50):
    with get_conn() as conn:
        sql = "SELECT * FROM trade_log WHERE account_id=?"
        params = [account_id]
        if code:
            sql += " AND code=?"
            params.append(code)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ── 技术指标 ───────────────────────────────────────────────

def upsert_indicator(code, date, name, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO indicators(code,date,name,value) VALUES(?,?,?,?)",
            (code, date, name, value),
        )


def upsert_indicator_batch(records):
    """批量写入 [(code,date,name,value), ...]"""
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO indicators(code,date,name,value) VALUES(?,?,?,?)",
            records,
        )


def get_indicator(code, name, limit=None):
    with get_conn() as conn:
        sql = "SELECT date, value FROM indicators WHERE code=? AND name=? ORDER BY date DESC"
        params = [code, name]
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_indicators_multi(code, names, date=None):
    """一次查多个指标，返回 {name: value}"""
    with get_conn() as conn:
        placeholders = ",".join("?" * len(names))
        sql = f"SELECT name, value FROM indicators WHERE code=? AND name IN ({placeholders})"
        params = [code] + list(names)
        if date:
            sql += " AND date=?"
            params.append(date)
        else:
            sql += " AND date=(SELECT MAX(date) FROM indicators WHERE code=?)"
            params.append(code)
        rows = conn.execute(sql, params).fetchall()
        return {r["name"]: r["value"] for r in rows}


# ── 统计 ───────────────────────────────────────────────────

def db_stats():
    with get_conn() as conn:
        tables = ["stock_pool", "daily_kline", "account", "holdings", "trade_log", "indicators"]
        stats = {}
        for t in tables:
            row = conn.execute(f"SELECT COUNT(*) as n FROM {t}").fetchone()
            stats[t] = row["n"]
        stats["db_size_mb"] = round(
            os.path.getsize(DB_PATH) / 1024 / 1024, 2
        ) if os.path.exists(DB_PATH) else 0
        return stats


# ── 模拟盘兼容层 ────────────────────────────────────────────
# 让现有 sim 脚本只改几行 import 就能从 DB 读写

def load_account_for_sim(account_id=1):
    """
    返回 (state_dict, loaded)
    state_dict 格式兼容现有 sim 脚本:
      {cash, initial_capital, holdings: {code: {shares, cost_price, name, ...}}, trade_log}
    """
    from core.account import PortfolioState

    acct = get_account(account_id)
    if not acct:
        return None, False

    holdings = get_holdings(account_id)
    # 标准化 holdings 格式
    holdings_out = {}
    for code, h in holdings.items():
        holdings_out[code] = {
            "shares": h["shares"],
            "cost_price": h["cost_price"],
            "name": h.get("name", code),
            "tp_taken": json.loads(h.get("tp_taken", "[]")),
        }

    state = PortfolioState(
        cash=acct["cash"],
        initial_capital=acct["initial_capital"],
        holdings=holdings_out,
        trade_log=[],
    )
    return state, True


def save_account_for_sim(state, account_id=1):
    """从 sim 的 PortfolioState 写回 DB"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 更新账户现金
    with get_conn() as conn:
        conn.execute(
            "UPDATE account SET cash=?, updated_at=? WHERE id=?",
            (state.cash, now_str, account_id),
        )
    # 清空并重建持仓
    clear_holdings(account_id)
    # 从 stock_pool 获取名称
    name_map = get_stock_name_map()
    for code, h in state.holdings.items():
        name = h.get("name", "") if isinstance(h, dict) else ""
        if not name or name == code:
            name = name_map.get(code, code)
        tp = h.get("tp_taken", [])
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO holdings(account_id,code,name,shares,cost_price,tp_taken) VALUES(?,?,?,?,?,?)",
                (account_id, code, name, int(h["shares"]), float(h["cost_price"]),
                 json.dumps(tp) if isinstance(tp, list) else str(tp)),
            )
    # 写入 trade_log
    if state.trade_log:
        with get_conn() as conn:
            for t in state.trade_log:
                code = t.get("code", "")
                name = t.get("name", "") or name_map.get(code, code)
                action = t.get("action", "")
                shares = t.get("shares", 0)
                price = t.get("price", 0)
                amount = t.get("amount", 0)
                reason = t.get("reason", "")
                trade_date = t.get("date", "")
                conn.execute(
                    "INSERT INTO trade_log(account_id,code,name,action,shares,price,amount,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (account_id, code, name, action, shares, price, amount, reason, trade_date),
                )


def load_kline_for_sim(codes=None, lookback=250):
    """
    从 DB 加载日K线，返回 {code: DataFrame} 格式（兼容现有 sim 脚本）
    DataFrame 列: open, high, low, close, volume, amount，index=date
    """
    import pandas as pd

    result = {}
    with get_conn() as conn:
        if codes:
            placeholders = ",".join("?" * len(codes))
            sql = f"SELECT * FROM daily_kline WHERE code IN ({placeholders}) ORDER BY code, date"
            rows = conn.execute(sql, codes).fetchall()
        else:
            rows = conn.execute("SELECT * FROM daily_kline ORDER BY code, date").fetchall()

    if not rows:
        return result

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])

    for code, grp in df.groupby("code"):
        grp = grp.set_index("date").sort_index()
        if lookback and len(grp) > lookback:
            grp = grp.tail(lookback)
        result[code] = grp

    return result


def get_latest_trade_date():
    """返回数据库中最新的交易日"""
    return get_latest_date()


def load_panel_from_db(start_date=None, end_date=None,
                       need_open=False, need_hl=False, pool="zz800"):
    """
    从数据库加载日K线，构建回测 panel（兼容 core/data.py 的 load_and_build_panel 返回格式）

    Returns
    -------
    tuple: (close_panel, volume_panel, amount_panel[, open_panel[, high_panel, low_panel]])
    list: stock codes
    """
    import pandas as pd

    from core.config import MarketFilter

    with get_conn() as conn:
        # 获取股票池
        pool_rows = conn.execute(
            "SELECT code FROM stock_pool WHERE pool=? AND is_active=1", (pool,)
        ).fetchall()
        pool_codes = [r["code"] for r in pool_rows]

        # 构建查询
        sql = "SELECT code, date, open, high, low, close, volume, amount FROM daily_kline WHERE 1=1"
        params = []
        if start_date:
            sql += " AND date>=?"
            params.append(start_date)
        if end_date:
            sql += " AND date<=?"
            params.append(end_date)

        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return tuple(pd.DataFrame() for _ in range(3 + int(need_open) + int(need_hl)*2)), []

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])

    # 过滤股票池：优先用 stock_pool 表，再叠加 MarketFilter 排除规则
    if pool_codes:
        df = df[df["code"].isin(pool_codes)]
    # 无论是否有 stock_pool，都应用 exclude_prefixes 和退市过滤
    mf = MarketFilter()
    if mf.exclude_prefixes:
        for prefix in mf.exclude_prefixes:
            df = df[~df["code"].str.startswith(prefix)]
    # 退市/停牌过滤：用 DB 里最后交易日期判断
    if mf.exclude_delisted and end_date:
        from datetime import datetime, timedelta
        cutoff = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=mf.delist_max_gap)).strftime("%Y-%m-%d")
        latest_dates = df.groupby("code")["date"].max()
        active_codes = latest_dates[latest_dates >= cutoff].index.tolist()
        df = df[df["code"].isin(active_codes)]

    # 构建 panel（date × code 的矩阵）
    codes = sorted(df["code"].unique())

    close_panel  = df.pivot(index="date", columns="code", values="close")
    volume_panel = df.pivot(index="date", columns="code", values="volume")
    amount_panel = df.pivot(index="date", columns="code", values="amount")

    # 用 close * volume 填充 amount 中的 NaN（部分老数据可能没有 amount）
    if amount_panel.isna().any().any():
        fill = close_panel * volume_panel
        amount_panel = amount_panel.fillna(fill)

    result = (close_panel, volume_panel, amount_panel)

    if need_open or need_hl:
        open_panel = df.pivot(index="date", columns="code", values="open")
        result += (open_panel,)
    if need_hl:
        high_panel = df.pivot(index="date", columns="code", values="high")
        low_panel  = df.pivot(index="date", columns="code", values="low")
        result += (high_panel, low_panel)

    return result, list(codes)


if __name__ == "__main__":
    init_db()
    print(db_stats())


def load_industry_map():
    """
    从 DB 加载行业分类映射。
    Returns: dict {code: industry_name}
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT code, industry FROM industry_map WHERE industry!=''").fetchall()
    if rows:
        return {r["code"]: r["industry"] for r in rows}
    return {}


def load_industry_table():
    """
    从 DB 加载完整行业分类表。
    Returns: DataFrame with columns [code, industry, industry_m, industry_s]
    """
    import pandas as pd
    with get_conn() as conn:
        rows = conn.execute("SELECT code, industry, industry_m, industry_s FROM industry_map").fetchall()
    if rows:
        return pd.DataFrame([dict(r) for r in rows]).set_index("code")
    return pd.DataFrame(columns=["code", "industry", "industry_m", "industry_s"])

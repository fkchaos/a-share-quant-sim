"""
数据库层 — SQLite（双库分离）

股票库 (quant_stocks.db)：
  - stock_pool: 股票池（中证800等）
  - daily_kline: 股票日K线
  - indicators: 技术指标
  - industry_map: 行业分类

账户库 (quant_accounts.db)：
  - account: 账户（现金、初始资金）
  - holdings: 持仓
  - trade_log: 交易记录

用法：与原来完全一致，from core.db import get_conn, init_db, ...
"""
import sqlite3
import os
import json
from contextlib import contextmanager
from datetime import datetime
from typing import Tuple, List, Union

import pandas as pd


def _default_db_dir():
    """默认DB目录：项目根目录下的 data/"""
    project_root = os.environ.get(
        "PROJECT_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(project_root, "data")


def _db_path(name):
    """返回指定DB文件的路径"""
    return os.path.join(_default_db_dir(), name)


# ── DB 文件路径 ──────────────────────────────────────────────

STOCKS_DB = os.environ.get("QUANT_STOCKS_DB", "") or _db_path("quant_stocks.db")
ACCOUNTS_DB = os.environ.get("QUANT_ACCOUNTS_DB", "") or _db_path("quant_accounts.db")

# 表名 → DB 文件映射
_STOCK_TABLES = {"stock_pool", "daily_kline", "index_kline", "indicators", "industry_map"}
_ACCOUNT_TABLES = {"account", "holdings", "trade_log"}


def _resolve_db(table_name):
    """根据表名返回对应的DB路径"""
    if table_name in _STOCK_TABLES:
        return STOCKS_DB
    if table_name in _ACCOUNT_TABLES:
        return ACCOUNTS_DB
    # 未知表：尝试从环境变量读取，否则默认 stocks
    return STOCKS_DB


def _extract_table(sql):
    """从SQL语句中提取第一个表名（简单启发式）"""
    import re
    # 匹配 FROM table, INTO table, UPDATE table, CREATE TABLE IF NOT EXISTS table, etc.
    patterns = [
        r'(?:FROM|INTO|UPDATE|TABLE|JOIN)\s+([a-z_]+)',
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)',
    ]
    sql_upper = sql.strip().upper()
    for pat in patterns:
        m = re.search(pat, sql_upper)
        if m:
            return m.group(1).lower()
    return None


@contextmanager
def get_conn(table_hint=None):
    """
    获取数据库连接。
    - table_hint: 表名提示，用于路由到正确的DB
    - 如果 table_hint 为 None，自动从 SQL 中提取（仅对简单查询有效）
    - 兼容旧代码：不传 table_hint 时默认用 stocks DB
    """
    if table_hint is None:
        table_hint = "stock_pool"  # 默认 stocks
    db_path = _resolve_db(table_hint)
    conn = sqlite3.connect(db_path)
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


def get_stocks_conn():
    """获取股票库连接"""
    return get_conn("stock_pool")


def get_accounts_conn():
    """获取账户库连接"""
    return get_conn("account")


def init_db():
    """建表（幂等）— 同时初始化两个库"""
    _init_stocks_db()
    _init_accounts_db()
    print(f"✅ 数据库初始化完成")
    print(f"   股票库: {STOCKS_DB}")
    print(f"   账户库: {ACCOUNTS_DB}")


def _init_stocks_db():
    with get_conn("stock_pool") as conn:
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

            CREATE TABLE IF NOT EXISTS index_kline (
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

            CREATE INDEX IF NOT EXISTS idx_ikline_date ON index_kline(date);
            CREATE INDEX IF NOT EXISTS idx_ikline_code ON index_kline(code);

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

            CREATE INDEX IF NOT EXISTS idx_ind_name ON indicators(ind_name, date);
        """)


def _init_accounts_db():
    with get_conn("account") as conn:
        conn.executescript("""
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
                action      TEXT NOT NULL,
                shares      INTEGER NOT NULL,
                price       REAL NOT NULL,
                amount      REAL NOT NULL,
                reason      TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trade_account ON trade_log(account_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_trade_code ON trade_log(code);
        """)


# ── 股票池 ─────────────────────────────────────────────────

def upsert_stock(code, name="", board="", pool="zz800"):
    with get_conn("stock_pool") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stock_pool(code,name,board,pool) VALUES(?,?,?,?)",
            (code, name, board, pool),
        )


def get_stock_pool(pool="zz800", active_only=True):
    with get_conn("stock_pool") as conn:
        sql = "SELECT code, name, board FROM stock_pool WHERE pool=?"
        params = [pool]
        if active_only:
            sql += " AND is_active=1"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_stock_name_map(pool="zz800"):
    """返回 {code: name} 映射"""
    with get_conn("stock_pool") as conn:
        rows = conn.execute(
            "SELECT code, name FROM stock_pool WHERE pool=?", (pool,)
        ).fetchall()
        return {r["code"]: r["name"] for r in rows}


# ── 日K线 ──────────────────────────────────────────────────

def upsert_kline(code, date, open_, high, low, close, volume, amount):
    with get_conn("daily_kline") as conn:
        conn.execute(
            """INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume,amount)
               VALUES(?,?,?,?,?,?,?,?)""",
            (code, date, open_, high, low, close, volume, amount),
        )


def upsert_kline_batch(records):
    """批量写入 [(code,date,open,high,low,close,volume,amount), ...]"""
    with get_conn("daily_kline") as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume,amount)
               VALUES(?,?,?,?,?,?,?,?)""",
            records,
        )


# ── 指数K线 ─────────────────────────────────────────────────

def upsert_index_batch(records):
    """批量写入 [(code,date,open,high,low,close,volume,amount), ...]"""
    with get_conn("index_kline") as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO index_kline(code,date,open,high,low,close,volume,amount)
               VALUES(?,?,?,?,?,?,?,?)""",
            records,
        )


def upsert_index(code, date, open_, high, low, close, volume, amount):
    with get_conn("index_kline") as conn:
        conn.execute(
            """INSERT OR REPLACE INTO index_kline(code,date,open,high,low,close,volume,amount)
               VALUES(?,?,?,?,?,?,?,?)""",
            (code, date, open_, high, low, close, volume, amount),
        )


def get_index_kline(code, limit=None, start_date=None, end_date=None):
    """返回某条指数的K线，按日期升序"""
    with get_conn("index_kline") as conn:
        sql = "SELECT * FROM index_kline WHERE code=?"
        params = [code]
        if start_date:
            sql += " AND date>=?"
            params.append(start_date)
        if end_date:
            sql += " AND date<=?"
            params.append(end_date)
        sql += " ORDER BY date"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return conn.execute(sql, params).fetchall()


def get_all_index_codes():
    """返回 index_kline 中所有不同的 code"""
    with get_conn("index_kline") as conn:
        rows = conn.execute("SELECT DISTINCT code FROM index_kline ORDER BY code").fetchall()
        return [r["code"] for r in rows]


def get_index_latest_date(code):
    """返回某条指数最新日期"""
    with get_conn("index_kline") as conn:
        row = conn.execute("SELECT MAX(date) as d FROM index_kline WHERE code=?", (code,)).fetchone()
        return row["d"] if row else None


# ── 股票日K线（继续）──────────────────────────────────────────

def get_kline(code, limit=None, start_date=None, end_date=None):
    """返回某只股票的日K线，按日期升序"""
    with get_conn("daily_kline") as conn:
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
    with get_conn("daily_kline") as conn:
        rows = conn.execute("SELECT DISTINCT code FROM daily_kline ORDER BY code").fetchall()
        return [r["code"] for r in rows]


def get_latest_date():
    """返回数据库中最新的交易日"""
    with get_conn("daily_kline") as conn:
        row = conn.execute("SELECT MAX(date) as d FROM daily_kline").fetchone()
        return row["d"] if row else None


def get_kline_df(codes=None, start_date=None):
    """返回 DataFrame 格式，兼容现有代码"""
    import pandas as pd
    with get_conn("daily_kline") as conn:
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
    with get_conn("account") as conn:
        row = conn.execute("SELECT * FROM account WHERE id=?", (account_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json", "{}"))
        return d


def upsert_account(account_id=1, name="main", cash=200000, initial_capital=200000,
                   strategy="", params=None):
    with get_conn("account") as conn:
        conn.execute(
            """INSERT OR REPLACE INTO account(id,name,cash,initial_capital,strategy,params_json,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (account_id, name, cash, initial_capital, strategy,
             json.dumps(params or {}),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def list_accounts():
    """返回所有账户列表 [{"id", "name", "strategy", "cash", "initial_capital"}, ...]"""
    with get_conn("account") as conn:
        rows = conn.execute(
            "SELECT id, name, strategy, cash, initial_capital, updated_at FROM account ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def create_account(account_id, name="", cash=100000, initial_capital=100000, strategy=""):
    """创建新账户（如果已存在则跳过）"""
    with get_conn("account") as conn:
        existing = conn.execute("SELECT id FROM account WHERE id=?", (account_id,)).fetchone()
        if existing:
            return False  # 已存在
        conn.execute(
            """INSERT INTO account(id,name,cash,initial_capital,strategy,params_json,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (account_id, name, cash, initial_capital, strategy,
             json.dumps({}),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        return True


def switch_strategy(account_id, strategy):
    """切换账户绑定的策略"""
    with get_conn("account") as conn:
        conn.execute(
            "UPDATE account SET strategy=?, updated_at=? WHERE id=?",
            (strategy, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), account_id),
        )
        return conn.total_changes > 0


def update_cash(account_id=1, cash=None, delta=None):
    """更新现金：直接设值 或 增减"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn("account") as conn:
        if cash is not None:
            conn.execute("UPDATE account SET cash=?, updated_at=? WHERE id=?",
                         (cash, now_str, account_id))
        elif delta is not None:
            conn.execute("UPDATE account SET cash=cash+?, updated_at=? WHERE id=?",
                         (delta, now_str, account_id))


# ── 持仓 ───────────────────────────────────────────────────

def get_holdings(account_id=1):
    """返回 {code: {name, shares, cost_price, ...}}"""
    with get_conn("holdings") as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE account_id=?", (account_id,)
        ).fetchall()
        return {r["code"]: dict(r) for r in rows}


def upsert_holding(account_id, code, name, shares, cost_price):
    with get_conn("holdings") as conn:
        conn.execute(
            """INSERT OR REPLACE INTO holdings(account_id,code,name,shares,cost_price,added_at)
               VALUES(?,?,?,?,?,COALESCE((SELECT added_at FROM holdings WHERE account_id=? AND code=?), ?))""",
            (account_id, code, name, shares, cost_price, account_id, code,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def delete_holding(account_id, code):
    with get_conn("holdings") as conn:
        conn.execute("DELETE FROM holdings WHERE account_id=? AND code=?",
                     (account_id, code))


def clear_holdings(account_id=1):
    with get_conn("holdings") as conn:
        conn.execute("DELETE FROM holdings WHERE account_id=?", (account_id,))


# ── 交易记录 ───────────────────────────────────────────────

def add_trade(account_id, code, name, action, shares, price, amount, reason=""):
    with get_conn("trade_log") as conn:
        conn.execute(
            """INSERT INTO trade_log(account_id,code,name,action,shares,price,amount,reason,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (account_id, code, name, action, shares, price, amount, reason,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def get_trades(account_id=1, code=None, limit=50):
    with get_conn("trade_log") as conn:
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
    with get_conn("indicators") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO indicators(code,date,name,value) VALUES(?,?,?,?)",
            (code, date, name, value),
        )


def upsert_indicator_batch(records):
    """批量写入 [(code,date,name,value), ...]"""
    with get_conn("indicators") as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO indicators(code,date,name,value) VALUES(?,?,?,?)",
            records,
        )


def get_indicator(code, name, limit=None):
    with get_conn("indicators") as conn:
        sql = "SELECT date, value FROM indicators WHERE code=? AND name=? ORDER BY date DESC"
        params = [code, name]
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_indicators_multi(code, names, date=None):
    """一次查多个指标，返回 {name: value}"""
    with get_conn("indicators") as conn:
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


# ── 行业分类 ───────────────────────────────────────────────

def upsert_industry(code, industry="", industry_m="", industry_s=""):
    with get_conn("industry_map") as conn:
        conn.execute(
            """INSERT OR REPLACE INTO industry_map(code,industry,industry_m,industry_s,updated_at)
               VALUES(?,?,?,?,?)""",
            (code, industry, industry_m, industry_s,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )


def get_industry_map():
    """返回 {code: {industry, industry_m, industry_s}}"""
    with get_conn("industry_map") as conn:
        rows = conn.execute("SELECT * FROM industry_map").fetchall()
        return {r["code"]: dict(r) for r in rows}


# ── 统计 ───────────────────────────────────────────────────

def db_stats():
    with get_conn("stock_pool") as conn:
        stock_tables = ["stock_pool", "daily_kline", "indicators", "industry_map"]
        stats = {}
        for t in stock_tables:
            row = conn.execute(f"SELECT COUNT(*) as n FROM {t}").fetchone()
            stats[t] = row["n"]
        stats["stocks_db_size_mb"] = round(
            os.path.getsize(STOCKS_DB) / 1024 / 1024, 2
        ) if os.path.exists(STOCKS_DB) else 0

    with get_conn("account") as conn:
        account_tables = ["account", "holdings", "trade_log"]
        for t in account_tables:
            row = conn.execute(f"SELECT COUNT(*) as n FROM {t}").fetchone()
            stats[t] = row["n"]
        stats["accounts_db_size_mb"] = round(
            os.path.getsize(ACCOUNTS_DB) / 1024 / 1024, 2
        ) if os.path.exists(ACCOUNTS_DB) else 0

    return stats


# ── 向后兼容 ────────────────────────────────────────────────

# 旧代码可能 import DB_PATH，保持兼容（指向 stocks DB）
DB_PATH = STOCKS_DB

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
    with get_conn("account") as conn:
        conn.execute(
            "UPDATE account SET cash=?, updated_at=? WHERE id=?",
            (state.cash, now_str, account_id),
        )
    clear_holdings(account_id)
    name_map = get_stock_name_map()
    for code, h in state.holdings.items():
        name = h.get("name", "") if isinstance(h, dict) else ""
        if not name or name == code:
            name = name_map.get(code, code)
        tp = h.get("tp_taken", [])
        with get_conn("holdings") as conn:
            conn.execute(
                "INSERT OR REPLACE INTO holdings(account_id,code,name,shares,cost_price,tp_taken) VALUES(?,?,?,?,?,?)",
                (account_id, code, name, int(h["shares"]), float(h["cost_price"]),
                 json.dumps(tp) if isinstance(tp, list) else str(tp)),
            )
    if state.trade_log:
        with get_conn("trade_log") as conn:
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
    with get_conn("daily_kline") as conn:
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


def load_panel_from_db(start_date=None, end_date=None, need_open=False, need_hl=False, pool="zz800") -> tuple[tuple[pd.DataFrame, ...], list[str]]:
    """
    从 SQLite 数据库加载日K线面板数据。

    参数:
        start_date: str — 起始日期 (默认: 数据库最早日期)
        end_date: str — 结束日期 (默认: 数据库最晚日期)
        need_open: bool — 是否包含 open_panel
        need_hl: bool — 是否包含 high_panel + low_panel
        pool: str — 股票池 (默认: "zz800")

    返回:
        tuple — (close_panel, volume_panel, amount_panel, [open_panel], [high_panel], [low_panel])
        list — 股票代码列表
    """
    import pandas as pd

    with get_conn("daily_kline") as conn:
        # 获取股票池
        if pool:
            pool_rows = conn.execute(
                "SELECT code FROM stock_pool WHERE pool=? AND is_active=1", (pool,)
            ).fetchall()
            pool_codes = [r["code"] for r in pool_rows]
        else:
            pool_codes = None

        # 查询日K线
        if pool_codes:
            placeholders = ",".join("?" * len(pool_codes))
            sql = f"SELECT code, date, open, high, low, close, volume, amount FROM daily_kline WHERE code IN ({placeholders})"
            params = list(pool_codes)
        else:
            sql = "SELECT code, date, open, high, low, close, volume, amount FROM daily_kline"
            params = []

        if start_date:
            sql += " AND date>=?"
            params.append(start_date)
        if end_date:
            sql += " AND date<=?"
            params.append(end_date)

        sql += " ORDER BY code, date"
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        empty = pd.DataFrame()
        return (empty, empty, empty), []

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"])

    # 构建面板
    close_panel = df.pivot(index="date", columns="code", values="close").sort_index()
    volume_panel = df.pivot(index="date", columns="code", values="volume").sort_index()
    amount_panel = df.pivot(index="date", columns="code", values="amount").sort_index()

    result = (close_panel, volume_panel, amount_panel)

    if need_open:
        open_panel = df.pivot(index="date", columns="code", values="open").sort_index()
        result += (open_panel,)

    if need_hl:
        high_panel = df.pivot(index="date", columns="code", values="high").sort_index()
        low_panel = df.pivot(index="date", columns="code", values="low").sort_index()
        result += (high_panel, low_panel)

    codes = sorted(df["code"].unique().tolist())
    return result, codes

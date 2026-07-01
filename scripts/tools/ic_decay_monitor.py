#!/usr/bin/env python3
"""
因子 IC 衰减监控脚本
每月运行一次，检测现有策略因子的 IC 是否衰减。
输出报告到 docs/ic_decay_report.md（追加当月记录）。
"""

import os
import sys
import json
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 项目根目录 (scripts/tools/ → 上溯两层)
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DOCS_DIR = os.path.join(PROJECT_DIR, "docs")
REPORT_FILE = os.path.join(DOCS_DIR, "ic_decay_report.db")

# 数据库路径
STOCK_DB = os.path.join(DATA_DIR, "quant_stocks.db")


def load_stock_panel():
    """从 quant_stocks.db 加载价量数据 panel"""
    conn = sqlite3.connect(STOCK_DB)
    cur = conn.cursor()

    # 检查表结构
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%kline%' OR name LIKE '%daily%')")
    tables = cur.fetchall()
    # 优先 daily_kline
    if tables:
        for t in tables:
            if 'daily_kline' in t[0]:
                tables = [t]
                break

    if not tables:
        print("[ERROR] 找不到日K线数据表")
        conn.close()
        return None

    table_name = tables[0][0]
    print(f"[INFO] 使用数据表: {table_name}")

    # 获取最近 252 个交易日的数据（全量股票）
    df = pd.read_sql_query(
        f"SELECT date, code, close, volume, amount, open, high, low "
        f"FROM {table_name} WHERE date >= date('now', '-1 year')",
        conn
    )
    conn.close()

    # 转换为 panel 格式
    df['date'] = pd.to_datetime(df['date'])
    dates = sorted(df['date'].unique())[-252:]  # 最近252天
    df = df[df['date'].isin(dates)]

    codes = sorted(df['code'].unique())
    date_index = pd.DatetimeIndex(dates)

    def make_panel(col):
        sub = df.pivot_table(index='date', columns='code', values=col)
        sub = sub.reindex(index=date_index, columns=codes)
        return sub.ffill().bfill()

    close = make_panel('close')
    volume = make_panel('volume')
    amount = make_panel('amount')
    high = make_panel('high')
    low = make_panel('low')
    open_ = make_panel('open')

    return close, volume, amount, high, low, open_


def calc_ic(forward_returns, factor_values):
    """计算 Spearman IC（横截面）"""
    # 对齐日期
    common_dates = forward_returns.index.intersection(factor_values.index)
    if len(common_dates) < 20:
        return np.nan

    ic_vals = []
    for date in common_dates:
        rets = forward_returns.loc[date]
        facs = factor_values.loc[date]
        # 去掉 NaN
        mask = rets.notna() & facs.notna()
        if mask.sum() < 10:
            continue
        # Spearman rank correlation
        from scipy.stats import spearmanr
        corr, _ = spearmanr(rets[mask], facs[mask])
        ic_vals.append(corr)

    return np.mean(ic_vals) if ic_vals else np.nan


def calc_factor_ic_decay(close, volume, amount, high, low, open_):
    """计算各因子的 IC 及衰减情况"""
    # 未来 5 日收益率作为因变量
    fwd_ret_5 = close.pct_change(5).shift(-5)

    results = {}

    # 1. 换手率因子 (负向)
    turnover = amount / (close * 100000000)  # 粗略换手率
    turnover_ma5 = turnover.rolling(5).mean()
    ic_turnover = calc_ic(fwd_ret_5, -turnover_ma5)  # 负向
    results['turnover_ma5_neg'] = {'IC': ic_turnover, 'direction': 'neg'}

    # 2. 市值因子 (负向)
    # 用 volume * close 估算市值
    mkt_cap = close * volume
    log_mkt_cap = np.log(mkt_cap + 1)
    ic_size = calc_ic(fwd_ret_5, -log_mkt_cap)  # 负向（小市值溢价）
    results['log_market_cap_neg'] = {'IC': ic_size, 'direction': 'neg'}

    # 3. 动量因子
    for window in [5, 10, 20]:
        mom = close.pct_change(window)
        ic_mom = calc_ic(fwd_ret_5, mom)
        results[f'mom_{window}'] = {'IC': ic_mom, 'direction': 'pos'}

    # 4. 波动率因子
    ret_daily = close.pct_change()
    for window in [5, 20]:
        vol = ret_daily.rolling(window).std()
        ic_vol = calc_ic(fwd_ret_5, vol)
        results[f'vol_{window}'] = {'IC': ic_vol, 'direction': 'pos'}

    # 5. 反转因子
    for window in [3, 5]:
        rev = close.pct_change(window)
        ic_rev = calc_ic(fwd_ret_5, -rev)  # 负向（反转）
        results[f'rev_{window}_neg'] = {'IC': ic_rev, 'direction': 'neg'}

    # 6. 量比因子
    vol_ma20 = volume.rolling(20).mean()
    vol_ratio = volume / vol_ma20
    ic_vol_ratio = calc_ic(fwd_ret_5, vol_ratio)
    results['vol_ratio'] = {'IC': ic_vol_ratio, 'direction': 'pos'}

    return results


def run_ic_decay_check():
    """执行 IC 衰减检查并输出报告"""
    print("=" * 60)
    print("因子 IC 衰减监控")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 加载数据
    panels = load_stock_panel()
    if panels is None:
        print("[ERROR] 无法加载数据")
        return

    close, volume, amount, high, low, open_ = panels
    print(f"[INFO] 数据范围: {close.index[0].date()} ~ {close.index[-1].date()}")
    print(f"[INFO] 股票数量: {len(close.columns)}")

    # 计算 IC
    print("[INFO] 计算各因子 IC...")
    results = calc_factor_ic_decay(close, volume, amount, high, low, open_)

    # 输出结果
    print("\n" + "-" * 60)
    print(f"{'因子':<25} {'IC Mean':>10} {'状态':>10}")
    print("-" * 60)

    for name, info in results.items():
        ic = info['IC']
        if np.isnan(ic):
            status = "N/A"
        elif abs(ic) > 0.03:
            status = "✅ 有效"
        elif abs(ic) > 0.01:
            status = "⚠️ 微弱"
        else:
            status = "❌ 衰减"
        print(f"{name:<25} {ic:>+10.4f} {status:>10}")

    print("-" * 60)

    # 保存到 JSON 报告
    report = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'data_range': f"{close.index[0].date()} ~ {close.index[-1].date()}",
        'n_stocks': len(close.columns),
        'results': {k: {'IC': round(v['IC'], 4) if not np.isnan(v['IC']) else None, 'direction': v['direction']} for k, v in results.items()}
    }

    report_path = os.path.join(DOCS_DIR, "ic_decay_latest.json")
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n[INFO] 报告已保存: {report_path}")

    # 总结
    valid = sum(1 for v in results.values() if not np.isnan(v['IC']) and abs(v['IC']) > 0.03)
    weak = sum(1 for v in results.values() if not np.isnan(v['IC']) and 0.01 < abs(v['IC']) <= 0.03)
    decayed = sum(1 for v in results.values() if not np.isnan(v['IC']) and abs(v['IC']) <= 0.01)

    print(f"\n总结: 有效={valid}, 微弱={weak}, 衰减={decayed}")

    return report


if __name__ == "__main__":
    run_ic_decay_check()

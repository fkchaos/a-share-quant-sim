#!/usr/bin/env python3
"""
sentiment_cycle — 情绪周期量化工具
=========================================

基于知乎文章"用qmt+python做情绪周期表"的实现：
- 连板高度序列
- 连板梯队（1板到N板数量）
- 晋级率（今日涨停→明日继续涨停）
- 次日溢价

用法：
    python scripts/sentiment_cycle.py --build     # 构建情绪指标
    python scripts/sentiment_cycle.py --signals   # 生成择时信号
    python scripts/sentiment_cycle.py --plot      # 输出情绪周期表
"""

import sys, os
import time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(os.environ.get('BACKTEST_DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')))
DB_PATH = DATA_DIR / 'quant.db'
REPORT_DIR = DATA_DIR / 'backtest_results'
REPORT_DIR.mkdir(exist_ok=True)

# ── 情绪指标构建 ────────────────────────────────────────────────
def build_sentiment_indicators(close_panel, volume_panel, high_panel, low_panel,
                                limit_up_pct=0.099, max_level=10):
    """
    构建情绪周期指标
    Returns: sentiment DataFrame, limit_up DataFrame, streak DataFrame
    """
    dates = close_panel.index
    n = len(dates)

    # 1. 判断每日是否涨停（逐股票向量化）
    limit_up = pd.DataFrame(False, index=dates, columns=close_panel.columns)
    for code in close_panel.columns:
        high = high_panel[code]
        close = close_panel[code]
        prev_close = close.shift(1)
        limit_up[code] = (high >= prev_close * 1.095) & (high == close)

    # 2. 连板数
    streak = pd.DataFrame(0, index=dates, columns=close_panel.columns, dtype=int)
    for i in range(1, n):
        mask = limit_up.iloc[i]
        prev = streak.iloc[i - 1]
        streak.iloc[i] = (prev + 1).where(mask, other=0)

    # 3. 连板梯队
    level_counts = {}
    for level in range(1, max_level + 1):
        level_counts['level_%d' % level] = (streak == level).sum(axis=1).astype(float)

    # 4. 晋级率
    promotion_rates = {}
    for level in range(1, max_level):
        today = (streak == level).sum(axis=1)
        nxt = (streak.shift(-1) == level + 1).sum(axis=1)
        promotion_rates['promo_%d_to_%d' % (level, level+1)] = np.where(today > 0, nxt / today, 0.0)

    # 5. 次日溢价
    next_ret = close_panel.pct_change().shift(-1)
    premium = pd.Series(0.0, index=dates)
    for i in range(n - 1):
        stocks = limit_up.iloc[i]
        cnt = stocks.sum()
        if cnt > 0:
            avg = next_ret.iloc[i][stocks].mean()
            if not pd.isna(avg):
                premium.iloc[i] = float(avg)

    # 6. 汇总
    sentiment = pd.DataFrame({
        'limit_up_count': limit_up.sum(axis=1).astype(float),
        'max_streak': streak.max(axis=1).astype(float),
        'next_premium': premium,
    })
    for k, v in level_counts.items():
        sentiment[k] = v
    for k, v in promotion_rates.items():
        sentiment[k] = v

    return sentiment, limit_up, streak

# ── 信号生成 ──────────────────────────────────────────────────────
def generate_sentiment_signals(sentiment_df, lookback=20):
    """
    基于情绪周期生成择时信号

    信号规则：
    - 情绪热度 = 连板数量 + 最高板高度 + 晋级率
    - 热度 > 历史 80% 分位 → 进攻（可追龙头）
    - 热度 < 历史 20% 分位 → 防守（空仓/轻仓）
    - 热度从低位回升 → 试错期（可低吸）
    - 热度从高位下降 → 退潮期（减仓）
    """
    df = sentiment_df.copy()

    # 情绪热度综合得分
    df['heat'] = (
        df['limit_up_count'].rolling(lookback).mean() / lookback +
        df['max_streak'] / 10 * 3 +
        df['next_premium'].rolling(lookback).mean() * 10
    )

    # 分位排名
    df['heat_pct'] = df['heat'].rolling(lookback * 3).rank(pct=True)

    # 信号
    df['signal'] = 0.0
    df.loc[df['heat_pct'] > 0.8, 'signal'] = 1
    df.loc[df['heat_pct'] < 0.2, 'signal'] = -1

    # 趋势变化
    df['heat_ma5'] = df['heat'].rolling(5).mean()
    df['heat_ma20'] = df['heat'].rolling(20).mean()
    df.loc[(df['heat_pct'] > 0.3) & (df['heat_ma5'] > df['heat_ma20']), 'signal'] = 0.5
    df.loc[(df['heat_pct'] < 0.7) & (df['heat_ma5'] < df['heat_ma20']), 'signal'] = -0.5

    return df

# ── 主函数 ──────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="情绪周期量化")
    parser.add_argument("--build", action="store_true", help="构建情绪指标")
    parser.add_argument("--signals", action="store_true", help="生成择时信号")
    parser.add_argument("--plot", action="store_true", help="输出情绪周期表（CSV）")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    args = parser.parse_args()

    from core.db import load_panel_from_db

    print("加载数据...")
    tpl, _ = load_panel_from_db(args.start, args.end, need_open=True, need_hl=True)
    close_panel = tpl[0]
    volume_panel = tpl[1]
    high_panel = tpl[4]
    low_panel = tpl[5]
    print("  数据: %d 天 x %d 只" % (close_panel.shape[0], close_panel.shape[1]))

    if args.build:
        print("\n构建情绪指标...")
        t0 = time.time()
        sentiment, limit_up, streak = build_sentiment_indicators(
            close_panel, volume_panel, high_panel, low_panel
        )
        print("  耗时: %.1fs" % (time.time() - t0))

        sent_path = REPORT_DIR / "sentiment_cycle.csv"
        sentiment.to_csv(sent_path)
        print("  已保存: %s" % sent_path)

    if args.signals:
        sent_path = REPORT_DIR / "sentiment_cycle.csv"
        if not sent_path.exists():
            print("请先运行 --build")
            return
        sentiment = pd.read_csv(sent_path, index_col=0, parse_dates=True)
        signals = generate_sentiment_signals(sentiment)

        print("\n最近 10 天情绪信号:")
        print(signals[['limit_up_count', 'max_streak', 'heat', 'heat_pct', 'signal']].tail(10))

        sig_path = REPORT_DIR / "sentiment_signals.csv"
        signals.to_csv(sig_path)
        print("\n信号已保存: %s" % sig_path)

    if args.plot:
        sent_path = REPORT_DIR / "sentiment_cycle.csv"
        if not sent_path.exists():
            print("请先运行 --build")
            return
        sentiment = pd.read_csv(sent_path, index_col=0, parse_dates=True)

        print("\n情绪周期表（最近30天）:")
        print("=" * 80)
        cols = ['limit_up_count', 'max_streak', 'next_premium']
        if 'level_1' in sentiment.columns:
            cols.extend(['level_1', 'level_2', 'level_3'])
        print(sentiment[cols].tail(30).to_string())

if __name__ == "__main__":
    main()

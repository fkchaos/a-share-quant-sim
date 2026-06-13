#!/usr/bin/env python3
"""
news_sentiment_factor — 新闻情绪因子生成器
==============================================

架构：
1. 数据源：东方财富新闻/公告（AKShare）
2. 情绪分析：DeepSeek API（批量）
3. 因子构建：
   - 个股新闻情绪得分（-1 到 +1）
   - 行业新闻情绪得分
   - 情绪变化率（加速度）
4. 存储：写入 indicators 表

用法：
    python scripts/news_sentiment_factor.py --fetch        # 拉取新闻
    python scripts/news_sentiment_factor.py --analyze      # 分析情绪
    python scripts/news_sentiment_factor.py --build-factor # 构建因子
    python scripts/news_sentiment_factor.py --backtest     # 回测验证
"""

import sys, os
import json
import time
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = Path(os.environ.get('BACKTEST_DATA_DIR', '/root/data'))
DB_PATH = DATA_DIR / 'quant.db'
REPORT_DIR = DATA_DIR / 'backtest_results'
REPORT_DIR.mkdir(exist_ok=True)


# ── 新闻数据获取 ─────────────────────────────────────────────────
def fetch_stock_news(code, days=30):
    """获取单只股票的新闻/公告"""
    import akshare as ak
    try:
        # 东方财富个股新闻
        df = ak.stock_news_em(symbol=code)
        if df is not None and len(df) > 0:
            return df
    except Exception:
        pass
    return None


def fetch_all_news(codes, days=30, max_workers=2):
    """批量获取新闻（限速）"""
    results = {}
    total = len(codes)
    print(f"获取 {total} 只股票的新闻...")

    for i, code in enumerate(codes):
        df = fetch_stock_news(code, days)
        if df is not None and len(df) > 0:
            results[code] = df
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{total}] 已获取 {len(results)} 只")
        time.sleep(0.1)  # 限速

    print(f"完成: {len(results)}/{total} 只有新闻")
    return results


# ── 情绪分析 ─────────────────────────────────────────────────────
def analyze_sentiment_batch(news_list, api_key=None):
    """
    批量分析新闻情绪

    news_list: [{"code": str, "title": str, "content": str, "date": str}, ...]
    Returns: [{"code": str, "date": str, "sentiment": float, "confidence": float}, ...]
    """
    key = api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not key:
        print("未设置 DEEPSEEK_API_KEY")
        return []

    import requests

    # 构建批量 prompt
    news_text = "\n".join([
        f"{i+1}. [{n.get('date','')}] {n.get('title','')} - {n.get('content','')[:100]}"
        for i, n in enumerate(news_list[:20])  # 每次最多20条
    ])

    prompt = f"""分析以下 A 股新闻/公告的情绪（每条单独打分）：

{news_text}

对每条新闻，请返回：
- 情绪得分：-1（极度利空）到 +1（极度利好），0 为中性
- 置信度：0 到 1

格式：JSON 数组，每项包含 idx（序号）, sentiment, confidence
只返回 JSON，不要其他内容。"""

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 2000,
            },
            timeout=60,
        )
        result = resp.json()
        if 'choices' in result:
            content = result['choices'][0]['message']['content']
            # 解析 JSON
            try:
                data = json.loads(content)
                return data
            except json.JSONDecodeError:
                # 尝试提取 JSON 部分
                import re
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    return json.loads(match.group())
                return []
    except Exception as e:
        print(f"API 调用失败: {e}")
        return []


# ── 因子构建 ─────────────────────────────────────────────────────
def build_sentiment_factor(sentiment_data, close_panel):
    """
    构建新闻情绪因子

    sentiment_data: DataFrame with columns [code, date, sentiment, confidence]
    Returns: DataFrame (date × code) 情绪因子值
    """
    if sentiment_data is None or len(sentiment_data) == 0:
        return pd.DataFrame()

    # 按日期和股票聚合
    sentiment_data['date'] = pd.to_datetime(sentiment_data['date'])

    # 日频情绪得分（加权平均，权重=置信度）
    daily = sentiment_data.groupby(['code', 'date']).apply(
        lambda x: np.average(x['sentiment'], weights=x['confidence'])
        if x['confidence'].sum() > 0 else 0
    ).reset_index()
    daily.columns = ['code', 'date', 'sentiment']

    # 构建面板
    factor_panel = daily.pivot(index='date', columns='code', values='sentiment')

    # 情绪变化率（加速度）
    momentum_panel = factor_panel.diff()

    return factor_panel, momentum_panel


# ── 回测验证 ─────────────────────────────────────────────────────
def backtest_sentiment_factor(factor_panel, close_panel, fwd_period=5):
    """简单回测验证情绪因子"""
    if factor_panel.empty:
        print("情绪因子为空，无法回测")
        return

    fwd_ret = close_panel.pct_change(fwd_period).shift(-fwd_period)

    # 按情绪得分分组
    common_idx = factor_panel.index.intersection(fwd_ret.index)
    common_cols = factor_panel.columns.intersection(fwd_ret.columns)

    results = {}
    for date in common_idx[:100]:  # 只验证前100天
        fv = factor_panel.loc[date, common_cols].dropna()
        rv = fwd_ret.loc[date, common_cols].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < 20:
            continue

        # 按情绪得分分组
        q = pd.qcut(fv[common], 5, duplicates='drop')
        for group in q.unique():
            mask = q == group
            avg_ret = rv[common][mask].mean()
            results[group] = results.get(group, []) + [avg_ret]

    if results:
        print("\n情绪因子分组回测 (前100天):")
        for group, rets in sorted(results.items()):
            print(f"  {group}: 平均收益={np.mean(rets)*100:.3f}%, 样本={len(rets)}")


# ── 主函数 ──────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="新闻情绪因子")
    parser.add_argument("--fetch", action="store_true", help="拉取新闻")
    parser.add_argument("--analyze", action="store_true", help="分析情绪")
    parser.add_argument("--build-factor", action="store_true", help="构建因子")
    parser.add_argument("--backtest", action="store_true", help="回测验证")
    parser.add_argument("--codes", type=str, default=None, help="股票代码列表（逗号分隔）")
    args = parser.parse_args()

    # 获取股票代码
    if args.codes:
        codes = args.codes.split(',')
    else:
        # 从 DB 获取
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM daily_kline LIMIT 50").fetchall()]
        conn.close()

    if args.fetch:
        news = fetch_all_news(codes[:20])  # 先测试20只
        # 保存到文件
        output = {}
        for code, df in news.items():
            output[code] = df.to_dict('records')
        with open(REPORT_DIR / 'stock_news.json', 'w') as f:
            json.dump(output, f, ensure_ascii=False, default=str)
        print(f"新闻已保存: {REPORT_DIR / 'stock_news.json'}")

    if args.analyze:
        # 加载新闻
        news_path = REPORT_DIR / 'stock_news.json'
        if not news_path.exists():
            print("请先运行 --fetch")
            return
        with open(news_path) as f:
            news_data = json.load(f)

        # 构建分析列表
        news_list = []
        for code, items in news_data.items():
            for item in items[:5]:  # 每只股票取最新5条
                news_list.append({
                    'code': code,
                    'title': item.get('title', ''),
                    'content': item.get('content', ''),
                    'date': item.get('date', ''),
                })

        print(f"分析 {len(news_list)} 条新闻...")
        results = analyze_sentiment_batch(news_list)
        print(f"分析结果: {len(results)} 条")

        with open(REPORT_DIR / 'sentiment_results.json', 'w') as f:
            json.dump(results, f, ensure_ascii=False)

    if args.build_factor:
        sent_path = REPORT_DIR / 'sentiment_results.json'
        if not sent_path.exists():
            print("请先运行 --analyze")
            return
        with open(sent_path) as f:
            sent_data = json.load(f)

        # 构建因子
        df = pd.DataFrame(sent_data)
        print(f"情绪数据: {len(df)} 条")

    if args.backtest:
        print("回测验证...")


if __name__ == "__main__":
    main()

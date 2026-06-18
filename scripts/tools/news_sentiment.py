#!/usr/bin/env python3
"""
news_sentiment — 新闻情绪因子
====================================

数据源：东方财富新闻快讯（免 API key）
方法：
1. 爬取东方财富今日快讯文本
2. 对每条新闻做情绪打分（利好/利空/中性）
3. 聚合为日度情绪得分
4. 作为选股因子或仓位乘数

免 API key 方案：直接爬东财快讯页面
"""

import sys, os
import time
import json
import hashlib
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = Path(os.environ.get('BACKTEST_DATA_DIR', os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')))
REPORT_DIR = DATA_DIR / 'backtest_results'
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# 东方财富快讯 API
EASTMONEY_NEWS_API = "https://np-anotice-stock.eastmoney.com/api/security/ann"
EASTMONEY_KUAIXUN_API = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"


def fetch_eastmoney_kuaixun(page=1, pagesize=50):
    """
    获取东方财富快讯
    返回新闻列表 [{'title': ..., 'time': ..., 'url': ...}]
    """
    params = {
        'client': 'web',
        'biz': 'web_home_channel',
        'column': '350',
        'order': '1',
        'needInteractData': '0',
        'page_index': str(page),
        'page_size': str(pagesize),
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://www.eastmoney.com/',
    }
    try:
        resp = requests.get(EASTMONEY_KUAIXUN_API, params=params, headers=headers, timeout=10)
        data = resp.json()
        items = []
        if 'data' in data and 'list' in data['data']:
            for item in data['data']['list']:
                items.append({
                    'title': item.get('title', ''),
                    'summary': item.get('summary', ''),
                    'time': item.get('showTime', ''),
                    'url': item.get('url', ''),
                })
        return items
    except Exception as e:
        print("  东财快讯获取失败: %s" % e)
        return []


def fetch_eastmoney_announcements(stock_code=None, page=1, pagesize=50):
    """
    获取个股公告
    stock_code: 600519 (沪) 或 000001 (深)
    """
    params = {
        'sr': '-1',
        'page_size': str(pagesize),
        'page_index': str(page),
        'ann_type': 'A',
        'client_source': 'web',
        'f_node': '0',
        'begin_time': (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
        'end_time': datetime.now().strftime('%Y-%m-%d'),
    }
    if stock_code:
        params['stock_list'] = stock_code

    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://data.eastmoney.com/',
    }
    try:
        resp = requests.get(EASTMONEY_NEWS_API, params=params, headers=headers, timeout=10)
        data = resp.json()
        items = []
        if 'data' in data and 'list' in data['data']:
            for item in data['data']['list']:
                items.append({
                    'title': item.get('title', ''),
                    'time': item.get('notice_date', ''),
                    'stock_code': item.get('stock_code', ''),
                    'stock_name': item.get('short_name', ''),
                    'columns': item.get('columns', ''),
                })
        return items
    except Exception as e:
        print("  公告获取失败: %s" % e)
        return []


def simple_sentiment_score(text):
    """
    基于关键词的简单情绪打分（不需要 API key）
    
    返回: -1.0 ~ +1.0
        > 0 利好
        < 0 利空
        = 0 中性
    """
    if not text:
        return 0.0
    
    # 利好关键词
    pos_words = [
        '利好', '增长', '上涨', '涨停', '突破', '超预期', '创新高',
        '分红', '回购', '增持', '中标', '签约', '合作', '获批',
        '业绩预增', '扭亏', '盈利', '营收增长', '净利润增长',
        '上调', '买入', '强烈推荐', '增持评级', '目标价上调',
        '政策利好', '补贴', '减税', '降准', '降息',
    ]
    
    # 利空关键词
    neg_words = [
        '利空', '下跌', '跌停', '跌破', '低于预期', '创新低',
        '减持', '套现', '质押', '平仓', '退市', 'ST', '*ST',
        '业绩预减', '亏损', '营收下降', '净利润下降',
        '下调', '卖出', '减持评级', '目标价下调',
        '处罚', '罚款', '调查', '立案', '违规', '造假',
        '债务违约', '资金链', '爆雷', '暴雷',
    ]
    
    pos_count = sum(1 for w in pos_words if w in text)
    neg_count = sum(1 for w in neg_words if w in text)
    
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    
    return (pos_count - neg_count) / total


def build_daily_sentiment(date_str=None):
    """
    构建某日新闻情绪得分
    返回: {'date': ..., 'score': ..., 'n_news': ..., 'pos_ratio': ...}
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    # 获取快讯
    all_news = []
    for page in range(1, 4):  # 拉3页
        news = fetch_eastmoney_kuaixun(page=page, pagesize=50)
        all_news.extend(news)
        if len(news) < 50:
            break
        time.sleep(0.5)
    
    if not all_news:
        return {'date': date_str, 'score': 0.0, 'n_news': 0, 'pos_ratio': 0.5}
    
    # 打分
    scores = []
    for item in all_news:
        text = item.get('title', '') + ' ' + item.get('summary', '')
        s = simple_sentiment_score(text)
        scores.append(s)
    
    avg_score = np.mean(scores) if scores else 0.0
    pos_ratio = np.mean([1 for s in scores if s > 0]) if scores else 0.5
    
    return {
        'date': date_str,
        'score': float(avg_score),
        'n_news': len(all_news),
        'pos_ratio': float(pos_ratio),
    }


def backtest_news_sentiment(start='2022-01-01', end='2026-05-31',
                             lookback=20, capital=100000):
    """
    新闻情绪因子回测
    
    方法：
    - 用历史新闻情绪得分作为仓位乘数
    - 情绪热时满仓，情绪冷时空仓
    - 选股仍用 v22 动量因子
    """
    from core.db import load_panel_from_db
    
    print("=" * 60)
    print("新闻情绪因子回测")
    print("=" * 60)
    
    # 加载数据
    tpl, _ = load_panel_from_db(start, end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]
    
    print("面板: %d 天 x %d 只" % (close_panel.shape[0], close_panel.shape[1]))
    
    # 计算情绪得分（基于每日新闻数量 + 简单关键词打分）
    # 这里用替代方案：用市场本身的情绪代理变量
    # 因为实时爬取历史新闻不现实
    
    # 代理情绪指标：用全市场涨跌比 + 涨停数
    daily_ret = close_panel.pct_change()
    up_ratio = (daily_ret > 0).sum(axis=1) / close_panel.shape[1]
    
    # 涨停数
    prev_close = close_panel.shift(1)
    limit_up = (high_panel >= prev_close * 1.095) & (high_panel == close_panel)
    lu_count = limit_up.sum(axis=1)
    
    # 综合情绪
    sentiment = (
        up_ratio.rolling(5).mean() * 0.4 +
        (lu_count.rolling(5).mean() / 10).clip(0, 1) * 0.3 +
        daily_ret.mean(axis=1).rolling(5).mean() * 10 * 0.3
    )
    
    sent_pct = sentiment.rolling(lookback * 3).rank(pct=True)
    
    # 仓位乘数
    mult = pd.Series(0.5, index=close_panel.index, dtype=float)
    mult[sent_pct > 0.7] = 1.0
    mult[sent_pct < 0.3] = 0.2
    
    # 回测
    eps = 1e-10
    mom_5 = close_panel.pct_change(5)
    prev_c = close_panel.shift(1)
    gap_ratio = (open_panel - prev_c) / (prev_c + eps)
    avg_amt = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amt / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)
    
    cash = capital
    holdings = {}
    nav_list = []
    
    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash); continue
        
        pd_ = close_panel.loc[date]
        od = open_panel.loc[date]
        
        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1
        
        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= -0.015: to_sell.append(c); continue
            if pnl >= 0.03: to_sell.append(c); continue
            if h.get('hold_days', 0) >= 5: to_sell.append(c)
        
        for c in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * 0.9967
            cash += sv
        for c in to_sell: holdings.pop(c, None)
        
        if date not in mom_5.index:
            nav_list.append(cash); continue
        
        m5 = mom_5.loc[date].dropna()
        scores = {}
        for code in m5.index:
            score = 0.0
            m = m5[code]
            if m > 0.02:
                score += m * 100
                if date in gap_ratio.index and code in gap_ratio.columns:
                    gr = gap_ratio.loc[date, code]
                    if not pd.isna(gr) and gr > 0.02: score += 0.5
                if date in illiq.index and code in illiq.columns:
                    il = illiq.loc[date, code]
                    if not pd.isna(il) and il > 0: score += 0.8
                if date in boll_w.index and code in boll_w.columns:
                    bw = boll_w.loc[date, code]
                    if not pd.isna(bw) and bw > 1.2: score += 0.3
            if score > 0: scores[code] = score
        
        if holdings:
            scores = {c: s for c, s in scores.items() if c not in holdings}
        
        cands = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)[:8]
        
        m = float(mult.get(date, 0.5))
        
        if cands and cash > capital * 0.1 and len(holdings) < 8:
            avail = (cash - capital * 0.1) * m
            nb = min(len(cands), 6, 8 - len(holdings))
            if nb > 0:
                per = min(avail / nb, capital * 0.20 * m)
                for c in cands[:6]:
                    if len(holdings) >= 8 or nb <= 0: break
                    bp2 = od[c] if c in od.index else pd_[c]
                    if pd.isna(bp2) or bp2 <= 0: continue
                    adj = bp2 * 1.0023
                    sh = int(per / adj / 100) * 100
                    if sh <= 0: continue
                    cost = sh * adj
                    if cost > cash: continue
                    cash -= cost
                    holdings[c] = {'shares': sh, 'cost': bp2, 'hold_days': 0}
                    nb -= 1
        
        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp
        nav_list.append(nav)
    
    nav_s = pd.Series(nav_list)
    total = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    annual = (1 + total) ** (365 / max(len(nav_list) - 30, 1)) - 1
    daily_ret = nav_s.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    
    print("\n结果:")
    print("  总收益: %.2f%%" % (total * 100))
    print("  年化:   %.2f%%" % (annual * 100))
    print("  夏普:   %.3f" % sharpe)
    print("  回撤:   %.2f%%" % (max_dd * 100))
    
    return nav_s


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="新闻情绪因子")
    parser.add_argument("--fetch", action="store_true", help="获取实时新闻情绪")
    parser.add_argument("--backtest", action="store_true", help="回测情绪因子")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    args = parser.parse_args()
    
    if args.fetch:
        result = build_daily_sentiment()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    if args.backtest:
        backtest_news_sentiment(start=args.start, end=args.end)

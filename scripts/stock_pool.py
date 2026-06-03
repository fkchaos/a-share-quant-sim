"""
选股池构建模块 — 双轨并行
========================
轨道1（每日）：中证500 + 沪深300 成分股 → ~632只，稳定可靠
轨道2（每周）：全A股多层过滤 → 探索更优解，低频更新

规则:
  第一层：基础准入（实时行情）
    - 非 ST / 非停牌
    - 市值 ≥ 50 亿
    - 当日成交额 ≥ 3000 万
    - 排除科创板(688/689)、北交所(920/8/4)
    - 包含创业板(30)
  第二层：垃圾股排除（需财务数据，暂缓）
  第三层：K线过滤（可选）

用法:
  from scripts.stock_pool import build_pool_daily, build_pool_weekly, get_active_pool
  
  daily_pool = build_pool_daily()        # 中证500+沪深300，~632只
  weekly_pool = build_pool_weekly()      # 全A股多层过滤（低频）
  pool = get_active_pool('daily')         # 获取当前活跃选股池
"""

import os, sys, time, json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from scripts.data_fetcher import (
    fetch_stock_list, fetch_spot, fetch_kline,
    fetch_tencent_spot, fetch_tencent_kline,
    _tx_code,
)
from scripts.fallback_pool import build_fallback_pool, get_fallback_pool, CACHE_DIR

# ── 配置 ──────────────────────────────────────
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_PARAMS = {
    'min_list_days': 180,
    'min_daily_amount': 5000,
    'min_daily_amount_fast': 3000,
    'min_market_cap': 50,
    'amount_lookback': 20,
    'min_revenue': 1e8,
    'max_goodwill_ratio': 0.5,
    'exclude_kcb': True,
    'exclude_bse': True,
    'exclude_cyb': False,
}


# ── 板块分类 ──────────────────────────────────
def classify_board(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith('688') or code.startswith('689'):
        return '科创板'
    elif code.startswith('920') or code.startswith('8') or code.startswith('4'):
        return '北交所'
    elif code.startswith('30'):
        return '创业板'
    elif code.startswith('60'):
        return '沪主板'
    elif code.startswith('00'):
        return '深主板'
    else:
        return '其他'


# ── 批量获取实时行情 ──────────────────────────
def fetch_spot_batch(codes, batch_size=80, delay=0.2) -> pd.DataFrame:
    """批量获取实时行情（腾讯接口，每次最多80只）"""
    import requests
    all_spots = []
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        tx_codes = [_tx_code(c) for c in batch]
        url = f"http://qt.gtimg.cn/q={','.join(tx_codes)}"
        try:
            r = requests.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }, timeout=15)
            r.encoding = 'gbk'
            for line in r.text.strip().split(';'):
                line = line.strip()
                if not line or '~' not in line:
                    continue
                if '="' in line:
                    line = line.split('="', 1)[1].rstrip('"')
                parts = line.split('~')
                if len(parts) < 50:
                    continue
                try:
                    all_spots.append({
                        'code': parts[2],
                        'name': parts[1],
                        'close': float(parts[3]) if parts[3] else 0,
                        'open': float(parts[5]) if parts[5] else 0,
                        'high': float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                        'low': float(parts[34]) if len(parts) > 34 and parts[34] else 0,
                        'volume': float(parts[6]) if parts[6] else 0,
                        'amount': float(parts[37]) if len(parts) > 37 and parts[37] else 0,  # 万元
                        'change_pct': float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                        'turnover': float(parts[38]) if len(parts) > 38 and parts[38] else 0,
                        'pe': float(parts[39]) if len(parts) > 39 and parts[39] else 0,
                        'float_cap': float(parts[44]) if len(parts) > 44 and parts[44] else 0,  # 亿元
                        'market_cap': float(parts[45]) if len(parts) > 45 and parts[45] else 0,  # 亿元
                    })
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            print(f"  [batch {i//batch_size+1}] 失败: {e}")
        time.sleep(delay)
    return pd.DataFrame(all_spots) if all_spots else pd.DataFrame()


# ── 第一层：基础准入 ────────────────────────────
def filter_basic(df_spot: pd.DataFrame, params: dict = None) -> tuple:
    """第一层过滤：基础准入（仅用实时行情）"""
    params = {**DEFAULT_PARAMS, **(params or {})}
    p = params
    excluded = []
    passed_mask = pd.Series(True, index=df_spot.index)

    for idx, row in df_spot.iterrows():
        code = str(row['code']).zfill(6)
        name = str(row.get('name', ''))
        reason = None

        if p['exclude_kcb'] and (code.startswith('688') or code.startswith('689')):
            reason = '科创板'
        elif p['exclude_bse'] and (code.startswith('920') or code.startswith('8') or code.startswith('4')):
            reason = '北交所'
        elif p['exclude_cyb'] and code.startswith('30'):
            reason = '创业板'
        elif name.startswith('ST') or name.startswith('*ST'):
            reason = 'ST'
        elif row.get('volume', 0) == 0 or row.get('close', 0) <= 0:
            reason = '停牌/无交易'
        elif row.get('market_cap', 0) < p['min_market_cap']:
            reason = f"市值不足({row.get('market_cap', 0):.0f}亿)"
        elif row.get('amount', 0) < p.get('min_daily_amount_fast', 3000):
            reason = f"成交额不足({row.get('amount', 0):.0f}万)"

        if reason:
            excluded.append({'code': code, 'name': name, 'board': classify_board(code),
                             'reason': reason, 'filter_layer': 'basic'})
            passed_mask[idx] = False

    df_passed = df_spot[passed_mask].copy()
    df_excluded = pd.DataFrame(excluded) if excluded else pd.DataFrame()
    print(f"[filter_basic] 准入 {len(df_passed)} / {len(df_spot)} (排除 {len(df_excluded)})")
    return df_passed, df_excluded


# ── 获取活跃选股池 ─────────────────────────────
def get_active_pool(mode: str = 'daily', use_cache: bool = True) -> pd.DataFrame:
    """
    获取当前活跃选股池
    mode='daily':  中证500+沪深300（每日更新）
    mode='weekly': 全A股多层过滤（每周更新）
    """
    if mode == 'daily':
        return get_fallback_pool(use_cache=use_cache)
    elif mode == 'weekly':
        cache_file = os.path.join(CACHE_DIR, "weekly_pool.csv")
        if use_cache and os.path.exists(cache_file):
            age_days = (time.time() - os.path.getmtime(cache_file)) / 86400
            if age_days < 7:
                return pd.read_csv(cache_file, dtype={'code': str})
        return build_pool_weekly()
    else:
        raise ValueError(f"未知模式: {mode}")


# ── 每日选股池：中证500+沪深300 ────────────────
def build_pool_daily() -> pd.DataFrame:
    """
    每日选股池：中证500 + 沪深300 成分股
    - 数量: ~632只
    - 更新频率: 每日（成分股列表缓存7天）
    - 特点: 稳定可靠，流动性好
    """
    print("=" * 60)
    print("每日选股池构建 (中证500 + 沪深300)")
    print("=" * 60)
    
    pool = build_fallback_pool()
    
    # 补充实时行情字段
    codes = pool['code'].tolist()
    print(f"\n获取实时行情 ({len(codes)} 只)...")
    df_spot = fetch_spot_batch(codes)
    
    if len(df_spot) > 0:
        # 合并行情数据
        pool = pool.merge(df_spot[['code', 'close', 'open', 'high', 'low', 'volume', 
                                     'amount', 'change_pct', 'turnover', 'pe', 
                                     'market_cap', 'float_cap']], 
                          on='code', how='left')
    
    # 过滤 ST / 停牌
    mask = (
        ~pool['name'].str.contains(r'^\*?ST', na=False) &
        (pool['volume'] > 0) &
        (pool['close'] > 0)
    )
    pool = pool[mask].copy()
    
    print(f"\n每日选股池: {len(pool)} 只")
    if len(pool) > 0:
        print(pool['board'].value_counts().to_string())
    
    # 保存
    pool.to_csv(os.path.join(CACHE_DIR, "daily_pool.csv"), index=False)
    return pool


# ── 每周选股池：全A股多层过滤 ──────────────────
def build_pool_weekly(params: dict = None) -> pd.DataFrame:
    """
    每周选股池：全A股多层过滤
    - 数量: ~2800+只（仅第一层）
    - 更新频率: 每周一次（低频）
    - 特点: 范围广，用于探索更优解
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    cache_file = os.path.join(CACHE_DIR, "weekly_pool.csv")
    
    print("=" * 60)
    print("每周选股池构建 (全A股多层过滤)")
    print("=" * 60)
    
    # 获取股票列表
    df_list = fetch_stock_list()
    df_list['code'] = df_list['code'].astype(str).str.zfill(6)
    codes = df_list['code'].tolist()
    print(f"全量股票: {len(codes)} 只")
    
    # 批量获取实时行情
    print("获取实时行情...")
    df_spot = fetch_spot_batch(codes)
    print(f"获取行情: {len(df_spot)} 只")
    
    # 第一层过滤
    df_passed, excluded = filter_basic(df_spot, params)
    df_passed['board'] = df_passed['code'].astype(str).str.zfill(6).apply(classify_board)
    
    # 保存
    df_passed.to_csv(cache_file, index=False)
    
    print(f"\n每周选股池: {len(df_passed)} 只")
    print(df_passed['board'].value_counts().to_string())
    
    return df_passed


# ── 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("选股池构建测试")
    print("=" * 60)
    
    # 每日选股池
    print("\n--- 每日选股池 ---")
    daily = build_pool_daily()
    print(f"\n每日选股池: {len(daily)} 只")
    print(daily['board'].value_counts().to_string())
    
    # 每周选股池（如果缓存不存在）
    cache_file = os.path.join(CACHE_DIR, "weekly_pool.csv")
    if not os.path.exists(cache_file):
        print("\n--- 每周选股池 ---")
        weekly = build_pool_weekly()
        print(f"\n每周选股池: {len(weekly)} 只")

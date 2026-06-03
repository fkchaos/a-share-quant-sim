"""
选股池构建模块 — 多层过滤
============================
在 data_fetcher.py 数据获取层之上，实现选股池的准入+排除规则。

规则（用户提供）:

第一层：基础准入（基于行情数据，可实时计算）
  - 非 ST / 非停牌（price > 0, volume > 0）
  - 总市值 ≥ 50 亿
  - 当日成交额 ≥ 3000 万（近似日均）
  - 排除科创板(688/689)、北交所(920/8/4)
  - 包含创业板(30)

第二层：垃圾股排除（需要财务数据，定期更新）
  - 排除连续亏损 + 营收 < 1 亿
  - 排除净资产为负
  - 排除商誉占比 ≥ 50%
  - （审计意见/立案调查/质押/减持 需额外数据源，暂缓）

第三层：进阶优化（可选）
  - 上市满 180 天（需 K 线数据）
  - 日均成交额 ≥ 5000 万（需 20 日 K 线）

用法:
  from scripts.stock_pool import build_pool_fast, build_pool_full
  
  # 快速模式（仅实时行情，~30s）
  pool = build_pool_fast()
  
  # 完整模式（含 K 线过滤，较慢）
  pool, excluded = build_pool_full(max_kline_fetch=3000)
"""

import os, sys, time, json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

# 项目路径
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from scripts.data_fetcher import (
    fetch_stock_list, fetch_spot, fetch_kline,
    fetch_tencent_spot, fetch_tencent_kline,
    _tx_code,
)

# ── 配置 ──────────────────────────────────────
CACHE_DIR = os.path.join(_BASE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

DEFAULT_PARAMS = {
    # 基础准入
    'min_list_days': 180,          # 上市最少天数（需K线）
    'min_daily_amount': 5000,      # 日均成交额下限（万元，需K线）
    'min_market_cap': 50,          # 总市值下限（亿元）
    'min_daily_amount_fast': 3000, # 快速模式成交额下限（万元）
    'amount_lookback': 20,         # 成交额回溯天数
    # 垃圾股排除
    'min_revenue': 1e8,            # 营收下限（元）
    'max_goodwill_ratio': 0.5,     # 商誉/净资产上限
    # 板块过滤
    'exclude_kcb': True,           # 排除科创板(688/689)
    'exclude_bse': True,           # 排除北交所(920/8/4)
    'exclude_cyb': False,          # 是否排除创业板(30)，默认包含
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
def fetch_spot_batch(codes: List[str], batch_size: int = 80, delay: float = 0.2) -> pd.DataFrame:
    """
    批量获取实时行情（腾讯接口，每次最多80只）
    返回 DataFrame: [code, name, close, open, high, low, volume, amount, change_pct, ...]
    """
    all_spots = []
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        # 构造批量请求
        tx_codes = [_tx_code(c) for c in batch]
        url = f"http://qt.gtimg.cn/q={','.join(tx_codes)}"
        try:
            import requests
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
def filter_basic(
    df_spot: pd.DataFrame,
    params: dict = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    第一层过滤：基础准入（仅用实时行情）
    返回 (passed, excluded)
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    p = params

    excluded = []
    passed_mask = pd.Series(True, index=df_spot.index)

    for idx, row in df_spot.iterrows():
        code = str(row['code']).zfill(6)
        name = str(row.get('name', ''))
        reason = None

        # 1. 排除科创板
        if p['exclude_kcb'] and (code.startswith('688') or code.startswith('689')):
            reason = '科创板'
        # 2. 排除北交所
        elif p['exclude_bse'] and (code.startswith('920') or code.startswith('8') or code.startswith('4')):
            reason = '北交所'
        # 3. 排除创业板（可配置）
        elif p['exclude_cyb'] and code.startswith('30'):
            reason = '创业板'
        # 4. ST
        elif name.startswith('ST') or name.startswith('*ST'):
            reason = 'ST'
        # 5. 停牌/无交易
        elif row.get('volume', 0) == 0 or row.get('close', 0) <= 0:
            reason = '停牌/无交易'
        # 6. 市值
        elif row.get('market_cap', 0) < p['min_market_cap']:
            reason = f"市值不足({row.get('market_cap', 0):.0f}亿)"
        # 7. 成交额（快速模式用当日值，单位：万元）
        elif row.get('amount', 0) < p.get('min_daily_amount_fast', 3000):
            reason = f"成交额不足({row.get('amount', 0):.0f}万)"

        if reason:
            excluded.append({
                'code': code, 'name': name,
                'board': classify_board(code),
                'reason': reason, 'filter_layer': 'basic'
            })
            passed_mask[idx] = False

    df_passed = df_spot[passed_mask].copy()
    df_excluded = pd.DataFrame(excluded) if excluded else pd.DataFrame()
    print(f"[filter_basic] 准入 {len(df_passed)} / {len(df_spot)} (排除 {len(df_excluded)})")
    return df_passed, df_excluded


# ── 第二层：垃圾股排除 ──────────────────────────
def filter_quality(
    df_spot: pd.DataFrame,
    financial_data: Dict[str, Dict] = None,
    params: dict = None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    第二层过滤：垃圾股排除（需财务数据）
    financial_data: {code: {revenue, net_profit, equity, goodwill, total_assets}}
    """
    params = {**DEFAULT_PARAMS, **(params or {})}

    if not financial_data:
        print("[filter_quality] ⚠️ 无财务数据，跳过")
        return df_spot, pd.DataFrame()

    excluded = []
    passed_indices = []

    for idx, row in df_spot.iterrows():
        code = str(row['code']).zfill(6)
        name = str(row.get('name', ''))
        reason = None
        fin = financial_data.get(code)

        if fin:
            # 净资产为负
            equity = fin.get('equity')
            if equity is not None and equity < 0:
                reason = f'净资产为负({equity/1e8:.2f}亿)'
            # 亏损+营收不足
            if reason is None:
                revenue = fin.get('revenue')
                net_profit = fin.get('net_profit') or fin.get('net_profit_att_p')
                if net_profit is not None and net_profit < 0:
                    if revenue is not None and revenue < params['min_revenue']:
                        reason = f'亏损+营收不足({revenue/1e8:.2f}亿)'
            # 商誉占比
            if reason is None:
                goodwill = fin.get('goodwill')
                total_assets = fin.get('total_assets')
                if goodwill is not None and total_assets is not None and total_assets > 0:
                    gw_ratio = goodwill / total_assets
                    if gw_ratio >= params['max_goodwill_ratio']:
                        reason = f'商誉占比过高({gw_ratio:.1%})'

        if reason:
            excluded.append({
                'code': code, 'name': name,
                'board': classify_board(code),
                'reason': reason, 'filter_layer': 'quality'
            })
        else:
            passed_indices.append(idx)

    df_passed = df_spot.loc[passed_indices].copy() if passed_indices else pd.DataFrame()
    df_excluded = pd.DataFrame(excluded) if excluded else pd.DataFrame()
    print(f"[filter_quality] 通过 {len(df_passed)} / {len(df_spot) + len(df_excluded)} (排除 {len(df_excluded)})")
    return df_passed, df_excluded


# ── 第三层：K 线过滤 ────────────────────────────
def filter_kline(
    df_spot: pd.DataFrame,
    params: dict = None,
    max_fetch: int = 3000,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    第三层过滤：基于 K 线的准入
    - 上市满 N 天
    - 日均成交额 ≥ 阈值
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    p = params

    excluded = []
    passed_indices = []
    codes = df_spot['code'].tolist()[:max_fetch]

    for i, code in enumerate(codes):
        code = str(code).zfill(6)
        row = df_spot[df_spot['code'].astype(str).str.zfill(6) == code]
        if row.empty:
            continue
        row = row.iloc[0]
        name = str(row.get('name', ''))
        reason = None

        # 获取 K 线
        df_kline = fetch_tencent_kline(code, days=max(p['amount_lookback'] + 10, 200))
        if df_kline is None or len(df_kline) < 10:
            reason = f'K线数据不足'
        else:
            # 上市天数
            first_date = df_kline.index.min()
            list_days = (pd.Timestamp.now() - first_date).days
            if list_days < p['min_list_days']:
                reason = f'上市不足{list_days}天'
            # 日均成交额
            if reason is None:
                recent = df_kline.tail(p['amount_lookback'])
                avg_amount = (recent['close'] * recent['volume']).mean() / 10000  # 万元
                if avg_amount < p['min_daily_amount']:
                    reason = f'日均成交额不足({avg_amount:.0f}万)'

        if reason:
            excluded.append({
                'code': code, 'name': name,
                'board': classify_board(code),
                'reason': reason, 'filter_layer': 'kline'
            })
        else:
            passed_indices.append(row.name)  # 原始 index

        if (i + 1) % 500 == 0:
            print(f"  [kline] 进度 {i+1}/{len(codes)}")
        time.sleep(0.05)

    df_passed = df_spot.loc[passed_indices].copy() if passed_indices else pd.DataFrame()
    df_excluded = pd.DataFrame(excluded) if excluded else pd.DataFrame()
    print(f"[filter_kline] 通过 {len(df_passed)} / {len(codes)} (排除 {len(df_excluded)})")
    return df_passed, df_excluded


# ── 快速选股池 ─────────────────────────────────
def build_pool_fast(
    params: dict = None,
    use_cache: bool = True,
    cache_ttl_hours: int = 6,
) -> pd.DataFrame:
    """
    快速选股池 — 仅用实时行情，不拉 K 线
    适用于日内信号生成（速度优先，~30s）
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    cache_file = os.path.join(CACHE_DIR, "stock_pool_fast.csv")

    if use_cache and os.path.exists(cache_file):
        age_hours = (time.time() - os.path.getmtime(cache_file)) / 3600
        if age_hours < cache_ttl_hours:
            return pd.read_csv(cache_file, dtype={'code': str})

    print("=" * 60)
    print("快速选股池构建")
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

    # 添加板块
    df_passed['board'] = df_passed['code'].astype(str).str.zfill(6).apply(classify_board)

    # 保存
    df_passed.to_csv(cache_file, index=False)
    print(f"\n选股池: {len(df_passed)} 只")
    print(df_passed['board'].value_counts().to_string())

    return df_passed


# ── 完整选股池 ─────────────────────────────────
def build_pool_full(
    params: dict = None,
    use_cache: bool = True,
    cache_ttl_hours: int = 24,
    max_kline_fetch: int = 3000,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    完整选股池 — 三层过滤
    返回 (pool, excluded)
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    cache_file = os.path.join(CACHE_DIR, "stock_pool_full.csv")
    excluded_cache = os.path.join(CACHE_DIR, "stock_pool_full_excluded.csv")

    if use_cache and os.path.exists(cache_file):
        age_hours = (time.time() - os.path.getmtime(cache_file)) / 3600
        if age_hours < cache_ttl_hours:
            pool = pd.read_csv(cache_file, dtype={'code': str})
            excluded = pd.read_csv(excluded_cache, dtype={'code': str}) if os.path.exists(excluded_cache) else pd.DataFrame()
            print(f"[build_pool_full] 使用缓存: {len(pool)} 只")
            return pool, excluded

    t0 = time.time()
    print("=" * 60)
    print("完整选股池构建")
    print("=" * 60)

    # 获取股票列表 + 实时行情
    df_list = fetch_stock_list()
    df_list['code'] = df_list['code'].astype(str).str.zfill(6)
    codes = df_list['code'].tolist()
    print(f"\n全量股票: {len(codes)} 只")

    print("\n[Step 1] 获取实时行情...")
    df_spot = fetch_spot_batch(codes)
    print(f"获取行情: {len(df_spot)} 只")

    # 第一层
    print("\n[Step 2] 第一层: 基础准入...")
    df_passed, excl_basic = filter_basic(df_spot, params)

    # 第二层（财务数据，当前跳过）
    print("\n[Step 3] 第二层: 垃圾股排除...")
    financial_data = {}  # TODO: 接入财务数据源
    df_passed, excl_quality = filter_quality(df_passed, financial_data, params)

    # 第三层（K 线）
    print(f"\n[Step 4] 第三层: K 线过滤 (前 {max_kline_fetch} 只)...")
    df_pool, excl_kline = filter_kline(df_passed, params, max_fetch=max_kline_fetch)

    # 汇总
    all_excluded = pd.concat([excl_basic, excl_quality, excl_kline], ignore_index=True)
    df_pool['board'] = df_pool['code'].astype(str).str.zfill(6).apply(classify_board)

    # 保存
    df_pool.to_csv(cache_file, index=False)
    all_excluded.to_csv(excluded_cache, index=False)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"选股池构建完成: {len(df_pool)} 只 (排除 {len(all_excluded)})")
    print(f"耗时: {elapsed:.1f}s")
    if len(df_pool) > 0:
        print(df_pool['board'].value_counts().to_string())

    return df_pool, all_excluded


# ── 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("选股池构建测试")
    print("=" * 60)

    # 快速模式
    pool = build_pool_fast(use_cache=False)
    print(f"\n快速选股池: {len(pool)} 只")
    print(pool['board'].value_counts().to_string())

    if len(pool) > 0:
        print(f"\n市值: min={pool['market_cap'].min():.0f}, median={pool['market_cap'].median():.0f}, max={pool['market_cap'].max():.0f}")
        print(f"成交额: min={pool['amount'].min()/10000:.0f}万, median={pool['amount'].median()/10000:.0f}万")

        # 验证过滤干净
        code_str = pool['code'].astype(str).str.zfill(6)
        bse = code_str.str.startswith('920') | code_str.str.startswith('8') | code_str.str.startswith('4')
        kcb = code_str.str.startswith('688') | code_str.str.startswith('689')
        st = pool['name'].str.contains(r'^\*?ST', na=False)
        print(f"\n北交所残留: {bse.sum()}, 科创板残留: {kcb.sum()}, ST残留: {st.sum()}")

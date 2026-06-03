"""
财务数据获取与选股池第二层过滤
====================================
基于 AKShare stock_financial_report_sina 接口获取财务数据，
实现垃圾股排除规则：
  1. 净资产为负（所有者权益 < 0）
  2. 连续亏损 + 营收 < 1亿
  3. 商誉占比 ≥ 50%（商誉 / 资产总计）

用法:
  from scripts.financial_filter import fetch_financial, filter_quality_batch
  
  # 获取单只股票财务数据
  fin = fetch_financial('600519')
  
  # 批量过滤
  pool_filtered, excluded = filter_quality_batch(pool, max_stocks=3000)
"""

import os, sys, time, json
import pandas as pd
import numpy as np
from typing import Optional, Dict, Tuple, List
from pathlib import Path

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

import akshare as ak

# ── 缓存 ──────────────────────────────────────
CACHE_DIR = os.path.join(_BASE_DIR, "data", "cache")
FIN_CACHE_DIR = os.path.join(CACHE_DIR, "financial")
os.makedirs(FIN_CACHE_DIR, exist_ok=True)

# ── 财务数据获取 ──────────────────────────────
def fetch_financial(code: str, use_cache: bool = True, cache_days: int = 30) -> Optional[Dict]:
    """
    获取单只股票最新财务数据
    返回 dict with keys:
      - code, name
      - report_date: 报告日期
      - revenue: 营业总收入（元）
      - net_profit: 归属于母公司所有者的净利润（元）
      - total_assets: 资产总计（元）
      - total_liabilities: 负债合计（元）
      - equity: 所有者权益（元）= total_assets - total_liabilities
      - goodwill: 商誉（元）
      - goodwill_ratio: 商誉 / 资产总计
      - years_data: 近N年数据 DataFrame
    """
    cache_file = os.path.join(FIN_CACHE_DIR, f"{code}.json")

    # 检查缓存
    if use_cache and os.path.exists(cache_file):
        age_days = (time.time() - os.path.getmtime(cache_file)) / 86400
        if age_days < cache_days:
            try:
                with open(cache_file, 'r') as f:
                    return json.load(f)
            except:
                pass

    try:
        # 资产负债表
        df_bs = ak.stock_financial_report_sina(stock=code, symbol='资产负债表')
        if df_bs is None or len(df_bs) == 0:
            return None

        # 利润表
        df_pl = ak.stock_financial_report_sina(stock=code, symbol='利润表')
        if df_pl is None or len(df_pl) == 0:
            return None

        # 取最新报告期
        bs = df_bs.iloc[0]
        pl = df_pl.iloc[0]

        # 提取字段
        total_assets = _safe_float(bs.get('资产总计'))
        total_liabilities = _safe_float(bs.get('负债合计'))
        equity = _safe_float(bs.get('所有者权益(或股东权益)合计'))
        goodwill = _safe_float(bs.get('商誉'))
        revenue = _safe_float(pl.get('营业总收入'))
        net_profit = _safe_float(pl.get('归属于母公司所有者的净利润'))

        # 如果所有者权益为空，用资产-负债计算
        if (equity is None or equity == 0) and total_assets is not None and total_liabilities is not None:
            equity = total_assets - total_liabilities

        # 商誉占比
        goodwill_ratio = None
        if goodwill is not None and total_assets is not None and total_assets > 0:
            goodwill_ratio = goodwill / total_assets

        # 近4年盈利情况（用于判断连续亏损）
        years_data = []
        for i in range(min(4, len(df_pl))):
            row = df_pl.iloc[i]
            y_revenue = _safe_float(row.get('营业总收入'))
            y_profit = _safe_float(row.get('归属于母公司所有者的净利润'))
            years_data.append({
                'report_date': str(row.get('报告日', '')),
                'revenue': y_revenue,
                'net_profit': y_profit,
            })

        result = {
            'code': code,
            'report_date': str(bs.get('报告日', '')),
            'revenue': revenue,
            'net_profit': net_profit,
            'total_assets': total_assets,
            'total_liabilities': total_liabilities,
            'equity': equity,
            'goodwill': goodwill,
            'goodwill_ratio': goodwill_ratio,
            'years_data': years_data,
        }

        # 缓存
        with open(cache_file, 'w') as f:
            json.dump(result, f, ensure_ascii=False, default=str)

        return result

    except Exception as e:
        return None


def _safe_float(val) -> Optional[float]:
    """安全转 float"""
    if val is None:
        return None
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


# ── 第二层过滤规则 ────────────────────────────
def check_quality(fin: Dict, params: dict = None) -> Tuple[bool, str]:
    """
    垃圾股排除检查
    返回 (pass: bool, reason: str)
    """
    params = params or {}
    min_revenue = params.get('min_revenue', 1e8)  # 1亿
    max_goodwill_ratio = params.get('max_goodwill_ratio', 0.5)  # 50%

    if fin is None:
        return True, ''  # 无数据不排除（宁可放过）

    equity = fin.get('equity')
    revenue = fin.get('revenue')
    net_profit = fin.get('net_profit')
    goodwill_ratio = fin.get('goodwill_ratio')
    years_data = fin.get('years_data', [])

    # 1. 净资产为负
    if equity is not None and equity < 0:
        return False, f'净资产为负({equity/1e8:.2f}亿)'

    # 2. 商誉占比过高
    if goodwill_ratio is not None and goodwill_ratio >= max_goodwill_ratio:
        return False, f'商誉占比过高({goodwill_ratio:.1%})'

    # 3. 连续亏损 + 营收不足
    # 定义：最近2年净利润都为负，且最近1年营收 < 1亿
    if len(years_data) >= 2:
        y1_profit = years_data[0].get('net_profit')  # 最近1年
        y2_profit = years_data[1].get('net_profit')  # 前1年
        y1_revenue = years_data[0].get('revenue')

        if (y1_profit is not None and y1_profit < 0 and
            y2_profit is not None and y2_profit < 0 and
            y1_revenue is not None and y1_revenue < min_revenue):
            return False, f'连续亏损+营收不足({y1_revenue/1e8:.2f}亿)'

    return True, ''


# ── 批量过滤 ──────────────────────────────────
def filter_quality_batch(
    pool: pd.DataFrame,
    params: dict = None,
    max_stocks: int = 3000,
    use_cache: bool = True,
    cache_days: int = 30,
    delay: float = 0.3,
    n_workers: int = 8,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    对选股池批量执行第二层过滤（多线程并行）
    返回 (passed, excluded)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    params = params or {}
    codes = pool['code'].tolist()[:max_stocks]
    code_to_row = {}
    for _, row in pool.iterrows():
        code_to_row[str(row['code']).zfill(6)] = row

    passed = []
    excluded = []
    no_data = []
    lock = __import__('threading').Lock()

    def _process(code):
        row = code_to_row.get(code)
        if row is None:
            return None
        name = str(row.get('name', ''))
        fin = fetch_financial(code, use_cache=use_cache, cache_days=cache_days)
        ok, reason = check_quality(fin, params)
        return {
            'code': code, 'name': name, 'row': row,
            'ok': ok, 'reason': reason, 'has_data': fin is not None,
            'board': row.get('board', ''),
            'equity': fin.get('equity') if fin else None,
            'revenue': fin.get('revenue') if fin else None,
            'goodwill_ratio': fin.get('goodwill_ratio') if fin else None,
        }

    print(f"[filter_quality] 开始 {len(codes)} 只 (workers={n_workers})...")
    completed = 0

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_process, str(c).zfill(6)): c for c in codes}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result is None:
                continue
            if result['ok']:
                passed.append(result['row'])
            elif not result['has_data']:
                no_data.append({'code': result['code'], 'name': result['name'],
                               'reason': '无财务数据', 'filter_layer': 'quality'})
                passed.append(result['row'])  # 无数据默认通过
            else:
                excluded.append({
                    'code': result['code'], 'name': result['name'],
                    'board': result['board'], 'reason': result['reason'],
                    'filter_layer': 'quality',
                    'equity': result['equity'],
                    'revenue': result['revenue'],
                    'goodwill_ratio': result['goodwill_ratio'],
                })
            if completed % 500 == 0:
                print(f"  [quality] 进度 {completed}/{len(codes)}, 排除 {len(excluded)}")

    df_passed = pd.DataFrame(passed) if passed else pd.DataFrame()
    df_excluded = pd.DataFrame(excluded) if excluded else pd.DataFrame()

    print(f"[filter_quality] 通过 {len(df_passed)} / {len(codes)} (排除 {len(excluded)}, 无数据 {len(no_data)})")
    if len(excluded) > 0:
        print(f"  排除原因统计:")
        for _, ex in df_excluded.iterrows():
            print(f"    {ex['code']} {ex['name']}: {ex['reason']}")

    return df_passed, df_excluded


# ── 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    print("财务数据获取测试")
    print("=" * 60)

    # 测试几只典型股票
    test_stocks = [
        ('600519', '茅台'),      # 正常
        ('000001', '平安银行'),   # 正常
        ('300001', '特锐德'),     # 创业板
    ]

    for code, expected_name in test_stocks:
        print(f"\n--- {code} ---")
        fin = fetch_financial(code, use_cache=False)
        if fin:
            print(f"  报告期: {fin['report_date']}")
            print(f"  营收: {fin['revenue']/1e8:.2f}亿" if fin['revenue'] else "  营收: N/A")
            print(f"  净利润: {fin['net_profit']/1e8:.2f}亿" if fin['net_profit'] else "  净利润: N/A")
            print(f"  净资产: {fin['equity']/1e8:.2f}亿" if fin['equity'] else "  净资产: N/A")
            print(f"  商誉占比: {fin['goodwill_ratio']:.2%}" if fin['goodwill_ratio'] else "  商誉: N/A")
            ok, reason = check_quality(fin)
            print(f"  质量检查: {'✅ 通过' if ok else f'❌ {reason}'}")
        else:
            print(f"  ❌ 获取失败")

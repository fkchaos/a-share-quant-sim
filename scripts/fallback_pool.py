"""
备选股票池 — 中证500 + 沪深300
================================
当主选股池（全A股多层过滤）因条件过严导致数量不足时，
回落到备选池：中证500 + 沪深300 成分股（排除科创板/北交所/ST）

用法:
  from scripts.fallback_pool import build_fallback_pool
  
  pool = build_fallback_pool()  # ~500-600只
"""

import os, sys, time
import pandas as pd
import numpy as np

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

import akshare as ak

CACHE_DIR = os.path.join(_BASE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def fetch_index_constituents(index_symbol: str) -> pd.DataFrame:
    """获取指数成分股"""
    cache_file = os.path.join(CACHE_DIR, f"index_{index_symbol}.csv")
    
    # 缓存7天
    if os.path.exists(cache_file):
        age_days = (time.time() - os.path.getmtime(cache_file)) / 86400
        if age_days < 7:
            return pd.read_csv(cache_file, dtype={'code': str})
    
    try:
        df = ak.index_stock_cons(symbol=index_symbol)
        df = df.rename(columns={'品种代码': 'code', '品种名称': 'name'})
        df['code'] = df['code'].astype(str).str.zfill(6)
        df['index'] = index_symbol
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"❌ 获取 {index_symbol} 成分股失败: {e}")
        return pd.DataFrame()


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


def build_fallback_pool(
    exclude_kcb: bool = True,
    exclude_bse: bool = True,
    exclude_st: bool = True,
    min_market_cap: float = 0,  # 备选池不过滤市值
) -> pd.DataFrame:
    """
    构建备选股票池：中证500 + 沪深300
    自动去重（中证500不含沪深300成分股，但以防万一）
    """
    print("=" * 60)
    print("备选股票池构建 (中证500 + 沪深300)")
    print("=" * 60)
    
    # 获取成分股
    df_hs300 = fetch_index_constituents('000300')
    df_zz500 = fetch_index_constituents('000905')
    
    print(f"沪深300: {len(df_hs300)} 只")
    print(f"中证500: {len(df_zz500)} 只")
    
    # 合并去重
    df = pd.concat([df_hs300, df_zz500], ignore_index=True)
    df = df.drop_duplicates(subset=['code'])
    print(f"合并去重: {len(df)} 只")
    
    # 板块分类
    df['board'] = df['code'].apply(classify_board)
    
    # 过滤
    before = len(df)
    
    if exclude_kcb:
        df = df[~df['board'].isin(['科创板'])]
    if exclude_bse:
        df = df[~df['board'].isin(['北交所'])]
    if exclude_st:
        df = df[~df['name'].str.contains(r'^\*?ST', na=False)]
    
    print(f"过滤后: {len(df)} 只 (排除 {before - len(df)})")
    print(df['board'].value_counts().to_string())
    
    # 保存
    cache_file = os.path.join(CACHE_DIR, "fallback_pool.csv")
    df.to_csv(cache_file, index=False)
    print(f"\n已保存: {cache_file}")
    
    return df


def get_fallback_pool(use_cache: bool = True, cache_ttl_hours: int = 24) -> pd.DataFrame:
    """获取备选池（带缓存）"""
    cache_file = os.path.join(CACHE_DIR, "fallback_pool.csv")
    
    if use_cache and os.path.exists(cache_file):
        age_hours = (time.time() - os.path.getmtime(cache_file)) / 3600
        if age_hours < cache_ttl_hours:
            return pd.read_csv(cache_file, dtype={'code': str})
    
    return build_fallback_pool()


# ── 测试入口 ──────────────────────────────────
if __name__ == "__main__":
    pool = build_fallback_pool()
    print(f"\n备选池: {len(pool)} 只")
    print(pool['board'].value_counts().to_string())
    
    # 验证
    code_str = pool['code'].astype(str).str.zfill(6)
    kcb = code_str.str.startswith('688') | code_str.str.startswith('689')
    bse = code_str.str.startswith('920') | code_str.str.startswith('8') | code_str.str.startswith('4')
    st = pool['name'].str.contains(r'^\*?ST', na=False)
    print(f"\n科创板残留: {kcb.sum()}, 北交所残留: {bse.sum()}, ST残留: {st.sum()}")

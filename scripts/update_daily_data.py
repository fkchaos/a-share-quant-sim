"""
A股日频数据更新脚本 (腾讯行情接口)
=====================================
每天收盘后运行，从腾讯行情接口拉取最新K线数据追加到本地 CSV

用法:
  python update_daily_data.py          # 更新所有股票
  python update_daily_data.py --date 20260528  # 更新指定日期
  python update_daily_data.py --check  # 只检查不更新
"""
import os, sys, time, argparse
import pandas as pd
import numpy as np
import requests
import json
from datetime import datetime, timedelta

import os
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://stockapp.finance.qq.com/',
}

# 腾讯接口列顺序: [日期, 开盘, 收盘, 最高, 最低, 成交量(手)]
TX_COLS = ['date', 'open', 'close', 'high', 'low', 'volume']

def get_stock_list():
    """从本地 CSV 文件获取股票列表"""
    files = sorted([f for f in os.listdir(DAILY_DIR) if f.endswith('.csv')])
    return [f.replace('.csv', '') for f in files]

def get_local_latest_date(code):
    """获取本地某只股票的最新日期"""
    csv_file = os.path.join(DAILY_DIR, f"{code}.csv")
    if not os.path.exists(csv_file):
        return None
    df = pd.read_csv(csv_file, index_col='date', parse_dates=True)
    if len(df) == 0:
        return None
    return df.index[-1]

def fetch_tencent_kline(code, days=30):
    """
    从腾讯行情接口获取前复权日K线数据
    返回: DataFrame with columns [open, high, low, close, volume, amount]
    """
    # 判断市场前缀
    if code.startswith('6') or code.startswith('9'):
        tx_code = f"sh{code}"
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        tx_code = f"sz{code}"
    else:
        tx_code = f"sz{code}"
    
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        'param': f"{tx_code},day,,,{days},qfq"
    }
    
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()
        
        if data.get('code') != 0:
            return None
        
        stock_data = data.get('data', {}).get(tx_code.replace('sh', '').replace('sz', ''), None)
        if stock_data is None:
            # 尝试带前缀的键
            stock_data = data.get('data', {}).get(tx_code, None)
        
        if stock_data is None:
            return None
        
        qfq_key = 'qfqday'
        if qfq_key not in stock_data:
            # 可能没有前复权数据，用普通 day
            if 'day' in stock_data:
                qfq_key = 'day'
            else:
                return None
        
        klines = stock_data[qfq_key]
        if not klines or len(klines) == 0:
            return None
        
        # 解析数据
        records = []
        for k in klines:
            if len(k) < 6:
                continue
            records.append({
                'date': k[0],
                'open': float(k[1]),
                'close': float(k[2]),
                'high': float(k[3]),
                'low': float(k[4]),
                'volume': float(k[5]),
                'amount': 0,  # 腾讯接口不提供成交额，后续可估算
            })
        
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        df = df.sort_index()
        
        # 估算成交额: 均价 * 成交量
        # 腾讯接口返回的 volume 单位是"股"，不需要再乘 100
        if df['amount'].eq(0).all():
            vwap = (df['open'] + df['close'] + df['high'] + df['low']) / 4
            df['amount'] = vwap * df['volume']
        
        # 添加 turnover 和 outstanding_share 列（用 NaN 填充，后续不需要的话不影响策略）
        df['outstanding_share'] = np.nan
        df['turnover'] = np.nan
        
        return df
    
    except Exception as e:
        raise e

def fetch_tencent_spot(code):
    """从腾讯获取当日实时行情（用于补充）"""
    if code.startswith('6') or code.startswith('9'):
        tx_code = f"sh{code}"
    else:
        tx_code = f"sz{code}"
    
    url = f"http://qt.gtimg.cn/q={tx_code}"
    
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        text = r.text
        
        # 解析腾讯行情字符串
        # 格式: v_sz000001="51~平安银行~000001~现价~昨收~今开~成交量(手)~...~最高~最低~..."
        if '~' not in text:
            return None
        
        parts = text.split('~')
        if len(parts) < 50:
            return None
        
        return {
            'name': parts[1],
            'code': parts[2],
            'close': float(parts[3]),
            'prev_close': float(parts[4]),
            'open': float(parts[5]),
            'volume': float(parts[6]),  # 手
            'high': float(parts[33]) if len(parts) > 33 else 0,
            'low': float(parts[34]) if len(parts) > 34 else 0,
            'change_pct': float(parts[32]) if len(parts) > 32 else 0,
            'amount': float(parts[37]) * 10000 if len(parts) > 37 else 0,  # 万 -> 元
        }
    except:
        return None

def update_stock(code, days=5):
    """
    更新单只股票的数据
    返回: (新增行数, 是否成功)
    """
    csv_file = os.path.join(DAILY_DIR, f"{code}.csv")

    local_latest = get_local_latest_date(code)

    # 获取远程数据
    df = fetch_tencent_kline(code, days=days)
    if df is None or len(df) == 0:
        return 0, False

    # 如果本地有数据，只保留本地没有的新数据
    if local_latest is not None:
        new_data = df[df.index > local_latest]
        if len(new_data) == 0:
            return 0, True  # 已经是最新
    else:
        new_data = df

    if local_latest is not None:
        # 追加新数据
        old_df = pd.read_csv(csv_file, index_col='date', parse_dates=True)
        combined = pd.concat([old_df, new_data])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined = combined.sort_index()
        combined.to_csv(csv_file)
    else:
        # 新文件
        new_data.to_csv(csv_file)

    return len(new_data), True

def update_all_stocks(target_date=None):
    """更新所有股票"""
    print("=" * 60)
    print(f"A股日频数据更新 (腾讯行情) - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    os.makedirs(DAILY_DIR, exist_ok=True)
    
    stocks = get_stock_list()
    print(f"\n📋 本地股票数量: {len(stocks)}")
    
    if not stocks:
        print("❌ 没有找到本地股票数据，请先初始化")
        return
    
    # 检查当前数据状态
    latest_dates = {}
    for code in stocks:
        d = get_local_latest_date(code)
        if d is not None:
            latest_dates[code] = d
    
    if latest_dates:
        newest = max(latest_dates.values())
        oldest = min(latest_dates.values())
        print(f"📅 本地数据范围: {oldest.date()} ~ {newest.date()}")
    
    today = datetime.now().date()
    weekday = today.weekday()
    
    if target_date is not None:
        need_update_date = target_date
        print(f"\n🎯 指定更新日期: {need_update_date}")
    else:
        need_update_date = today
    
    # 判断是否需要更新，并筛选出需要更新的股票
    need_update_codes = []
    if latest_dates:
        newest = max(latest_dates.values())
        oldest = min(latest_dates.values())
        print(f"📅 本地数据范围: {oldest.date()} ~ {newest.date()}")

        # 周末跳过（仅自动模式，--date 指定日期时不跳过）
        if target_date is None:
            today = datetime.now().date()
            weekday = today.weekday()
            if weekday >= 5:
                friday = today - timedelta(days=(weekday - 4))
                if newest.date() >= friday:
                    print(f"✅ 周末无需更新 (最新: {newest.date()})")
                    return True

        # 筛选：本地最新日期 < 需要更新日期
        for code in stocks:
            d = latest_dates.get(code)
            if d is None or d.date() < need_update_date:
                need_update_codes.append(code)

        if not need_update_codes:
            print(f"\n✅ 所有 {len(latest_dates)} 只股票数据已经是最新（最新: {newest.date()}），无需更新")
            return True

        print(f"\n📊 {len(need_update_codes)}/{len(latest_dates)} 只股票需要更新")
    else:
        need_update_codes = stocks

    print(f"\n🔄 开始更新数据...")
    print(f"  目标: 补充到 {need_update_date}")
    print(f"  更新数量: {len(need_update_codes)} 只")

    success = 0
    fail = 0
    no_new = 0
    fail_list = []

    for i, code in enumerate(need_update_codes):
        local_date = latest_dates.get(code)
        days = 10  # 请求最近10天数据

        # 如果本地日期差距较大，多请求一些
        if local_date is not None:
            gap = (need_update_date - local_date.date()).days
            if gap > 0:
                days = gap + 5  # 多请求几天防止遗漏

        try:
            new_rows, ok = update_stock(code, days=days)
            if not ok:
                fail += 1
                fail_list.append(code)
            elif new_rows > 0:
                success += 1
            else:
                no_new += 1
        except Exception as e:
            fail += 1
            fail_list.append(code)
            if fail <= 3:
                print(f"  ❌ {code}: {e}")

        if (i + 1) % 30 == 0:
            print(f"  进度: {i+1}/{len(need_update_codes)} ✅{success} ❌{fail} ⏭️{no_new}")

        time.sleep(0.15)  # 避免请求过快
    
    # 重试失败（只重试失败的）
    if fail_list:
        print(f"\n🔁 重试 {len(fail_list)} 只失败的股票...")
        time.sleep(3)
        retry_fail = []
        for code in fail_list:
            try:
                new_rows, ok = update_stock(code, days=20)
                if ok:
                    if new_rows > 0:
                        success += 1
                        fail -= 1
                    else:
                        fail -= 1
                        no_new += 1
                else:
                    retry_fail.append(code)
            except:
                retry_fail.append(code)
            time.sleep(0.3)
        fail_list = retry_fail
    
    print(f"\n📊 更新结果:")
    print(f"  新增数据: {success} 只股票")
    print(f"  无需更新: {no_new} 只股票")
    print(f"  更新失败: {fail} 只股票")
    
    if fail_list:
        print(f"  失败列表: {fail_list[:20]}{'...' if len(fail_list) > 20 else ''}")
    
    # 验证 + 时效校验
    final_dates = {}
    for code in stocks:
        d = get_local_latest_date(code)
        if d is not None:
            final_dates[code] = d

    if final_dates:
        newest_final = max(final_dates.values())
        oldest_final = min(final_dates.values())
        today = datetime.now().date()
        days_behind = (today - newest_final.date()).days

        print(f"\n  最新数据日期: {newest_final.date()}")
        print(f"  最旧数据日期: {oldest_final.date()}")

        # 时效警告
        if days_behind > 3:
            print(f"\n  ⚠️ ⚠️ ⚠️ 数据严重滞后！最新数据距今 {days_behind} 天")
            print(f"     数据停留在 {newest_final.date()}，请检查腾讯接口或网络")
        elif days_behind > 1:
            print(f"\n  ⚠️ 数据轻微滞后：最新数据距今 {days_behind} 天（{newest_final.date()}）")
        else:
            print(f"\n  ✅ 数据时效正常（最新: {newest_final.date()}）")

        # 检查有没有股票日期严重不一致
        stale_threshold = newest_final - timedelta(days=5)
        stale_stocks = [c for c, d in final_dates.items() if d < stale_threshold]
        if stale_stocks:
            print(f"\n  ⚠️ {len(stale_stocks)} 只股票数据滞后 >5 天:")
            for c in stale_stocks[:10]:
                print(f"       {c}: {final_dates[c].date()}")

        print("=" * 60)

    return fail == 0

def check_status():
    """检查数据状态"""
    print(f"📊 数据状态检查 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    stocks = get_stock_list()
    print(f"  本地股票: {len(stocks)} 只")
    
    latest_dates = {}
    for code in stocks:
        d = get_local_latest_date(code)
        if d is not None:
            latest_dates[code] = d
    
    if not latest_dates:
        print("  没有有效数据")
        return
    
    newest = max(latest_dates.values())
    oldest = min(latest_dates.values())
    today = datetime.now().date()
    
    up_to_date = sum(1 for d in latest_dates.values() if d.date() >= today - timedelta(days=1))
    
    print(f"  最新日期: {newest.date()}")
    print(f"  最旧日期: {oldest.date()}")
    print(f"  已是最新(T或T-1): {up_to_date}/{len(latest_dates)}")
    print(f"  缓存今天是: {today}")

    # 时效警告
    days_behind = (today - newest.date()).days
    if days_behind > 3:
        print(f"\n  ⚠️ ⚠️ ⚠️ 数据严重滞后！最新数据距今 {days_behind} 天")
    elif days_behind > 1:
        print(f"\n  ⚠️ 数据轻微滞后：最新数据距今 {days_behind} 天")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='A股日频数据更新')
    parser.add_argument('--date', type=str, help='指定更新目标日期 YYYYMMDD')
    parser.add_argument('--check', action='store_true', help='只检查不更新')
    args = parser.parse_args()
    
    if args.check:
        check_status()
    else:
        target = None
        if args.date:
            target = datetime.strptime(args.date, '%Y%m%d').date()
        update_all_stocks(target)

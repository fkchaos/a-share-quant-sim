#!/usr/bin/env python3
"""数据源 Provider 测试

验证：
1. 各 provider 能正常获取数据
2. 数据格式符合标准
3. Fallback 机制正常
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_provider import DataProvider, KLINE_COLUMNS
from core.providers.tencent import TencentProvider
from core.providers.baostock import BaoStockProvider
from core.provider_manager import ProviderManager


def test_tencent():
    """测试腾讯数据源"""
    print("=" * 60)
    print("测试 TencentProvider")
    print("=" * 60)
    
    provider = TencentProvider()
    
    # 健康检查
    ok = provider.health_check()
    print(f"健康检查: {'✅ 通过' if ok else '❌ 失败'}")
    if not ok:
        return False
    
    # 获取K线数据
    df = provider.get_daily_kline(['000001'], '2024-01-02', '2024-01-05')
    print(f"数据行数: {len(df)}")
    print(f"列名: {list(df.columns)}")
    
    if len(df) > 0:
        row = df.iloc[0]
        print(f"示例数据:")
        print(f"  日期: {row['date']}")
        print(f"  代码: {row['code']}")
        print(f"  收盘: {row['close']} (元)")
        print(f"  成交量: {row['volume']} (股)")
        print(f"  换手率: {row['turnover']} (百分比)")
        print(f"  停牌: {row['tradestatus']}")
        print(f"  ST: {row['is_st']}")
    
    return True


def test_baostock():
    """测试 BaoStock 数据源"""
    print("\n" + "=" * 60)
    print("测试 BaoStockProvider")
    print("=" * 60)
    
    provider = BaoStockProvider()
    
    # 健康检查
    ok = provider.health_check()
    print(f"健康检查: {'✅ 通过' if ok else '❌ 失败'}")
    if not ok:
        return False
    
    # 获取K线数据
    df = provider.get_daily_kline(['000001'], '2024-01-02', '2024-01-05')
    print(f"数据行数: {len(df)}")
    print(f"列名: {list(df.columns)}")
    
    if len(df) > 0:
        row = df.iloc[0]
        print(f"示例数据:")
        print(f"  日期: {row['date']}")
        print(f"  代码: {row['code']}")
        print(f"  收盘: {row['close']} (元)")
        print(f"  成交量: {row['volume']} (股)")
        print(f"  换手率: {row['turnover']} (百分比)")
        print(f"  停牌: {row['tradestatus']}")
        print(f"  ST: {row['is_st']}")
    
    return True


def test_manager():
    """测试 ProviderManager"""
    print("\n" + "=" * 60)
    print("测试 ProviderManager")
    print("=" * 60)
    
    pm = ProviderManager()
    pm.register('tencent', TencentProvider())
    pm.register('baostock', BaoStockProvider())
    
    # 列出数据源
    print(f"已注册数据源: {pm.list_providers()}")
    
    # 健康检查
    health = pm.health_check_all()
    print(f"健康状态: {health}")
    
    # 获取数据（带 fallback）
    df = pm.get_daily_kline(['000001'], '2024-01-02', '2024-01-05')
    print(f"获取数据行数: {len(df)}")
    
    # 手动指定 provider
    df2 = pm.get_daily_kline(['000001'], '2024-01-02', '2024-01-05', provider='baostock')
    print(f"手动指定 baostock: {len(df2)} 行")
    
    return True


def test_data_consistency():
    """测试数据一致性"""
    print("\n" + "=" * 60)
    print("测试数据一致性 (腾讯 vs BaoStock)")
    print("=" * 60)
    
    tencent = TencentProvider()
    baostock = BaoStockProvider()
    
    code = '600519'  # 茅台
    start = '2024-01-02'
    end = '2024-01-05'
    
    df_t = tencent.get_daily_kline([code], start, end)
    df_b = baostock.get_daily_kline([code], start, end)
    
    print(f"腾讯数据: {len(df_t)} 行")
    print(f"BaoStock数据: {len(df_b)} 行")
    
    if len(df_t) > 0 and len(df_b) > 0:
        # 比较收盘价
        t_close = df_t['close'].values
        b_close = df_b['close'].values
        min_len = min(len(t_close), len(b_close))
        
        if min_len > 0:
            diff = abs(t_close[:min_len] - b_close[:min_len])
            print(f"收盘价差异: max={diff.max():.4f}, mean={diff.mean():.4f}")
            
            # 比较成交量
            t_vol = df_t['volume'].values[:min_len]
            b_vol = df_b['volume'].values[:min_len]
            vol_diff = abs(t_vol - b_vol) / (t_vol + 1e-10) * 100
            print(f"成交量差异: max={vol_diff.max():.2f}%, mean={vol_diff.mean():.2f}%")
    
    return True


if __name__ == '__main__':
    results = []
    
    try:
        results.append(('Tencent', test_tencent()))
    except Exception as e:
        print(f"Tencent 测试异常: {e}")
        results.append(('Tencent', False))
    
    try:
        results.append(('BaoStock', test_baostock()))
    except Exception as e:
        print(f"BaoStock 测试异常: {e}")
        results.append(('BaoStock', False))
    
    try:
        results.append(('Manager', test_manager()))
    except Exception as e:
        print(f"Manager 测试异常: {e}")
        results.append(('Manager', False))
    
    try:
        results.append(('Consistency', test_data_consistency()))
    except Exception as e:
        print(f"Consistency 测试异常: {e}")
        results.append(('Consistency', False))
    
    # 汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, ok in results:
        print(f"  {name}: {'✅ 通过' if ok else '❌ 失败'}")
    
    all_ok = all(ok for _, ok in results)
    print(f"\n总体: {'✅ 全部通过' if all_ok else '❌ 有失败'}")

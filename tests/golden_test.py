#!/usr/bin/env python3
"""Golden Test — 回归测试套件

用于验证策略/环境/数据变动前后，核心逻辑的结果一致性。

测试层级：
1. 单元测试：因子计算、选股逻辑
2. 集成测试：单策略回测
3. 回归测试：与历史基准对比

使用方法：
    python -m pytest tests/golden_test.py -v
    python tests/golden_test.py  # 直接运行
"""
import sys, os
import json
import hashlib
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

# ── Golden 基准数据 ──────────────────────────────────────────────
# 这些值来自历史回测，变动前后应保持一致
GOLDEN_FILE = Path(__file__).parent / "golden_baselines.json"

DEFAULT_BASELINES = {
    "v39g_full_backtest": {
        "description": "v39g 全量回测 (2021-01-01 ~ 2026-05-31)",
        "params": {
            "strategy": "v39g",
            "start": "2021-01-01",
            "end": "2026-05-31",
            "pool": "zz1800",
        },
        "expected": {
            "total_return_pct": 93.95,
            "sharpe_ratio": 0.533,
            "max_drawdown_pct": -35.20,
            "final_nav": 387892.21,
        },
        "tolerance": {
            "total_return_pct": 2.0,  # ±2%
            "sharpe_ratio": 0.02,
            "max_drawdown_pct": 0.5,
            "final_nav": 5000,
        }
    },
    "v39g_factor_calc": {
        "description": "v39g 因子计算一致性",
        "test": "factor_consistency",
    },
    "data_source_consistency": {
        "description": "数据源一致性（腾讯 vs BaoStock）",
        "test": "data_consistency",
    }
}


def load_baselines():
    """加载 Golden 基准"""
    if GOLDEN_FILE.exists():
        with open(GOLDEN_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_BASELINES


def save_baselines(baselines):
    """保存 Golden 基准"""
    with open(GOLDEN_FILE, 'w') as f:
        json.dump(baselines, f, indent=2, ensure_ascii=False)


def check_tolerance(actual, expected, tolerance, name=""):
    """检查是否在容差范围内"""
    passed = True
    details = []
    
    for key in expected:
        if key not in actual:
            details.append(f"  ❌ {key}: 缺失")
            passed = False
            continue
        
        exp_val = expected[key]
        act_val = actual[key]
        tol = tolerance.get(key, 0)
        
        diff = abs(act_val - exp_val)
        if diff > tol:
            details.append(f"  ❌ {key}: {act_val} vs {exp_val} (差{diff:.4f}, 容差{tol})")
            passed = False
        else:
            details.append(f"  ✅ {key}: {act_val} (差{diff:.4f}, 容差{tol})")
    
    return passed, details


# ── 测试函数 ──────────────────────────────────────────────────────

def test_v39g_full_backtest():
    """测试 v39g 全量回测结果"""
    print("\n" + "="*60)
    print("测试 1: v39g 全量回测结果")
    print("="*60)
    
    baselines = load_baselines()
    baseline = baselines.get("v39g_full_backtest")
    if not baseline:
        print("  ⚠️ 无基准数据，跳过")
        return None
    
    # 运行回测
    from scripts.backtest.wf_runner import run_wf
    
    params = baseline["params"]
    print(f"  运行 v39g 全量回测...")
    print(f"  区间: {params['start']} ~ {params['end']}")
    
    # 捕获输出
    import io
    from contextlib import redirect_stdout
    
    f = io.StringIO()
    with redirect_stdout(f):
        run_wf(
            params["strategy"],
            train_days=252, test_days=252, step_days=252,
            start_date=params["start"], end_date=params["end"],
            full=True, pool_override=params["pool"]
        )
    
    output = f.getvalue()
    
    # 解析结果
    actual = {}
    for line in output.split('\n'):
        if '总收益率:' in line:
            actual['total_return_pct'] = float(line.split(':')[1].strip().replace('%', ''))
        elif '夏普比率:' in line:
            actual['sharpe_ratio'] = float(line.split(':')[1].strip())
        elif '最大回撤:' in line:
            actual['max_drawdown_pct'] = -float(line.split(':')[1].strip().replace('%', ''))
        elif '最终净值:' in line:
            actual['final_nav'] = float(line.split(':')[1].strip().replace(',', ''))
    
    if not actual:
        print("  ❌ 无法解析回测结果")
        return False
    
    # 检查容差
    passed, details = check_tolerance(
        actual, baseline["expected"], baseline["tolerance"], "v39g"
    )
    
    print(f"\n  结果:")
    for d in details:
        print(f"    {d}")
    
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


def test_factor_consistency():
    """测试因子计算一致性"""
    print("\n" + "="*60)
    print("测试 2: 因子计算一致性")
    print("="*60)
    
    import sqlite3
    from scripts.strategies.v39c_pv_resonance import calc_factors
    
    # 加载少量数据
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    codes = pd.read_sql_query("SELECT code FROM stock_pool_zz1800 LIMIT 10", conn)['code'].tolist()
    conn.close()
    
    # 构造简单面板
    np.random.seed(42)
    dates = pd.date_range('2021-01-01', periods=100, freq='B')
    n_stocks = len(codes)
    
    close_panel = pd.DataFrame(
        np.random.uniform(10, 50, (100, n_stocks)),
        index=dates, columns=codes
    )
    volume_panel = pd.DataFrame(
        np.random.uniform(1000, 10000, (100, n_stocks)),
        index=dates, columns=codes
    )
    amount_panel = close_panel * volume_panel * 100
    
    # 计算因子
    factors1 = calc_factors(close_panel, volume_panel, amount_panel,
                           close_panel, close_panel, close_panel)
    factors2 = calc_factors(close_panel, volume_panel, amount_panel,
                           close_panel, close_panel, close_panel)
    
    # 比较
    passed = True
    for key in factors1:
        if key not in factors2:
            print(f"  ❌ 因子 {key} 在第二次计算中缺失")
            passed = False
            continue
        
        val1 = factors1[key]
        val2 = factors2[key]
        
        if isinstance(val1, pd.DataFrame):
            if not val1.equals(val2):
                diff = (val1 - val2).abs().max().max()
                print(f"  ❌ 因子 {key}: 不一致 (最大差异 {diff})")
                passed = False
            else:
                print(f"  ✅ 因子 {key}: 一致")
        elif isinstance(val1, pd.Series):
            if not val1.equals(val2):
                diff = (val1 - val2).abs().max()
                print(f"  ❌ 因子 {key}: 不一致 (最大差异 {diff})")
                passed = False
            else:
                print(f"  ✅ 因子 {key}: 一致")
        else:
            if val1 != val2:
                print(f"  ❌ 因子 {key}: {val1} vs {val2}")
                passed = False
            else:
                print(f"  ✅ 因子 {key}: 一致")
    
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


def test_data_consistency():
    """测试数据源一致性"""
    print("\n" + "="*60)
    print("测试 3: 数据源一致性（腾讯 vs BaoStock）")
    print("="*60)
    
    import sqlite3
    from core.providers.baostock import BaoStockProvider
    
    # 测试股票
    code = '000001'
    date = '2021-01-04'
    
    # SQLite 腾讯
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    cursor = conn.execute(
        "SELECT volume FROM daily_kline WHERE code=? AND date=?",
        (code, date)
    )
    row = cursor.fetchone()
    tx_volume = row[0] if row else None
    conn.close()
    
    # BaoStock
    provider = BaoStockProvider()
    df = provider.get_daily_kline([code], date, date)
    bs_volume = df['volume'].iloc[0] if not df.empty else None
    
    if tx_volume is None or bs_volume is None:
        print(f"  ❌ 数据缺失: 腾讯={tx_volume}, BaoStock={bs_volume}")
        return False
    
    diff = abs(tx_volume - bs_volume)
    passed = diff <= 1  # 最多差1手
    
    print(f"  SQLite (腾讯): {tx_volume} 手")
    print(f"  BaoStock: {bs_volume} 手")
    print(f"  差异: {diff} 手")
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


def test_strategy_adapter_integrity():
    """测试策略适配器完整性"""
    print("\n" + "="*60)
    print("测试 4: 策略适配器完整性")
    print("="*60)
    
    from scripts.backtest.strategy_adapter import StrategyAdapter
    
    adapter = StrategyAdapter()
    
    # 检查关键策略是否注册
    required_strategies = ['v39g', 'v61b', 'v68']
    passed = True
    
    for strategy in required_strategies:
        if strategy in adapter._select_fns:
            print(f"  ✅ {strategy}: 已注册")
        else:
            print(f"  ❌ {strategy}: 未注册")
            passed = False
    
    # 检查参数是否完整
    for strategy in required_strategies:
        if strategy in adapter._risk_params:
            params = adapter._risk_params[strategy]
            if 'STOP_LOSS' in params and 'MAX_HOLDINGS' in params:
                print(f"  ✅ {strategy}: 参数完整")
            else:
                print(f"  ❌ {strategy}: 参数不完整")
                passed = False
        else:
            print(f"  ❌ {strategy}: 无参数")
            passed = False
    
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


# ── 主入口 ──────────────────────────────────────────────────────

def main():
    print("="*60)
    print("Golden Test — 回归测试套件")
    print("="*60)
    
    results = {}
    
    # 运行所有测试
    tests = [
        ("数据源一致性", test_data_consistency),
        ("因子计算一致性", test_factor_consistency),
        ("策略适配器完整性", test_strategy_adapter_integrity),
        ("v39g全量回测", test_v39g_full_backtest),
    ]
    
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results[name] = passed
        except Exception as e:
            print(f"\n  ❌ 异常: {e}")
            results[name] = False
    
    # 汇总
    print("\n" + "="*60)
    print("测试汇总")
    print("="*60)
    
    all_passed = True
    for name, passed in results.items():
        status = "✅" if passed else "❌"
        print(f"  {status} {name}")
        if not passed:
            all_passed = False
    
    print(f"\n{'='*60}")
    print(f"总体结果: {'✅ 全部通过' if all_passed else '❌ 存在失败'}")
    print(f"{'='*60}")
    
    return all_passed


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

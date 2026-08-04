#!/usr/bin/env python3
"""Golden Test — 回归测试套件（使用独立 Golden Dataset）

标准输入: tests/golden/golden_stocks.db (10只股票, 2021全年)
标准答案: tests/golden/golden_baselines.json

使用方法：
    python tests/golden_test.py
    python -m pytest tests/golden_test.py -v
"""
import sys, os
import json
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 路径定义 ──
GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_DB = GOLDEN_DIR / "golden_stocks.db"
GOLDEN_JSON = GOLDEN_DIR / "golden_baselines.json"

# 测试时使用 golden 数据库
os.environ["BACKTEST_DATA_DIR"] = str(GOLDEN_DIR)


def load_baselines():
    """加载标准参考答案"""
    if GOLDEN_JSON.exists():
        with open(GOLDEN_JSON) as f:
            return json.load(f)
    return {}


def check_tolerance(actual, expected, tolerance, name=""):
    """检查结果是否在容差范围内"""
    passed = True
    details = []
    for key in expected:
        a = actual.get(key)
        e = expected[key]
        t = tolerance.get(key, 0)
        if a is None:
            details.append(f"❌ {key}: 缺失")
            passed = False
        elif abs(a - e) > t:
            details.append(f"❌ {key}: {a} (差{abs(a-e):.4f}, 容差{t})")
            passed = False
        else:
            details.append(f"✅ {key}: {a} (差{abs(a-e):.4f}, 容差{t})")
    return passed, details


# ── 测试函数 ──────────────────────────────────────────────────────

def test_golden_data_exists():
    """测试标准输入数据是否存在"""
    print("\n" + "="*60)
    print("测试 0: 标准输入数据")
    print("="*60)
    
    if GOLDEN_DB.exists():
        import sqlite3
        conn = sqlite3.connect(str(GOLDEN_DB))
        n_kline = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
        n_pool = conn.execute("SELECT COUNT(*) FROM stock_pool").fetchone()[0]
        conn.close()
        print(f"  ✅ golden_stocks.db: {n_kline} 条K线, {n_pool} 只股票")
        return True
    else:
        print(f"  ❌ golden_stocks.db 不存在")
        print(f"     请先运行: python tests/golden/build_golden.py")
        return False


def test_v39g_golden_backtest():
    """测试 v39g 在 golden 数据集上的回测结果"""
    print("\n" + "="*60)
    print("测试 1: v39g golden 回测结果")
    print("="*60)
    
    baselines = load_baselines()
    baseline = baselines.get("v39g_golden_2021")
    if not baseline:
        print("  ⚠️ 无基准数据，跳过")
        return None
    
    from scripts.backtest.wf_runner import run_wf
    
    params = baseline["params"]
    print(f"  运行 v39g 全量回测...")
    print(f"  区间: {params['start']} ~ {params['end']}")
    print(f"  数据源: golden_stocks.db")
    
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
    
    passed, details = check_tolerance(
        actual, baseline["expected"], baseline["tolerance"], "v39g"
    )
    
    print(f"\n  结果:")
    for d in details:
        print(f"    {d}")
    
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


def test_v61b_golden_backtest():
    """测试 v61b 在 golden 数据集上的回测结果"""
    print("\n" + "="*60)
    print("测试 2: v61b golden 回测结果")
    print("="*60)
    
    baselines = load_baselines()
    baseline = baselines.get("v61b_golden_2021")
    if not baseline:
        print("  ⚠️ 无基准数据，跳过")
        return None
    
    from scripts.backtest.v61b_risk_scan import run_wf_overlay
    
    params = baseline["params"]
    print(f"  运行 v61b 全量回测...")
    print(f"  区间: {params['start']} ~ {params['end']}")
    print(f"  数据源: golden_stocks.db")
    
    import io
    from contextlib import redirect_stdout
    
    f = io.StringIO()
    with redirect_stdout(f):
        result = run_wf_overlay(
            train_days=252, test_days=252, step_days=252,
            start_date=params["start"], end_date=params["end"],
            full=True
        )
    
    # v61b 返回标准格式 {total, sharpe, dd, ...}
    actual = {
        'total_return_pct': result['total'],
        'sharpe_ratio': result['sharpe'],
        'max_drawdown_pct': result['dd'],
        'final_nav': 100000 * (1 + result['total'] / 100),
    }
    
    passed, details = check_tolerance(
        actual, baseline["expected"], baseline["tolerance"], "v61b"
    )
    
    print(f"\n  结果:")
    for d in details:
        print(f"    {d}")
    
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


def test_v68_golden_backtest():
    """测试 v68 在 golden 数据集上的回测结果"""
    print("\n" + "="*60)
    print("测试 3: v68 golden 回测结果")
    print("="*60)
    
    baselines = load_baselines()
    baseline = baselines.get("v68_golden_2021")
    if not baseline:
        print("  ⚠️ 无基准数据，跳过")
        return None
    
    from scripts.backtest.wf_runner import run_wf
    
    params = baseline["params"]
    print(f"  运行 v68 全量回测...")
    print(f"  区间: {params['start']} ~ {params['end']}")
    print(f"  数据源: golden_stocks.db")
    
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
    
    passed, details = check_tolerance(
        actual, baseline["expected"], baseline["tolerance"], "v68"
    )
    
    print(f"\n  结果:")
    for d in details:
        print(f"    {d}")
    
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


def test_factor_consistency():
    """测试因子计算一致性（使用 golden 数据）"""
    print("\n" + "="*60)
    print("测试 2: 因子计算一致性")
    print("="*60)
    
    import sqlite3
    from scripts.strategies.v39c_pv_resonance import calc_factors
    
    # 从 golden 数据库加载数据
    conn = sqlite3.connect(str(GOLDEN_DB))
    codes = pd.read_sql_query("SELECT code FROM stock_pool", conn)['code'].tolist()
    
    kline = pd.read_sql_query(
        "SELECT code, date, open, high, low, close, volume, amount FROM daily_kline ORDER BY code, date",
        conn
    )
    conn.close()
    
    if len(kline) == 0:
        print("  ❌ golden 数据库无数据")
        return False
    
    kline['date'] = pd.to_datetime(kline['date'])
    dates = sorted(kline['date'].unique())
    n_stocks = len(codes)
    
    # 构造面板
    close_panel = kline.pivot(index='date', columns='code', values='close')
    volume_panel = kline.pivot(index='date', columns='code', values='volume')
    amount_panel = kline.pivot(index='date', columns='code', values='amount')
    high_panel = kline.pivot(index='date', columns='code', values='high')
    low_panel = kline.pivot(index='date', columns='code', values='low')
    open_panel = kline.pivot(index='date', columns='code', values='open')
    
    # 计算因子（两次，验证确定性）
    factors1 = calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel)
    factors2 = calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel)
    
    # 对比
    passed = True
    for key in factors1:
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


def test_strategy_adapter():
    """测试策略适配器完整性"""
    print("\n" + "="*60)
    print("测试 3: 策略适配器完整性")
    print("="*60)
    
    from scripts.backtest.strategy_adapter import get_adapter
    adapter = get_adapter()
    
    required = ['v39g', 'v61b', 'v68']
    passed = True
    
    for s in required:
        if s in adapter.list_strategies():
            print(f"  ✅ {s}: 已注册")
        else:
            print(f"  ❌ {s}: 未注册")
            passed = False
        
        if s in adapter._risk_params:
            params = adapter._risk_params[s]
            if 'STOP_LOSS' in params and 'TAKE_PROFIT' in params:
                print(f"  ✅ {s}: 参数完整")
            else:
                print(f"  ❌ {s}: 参数不完整")
                passed = False
        else:
            print(f"  ❌ {s}: 无风险参数")
            passed = False
    
    print(f"\n  {'✅ 通过' if passed else '❌ 失败'}")
    return passed


# ── 主函数 ──────────────────────────────────────────────────────

def main():
    print("="*60)
    print("Golden Test — 回归测试套件")
    print(f"标准输入: {GOLDEN_DB}")
    print(f"标准答案: {GOLDEN_JSON}")
    print("="*60)
    
    results = {}
    
    results['data'] = test_golden_data_exists()
    results['adapter'] = test_strategy_adapter()
    results['v39g'] = test_v39g_golden_backtest()
    results['v61b'] = test_v61b_golden_backtest()
    results['v68'] = test_v68_golden_backtest()
    
    # 汇总
    print("\n" + "="*60)
    print("测试汇总")
    print("="*60)
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败" if passed is False else "⚠️ 跳过"
        print(f"  {status} {name}")
    
    all_passed = all(v for v in results.values() if v is not None)
    print(f"\n总体结果: {'✅ 全部通过' if all_passed else '❌ 存在失败'}")
    
    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())

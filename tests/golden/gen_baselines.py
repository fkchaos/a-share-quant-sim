#!/usr/bin/env python3
"""用 Golden Dataset 跑 v39g 全量回测，生成标准参考答案

用法：
    python tests/golden/gen_baselines.py
"""
import os
import sys
import json
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

GOLDEN_DIR = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DB = os.path.join(GOLDEN_DIR, 'golden_stocks.db')
BASELINES_JSON = os.path.join(GOLDEN_DIR, 'golden_baselines.json')


def run_v39g_golden():
    """在 golden 数据集上跑 v39g 全量回测"""
    # 临时替换数据库路径
    os.environ['BACKTEST_DATA_DIR'] = GOLDEN_DIR
    # 修改 core/db.py 使用的数据库路径
    import core.db as db_mod
    original_get_conn = db_mod.get_kline.__code__

    from scripts.backtest.wf_runner import run_wf

    print("="*60)
    print("Golden Dataset: v39g 全量回测")
    print(f"数据源: {GOLDEN_DB}")
    print("="*60)

    f = io.StringIO()
    import contextlib
    with contextlib.redirect_stdout(f):
        run_wf(
            "v39g",
            train_days=252, test_days=252, step_days=252,
            start_date='2021-01-01', end_date='2021-12-31',
            full=True, pool_override='zz1800'
        )

    output = f.getvalue()
    print(output)

    # 解析结果
    result = {}
    for line in output.split('\n'):
        if '总收益率' in line:
            result['total_return_pct'] = float(line.split(':')[1].strip().replace('%', ''))
        elif '夏普比率' in line:
            result['sharpe_ratio'] = float(line.split(':')[1].strip())
        elif '最大回撤' in line:
            result['max_drawdown_pct'] = float(line.split(':')[1].strip().replace('%', ''))
        elif '最终净值' in line:
            result['final_nav'] = float(line.split(':')[1].strip())

    return result


if __name__ == '__main__':
    result = run_v39g_golden()

    baselines = {
        "v39g_golden_2021": {
            "description": "v39g 全量回测 (golden dataset: 10只股票, 2021全年)",
            "params": {
                "strategy": "v39g",
                "start": "2021-01-01",
                "end": "2021-12-31",
                "pool": "zz1800"
            },
            "expected": result,
            "tolerance": {
                "total_return_pct": 2.0,
                "sharpe_ratio": 0.02,
                "max_drawdown_pct": 0.5,
                "final_nav": 500
            }
        }
    }

    with open(BASELINES_JSON, 'w') as f:
        json.dump(baselines, f, indent=2, ensure_ascii=False)

    print(f"\n📦 标准参考答案已保存: {BASELINES_JSON}")
    print(json.dumps(result, indent=2))

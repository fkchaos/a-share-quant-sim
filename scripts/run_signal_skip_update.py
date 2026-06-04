"""
跳过数据更新，直接运行上午信号生成。
用于 update_daily_data.py 超时但日K数据已是最新时。

用法:
  cd /root/a-share-quant-sim && BACKTEST_DATA_DIR=/root/data python scripts/run_signal_skipupdate.py

前提: /root/data/daily/*.csv 已是最新（检查时间戳）
"""
import sys, os, json

sys.path.insert(0, "/root/a-share-quant-sim")
os.environ.setdefault("BACKTEST_DATA_DIR", "/root/data")

import scripts.sim_daily_v7 as m


def noop_update():
    m.logger.info("⏭️ 跳过数据更新 (数据已是最新)")
    return True


# Monkey-patch: 替换 step_update_data 为空操作
m.step_update_data = noop_update

from scripts.sim_daily_v7 import run_intraday_signal, PORTFOLIO_DIR


if __name__ == "__main__":
    plan = run_intraday_signal()
    if plan:
        plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan.json")
        if os.path.exists(plan_file):
            with open(plan_file) as f:
                data = json.load(f)
            print(f"\n✅ 信号生成完成，已保存到 {plan_file}")
            print(f"操作数量: {data.get('trade_count', 0)}")
            print(f"卖出: {len(data.get('sell_plan', []))} 只")
            print(f"持有: {len(data.get('hold_plan', []))} 只")
            print(f"买入: {len(data.get('buy_plan', []))} 只")
        else:
            print("✅ 信号生成完成（无操作计划）")
    else:
        print("❌ 信号生成失败")
        sys.exit(1)

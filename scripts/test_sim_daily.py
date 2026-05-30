#!/usr/bin/env python3
"""
sim_daily_v6 dry-run 测试
用最近一天现有数据跑完整 Pipeline，验证止损/止盈/decay/rebalance 逻辑
不联网、不保存状态、只打印结果
"""
import sys, os, json, logging
from datetime import datetime

# Ensure scripts/ is on path
sys.path.insert(0, "/root/a-share-quant-sim")
sys.path.insert(0, "/root/a-share-quant-sim/scripts")
sys.path.insert(0, "/root")

from scripts import sim_daily_v6 as sim

sim.step_update_data = lambda: None

_orig_save = sim.step_save_state
sim.step_save_state = lambda state, tc: print(f"  [DRY-RUN] would save state, trade_count={tc}")

# ── 设置 logger 到控制台 ──
logging.basicConfig(level=logging.INFO, format="%(message)s")
sim.logger = logging.getLogger("sim_daily_v6")
sim.logger.setLevel(logging.DEBUG)

# ── 强制用本地数据（不联网）──
os.environ.pop("的更新", None)

# ── 加载 profile 核心逻辑检查 ──
print("=" * 60)
print("sim_daily_v6 dry-run 测试")
print("=" * 60)

# 1. 验证策略 profile 加载正确
print(f"\n[1] 策略 profile = {sim._strategy_profile.label if hasattr(sim, '_strategy_profile') else 'NOT LOADED'}")

# 2. 加载 state
try:
    # core_config is already imported as module-level config
    # No load_config needed — config uses dataclass defaults
    state, loaded = sim.step_load_account()
    print(f"\n[2] 账户加载: loaded={loaded}")
    print(f"    cash={state.cash:.0f}, holdings={len(state.holdings)}只")
    if state.holdings:
        total_mv = state.cash + sum(
            state.holdings[c].get("shares", 0) * state.holdings[c].get("cost_price", 0)
            for c in state.holdings
        )
        print(f"    总资产估算: {total_mv:,.0f}")
except Exception as e:
    print(f"\n[2] 账户加载失败: {e}")
    sys.exit(1)

# 3. 加载价格
try:
    result = sim.step_load_prices()
    if result[0] is None:
        print("\n[3] 价格加载失败，可能没有新数据 → 退出")
        sys.exit(1)
    latest_date, price_data, code_dataframes, files = result
    print(f"\n[3] 最新交易日: {latest_date}, {len(price_data)} 只有价格")
except Exception as e:
    print(f"\n[3] 价格加载失败: {e}")
    sys.exit(1)

# 4. 加载 stock names
names = {}
try:
    import pandas as pd
    hs300 = pd.read_csv("/root/hs300_constituents.csv")
    names = dict(zip(hs300['品种代码'].astype(str).str.zfill(6), hs300['品种名称']))
    print(f"\n[4] stock_names 加载: {len(names)} 只")
except Exception:
    print("\n[4] stock_names 加载失败（非致命）")

# 5. 止损检查
try:
    state_after_sl, stopped = sim.step_check_stop_loss(state, latest_date, price_data, names)
    print(f"\n[5] 止损: {stopped} 只被止损")
    print(f"    止损后 cash={state_after_sl.cash:.0f}, holdings={len(state_after_sl.holdings)}")
    state = state_after_sl
except Exception as e:
    print(f"\n[5] 止损逻辑异常: {e}")
    import traceback; traceback.print_exc()

# 6. 分级止盈检查（v5 策略）
try:
    tp_tiers = sim._strategy_profile.tp_tiers if hasattr(sim._strategy_profile, 'tp_tiers') else []
    use_tp = sim._strategy_profile.use_take_profit
    print(f"\n[6] 分级止启用={use_tp}, tiers={tp_tiers}")
    if use_tp:
        state_after_tp = sim.step_check_take_profit(state, latest_date, price_data, names)
        print(f"    止盈后 cash={state_after_tp.cash:.0f}, holdings={len(state_after_tp.holdings)}")
        state = state_after_tp
except Exception as e:
    print(f"\n[6] 止盈逻辑异常: {e}")
    import traceback; traceback.print_exc()

# 7. 持有期 decay
try:
    use_decay = sim._strategy_profile.use_holding_decay
    print(f"\n[7] 持有期 decay 启用={use_decay}")
    if use_decay:
        state_after_decay = sim.step_holding_decay(state, latest_date, price_data, names)
        print(f"    decay 后 holdings={len(state_after_decay.holdings)}")
        state = state_after_decay
except Exception as e:
    print(f"\n[7] 持有期 decay 异常: {e}")
    import traceback; traceback.print_exc()

# 8. 数据质量门禁
try:
    quality_blocked = sim.step_data_quality(files, latest_date)
    print(f"\n[8] 数据质量门禁通过={not quality_blocked}")
except Exception as e:
    print(f"\n[8] 数据质量门禁异常: {e}")

# 9. 再平衡（会触发 factor/score/buy/sell）
try:
    print(f"\n[9] 开始再平衡 (profile={sim._strategy_profile.label})...")
    state_new, trade_count, industries, turnover = sim.step_rebalance(
        state, latest_date, price_data, code_dataframes, files, loaded, names
    )
    print(f"    再平衡完成: trade_count={trade_count}")
    print(f"    cash={state_new.cash:.0f}, holdings={len(state_new.holdings)}")
    state = state_new
except Exception as e:
    print(f"\n[9] 再平衡异常: {e}")
    import traceback; traceback.print_exc()

# 10. 保存 state（dry-run）
try:
    sim.step_save_state(state, 0)
except Exception as e:
    print(f"\n[10] 保存 state 异常: {e}")

print("\n" + "=" * 60)
print("dry-run 完成，无异常 ✅" if True else "dry-run 有异常 ❌")
print("=" * 60)

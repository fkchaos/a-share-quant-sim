#!/usr/bin/env python3
"""
v28_kronos_demo — Kronos 预测 demo
====================================
加载 Kronos-small 模型，对 v27 初筛候选股做预测，提取 kronos 因子。
"""
import sys, os
import time
import numpy as np
import pandas as pd

KRONOS_ROOT = '/root/Kronos'
sys.path.insert(0, KRONOS_ROOT)
sys.path.insert(0, os.path.join(KRONOS_ROOT, 'model'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from model import Kronos, KronosTokenizer, KronosPredictor
from core.db import load_panel_from_db, get_kline

# ── 配置 ──
TOKENIZER_PATH = '/root/Kronos/tokenizer'
MODEL_PATH = '/root/Kronos/model_small'
DEVICE = "cpu"
MAX_CONTEXT = 512
LOOKBACK = 200
PRED_LEN = 5
T = 0.8
TOP_P = 0.85
SAMPLE_COUNT = 5

# ── Step 1: 加载模型 ──
print("=" * 60)
print("Kronos 预测 Demo")
print("=" * 60)
print(f"\n加载模型...")
t0 = time.time()
tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_PATH)
model = Kronos.from_pretrained(MODEL_PATH)
model.to(DEVICE)
model.eval()
predictor = KronosPredictor(model, tokenizer, max_context=MAX_CONTEXT)
print(f"模型加载完成 ({time.time()-t0:.1f}s)")

# ── Step 2: 加载数据，跑 v27 初筛 ──
print(f"\n加载面板数据...")
tpl, codes = load_panel_from_db('2021-01-01', '2026-07-15', need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel = tpl[3]

eps = 1e-10
mom_5 = close_panel.pct_change(5)
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)
daily_ret = close_panel.pct_change()
ret_mean_20 = daily_ret.rolling(20).mean()
vr_mean_20 = vr.rolling(20).mean()
cov_20 = ((daily_ret - ret_mean_20) * (vr - vr_mean_20)).rolling(20).mean()
pv_corr_20 = cov_20 / (daily_ret.rolling(20).std() * vr.rolling(20).std() + eps)

# 取最新日期
latest_date = close_panel.index[-1]
print(f"最新数据日期: {latest_date}")

# v27 初筛
m5 = mom_5.loc[latest_date].dropna()
mask = m5 > 0.02
cands = m5[mask].index.tolist()

# 排除 pv_corr_10 < -0.5
pv10 = (daily_ret.rolling(10).mean() * vr.rolling(10).mean()).rolling(10).mean()  # 简化
# 直接用 pv_corr_20 > 0 过滤
if latest_date in pv_corr_20.index:
    pv20 = pv_corr_20.loc[latest_date]
    cands = [c for c in cands if c in pv20.index and pv20[c] > -0.5]

# 评分
scores = {}
for c in cands:
    s = m5[c] * 100
    if latest_date in pv_corr_20.index and c in pv_corr_20.columns:
        pv20_v = pv_corr_20.loc[latest_date, c]
        if not pd.isna(pv20_v) and pv20_v > 0:
            s += 0.5
    scores[c] = s

cands_sorted = sorted(scores.items(), key=lambda x: x[1], reverse=True)
top50 = cands_sorted[:50]
print(f"v27 初筛: {len(cands)} 只通过 mom>2%，Top 50 候选")

# ── Step 3: 对 Top 50 跑 Kronos 预测 ──
print(f"\n对 Top 50 候选跑 Kronos 预测...")
results = []
t0 = time.time()

for i, (code, v27_score) in enumerate(top50):
    # 从 DB 读 K 线
    rows = get_kline(code, limit=LOOKBACK + 10)
    if not rows or len(rows) < 50:
        continue

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # 只取 latest_date 之前的数据
    cutoff = pd.Timestamp(latest_date)
    df = df[df['date'] < cutoff].tail(LOOKBACK)
    if len(df) < 50:
        continue

    try:
        x_timestamp = pd.Series(df['date'])
        last_date = df['date'].iloc[-1]
        future_dates = pd.date_range(start=last_date + pd.Timedelta(days=1), periods=PRED_LEN, freq='B')
        y_timestamp = pd.Series(future_dates[:PRED_LEN])

        pred_df = predictor.predict(
            df=df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
            pred_len=PRED_LEN, T=T, top_p=TOP_P,
            sample_count=SAMPLE_COUNT, verbose=False,
        )

        if pred_df is not None and len(pred_df) > 0:
            last_close = df['close'].iloc[-1]
            pred_close = pred_df['close'].values
            pred_ret = (pred_close[-1] - last_close) / last_close
            pred_vol = np.std(np.diff(pred_close) / pred_close[:-1]) if len(pred_close) > 1 else 0
            conf = 1.0 / (np.std(pred_close / last_close - 1) + 0.01)
            conf = min(conf, 5.0)

            results.append({
                'code': code,
                'v27_score': v27_score,
                'kronos_ret': pred_ret,
                'kronos_conf': conf,
                'kronos_vol': pred_vol,
                'last_close': last_close,
                'pred_close': pred_close[-1],
            })
    except Exception as e:
        print(f"  {code} 预测失败: {e}")
        continue

    if (i + 1) % 10 == 0:
        elapsed = time.time() - t0
        print(f"  [{i+1}/{len(top50)}] 已预测, 耗时 {elapsed:.1f}s")

total_time = time.time() - t0
print(f"\n预测完成: {len(results)}/{len(top50)} 只成功, 总耗时 {total_time:.1f}s")

# ── Step 4: 综合评分 ──
print(f"\n{'=' * 60}")
print(f"综合评分排名 (v27 + Kronos 增强)")
print(f"{'=' * 60}")
print(f"{'排名':>4} {'代码':>8} {'v27分':>8} {'kronos_ret':>10} {'conf':>6} {'综合分':>8}")
print(f"{'-'*50}")

alpha = 0.5
conf_threshold = 0.3
final = []
for r in results:
    conf_weight = min(r['kronos_conf'] / conf_threshold, 1.0)
    enhance = alpha * r['kronos_ret'] * conf_weight
    final_score = r['v27_score'] + enhance
    final.append((r['code'], final_score, r['v27_score'], r['kronos_ret'], r['kronos_conf'], r['last_close'], r['pred_close']))

final.sort(key=lambda x: x[1], reverse=True)
for i, (code, fs, v27, kr, kc, lc, pc) in enumerate(final[:20]):
    print(f"{i+1:>4} {code:>8} {v27:>8.2f} {kr:>+10.2%} {kc:>6.2f} {fs:>8.2f}  (close={lc:.1f}→{pc:.1f})")

# ── 对比：v27 Top 20 vs v28 Top 20 ──
print(f"\n{'=' * 60}")
print(f"v27 Top 20 vs v28+Kronos Top 20 对比")
print(f"{'=' * 60}")
v27_top20 = set(c for c, _ in top50[:20])
v28_top20 = set(x[0] for x in final[:20])
overlap = v27_top20 & v28_top20
print(f"v27 Top 20:   {[c for c,_ in top50[:20]]}")
print(f"v28 Top 20:   {[x[0] for x in final[:20]]}")
print(f"重叠: {len(overlap)}/20")
print(f"新增(被Kronos提升): {v28_top20 - v27_top20}")
print(f"移除(被Kronos降低): {v27_top20 - v28_top20}")

#!/usr/bin/env python3
"""ML Ensemble 全量训练脚本 — 一次性训练并保存模型"""
import os, sys, time, json
sys.path.insert(0, '/root/a-share-quant-sim')
os.environ['BACKTEST_DATA_DIR'] = '/root/data'

from core.ml_predictor import train_and_save
from core.data import load_and_build_panel
from core.factors import calc_factors_panel
from core.config import STRATEGY_PROFILES

PROFILE = "v6b_8f_pos_ic"
HYBRID_ALPHA = 0.8
FORWARD_PERIODS = [5, 20]
TRAIN_DAYS = 252
MODEL_DIR = "/root/data/ml_models"
DATA_START = "2021-01-01"
DATA_END = None  # None = 最新日期

print("=" * 60)
print(f"ML Ensemble 训练")
print(f"  profile={PROFILE} α={HYBRID_ALPHA} fwd={FORWARD_PERIODS}")
print(f"  数据: {DATA_START} ~ {DATA_END or '最新'}")
print(f"  训练窗口: {TRAIN_DAYS}d")
print(f"  模型目录: {MODEL_DIR}")
print("=" * 60)

# 加载数据
print("\n[1/3] 加载数据...")
t0 = time.time()
loaded, codes = load_and_build_panel(DATA_START, DATA_END, need_open=False, need_hl=True)
close_panel = loaded[0]
print(f"  {close_panel.shape[0]}d × {close_panel.shape[1]}s | {len(codes)} codes | {time.time()-t0:.1f}s")

# 计算因子
print("\n[2/3] 计算因子...")
t0 = time.time()
factors = calc_factors_panel(close_panel, loaded[1], loaded[2])
print(f"  {len(factors)} factors | {time.time()-t0:.1f}s")

# 训练
print("\n[3/3] 训练 Ensemble (LGB+XGB+Ridge)...")
t0 = time.time()
meta = train_and_save(
    factors=factors,
    close_panel=close_panel,
    model_dir=MODEL_DIR,
    profile=PROFILE,
    hybrid_alpha=HYBRID_ALPHA,
    forward_periods=FORWARD_PERIODS,
    train_days=TRAIN_DAYS,
    verbose=True,
)
print(f"\n训练完成! 耗时: {time.time()-t0:.1f}s")

print(f"\n模型元数据:")
print(f"  训练日期: {meta['train_end_date']}")
print(f"  训练样本: {meta['n_train_samples']}")
print(f"  模型: {meta['model_names']}")
print(f"  Stacking 权重: {meta['stacking_weights']}")
print(f"  模型路径: {MODEL_DIR}/latest.json")

# 验证模型可加载
print("\n验证模型加载...")
from core.ml_predictor import MLPredictor
predictor = MLPredictor(model_dir=MODEL_DIR)
print(f"  ✅ 模型加载成功: {list(predictor.models.keys())}")
print(f"  hybrid_alpha={predictor.hybrid_alpha}, profile={predictor.profile}")

# 写策略配置（保持 factor 模式，需要时手动改）
config_path = os.path.join(MODEL_DIR, '..', 'strategy_config.json')
config_path = os.path.normpath(config_path)
print(f"\n⚠️  模型已保存。切换模拟盘到 hybrid 模式:")
print(f"  修改 {config_path}")
print(f"  将 mode 从 'factor' 改为 'hybrid'")
print(f"  然后重启模拟盘 cron 或手动运行")

# 打印示例选股
print("\n示例：用模型预测最近一天的截面...")
import pandas as pd
import numpy as np
from core.factors import calc_factors_single

DAILY_DIR = '/root/data/daily'
all_factors = {}
t1 = time.time()
for i, f in enumerate(os.listdir(DAILY_DIR)):
    if not f.endswith('.csv'):
        continue
    code = f.replace('.csv', '')
    df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
    if len(df) > 120:
        all_factors[code] = calc_factors_single(df)
    if i > 300:
        break

preds = predictor.predict(all_factors)
print(f"  预测 {len(preds)} 只股票 | 耗时: {time.time()-t1:.2f}s")
top10 = sorted(preds.items(), key=lambda x: x[1], reverse=True)[:10]
print(f"  Top 10:")
for code, score in top10:
    print(f"    {code}: {score:.4f}")

print("\n✅ 全部完成!")

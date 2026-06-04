#!/usr/bin/env python3
"""ML 因子快速测试（50只股票子集）"""
import sys, os, time, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.factors import calc_factors_panel
import numpy as np, pandas as pd, xgboost as xgb

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])

close_p, vol_p = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(df)>100: close_p[c]=df['close']; vol_p[c]=df['volume']
close = pd.DataFrame(close_p); vol = pd.DataFrame(vol_p); amt = close*vol
dates = close.dropna(how='all').index.sort_values()
print(f"面板: {close.shape}")

t0=time.time()
factors = calc_factors_panel(close, vol, amt)
print(f"因子: {len(factors)} 个, {time.time()-t0:.1f}s")

fwd_5 = close.pct_change(5).shift(-5)

# 截面标准化
fn = {}
for name, df in factors.items():
    mean = df.rolling(60, min_periods=20).mean()
    std = df.rolling(60, min_periods=20).std()
    fn[name] = (df - mean) / (std + 1e-8)

fac_names = list(fn.keys())
n_fac = len(fac_names)

# 只用50只股票加速
sub_codes = codes[:50]

# 构建训练集
train_dates = [d for d in dates[60:] if d <= pd.Timestamp('2023-12-31')][::10]  # 每10天
test_dates = [d for d in dates if d > pd.Timestamp('2023-12-31')][::10]

print(f"训练截面: {len(train_dates)}, 测试截面: {len(test_dates)}")

def build_dataset(date_list, stock_list):
    X, y = [], []
    for dt in date_list:
        if dt not in fwd_5.index: continue
        for c in stock_list:
            if c not in fwd_5.columns: continue
            yv = fwd_5.loc[dt, c]
            if np.isnan(yv): continue
            x = []; ok = True
            for name in fac_names:
                v = fn[name].loc[dt, c] if dt in fn[name].index and c in fn[name].columns else np.nan
                if np.isnan(v) or abs(v)>10: ok=False; break
                x.append(v)
            if ok and len(x)==n_fac:
                X.append(x); y.append(yv)
    return np.array(X), np.array(y)

t0=time.time()
X_train, y_train = build_dataset(train_dates, sub_codes)
print(f"训练集: {X_train.shape}, {time.time()-t0:.1f}s")

t0=time.time()
X_test, y_test = build_dataset(test_dates, sub_codes)
print(f"测试集: {X_test.shape}, {time.time()-t0:.1f}s")

# 训练
print("\n训练 XGBoost...")
t0=time.time()
model = xgb.XGBRegressor(
    n_estimators=200, max_depth=4, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.6, reg_alpha=0.1,
    min_child_weight=30, tree_method='hist', verbosity=0, n_jobs=-1,
)
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
print(f"训练耗时: {time.time()-t0:.1f}s")

# 特征重要性
imp = model.feature_importances_
idx = np.argsort(imp)[::-1]
print(f"\n特征重要性 (全部 {n_fac} 个):")
for i in idx[:15]:
    print(f"  {fac_names[i]:>20}: {imp[i]:.4f}")
print(f"  ...")
for i in idx[-5:]:
    print(f"  {fac_names[i]:>20}: {imp[i]:.4f}")

# IC 分析
print("\nIC 分析...")
train_pred = model.predict(X_train)
test_pred = model.predict(X_test)

train_ic = np.corrcoef(train_pred, y_train)[0,1]
test_ic = np.corrcoef(test_pred, y_test)[0,1]
print(f"  训练集 IC: {train_ic:+.4f}")
print(f"  测试集 IC: {test_ic:+.4f}")
print(f"  过拟合比: {train_ic/max(abs(test_ic),0.001):.2f}x")

# 线性模型对比
print("\n线性模型对比...")
from core.config import STRATEGY_PROFILES
from core.scoring import composite_score

v8w = STRATEGY_PROFILES['v8_all_icir'].factor_weights
avail = {k:v for k,v in v8w.items() if k in fn}

# 在测试集日期上计算线性评分 IC
linear_ic_vals = []
for dt in test_dates[:50]:  # 取前50个测试截面
    if dt not in fwd_5.index: continue
    ls = {}
    for c in sub_codes:
        s = 0; ok = True
        for name, w in avail.items():
            v = fn[name].loc[dt, c] if dt in fn[name].index and c in fn[name].columns else np.nan
            if np.isnan(v): ok=False; break
            s += w * v
        if ok: ls[c] = s
    actual = fwd_5.loc[dt].dropna()
    lc = pd.Series(ls).dropna()
    common = lc.index.intersection(actual.index)
    if len(common)>=10:
        c = np.corrcoef(lc[common], actual[common])[0,1]
        if not np.isnan(c): linear_ic_vals.append(c)

print(f"  线性模型 IC (测试集): {np.mean(linear_ic_vals):+.4f}")
print(f"  ML 模型 IC (测试集):  {test_ic:+.4f}")
print(f"  ML 提升: {(test_ic - np.mean(linear_ic_vals))*10000:+.1f}bp")

# 时序 IC（按时间段）
print("\nML IC 时序稳定性:")
for year in ['2024', '2025']:
    y_ic = []
    for dt in test_dates:
        if dt.strftime('%Y') != year: continue
        if dt not in fwd_5.index: continue
        preds = []
        actuals = []
        for c in sub_codes:
            x = []; ok = True
            for name in fac_names:
                v = fn[name].loc[dt, c] if dt in fn[name].index and c in fn[name].columns else np.nan
                if np.isnan(v) or abs(v)>10: ok=False; break
                x.append(v)
            if ok and len(x)==n_fac and c in fwd_5.columns:
                yv = fwd_5.loc[dt, c]
                if not np.isnan(yv):
                    preds.append(model.predict(np.array([x]))[0])
                    actuals.append(yv)
        if len(preds) >= 10:
            ic = np.corrcoef(preds, actuals)[0,1]
            if not np.isnan(ic): y_ic.append(ic)
    if y_ic:
        print(f"  {year}: IC={np.mean(y_ic):+.4f}, IR={np.mean(y_ic)/np.std(y_ic):+.4f}, N={len(y_ic)}")

print("\n完成")

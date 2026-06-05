"""
Barra 风格归因分析 — v11b_zz800_union 策略

分析步骤：
1. 加载日 K 线数据，构建 Barra 风格因子截面面板
2. 加载策略回测 NAV 和交易记录
3. 横截面回归归因（每期持仓 → 风格暴露）
4. 时间序列回归归因（策略收益 → 风格因子收益）
5. 输出归因报告到 docs/BARRA_ATTRIBUTION.md

Barra 风格因子：
  SIZE  = log(流通市值)  — close × outstanding_share
  MOM   = 过去 20/60/120 日收益
  VOL   = 过去 20/60 日收益标准差
  LIQ   = 过去 20 日日均成交额（log）
  REV   = 过去 5/10 日反转收益
  VAL   = 估值代理（如有 PE/PB 数据）
"""

import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from scipy import stats as sp_stats

warnings.filterwarnings("ignore")

# ── 路径配置 ──────────────────────────────────────────────────────
PROJECT_DIR = "/root/a-share-quant-sim"
DATA_DIR = "/root/data"
DAILY_DIR = os.path.join(DATA_DIR, "daily")
NAV_CSV = os.path.join(DATA_DIR, "backtest_final_nav.csv")
TRADES_CSV = os.path.join(DATA_DIR, "backtest_final_trades.csv")
METRICS_JSON = os.path.join(DATA_DIR, "backtest_final_metrics.json")
ZZ800_CSV = os.path.join(PROJECT_DIR, "zz800_constituents.csv")
REPORT_PATH = os.path.join(PROJECT_DIR, "docs", "BARRA_ATTRIBUTION.md")

# 确保 docs 目录存在
os.makedirs(os.path.join(PROJECT_DIR, "docs"), exist_ok=True)

print("=" * 70)
print("Barra 风格归因分析 — v11b_zz800_union")
print("=" * 70)

# ══════════════════════════════════════════════════════════════════
# 步骤 1：加载中证 800 成分股
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 1] 加载中证 800 成分股...")

zz800_df = pd.read_csv(ZZ800_CSV)
print(f"  zz800_constituents.csv 列名: {list(zz800_df.columns)}")
print(f"  行数: {len(zz800_df)}")
print(f"  前5行:\n{zz800_df.head()}")

# 尝试找到代码列
code_col = None
for c in ['code', 'Code', 'CODE', 'stock_code', 'symbol', 'Symbol', 'ts_code']:
    if c in zz800_df.columns:
        code_col = c
        break
if code_col is None:
    code_col = zz800_df.columns[0]  # 默认第一列

zz800_codes = set(zz800_df[code_col].astype(str).str.zfill(6).tolist())
print(f"  代码列: {code_col}, 成分股数量: {len(zz800_codes)}")

# ══════════════════════════════════════════════════════════════════
# 步骤 2：加载日 K 线数据（中证 800 成分股）
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 2] 加载日 K 线数据...")

all_files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
print(f"  daily/ 目录共 {len(all_files)} 个 CSV 文件")

# 只加载中证 800 成分股 + 确保有足够历史数据
START_DATE = "2020-06-01"  # 提前加载以计算 120 日动量
END_DATE = "2026-05-31"

close_dict = {}
volume_dict = {}
amount_dict = {}
high_dict = {}
low_dict = {}
outstanding_dict = {}

loaded = 0
skipped = 0

for f in all_files:
    code = f.replace(".csv", "").zfill(6)
    if code not in zz800_codes:
        skipped += 1
        continue
    try:
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        df = df.sort_index()
        if len(df) < 150:  # 至少需要 150 个交易日
            skipped += 1
            continue
        # 过滤日期范围（含前后缓冲）
        close_dict[code] = df['close']
        volume_dict[code] = df['volume']
        amount_dict[code] = df.get('amount', df['close'] * df['volume'])
        if 'high' in df.columns:
            high_dict[code] = df['high']
        if 'low' in df.columns:
            low_dict[code] = df['low']
        if 'outstanding_share' in df.columns:
            outstanding_dict[code] = df['outstanding_share']
        loaded += 1
    except Exception as e:
        skipped += 1

print(f"  加载成功: {loaded} 只, 跳过: {skipped} 只")

# 构建面板
close_panel = pd.DataFrame(close_dict)
volume_panel = pd.DataFrame(volume_dict)
amount_panel = pd.DataFrame(amount_dict)
high_panel = pd.DataFrame(high_dict) if high_dict else None
low_panel = pd.DataFrame(low_dict) if low_dict else None

# 过滤到回测期间
close_panel = close_panel[(close_panel.index >= START_DATE) & (close_panel.index <= END_DATE)]
volume_panel = volume_panel.reindex(close_panel.index).ffill()
amount_panel = amount_panel.reindex(close_panel.index).ffill()

print(f"  面板形状: close={close_panel.shape}, 日期范围: {close_panel.index[0].date()} ~ {close_panel.index[-1].date()}")

# ══════════════════════════════════════════════════════════════════
# 步骤 3：构建 Barra 风格因子
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 3] 构建 Barra 风格因子...")

returns = close_panel.pct_change()
eps = 1e-10

barra_factors = {}

# --- SIZE: log(流通市值) ---
print("  [SIZE] 流通市值...")
if outstanding_dict:
    osh_panel = pd.DataFrame(outstanding_dict).reindex(close_panel.index).ffill()
    market_cap = close_panel * osh_panel
    barra_factors['SIZE'] = np.log(market_cap + eps)
    print(f"    使用 outstanding_share 计算, 非零比例: {(market_cap > 0).mean().mean():.2%}")
else:
    # 用 close × volume 估算（近似）
    approx_cap = close_panel * volume_panel * 20  # 粗略估算
    barra_factors['SIZE'] = np.log(approx_cap + eps)
    print(f"    使用 close×volume 估算（无 outstanding_share 数据）")

# --- MOM: 动量 ---
print("  [MOM] 动量因子...")
for w in [20, 60, 120]:
    barra_factors[f'MOM_{w}'] = close_panel.pct_change(w)
    print(f"    MOM_{w}: 非零比例={(~barra_factors[f'MOM_{w}'].isna()).mean().mean():.2%}")

# --- VOL: 波动率 ---
print("  [VOL] 波动率因子...")
for w in [20, 60]:
    barra_factors[f'VOL_{w}'] = returns.rolling(w).std()
    print(f"    VOL_{w}: 非零比例={(~barra_factors[f'VOL_{w}'].isna()).mean().mean():.2%}")

# --- LIQ: 流动性 ---
print("  [LIQ] 流动性因子...")
avg_amount_20 = amount_panel.rolling(20).mean()
barra_factors['LIQ'] = np.log(avg_amount_20 + eps)
print(f"    LIQ: 非零比例={(~barra_factors['LIQ'].isna()).mean().mean():.2%}")

# --- REV: 反转 ---
print("  [REV] 反转因子...")
for w in [5, 10]:
    barra_factors[f'REV_{w}'] = -close_panel.pct_change(w)
    print(f"    REV_{w}: 非零比例={(~barra_factors[f'REV_{w}'].isna()).mean().mean():.2%}")

# --- BETA: 市场 Beta ---
print("  [BETA] 市场 Beta...")
market_return = returns.mean(axis=1)  # 等权市场收益
market_var = market_return.rolling(60).var()
cov_with_market = returns.rolling(60).cov(market_return)
barra_factors['BETA'] = cov_with_market.div(market_var + eps, axis=0)
print(f"    BETA: 非零比例={(~barra_factors['BETA'].isna()).mean().mean():.2%}")

# --- 非线性规模 (NLSIZE): SIZE 的三次方残差 ---
print("  [NLSIZE] 非线性规模...")
SIZE_z = barra_factors['SIZE'].sub(barra_factors['SIZE'].mean(axis=1), axis=0).div(
    barra_factors['SIZE'].std(axis=1) + eps, axis=0
)
barra_factors['NLSIZE'] = SIZE_z ** 3

print(f"\n  Barra 风格因子构建完成，共 {len(barra_factors)} 个因子")
for name, panel in barra_factors.items():
    print(f"    {name}: shape={panel.shape}")

# ══════════════════════════════════════════════════════════════════
# 步骤 4：横截面回归归因（IC 分析 + 截面回归）
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 4] 横截面回归归因...")

# 未来 20 日收益（因变量）
future_ret_20 = close_panel.pct_change(20).shift(-20)

# 只在调仓日做截面回归（每 20 个交易日）
REBALANCE_FREQ = 20
all_dates = close_panel.index
# 找到有效的回测日期范围（有足够历史数据）
valid_start_idx = 120  # 需要 120 日历史数据
rebalance_dates = []
for i in range(valid_start_idx, len(all_dates) - 20, REBALANCE_FREQ):
    rebalance_dates.append(all_dates[i])

print(f"  回测期间共 {len(rebalance_dates)} 个调仓日")

# 用于截面回归的因子列表
cs_factor_names = ['SIZE', 'MOM_20', 'MOM_60', 'VOL_20', 'VOL_60', 'LIQ', 'REV_5', 'REV_10', 'BETA']

# 存储每期回归结果
cs_regression_results = []
ic_records = []

for date in rebalance_dates:
    # 获取当日因子值
    factor_values = {}
    valid = True
    for fname in cs_factor_names:
        if fname in barra_factors and date in barra_factors[fname].index:
            v = barra_factors[fname].loc[date]
            if v.notna().sum() < 30:  # 至少需要 30 只有效股票
                valid = False
                break
            factor_values[fname] = v
        else:
            valid = False
            break
    
    if not valid:
        continue
    
    # 获取未来收益
    if date not in future_ret_20.index:
        continue
    y = future_ret_20.loc[date]
    
    # 合并数据
    data = pd.DataFrame(factor_values)
    data['y'] = y
    data = data.dropna()
    
    if len(data) < 50:
        continue
    
    # 截面标准化（z-score）
    X = data[cs_factor_names].copy()
    X_z = X.sub(X.mean()).div(X.std() + eps)
    y_data = data['y']
    
    # 截面回归
    X_with_const = np.column_stack([np.ones(len(X_z)), X_z.values])
    try:
        beta, residuals, rank, sv = np.linalg.lstsq(X_with_const, y_data.values, rcond=None)
        alpha_val = beta[0]
        betas = beta[1:]
        
        # 计算 R²
        y_pred = X_with_const @ beta
        ss_res = ((y_data.values - y_pred) ** 2).sum()
        ss_tot = ((y_data.values - y_data.values.mean()) ** 2).sum()
        r_squared = 1 - ss_res / (ss_tot + eps)
        
        # 计算各因子 IC（Spearman 秩相关）
        ics = {}
        for fname in cs_factor_names:
            ic_val, _ = sp_stats.spearmanr(X[fname].values, y_data.values)
            ics[fname] = ic_val
            ic_records.append({
                'date': date,
                'factor': fname,
                'IC': ic_val,
            })
        
        cs_regression_results.append({
            'date': date,
            'alpha': alpha_val,
            'r_squared': r_squared,
            'n_stocks': len(data),
            **{f'beta_{k}': v for k, v in zip(cs_factor_names, betas)},
            **{f'IC_{k}': v for k, v in ics.items()},
        })
    except Exception:
        continue

print(f"  成功回归 {len(cs_regression_results)} 期")

# 汇总 IC 分析
ic_df = pd.DataFrame(ic_records)
if len(ic_df) > 0:
    ic_summary = ic_df.groupby('factor')['IC'].agg(['mean', 'std', 'count'])
    ic_summary['IC_IR'] = ic_summary['mean'] / (ic_summary['std'] + eps)
    ic_summary['|IC|'] = ic_summary['mean'].abs()
    ic_summary = ic_summary.sort_values('|IC|', ascending=False)
    print("\n  ── IC 分析汇总 ──")
    print(ic_summary.to_string())

# 汇总截面回归系数
cs_df = pd.DataFrame(cs_regression_results)
if len(cs_df) > 0:
    print("\n  ── 截面回归系数均值 ──")
    beta_cols = [c for c in cs_df.columns if c.startswith('beta_')]
    for col in beta_cols:
        mean_beta = cs_df[col].mean()
        std_beta = cs_df[col].std()
        t_stat = mean_beta / (std_beta / np.sqrt(len(cs_df)) + eps)
        sig = "***" if abs(t_stat) > 2.576 else "**" if abs(t_stat) > 1.96 else "*" if abs(t_stat) > 1.645 else ""
        print(f"    {col}: mean={mean_beta:.6f}, std={std_beta:.6f}, t-stat={t_stat:.3f} {sig}")
    
    print(f"\n  平均 R²: {cs_df['r_squared'].mean():.4f}")
    print(f"  平均 alpha: {cs_df['alpha'].mean():.6f}")
    print(f"  平均股票数: {cs_df['n_stocks'].mean():.0f}")

# ══════════════════════════════════════════════════════════════════
# 步骤 5：时间序列回归归因（策略收益 → 风格因子收益）
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 5] 时间序列回归归因...")

# 加载策略 NAV
nav_df = pd.read_csv(NAV_CSV, index_col='date', parse_dates=True)
nav_df = nav_df.sort_index()
print(f"  NAV 数据: {len(nav_df)} 行, 日期范围: {nav_df.index[0].date()} ~ {nav_df.index[-1].date()}")

# 计算策略日收益
strategy_col = nav_df.columns[0]
nav_df['strategy_ret'] = nav_df[strategy_col].pct_change()
strategy_returns = nav_df['strategy_ret'].dropna()

# 构建风格因子收益（多空组合收益）
print("  构建风格因子收益（多空组合）...")
factor_returns = {}

for fname in cs_factor_names:
    if fname not in barra_factors:
        continue
    factor_panel = barra_factors[fname]
    
    # 每期：因子值排名前 30% 做多，后 30% 做空
    factor_ret_series = []
    factor_dates = []
    
    for date in strategy_returns.index:
        if date not in factor_panel.index:
            continue
        vals = factor_panel.loc[date].dropna()
        if len(vals) < 30:
            continue
        
        # 下期收益
        try:
            idx = close_panel.index.get_loc(date)
            if idx + 1 >= len(close_panel):
                continue
            next_date = close_panel.index[idx + 1]
            if next_date not in returns.index:
                continue
            next_ret = returns.loc[next_date]
        except (KeyError, IndexError):
            continue
        
        # 对齐
        common = vals.index.intersection(next_ret.index)
        if len(common) < 30:
            continue
        vals_aligned = vals[common]
        next_ret_aligned = next_ret[common]
        
        # 排序分组
        n = len(vals_aligned)
        top_n = max(1, int(n * 0.3))
        bot_n = max(1, int(n * 0.3))
        
        top_stocks = vals_aligned.nlargest(top_n).index
        bot_stocks = vals_aligned.nsmallest(bot_n).index
        
        long_ret = next_ret_aligned[top_stocks].mean()
        short_ret = next_ret_aligned[bot_stocks].mean()
        
        factor_ret_series.append(long_ret - short_ret)
        factor_dates.append(date)
    
    if len(factor_ret_series) > 10:
        factor_returns[fname] = pd.Series(factor_ret_series, index=factor_dates)
        print(f"    {fname}: {len(factor_ret_series)} 期因子收益, 均值={factor_returns[fname].mean():.6f}")

# 合并策略收益和因子收益
ts_data = pd.DataFrame({'strategy': strategy_returns})
for fname, fret in factor_returns.items():
    ts_data[fname] = fret

ts_data = ts_data.dropna()
print(f"\n  时间序列回归数据: {len(ts_data)} 期")

if len(ts_data) > 60:
    # 时间序列回归
    y_ts = ts_data['strategy'].values
    X_ts_cols = [c for c in ts_data.columns if c != 'strategy']
    X_ts = ts_data[X_ts_cols].values
    
    # 标准化因子收益
    X_ts_mean = X_ts.mean(axis=0)
    X_ts_std = X_ts.std(axis=0) + eps
    X_ts_z = (X_ts - X_ts_mean) / X_ts_std
    
    # OLS 回归
    X_ts_with_const = np.column_stack([np.ones(len(X_ts_z)), X_ts_z])
    beta_ts, _, _, _ = np.linalg.lstsq(X_ts_with_const, y_ts, rcond=None)
    
    alpha_ts = beta_ts[0]
    betas_ts = beta_ts[1:]
    
    # 计算标准误
    y_pred_ts = X_ts_with_const @ beta_ts
    residuals_ts = y_ts - y_pred_ts
    n = len(y_ts)
    k = len(X_ts_cols)
    mse = (residuals_ts ** 2).sum() / (n - k - 1)
    var_beta = mse * np.linalg.inv(X_ts_with_const.T @ X_ts_with_const)
    se_beta = np.sqrt(np.diag(var_beta))
    t_stats = beta_ts / (se_beta + eps)
    p_values = 2 * (1 - sp_stats.t.cdf(np.abs(t_stats), df=n - k - 1))
    
    # R²
    ss_res = (residuals_ts ** 2).sum()
    ss_tot = ((y_ts - y_ts.mean()) ** 2).sum()
    r_squared_ts = 1 - ss_res / ss_tot
    adj_r_squared = 1 - (1 - r_squared_ts) * (n - 1) / (n - k - 1)
    
    print("\n  ── 时间序列回归结果 ──")
    print(f"  Alpha (日): {alpha_ts:.8f}, t-stat: {t_stats[0]:.3f}, p-value: {p_values[0]:.4f}")
    sig_alpha = "***" if p_values[0] < 0.01 else "**" if p_values[0] < 0.05 else "*" if p_values[0] < 0.1 else "不显著"
    print(f"  Alpha 显著性: {sig_alpha}")
    print(f"  Alpha (年化): {alpha_ts * 252:.4f} ({alpha_ts * 252 * 100:.2f}%)")
    print(f"\n  R²: {r_squared_ts:.4f}, 调整 R²: {adj_r_squared:.4f}")
    
    print(f"\n  {'因子':<15} {'Beta':>10} {'t-stat':>10} {'p-value':>10} {'显著性':>8} {'年化贡献':>12}")
    print("  " + "-" * 70)
    
    factor_decomp = {}
    for i, fname in enumerate(X_ts_cols):
        sig = "***" if p_values[i+1] < 0.01 else "**" if p_values[i+1] < 0.05 else "*" if p_values[i+1] < 0.1 else ""
        # 年化因子贡献 = beta × 因子年化收益
        factor_ann_ret = factor_returns[fname].mean() * 252
        ann_contribution = betas_ts[i] * factor_ann_ret
        factor_decomp[fname] = {
            'beta': betas_ts[i],
            't_stat': t_stats[i+1],
            'p_value': p_values[i+1],
            'annual_contribution': ann_contribution,
        }
        print(f"  {fname:<15} {betas_ts[i]:>10.4f} {t_stats[i+1]:>10.3f} {p_values[i+1]:>10.4f} {sig:>8} {ann_contribution:>11.4f}")
    
    # 策略收益分解
    total_ann_return = strategy_returns.mean() * 252
    alpha_ann = alpha_ts * 252
    style_ann = sum(v['annual_contribution'] for v in factor_decomp.values())
    
    print(f"\n  ── 策略收益分解 ──")
    print(f"  策略年化收益:     {total_ann_return:.4f} ({total_ann_return*100:.2f}%)")
    print(f"  Alpha (年化):     {alpha_ann:.4f} ({alpha_ann*100:.2f}%)")
    print(f"  风格因子贡献(年化): {style_ann:.4f} ({style_ann*100:.2f}%)")
    print(f"  Alpha 占比:       {alpha_ann/total_ann_return*100:.1f}%" if total_ann_return != 0 else "  Alpha 占比: N/A")
    print(f"  风格占比:         {style_ann/total_ann_return*100:.1f}%" if total_ann_return != 0 else "  风格占比: N/A")
else:
    print("  ⚠️ 时间序列数据不足，跳过时间序列回归")
    alpha_ts = 0
    r_squared_ts = 0
    adj_r_squared = 0
    factor_decomp = {}
    total_ann_return = strategy_returns.mean() * 252
    alpha_ann = 0
    style_ann = 0

# ══════════════════════════════════════════════════════════════════
# 步骤 6：持仓风格暴露分析
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 6] 持仓风格暴露分析...")

trades_df = pd.read_csv(TRADES_CSV)
print(f"  交易记录: {len(trades_df)} 行")
print(f"  列名: {list(trades_df.columns)}")

# 从交易记录重建持仓
trades_df['date'] = pd.to_datetime(trades_df['date'])
trades_df = trades_df.sort_values('date')

# 计算每日持仓
holdings = {}  # date -> {code: shares}
current_holdings = {}
prev_date = None

position_records = []

for _, row in trades_df.iterrows():
    date = row['date']
    code = str(row['code']).zfill(6)
    action = row['action']
    shares = row['shares']
    
    if action == 'BUY':
        current_holdings[code] = current_holdings.get(code, 0) + shares
    elif action == 'SELL':
        current_holdings[code] = current_holdings.get(code, 0) - shares
        if current_holdings[code] <= 0:
            del current_holdings[code]
    
    # 记录持仓快照
    if date != prev_date and prev_date is not None:
        position_records.append({
            'date': prev_date,
            'holdings': dict(current_holdings),
        })
    prev_date = date

# 计算持仓的风格暴露
print("  计算持仓风格暴露...")
exposure_records = []

for rec in position_records:
    date = rec['date']
    holdings_dict = rec['holdings']
    
    if not holdings_dict:
        continue
    if date not in close_panel.index:
        continue
    
    # 持仓股票列表
    hold_codes = [c for c in holdings_dict.keys() if c in close_panel.columns]
    if not hold_codes:
        continue
    
    # 持仓权重（按市值加权）
    hold_shares = pd.Series({c: holdings_dict[c] for c in hold_codes})
    hold_prices = close_panel.loc[date, hold_codes]
    hold_values = hold_shares * hold_prices
    weights = hold_values / hold_values.sum()
    
    # 计算持仓的风格暴露
    exposures = {}
    for fname in cs_factor_names:
        if fname in barra_factors and date in barra_factors[fname].index:
            factor_vals = barra_factors[fname].loc[date, hold_codes]
            valid = factor_vals.dropna()
            if len(valid) > 0:
                w_valid = weights[valid.index]
                w_norm = w_valid / w_valid.sum()
                exposures[fname] = (valid * w_norm).sum()
    
    exposures['date'] = date
    exposures['n_stocks'] = len(hold_codes)
    exposure_records.append(exposures)

exposure_df = pd.DataFrame(exposure_records)
if len(exposure_df) > 0:
    exposure_df['date'] = pd.to_datetime(exposure_df['date'])
    exposure_df = exposure_df.set_index('date').sort_index()
    print(f"  持仓暴露记录: {len(exposure_df)} 期")
    
    print("\n  ── 平均持仓风格暴露 ──")
    for fname in cs_factor_names:
        if fname in exposure_df.columns:
            mean_exp = exposure_df[fname].mean()
            std_exp = exposure_df[fname].std()
            print(f"    {fname}: mean={mean_exp:.4f}, std={std_exp:.4f}")
else:
    print("  ⚠️ 持仓暴露数据不足")

# ══════════════════════════════════════════════════════════════════
# 步骤 7：分年度归因
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 7] 分年度归因...")

nav_df['year'] = nav_df.index.year
nav_df['monthly_ret'] = nav_df[strategy_col].pct_change()

yearly_data = {}
for year in sorted(nav_df['year'].unique()):
    year_nav = nav_df[nav_df['year'] == year]
    if len(year_nav) < 10:
        continue
    start_val = year_nav[strategy_col].iloc[0]
    end_val = year_nav[strategy_col].iloc[-1]
    year_ret = end_val / start_val - 1 if start_val > 0 else 0
    year_vol = year_nav['strategy_ret'].std() * np.sqrt(252)
    year_sharpe = year_ret / (year_vol + eps) if year_vol > 0 else 0
    
    # 最大回撤
    cummax = year_nav[strategy_col].cummax()
    drawdown = (year_nav[strategy_col] - cummax) / cummax
    max_dd = drawdown.min()
    
    yearly_data[year] = {
        'return': year_ret,
        'volatility': year_vol,
        'sharpe': year_sharpe,
        'max_drawdown': max_dd,
    }
    print(f"  {year}: 收益={year_ret*100:.2f}%, 波动={year_vol*100:.2f}%, Sharpe={year_sharpe:.2f}, 最大回撤={max_dd*100:.2f}%")

# ══════════════════════════════════════════════════════════════════
# 步骤 8：生成归因报告
# ══════════════════════════════════════════════════════════════════
print("\n[步骤 8] 生成归因报告...")

# 加载回测指标
with open(METRICS_JSON, 'r') as f:
    metrics = json.load(f)

report_lines = []
report_lines.append("# Barra 风格归因分析报告")
report_lines.append("")
report_lines.append(f"**策略**: v11b_zz800_union (Ensemble 3组×4因子，中证800选股池)")
report_lines.append(f"**分析日期**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
report_lines.append(f"**回测期间**: {nav_df.index[0].date()} ~ {nav_df.index[-1].date()}")
report_lines.append("")

# ── 策略概览 ──
report_lines.append("## 1. 策略概览")
report_lines.append("")
report_lines.append(f"| 指标 | 值 |")
report_lines.append(f"|------|------|")
report_lines.append(f"| 年化收益 | {metrics['annual_return']*100:.2f}% |")
report_lines.append(f"| 年化波动 | {metrics['annual_volatility']*100:.2f}% |")
report_lines.append(f"| Sharpe 比 | {metrics['sharpe_ratio']:.3f} |")
report_lines.append(f"| 最大回撤 | {metrics['max_drawdown']*100:.2f}% |")
report_lines.append(f"| Calmar 比 | {metrics['calmar_ratio']:.3f} |")
report_lines.append(f"| 胜率 | {metrics['win_rate']*100:.1f}% |")
report_lines.append(f"| 总交易次数 | {metrics['total_trades']} |")
report_lines.append(f"| 止损次数 | {metrics['stop_loss_trades']} |")
report_lines.append(f"| 最终净值 | {metrics['final_value']:,.0f} |")
report_lines.append("")

# ── 分年度表现 ──
report_lines.append("## 2. 分年度表现")
report_lines.append("")
report_lines.append("| 年度 | 年化收益 | 波动率 | Sharpe | 最大回撤 |")
report_lines.append("|------|---------|--------|--------|---------|")
for year, yd in sorted(yearly_data.items()):
    report_lines.append(f"| {year} | {yd['return']*100:.2f}% | {yd['volatility']*100:.2f}% | {yd['sharpe']:.2f} | {yd['max_drawdown']*100:.2f}% |")
report_lines.append("")

# ── IC 分析 ──
report_lines.append("## 3. 风格因子 IC 分析")
report_lines.append("")
report_lines.append("横截面 IC（Information Coefficient）衡量因子值与未来收益的秩相关性。")
report_lines.append("IC_IR = IC均值 / IC标准差，衡量因子稳定性。")
report_lines.append("")
if len(ic_df) > 0:
    report_lines.append("| 因子 | IC 均值 | IC 标准差 | IC_IR | |IC| | 显著性 |")
    report_lines.append("|------|--------|----------|-------|------|--------|")
    for fname in ic_summary.index:
        row = ic_summary.loc[fname]
        sig = "***" if abs(row['IC_IR']) > 0.5 else "**" if abs(row['IC_IR']) > 0.3 else "*" if abs(row['IC_IR']) > 0.1 else ""
        report_lines.append(f"| {fname} | {row['mean']:.4f} | {row['std']:.4f} | {row['IC_IR']:.3f} | {row['|IC|']:.4f} | {sig} |")
    report_lines.append("")
    report_lines.append("显著性: *** |IC_IR|>0.5, ** |IC_IR|>0.3, * |IC_IR|>0.1")
else:
    report_lines.append("⚠️ IC 数据不足")
report_lines.append("")

# ── 截面回归 ──
report_lines.append("## 4. 横截面回归归因")
report_lines.append("")
report_lines.append("每期调仓日做横截面回归：R_i = α + Σ β_k × F_ki + ε_i")
report_lines.append(f"回归窗口: 滚动 60 日, 共 {len(cs_regression_results)} 期有效回归")
report_lines.append("")
if len(cs_df) > 0:
    report_lines.append(f"- 平均 R²: **{cs_df['r_squared'].mean():.4f}**")
    report_lines.append(f"- 平均 Alpha: **{cs_df['alpha'].mean():.6f}**")
    report_lines.append(f"- 平均股票数: **{cs_df['n_stocks'].mean():.0f}**")
    report_lines.append("")
    report_lines.append("### 截面回归系数（60 日滚动）")
    report_lines.append("")
    report_lines.append("| 因子 | Beta 均值 | Beta 标准差 | t-stat | 显著性 |")
    report_lines.append("|------|----------|------------|--------|--------|")
    for col in beta_cols:
        fname = col.replace('beta_', '')
        mean_beta = cs_df[col].mean()
        std_beta = cs_df[col].std()
        t_stat = mean_beta / (std_beta / np.sqrt(len(cs_df)) + eps)
        sig = "***" if abs(t_stat) > 2.576 else "**" if abs(t_stat) > 1.96 else "*" if abs(t_stat) > 1.645 else ""
        report_lines.append(f"| {fname} | {mean_beta:.6f} | {std_beta:.6f} | {t_stat:.3f} | {sig} |")
    report_lines.append("")
    report_lines.append("显著性: *** p<0.01, ** p<0.05, * p<0.1 (双尾 t 检验)")
else:
    report_lines.append("⚠️ 截面回归数据不足")
report_lines.append("")

# ── 时间序列回归 ──
report_lines.append("## 5. 时间序列回归归因")
report_lines.append("")
report_lines.append("对策略日收益做时间序列回归：R_strategy(t) = α + Σ β_k × F_k(t) + ε(t)")
report_lines.append(f"样本量: {len(ts_data)} 个交易日")
report_lines.append("")

if len(ts_data) > 60:
    sig_alpha = "***" if p_values[0] < 0.01 else "**" if p_values[0] < 0.05 else "*" if p_values[0] < 0.1 else "不显著"
    report_lines.append(f"- **Alpha (日)**: {alpha_ts:.8f}")
    report_lines.append(f"- **Alpha (年化)**: {alpha_ann*100:.2f}%")
    report_lines.append(f"- **Alpha t-stat**: {t_stats[0]:.3f}")
    report_lines.append(f"- **Alpha p-value**: {p_values[0]:.4f}")
    report_lines.append(f"- **Alpha 显著性**: {sig_alpha}")
    report_lines.append(f"- **R²**: {r_squared_ts:.4f}")
    report_lines.append(f"- **调整 R²**: {adj_r_squared:.4f}")
    report_lines.append("")
    
    report_lines.append("### 风格因子暴露与贡献")
    report_lines.append("")
    report_lines.append("| 因子 | Beta | t-stat | p-value | 显著性 | 年化贡献 |")
    report_lines.append("|------|------|--------|---------|--------|---------|")
    for fname, v in factor_decomp.items():
        sig = "***" if v['p_value'] < 0.01 else "**" if v['p_value'] < 0.05 else "*" if v['p_value'] < 0.1 else ""
        report_lines.append(f"| {fname} | {v['beta']:.4f} | {v['t_stat']:.3f} | {v['p_value']:.4f} | {sig} | {v['annual_contribution']*100:.2f}% |")
    report_lines.append("")
    
    report_lines.append("### 策略收益分解")
    report_lines.append("")
    report_lines.append(f"| 来源 | 年化收益 | 占比 |")
    report_lines.append(f"|------|---------|------|")
    report_lines.append(f"| 策略总收益 | {total_ann_return*100:.2f}% | 100% |")
    report_lines.append(f"| Alpha (纯选股) | {alpha_ann*100:.2f}% | {alpha_ann/total_ann_return*100:.1f}% |" if total_ann_return != 0 else f"| Alpha (纯选股) | {alpha_ann*100:.2f}% | N/A |")
    report_lines.append(f"| 风格因子贡献 | {style_ann*100:.2f}% | {style_ann/total_ann_return*100:.1f}% |" if total_ann_return != 0 else f"| 风格因子贡献 | {style_ann*100:.2f}% | N/A |")
    report_lines.append("")
else:
    report_lines.append("⚠️ 时间序列数据不足")
report_lines.append("")

# ── 持仓风格暴露 ──
report_lines.append("## 6. 持仓风格暴露分析")
report_lines.append("")
if len(exposure_df) > 0:
    report_lines.append("持仓的风格暴露 = Σ(权重_i × 因子值_i)")
    report_lines.append("")
    report_lines.append("| 因子 | 平均暴露 | 暴露标准差 | 最小值 | 最大值 |")
    report_lines.append("|------|---------|-----------|--------|--------|")
    for fname in cs_factor_names:
        if fname in exposure_df.columns:
            mean_e = exposure_df[fname].mean()
            std_e = exposure_df[fname].std()
            min_e = exposure_df[fname].min()
            max_e = exposure_df[fname].max()
            report_lines.append(f"| {fname} | {mean_e:.4f} | {std_e:.4f} | {min_e:.4f} | {max_e:.4f} |")
else:
    report_lines.append("⚠️ 持仓暴露数据不足（交易记录可能格式不匹配）")
report_lines.append("")

# ── 结论 ──
report_lines.append("## 7. 结论：策略赚的是什么钱？")
report_lines.append("")

if len(ts_data) > 60:
    alpha_pct = alpha_ann / total_ann_return * 100 if total_ann_return != 0 else 0
    style_pct = style_ann / total_ann_return * 100 if total_ann_return != 0 else 0
    
    report_lines.append(f"### 核心发现")
    report_lines.append("")
    
    if abs(alpha_pct) > 50:
        report_lines.append(f"✅ **策略以 Alpha 为主**：Alpha 占比 {alpha_pct:.1f}%，风格暴露占比 {style_pct:.1f}%")
        report_lines.append(f"   策略的收益主要来源于纯选股能力，风格择时贡献较小。")
    elif abs(alpha_pct) > 20:
        report_lines.append(f"⚡ **策略 Alpha 与风格并重**：Alpha 占比 {alpha_pct:.1f}%，风格暴露占比 {style_pct:.1f}%")
        report_lines.append(f"   策略同时依赖选股能力和风格暴露。")
    else:
        report_lines.append(f"⚠️ **策略以风格暴露为主**：Alpha 占比 {alpha_pct:.1f}%，风格暴露占比 {style_pct:.1f}%")
        report_lines.append(f"   策略的收益主要来源于风格因子暴露，纯选股 Alpha 较小。")
    
    report_lines.append("")
    
    # 主要风格暴露
    sorted_decomp = sorted(factor_decomp.items(), key=lambda x: abs(x[1]['annual_contribution']), reverse=True)
    report_lines.append(f"### 主要风格暴露来源")
    report_lines.append("")
    for fname, v in sorted_decomp[:5]:
        direction = "正向" if v['annual_contribution'] > 0 else "负向"
        report_lines.append(f"- **{fname}**: {direction}暴露，年化贡献 {v['annual_contribution']*100:.2f}% (t={v['t_stat']:.2f})")
    
    report_lines.append("")
    
    # Alpha 显著性
    if p_values[0] < 0.05:
        report_lines.append(f"### Alpha 显著性")
        report_lines.append(f"Alpha 在 5% 水平下统计显著 (p={p_values[0]:.4f})，说明策略确实存在超越风格因子的纯选股能力。")
    elif p_values[0] < 0.1:
        report_lines.append(f"### Alpha 显著性")
        report_lines.append(f"Alpha 在 10% 水平下边际显著 (p={p_values[0]:.4f})，纯选股能力有一定证据但不强。")
    else:
        report_lines.append(f"### Alpha 显著性")
        report_lines.append(f"Alpha 统计不显著 (p={p_values[0]:.4f})，策略收益可能主要来自风格暴露而非纯选股能力。")
    
    report_lines.append("")
    
    # R² 解读
    report_lines.append(f"### 模型解释力")
    report_lines.append(f"风格因子回归 R² = {r_squared_ts:.4f}，说明风格因子可以解释策略 {r_squared_ts*100:.1f}% 的收益波动。")
    if r_squared_ts > 0.5:
        report_lines.append(f"解释力较高，风格因子是策略收益的主要驱动因素。")
    elif r_squared_ts > 0.2:
        report_lines.append(f"解释力中等，风格因子和 Alpha 共同驱动策略收益。")
    else:
        report_lines.append(f"解释力较低，策略收益主要来自 Alpha 或其他未被捕捉的因素。")

report_lines.append("")
report_lines.append("---")
report_lines.append(f"*报告由 Barra 风格归因分析脚本自动生成*")
report_lines.append(f"*分析时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}*")

# 写入报告
report_content = "\n".join(report_lines)
with open(REPORT_PATH, 'w', encoding='utf-8') as f:
    f.write(report_content)

print(f"\n  ✅ 报告已写入: {REPORT_PATH}")

# ══════════════════════════════════════════════════════════════════
# 最终摘要
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("Barra 风格归因分析完成")
print("=" * 70)
print(f"\n策略: v11b_zz800_union")
print(f"回测期间: {nav_df.index[0].date()} ~ {nav_df.index[-1].date()}")
print(f"年化收益: {metrics['annual_return']*100:.2f}%")
print(f"Sharpe: {metrics['sharpe_ratio']:.3f}")
print(f"最大回撤: {metrics['max_drawdown']*100:.2f}%")
if len(ts_data) > 60:
    print(f"\nAlpha (年化): {alpha_ann*100:.2f}%")
    print(f"风格贡献(年化): {style_ann*100:.2f}%")
    print(f"Alpha 占比: {alpha_ann/total_ann_return*100:.1f}%" if total_ann_return != 0 else "Alpha 占比: N/A")
    print(f"R²: {r_squared_ts:.4f}")
print(f"\n报告路径: {REPORT_PATH}")

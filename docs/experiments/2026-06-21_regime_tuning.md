# v27 Regime 择时精调实验记录（2026-06-21）

## 背景
v27 价量共振策略 WF 13/13 正收益，夏普 7.15。regime 择时模块（牛熊判断→仓位乘数）是否对收益有贡献？

## 实验方案

### 方案A：斜率阈值化
- 在 `slope > 0` 基础上增加 `SLOPE_THRESHOLD`，过滤微小斜率
- 测试 ST=0.0/0.0003/0.0005/0.0008/0.001/0.0015/0.002，SD=3/5/10
- **结果：❌ 无效**，所有阈值 WF 结果完全一致（夏普6.419）
- 原因：SD=5 时 91.6% 的 |slope| > 0.001，阈值几乎不改变牛熊判定

### 方案B：线性连续映射
- slope 线性映射到 [bear_alloc, bull_alloc]
- 测试 slope_cap=0.01/0.008，aggressive bear=0.1
- **结果：❌ 无效**，与基准完全一致

### 方案C：多指数切换
- 在 calc_regime 中支持 REGIME_INDEX 参数切换上证/中证500
- **结果：⏳ 未单独测试**（中证500 K线数据未接入 DB）
- 代码已支持，后续有数据可直接用

### 方案D：波动率过滤
- 短期波动率 > 长期波动率×1.5 → 强制降仓到 bear_alloc
- 测试 vol_window=10/20, threshold=1.3/1.5
- **结果：❌ 无效**，与基准一致

### 方案E：熊市减仓
- 熊市时 MAX_HOLDINGS=4, MAX_DAILY_BUY=2（原来8/4）
- **结果：⚠️ 略降**，夏普6.831 vs 6.896
- 原因：限制持仓数后牛市也少买了票，错过机会

### 基准对比
关闭 REGIME_ENABLED 后结果完全一样 → **Regime 对 v27 WF 零贡献**

## 核心结论
v27 选股逻辑（mom_5>5% + 量价共振确认）**自带市场状态过滤**，熊市里很少有票能同时满足两个条件，仓位管理边际贡献几乎为零。

## 代码改动
- `strategy_adapter.py` calc_regime 升级：支持 REGIME_MODE=3class/linear/vol
- `strategy_map.py` v27 params 清理：移除 REGIME_ 相关参数
- `account_runner.py` 新增 POSITION_SCALE 账户级仓位控制参数（默认1.0）
- 收盘报告显示当前 POSITION_SCALE 设置

## POSITION_SCALE 设计
- 账户级配置（存 DB params_json），非策略参数
- 公式：available = cash × POSITION_SCALE - initial_capital × 0.03
- POSITION_SCALE=1.0 → 满仓（默认），0.8 → 80%仓位，0.5 → 半仓
- 通过 `create --position-scale 0.8` 或 DB 直接修改
- 收盘报告显示当前设置

## 教训
- 不要在选股策略自带择时特性的基础上叠加复杂择时模块
- 先验证模块是否对收益有贡献，再决定是否优化
- 简单三档（1.0/0.7/0.3）不比复杂线性映射差

## 实验脚本
- `scripts/backtest/sweep_v27_regime_slope.py` — 斜率阈值扫描
- `scripts/backtest/sweep_v27_regime_bcde.py` — B/C/D/E 综合扫描

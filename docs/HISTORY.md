# HISTORY — 已解决的问题记录

> 从 MEMORY 迁移过来的历史排查结论。按需查阅。

---

## 2026-06-01: 回测基准数字三岔路（17% / 20.72% / 24.82%）

### 现象
同一策略跑出三个不同年化收益数字。

### 根因

| 数字 | 根因 | 状态 |
|------|------|------|
| **17%** | `calc_factors_panel(close_panel)` 没传 `volume_panel/amount_panel` → vol_ratio 因子全灭 → 评分失真 | ✅ 已修复 |
| **20.72%** | 用了 HS300 股票池 + 行业仓位上限 25% 约束，收益被压低 | 这是正确的行业限制版基准 |
| **24.82%** | 无行业限制 + vol_panel 正确传入，这才是无约束基准 | ✅ 以此为准 |

### 教训
- **静默失败比 crash 更危险**：vol_panel 缺失不报错，只是数字全零
- **参数必须可追溯**：任何回测数字必须记录调用参数，否则无法复现
- 已在 run_backtest.py 加入 warning：vol_panel 缺失时打印 ⚠️

---

## 2026-06-01: boll_width_10 因子幽灵

### 现象
factors.py 里有 `boll_width_10` 计算代码，但 `FACTOR_WEIGHTS` 里没有它的权重（总共30个计算 vs 29个权重）。

### 处理
直接删除 `boll_width_10`，并将 `boll_width_20` 权重从 0.02 调为 0.03，保持总权重归一。

---

## 2026-05-30: sim_daily 和 run_backtest 两套代码

### 现象
回测和模拟盘用不同的因子计算、评分、交易逻辑 → 回测结果无法代表模拟盘表现。

### 处理
统一到 core/ 引擎，两套脚本都通过 `from core.account import ...` 调用相同函数。
代码级验证：正则扫描两个文件调用的函数名集合完全一致。

---

## 2026-05-30: /root/core/ 旧版残留

### 现象
`/root/core/` 和 `/root/a-share-quant-sim/core/` 同时存在，sim_daily 的 `sys.path.insert(0, "/root")` 导致优先加载旧版。

### 处理
1. 删除 `/root/core/` 旧版
2. sim_daily 的 sys.path 改为 `/root/a-share-quant-sim`

---

## 2026-05-30: sim_daily 非调仓日 trade_count 返回 0

### 现象
`step_rebalance` 在非调仓日返回 `trade_count=0`（硬编码），而非实际累加值。

### 处理
改为返回实际 `trade_count` 值。

---

## 2026-05-30: holdings 中 tp_taken 字段每次加载后被重置

### 现象
`step_load_account` 从 JSON 恢复 holdings 时，没有加载 `tp_taken` 字段 → 分级止盈状态丢失。

### 处理
load_state 时自动补 `tp_taken: []`。

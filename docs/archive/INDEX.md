# Archive 索引

> 最后更新: 2026-08-03
> 本目录存放已退役的代码和文档，仅供历史参考。生产代码请看 `scripts/` 和 `docs/`。

---

## 📁 scripts/archive/ (166 files)

### backtest/ — 旧版回测框架 (53 files)
已被 `scripts/backtest/` 和 `wf_runner.py` 替代。

| 分类 | 代表文件 | 说明 |
|------|---------|------|
| WF框架 | `walk_forward_v5/v6b/v8.py` | 早期WF实现，已被wf_runner.py替代 |
| 参数扫描 | `v13_*/v20c_*/v22_*/v27_*/v29_*` | 各版本止损止盈/参数扫描 |
| 策略对比 | `strategy_comparison.py`, `v27_v28_compare.py` | 策略间横向对比 |
| 集成探索 | `v11b_walk_forward.py`, `sweep_v11b_params.py` | v11b ensemble方案 |
| 滑点/成本 | `sl_tp_*.py` | 止损止盈边界测试 |

**参考价值**: ⭐⭐ 低。策略逻辑已迁移到 `scripts/strategies/`，扫描脚本结构可复用。

### research/ — 因子研究 (31 files)
早期因子研究脚本。

| 分类 | 代表文件 | 说明 |
|------|---------|------|
| 因子优化 | `factor_optimization.py`, `v6/v7/v8_factor_*` | 早期因子权重优化 |
| 择时研究 | `timing_v1/v2.py`, `timing_research.py` | 大盘择时方案 |
| 行业轮动 | `industry_rotation*.py` | 行业轮动策略 |
| 基本面 | `fundamental_factor_research.py`, `v9_fundamental_test.py` | 基本面因子 |
| 情绪因子 | `sentiment_factor_research.py`, `vol_sentiment.py` | 情绪/波动率因子 |
| ML因子 | `ml_factor_*.py` | 机器学习因子 |

**参考价值**: ⭐⭐⭐ 中。择时/行业轮动方向有复用价值，已归档到 `alpha-research/`。

### sim/ — 旧版模拟盘 (4 files)
v1-v3版模拟盘实现，已被 `scripts/sim/account_runner.py` 替代。

**参考价值**: ⭐ 低。完全过时。

### strategies/ — 退役策略 (1 file)
`v23_sentiment_combo.py` — 情绪组合策略，未进入生产。

**参考价值**: ⭐ 低。

### tools/ — 旧版工具 (40 files)
已被 `scripts/tools/` 和 `cmd.py` 替代。

| 分类 | 代表文件 | 说明 |
|------|---------|------|
| 旧CLI | `cli.py` | 被 cmd.py 替代 |
| 数据获取 | `fetch_eastmoney*.py`, `data_fetcher.py` | 早期数据爬虫 |
| DB迁移 | `migrate_db.py`, `rebuild_db*.py`, `import_csv_to_db.py` | 数据库重建脚本 |
| 数据修复 | `fix_amount_data.py`, `fix_stock_names.py`, `restore_amount.py` | 一次性修复 |
| 数据质量 | `data_quality.py`, `quality_data.py`, `analyze_pool.py` | 数据质检 |
| 行业数据 | `industry*.py`, `industry_map_db.py` | 行业分类数据 |
| ML训练 | `train_ml_model.py`, `ml_rolling_train.py` | 早期ML实验 |
| 新闻情绪 | `news_sentiment*.py` | 新闻情绪分析 |

**参考价值**: ⭐⭐ 低。数据获取/修复脚本的API调用模式可参考，但逻辑已过时。

### tune/ — 参数调优 (5 files)
v22/v23版参数调优（tuneB系列）。

**参考价值**: ⭐ 低。已被WF框架替代。

### v11b_explore/ — v11b集成探索 (4 files)
v11b ensemble方案探索（多组因子集成）。

**参考价值**: ⭐⭐ 中。ensemble思路可参考。

### wf/ — 旧版WF实现 (4 files)
早期Walk-Forward框架，已被 `wf_runner.py` 替代。

**参考价值**: ⭐ 低。

---

## 📁 docs/archive/ (8 files)

| 文件 | 说明 | 参考价值 |
|------|------|---------|
| `HISTORY.md` | 历史排查结论汇总（从MEMORY迁移） | ⭐⭐⭐ 重要 |
| `BARRA_ATTRIBUTION.md` | v11b策略Barra归因分析 | ⭐⭐⭐ 重要 |
| `BARRA_CONCLUSION.md` | Barra归因结论 | ⭐⭐⭐ 重要 |
| `research-report.md` | GitHub同类项目调研（2026-05） | ⭐⭐⭐ 重要 |
| `ROADMAP_REVIEW.md` | 路线图评审（大本事2026-06） | ⭐⭐ 中 |
| `api-notes.md` | 腾讯K线API格式笔记 | ⭐⭐ 中 |
| `v20_debug_report.md` | v20版数据一致性调试报告 | ⭐ 低 |
| `v61_turnover_3factor_nav.csv` | v61三因子净值曲线 | ⭐ 低 |

---

## 🗑️ 建议清理

以下目录/文件可安全删除（纯废弃，无参考价值）：

- `scripts/archive/sim/` (4 files) — 旧版模拟盘，完全过时
- `scripts/archive/tools_research/` (0 files) — 空目录
- `scripts/archive/tools/cli.py` — 已被 cmd.py 替代
- `scripts/archive/tools/backup_data.py` — 一次性备份
- `scripts/archive/tools/restore_amount.py` — 一次性修复
- `scripts/archive/tools/final_compare.py` — 临时对比脚本

预计可减少 ~100KB，166→158 files。

---

## 📝 维护规则

1. 新退役的代码放入对应子目录，更新本索引
2. 文件保留至少6个月再评估是否清理
3. 有参考价值的文件标记 ⭐⭐⭐，清理前必须确认不再需要

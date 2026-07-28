# 跨账户持仓去重 + 选股Top得分

> 日期: 2026-07-28
> 状态: 已实施

## 功能

### 1. 跨账户持仓去重

**目的**：其他账户已持有的股票，本账户不再建仓。

**实现**：
- `core/db.py` 新增 `get_other_accounts_holdings(account_id)` 函数
- `account_runner.py` 信号生成阶段，在选股后、截取前执行去重
- 通过账户级参数 `CROSS_ACCOUNT_DEDUP=true/false` 控制开关

**用法**：
```bash
# 启用
python scripts/sim/account_runner.py set-param --account-id 1 --key CROSS_ACCOUNT_DEDUP --value true
# 关闭
python scripts/sim/account_runner.py set-param --account-id 1 --key CROSS_ACCOUNT_DEDUP --value false
```

### 2. 选股Top得分展示

**目的**：信号输出展示策略选股得分，方便判断候选质量。

**实现**：
- 用空 holdings 调 `adapter.select`，让已持仓也参与打分
- `top_scores_raw` 保存原始选股得分
- `format_report.py` 展示 Top10 得分

**注意**：
- v61b 策略返回 score 全为 1.0（二值化评分），排序按策略内部综合得分
- v68 策略返回连续得分，可直接比较

### 3. set-param 通用命令

**目的**：设置任意账户级参数，不用改代码。

**用法**：
```bash
python scripts/sim/account_runner.py set-param --account-id <ID> --key <KEY> --value <VALUE>
```

### 4. list 命令增强

**改动**：`list_accounts()` 加载 `params_json`，`list` 命令显示所有账户级参数。

## 修改文件

| 文件 | 改动 |
|------|------|
| `core/db.py` | 新增 `get_other_accounts_holdings()`，修复 `list_accounts()` 加载 params_json |
| `scripts/sim/account_runner.py` | 跨账户去重逻辑、top_scores 生成、set-param 子命令、list 显示增强 |
| `scripts/tools/format_report.py` | 新增 Top得分展示区域 |

## 设计决策

- **去重在截取前执行**：排除后自动从后面补位候选股
- **set-param 只更新 params_json**：避免覆盖 cash/initial_capital（曾踩坑）
- **top_scores 用空 holdings**：让已持仓也参与打分，展示完整排名

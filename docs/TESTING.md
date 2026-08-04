# 标准用例测试

> 最后更新：2026-06-21

## 概述

标准用例测试用于确保框架核心功能和策略效果前后一致。
任何对核心模块的修改后，运行此测试套件验证无回归。

**运行命令：**
```bash
python -m pytest tests/standard/ -v              # 全部
python -m pytest tests/standard/test_account.py -v  # 只跑某模块
python -m pytest tests/standard/ -v --tb=short   # 简短错误信息
```

**当前状态：69 个用例，全部通过，耗时 ~0.2s**

## 测试架构

测试按工程架构分为 5 个独立模块，每个模块对应一个工程层级：

| 测试模块 | 工程模块 | 覆盖内容 |
|---------|---------|---------|
| `test_account.py` | `core/account.py` | 买卖/净值/止损/止盈/交易日志 |
| `test_sim.py` | `scripts/sim/account_runner.py` | 信号生成/执行/报告 JSON 格式 + 计划执行链路 |
| `test_strategies.py` | `scripts/strategies/*.py` | v39i/v44 因子计算 + plan 格式 |
| `test_backtest.py` | `scripts/backtest/` | 回测引擎/WF 分割/指标公式 |
| `test_integration.py` | `scripts/tools/` | send_report 格式化 + JSON 序列化 |

## 设计原则

1. **合成数据**：所有测试使用固定 seed 的合成数据，不依赖外部文件或网络
2. **独立性**：每个测试用例自包含，不依赖其他测试的状态
3. **确定性**：相同输入相同输出，可重复
4. **分层验证**：从原子操作到端到端，逐层验证
5. **快速**：整个套件 < 1 秒，适合每次修改后立即运行

## 共享 Fixtures

`tests/standard/conftest.py` 提供以下共享 fixture：

| Fixture | 说明 |
|---------|------|
| `empty_account` | 空账户，20万现金 |
| `sample_account` | 含3只持仓的账户 |
| `sample_prices` | 对应 sample_account 的价格序列 |
| `price_panel` | 60天×5只 合成价格面板 |
| `long_panel` | 250天×10只 长周期面板 |

工具函数：
- `make_prices(n_days, n_stocks, seed)` — 生成合成面板
- `make_account(cash, holdings)` — 快速构造测试账户
- `assert_valid_state(account)` — 账户状态合法性检查

## 运行规范

### 修改代码后

对以下文件的修改，**必须**运行全量标准用例：

```bash
# 修改 core/ 后
python -m pytest tests/standard/test_account.py tests/standard/test_sim.py -v

# 修改 scripts/strategies/ 后
python -m pytest tests/standard/test_strategies.py -v

# 修改 scripts/backtest/ 后
python -m pytest tests/standard/test_backtest.py -v

# 修改 scripts/tools/ 后
python -m pytest tests/standard/test_integration.py -v

# 大改后跑全量
python -m pytest tests/standard/ -v
```

### CI 集成

可在 `.github/workflows/` 中添加：
```yaml
- name: Run Standard Tests
  run: python -m pytest tests/standard/ -v
```

## 扩展指南

### 新增测试用例

在对应测试文件中添加 `class` 和 `test_` 方法：

```python
class TestNewFeature:
    def test_new_behavior(self, sample_account):
        """测试新功能"""
        # 使用 conftest.py 中的 fixture
        acc = sample_account
        # ... 执行操作
        assert 结果符合预期
```

### 新增测试模块

1. 在 `tests/standard/` 下创建 `test_xxx.py`
2. 在 `conftest.py` 中添加需要的 fixture
3. 在本文件更新测试架构表格

### 新增 Fixture

在 `conftest.py` 中添加：

```python
@pytest.fixture
def my_fixture():
    """说明"""
    return 数据
```

## 现有测试（补充）

除标准用例外，项目还保留以下专项测试：

| 文件 | 说明 | 状态 |
|------|------|------|
| `tests/test_sim_trading.py` | 模拟盘边界场景（39个用例） | ✅ 通过 |
| `scripts/tests/test_backtest_smoke.py` | 回测引擎冒烟测试 | ⚠️ 需修复导入 |
| `scripts/tests/test_backtest_edge_cases.py` | 回测边界用例 | ⚠️ 需修复导入 |

这些测试覆盖更细的边界场景，但依赖 `run_backtest.py` 的导入路径，待后续修复后可纳入标准用例。

---

## Golden Test — 回归测试套件

> 最后更新：2026-08-04

### 概述

Golden Test 用于验证**策略/环境/数据**变动前后，核心逻辑的结果一致性。与标准用例测试（单元测试）不同，Golden Test 关注的是**端到端的结果一致性**。

**运行命令：**
```bash
python tests/golden_test.py           # 直接运行
python -m pytest tests/golden_test.py -v  # 用 pytest
```

**当前状态：4 个测试，全部通过，耗时 ~60s**

### 测试内容

| 测试 | 说明 | 验证内容 |
|------|------|----------|
| 数据源一致性 | SQLite (腾讯) vs BaoStock | 价格/成交量差异 < 0.001% |
| 因子计算一致性 | v39g 因子两次计算对比 | 14个因子完全一致 |
| 策略适配器完整性 | v39g/v61b/v68 注册状态 | 策略已注册+参数完整 |
| v39g 全量回测 | 与历史基准对比 | 收益/夏普/回撤在容差内 |

### Golden 基准数据

基准数据存储在 `tests/golden_baselines.json`，包含：

```json
{
  "v39g_full_backtest": {
    "description": "v39g 全量回测 (2021-01-01 ~ 2026-05-31)",
    "params": {
      "strategy": "v39g",
      "start": "2021-01-01",
      "end": "2026-05-31",
      "pool": "zz1800"
    },
    "expected": {
      "total_return_pct": 93.95,
      "sharpe_ratio": 0.533,
      "max_drawdown_pct": -35.20,
      "final_nav": 387892.21
    },
    "tolerance": {
      "total_return_pct": 2.0,
      "sharpe_ratio": 0.02,
      "max_drawdown_pct": 0.5,
      "final_nav": 5000
    }
  }
}
```

### 容差说明

| 指标 | 容差 | 说明 |
|------|------|------|
| 收益率 | ±2% | 数据源/时间微小差异 |
| 夏普比率 | ±0.02 | 计算精度 |
| 最大回撤 | ±0.5% | 计算精度 |
| 最终净值 | ±5000元 | 绝对值容差 |

### 何时运行

**必须运行 Golden Test 的场景：**

1. **策略代码修改后**
   - `scripts/strategies/*.py`
   - `scripts/backtest/strategy_adapter.py`

2. **数据源切换/更新后**
   - `core/providers/*.py`
   - `scripts/tools/update_daily_data*.py`

3. **因子计算逻辑修改后**
   - `scripts/strategies/v39c_pv_resonance.py`
   - `core/factors.py`

4. **回测引擎修改后**
   - `scripts/backtest/wf_runner.py`
   - `core/account.py`

5. **数据库结构变更后**
   - `core/db.py`

### 如何更新基准

当回测结果因**合理原因**变化时（如修复 bug、优化算法），需要更新基准：

```python
# 在 tests/golden_test.py 中修改 DEFAULT_BASELINES
# 或直接编辑 tests/golden_baselines.json

# 更新后提交
git add tests/golden_baselines.json
git commit -m "test: 更新 golden 基准（原因说明）"
```

**警告：** 不要在没有合理原因的情况下更新基准。如果测试失败，应该先调查原因，而不是直接更新基准。

### 与标准用例的区别

| 维度 | 标准用例 | Golden Test |
|------|----------|-------------|
| 粒度 | 单元/集成 | 端到端 |
| 数据 | 合成数据 | 真实数据 |
| 耗时 | < 1秒 | ~60秒 |
| 关注点 | 功能正确性 | 结果一致性 |
| 运行频率 | 每次修改后 | 重要变更后 |

### 扩展 Golden Test

#### 新增回测基准

1. 运行回测获取结果
2. 在 `golden_baselines.json` 添加基准
3. 在 `golden_test.py` 添加测试函数
4. 运行验证通过

#### 新增数据源测试

1. 在 `test_data_consistency` 中添加对比逻辑
2. 设置合理的容差
3. 运行验证

#### 新增因子测试

1. 在 `test_factor_consistency` 中添加因子名
2. 运行验证因子计算确定性

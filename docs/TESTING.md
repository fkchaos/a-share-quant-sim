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
| `test_strategies.py` | `scripts/strategies/*.py` | v27/v32/v33/v35 因子计算 + plan 格式 |
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

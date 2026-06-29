# 新因子/策略扩展 — 架构设计方案

> 设计日期：2026-06-26
> 原则：解耦、可扩展、复用现有WF框架

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    信号生成入口                           │
│              account_runner.py  （run_and_send.py 已废弃）  │
└─────────────┬───────────────────────────────┬───────────┘
              │                               │
              ▼                               ▼
┌─────────────────────┐         ┌─────────────────────────┐
│   核心因子引擎       │         │   策略层                 │
│   (core/)           │         │   (strategies/)         │
│                     │         │                         │
│  ┌───────────────┐  │         │  v39i (动量+质量)        │
│  │ 行业动量因子   │  │         │  v44 (资金流+动量+低波)   │
│  │ 连板辨识度     │  │         │  v46a (行业过滤+连板+业绩) │
│  │ 业绩预告事件   │  │         │  v47 (ML因子版)          │
│  │ 情绪温度计     │  │         │  ...                     │
│  │ 增减持信号     │  │         │                         │
│  │ ML因子         │  │         │                         │
│  └───────────────┘  │         │                         │
│                     │         │                         │
│  输出: 因子DataFrame │         │  策略 = 因子加权组合      │
│  统一接口:           │         │  + 风控 + 仓位管理       │
│  compute(ctx) → df │         │                         │
└─────────────────────┘         └─────────────────────────┘
              │                               │
              ▼                               ▼
┌─────────────────────┐         ┌─────────────────────────┐
│   数据层             │         │   WF验证框架             │
│   (data/)           │         │   (wf_runner.py)        │
│                     │         │                         │
│  K线DB (SQLite)     │         │  复用现有框架            │
│  行业映射表         │         │  只需注册新策略           │
│  事件数据           │         │                         │
└─────────────────────┘         └─────────────────────────┘
```

---

## 二、解耦设计 — 三大原则

### 原则1：因子与策略解耦

- **因子** 是纯计算单元：输入 K线/事件数据，输出 因子值（Series/DataFrame）
- **策略** 是组合层：决定用哪些因子、什么权重、什么风控
- 同一个因子可以被多个策略复用（如行业动量因子可用于 v46 也可用于未来的 v48）

```
core/industry_momentum.py        ← 因子（纯计算，直接放 core/ 下）
scripts/strategies/v46a.py        ← 策略（引用因子 + 定义权重）
scripts/strategies/v48_future.py  ← 未来策略（可复用同一因子）
```

### 原则2：数据与因子解耦

- 数据获取层独立：每个数据源一个脚本（K线、行业分类、业绩预告、增减持）
- 因子层不直接调用数据源，而是通过统一的 Panel 对象传入
- 新增数据源不影响已有因子代码

```
data/fetch_industry_mapping.py   ← 获取行业分类
data/fetch_earnings_preview.py  ← 获取业绩预告
core/industry_momentum.py       ← 接收 Panel + mapping → 输出因子
```

### 原则3：策略注册化

- 新策略只需两步：
  1. 在 `scripts/strategies/` 下创建 `v{N}_{name}.py`
  2. 在 `core/strategy_map.py` 注册参数
- WF框架通过策略名直接加载，不需要改其他代码

---

## 三、因子接口规范

所有因子遵循统一接口：

```python
class FactorBase:
    """因子基类"""
    
    @property
    def name(self) -> str:
        """因子名称"""
        raise NotImplementedError
    
    def compute(self, panel: Panel, **kwargs) -> pd.Series:
        """
        计算因子值
        
        Args:
            panel: 标准面板数据 (close, vol, amt, high, low, open)
            **kwargs: 额外参数（如行业映射表、事件数据）
        
        Returns:
            pd.Series: 索引=股票代码, 值=因子值
        """
        raise NotImplementedError
```

**实际使用方式**：
- 简单因子用函数：`def compute_industry_momentum(panel, industry_map) -> pd.Series`
- 复杂因子用类（需要缓存/状态）
- 因子输出统一为 Series（index=股票代码），在策略层自动 merge

---

## 四、策略模板

新策略继承现有模式，最小改动：

```python
# strategies/v46_industry_filter.py

from core.strategy_base import StrategyBase
from factors.industry_momentum import compute_industry_momentum

class StrategyV46(StrategyBase):
    """v46: v39i + 行业轮动过滤"""
    
    name = "v46"
    
    def _calc_factors(self, panel, codes, date):
        # 1. 先算v39i原有因子
        factors = super()._calc_factors(panel, codes, date)
        
        # 2. 计算行业动量因子
        industry_mom = compute_industry_momentum(panel, self.industry_map)
        
        # 3. 行业过滤：只保留行业动量Top5的行业
        top_industries = industry_mom.groupby(industry_map).mean().nlargest(5).index
        industry_mask = pd.Series([industry_map.get(c) in top_industries for c in codes], index=codes)
        
        # 4. 非Top5行业的股票设因子值为NaN（不选）
        factors[~industry_mask] = np.nan
        
        return factors
```

---

## 五、目录结构

```
```
core/
├── factors.py                  ← 已有：因子引擎（630行，现有因子）
│                               ├── industry_momentum.py    ← 行业动量因子（✅ P1_2）
│   ├── streak_factor.py        ← 连板辨识度因子（✅ P1_4）
│   ├── sentiment_thermometer.py ← 情绪温度计（P2_1 待实现）
│   ├── insider_signal.py       ← 增减持信号（P3_1 待实现）
│   └── ml_factor.py            ← ML因子（P2_4 待实现）
├── db.py                       ← 已有：数据库（含 industry_map + stock_pool_zz1800 表）
├── strategy_map.py             ← 已有：策略注册
└── ...

scripts/
├── data/                       ← 新增：数据获取
│   ├── fetch_earnings_preview.py
│   └── fetch_insider_trades.py
├── ml/                         ← 新增：ML训练管道
│   ├── label_maker.py
│   └── lgbm_trainer.py
├── strategies/                 ← 已有：策略库
│   ├── v39i_optimized.py       ← 当前最优策略（夏普1.199）
│   ├── v44_flow_momentum.py     ← 备选策略（夏普1.252，zz800范围）
│   ├── v46a_industry_filter.py ← v39i+行业过滤+连板因子（✅ P1_3/P1_5）
│   └── v46_etf_rotation.py      ← 行业ETF动量轮动（已有）
├── portfolio/                  ← 新增：多策略组合
├── backtest/
│   └── wf_runner.py            ← 已有：WF验证入口（不修改）
├── tools/
│   └── init_project.py         ← 已更新：初始化含行业+zz1800导入
└── sim/
    └── account_runner.py       ← 已有：模拟盘执行

data/
├── zz800_constituents.csv      ← 已更新：800只成分股 + industry列
├── zz1000_constituents.csv     ← 新增：1000只成分股（含industry列）
├── stock_industry_map.json     ← 新增：5456只股票→行业映射
└── quant_stocks.db             ← SQLite（含 industry_map + stock_pool_zz1800 表）
```

---

## 六、扩展点设计

### 未来可接入的方向（不需要改架构）

| 方向 | 接入方式 | 需要新增的代码 |
|------|---------|--------------|
| 新因子 | 在 `factors/` 下新建文件 | 1个文件 |
| 新策略 | 在 `strategies/` 下新建文件 + 注册 | 1个文件 + strategy_map.py 1行 |
| 新数据源 | 在 `data/` 下新建文件 | 1个文件 |
| 新ML模型 | 在 `ml/` 下新建或修改 trainer | 1个文件 |
| 多策略组合 | 在 `portfolio/` 下修改 | 修改 multi_strategy.py |

### 关键约束

1. **不修改 wf_runner.py** — 新策略通过注册机制自动被WF框架识别
2. **不修改 core/db.py** — 新数据源独立存储或复用现有接口
3. **strategy_map.py 是唯一注册点** — 所有新策略在这里登记参数
4. **因子命名全局唯一** — 避免不同因子同名导致冲突

---

## 七、数据流示意

```
[每日信号生成流程]

1. load_panel_from_db(pool='zz800', ...)
   → 返回 (close, vol, amt, high, low, open) 矩阵

2. 加载辅助数据
   → industry_map (行业映射)
   → earnings_preview (业绩预告)
   → insider_trades (增减持)

3. 计算因子
   → factors = {factor.compute(panel) for factor in strategy.factors}
   → 合并为 DataFrame (stocks × factors)

4. 策略打分
   → scores = weighted_sum(factors, weights)
   → 应用过滤条件（行业过滤、连板过滤等）

5. 生成交易信号
   → 选Top N股票
   → 风控检查（仓位、涨跌停）
   → 输出 trade_plan.json
```

---

## 八、验证流程

```
[新因子/策略验证标准流程]

1. 快速回测（zz800, 2 folds, ~10s）
   → 如果2 folds正收益 < 50% → 直接放弃
   
2. 通过 → 上 WF（4 folds, train=252, test=126, step=63）
   → 对比 v39i 的夏普/回撤/正收益fold比例
   
3. 通过 → 参数扫描（±20% 权重变化）
   → 验证参数稳定性
   
4. 通过 → 加入策略组合候选池
   → 记录到 docs/strategy/RESULTS_LOG.md
```

---

## 九、风险隔离

| 风险 | 隔离方式 |
|------|---------|
| 新因子bug影响现有策略 | 因子独立文件，策略层通过 import 引用 |
| 行业数据缺失 | 行业映射加载失败时，跳过行业过滤（降级为v39i） |
| 业绩预告数据延迟 | 数据为空时，该因子不参与打分 |
| ML模型过拟合 | 独立WF验证，不通过不上线 |
| 多策略互相干扰 | 每个策略独立 trade_plan，账户层汇总 |

# 分域建模方案设计

> 创建: 2026-06-30
> 状态: 设计阶段
> 来源: 2026年量化策略新方向调研

---

## 1. 背景与动机

### 1.1 问题
- v39g（夏普1.297）和v61b（夏普2.186）在2026年市场情绪集中时表现不足
- v61b买冷门票，在科技/热点板块轮动时难以获利
- 全市场统一模型失效，需要分域建模

### 1.2 核心逻辑
**市场有效性提升 → 全市场统一模型失效 → 分域建模挖掘局部Alpha**

不同市值/风格的股票：
- 定价逻辑不同
- 流动性特征不同
- 参与者结构不同

### 1.3 参考
- 光大证券《量化选股系列报告之十四：因子分域初探》
- 光大证券《量化选股系列报告之十五：分域法改进因子的新尝试》
- 兴业证券《机器学习系列九：基于风格因子的非线性分域训练研究》

---

## 2. 分域方法设计

### 2.1 域划分标准

**按市值分割（主维度）：**

| 域名称 | 股票池 | 市值范围 | 特征 |
|--------|--------|---------|------|
| 大盘域 | 沪深300 | >500亿 | 流动性好、机构主导 |
| 中盘域 | 中证500 | 100-500亿 | 成长性、均衡 |
| 小盘域 | 中证1000 | <100亿 | 弹性大、散户多 |
| 微盘域 | 中证2000 | <50亿 | 高波动、流动性差 |

**可选补充维度（用于子域划分）：**
- 风格：价值/成长/动量/低波
- 行业：科技/消费/金融/周期

### 2.2 分域策略映射

| 域 | 当前策略 | 新策略 | 逻辑 |
|----|---------|--------|------|
| 大盘域 | v39g（动量） | 保持 | 机构主导，动量有效 |
| 中盘域 | 无 | **v70** | 需研究新因子 |
| 小盘域 | v61b（低换手） | 保持 | 散户主导，低流动性溢价 |
| 微盘域 | 无 | 可选 | 流动性差，风险高 |

### 2.3 分域执行流程

```
每日选股流程：
1. 获取全市场股票池（zz1800）
2. 按市值分割为3个域（大盘/中盘/小盘）
3. 每个域内独立计算因子和评分
4. 每个域内独立选股（各选N只）
5. 组合持仓（大盘域30% + 中盘域40% + 小盘域30%）
```

---

## 3. 架构设计

### 3.1 模块划分（解耦原则）

```
core/
├── domain_splitter.py      # 域分割器（新）
├── strategy_map.py          # 策略注册（修改）
└── factors.py               # 因子计算（复用）

scripts/strategies/
├── v39g_optimized.py        # 大盘域策略（复用）
├── v61_turnover_size.py     # 小盘域策略（复用）
└── v70_midcap_momentum.py   # 中盘域策略（新）

scripts/backtest/
├── domain_wf_runner.py      # 分域WF框架（新）
└── strategy_adapter.py      # 策略适配器（修改）
```

### 3.2 核心接口设计

```python
# core/domain_splitter.py

class DomainSplitter:
    """域分割器"""
    
    DOMAINS = {
        'large': {'pool': 'hs300', 'min_cap': 500e8},   # 大盘域
        'mid': {'pool': 'zz500', 'min_cap': 100e8},     # 中盘域
        'small': {'pool': 'zz1000', 'min_cap': 0},       # 小盘域
    }
    
    def split(self, codes, date) -> dict:
        """
        按市值分割股票池
        
        Returns:
            {
                'large': ['600519', '601318', ...],
                'mid': ['002415', '300750', ...],
                'small': ['002049', '300059', ...]
            }
        """
        pass
    
    def get_domain(self, code, date) -> str:
        """获取单只股票所属域"""
        pass
```

```python
# scripts/backtest/domain_wf_runner.py

class DomainWFRunner:
    """分域Walk-Forward框架"""
    
    def __init__(self, domain_strategies: dict):
        """
        Args:
            domain_strategies: {
                'large': 'v39g',
                'mid': 'v70',
                'small': 'v61b'
            }
        """
        pass
    
    def run(self, start_date, end_date, pool='zz1800'):
        """分域WF回测"""
        pass
    
    def _select_in_domain(self, domain, domain_codes, date):
        """域内选股"""
        pass
    
    def _combine_results(self, results):
        """组合各域结果"""
        pass
```

### 3.3 参数配置

```python
# 分域配置
DOMAIN_CONFIG = {
    'large': {
        'strategy': 'v39g',
        'weight': 0.30,        # 仓位权重
        'max_holdings': 3,     # 该域最多持仓
        'rebalance_days': 3,   # 调仓频率
    },
    'mid': {
        'strategy': 'v70',
        'weight': 0.40,
        'max_holdings': 4,
        'rebalance_days': 5,
    },
    'small': {
        'strategy': 'v61b',
        'weight': 0.30,
        'max_holdings': 3,
        'rebalance_days': 5,
    }
}
```

---

## 4. 中盘域策略设计（v70）

### 4.1 设计思路

中盘域（中证500）的特点：
- 市值100-500亿，流动性适中
- 成长性好，但波动也大
- 机构和散户混合参与

**v70核心逻辑：动量+质量+低波**

### 4.2 因子设计

| 因子 | 计算 | 方向 | 权重 |
|------|------|------|------|
| mom_20 | 20日动量 | 正向 | 0.40 |
| quality | ROE/PE | 正向 | 0.30 |
| vol_20 | 20日波动率 | 负向 | 0.30 |

### 4.3 风控参数

```python
V70_PARAMS = {
    'STOP_LOSS': -0.06,
    'TAKE_PROFIT': 0.12,
    'HOLD_DAYS_MAX': 5,
    'MAX_DAILY_BUY': 3,
    'MAX_POSITION': 0.25,
    'MAX_HOLDINGS': 4,
}
```

---

## 5. 实验步骤

### Phase 1: 域分割验证（1天）
1. 实现 `DomainSplitter` 类
2. 验证分割逻辑正确性
3. 统计各域股票数量和特征

### Phase 2: 中盘域因子研究（2天）
1. IC/IR分析：测试mom_20/quality/vol_20在中盘域的有效性
2. 单因子WF验证
3. 组合因子WF验证

### Phase 3: 分域WF框架（1天）
1. 实现 `DomainWFRunner`
2. 在v39g（大盘）和v61b（小盘）上验证框架正确性

### Phase 4: 全量WF验证（2天）
1. 分域策略 vs 全市场策略对比
2. 参数敏感性分析

---

## 6. 风险与预期

### 预期收益
- 分域建模可能提升夏普10-20%
- 各域独立优化，整体更稳定

### 主要风险
1. **域划分边界模糊** - 部分股票可能跨域
2. **风格切换** - 某个域可能阶段性失效
3. **组合复杂度** - 需要管理多个策略

### 验证重点
- 分域后各域因子IC是否提升
- 分域WF是否跑赢全市场WF
- 域间相关性是否足够低

---

## 7. 文件规划

```
docs/experiments/2026-06-30_subdomain_modeling_design.md  — 本文档
core/domain_splitter.py                                    — 域分割器
scripts/strategies/v70_midcap_momentum.py                  — 中盘域策略
scripts/backtest/domain_wf_runner.py                       — 分域WF框架
```

注册位置:
- core/strategy_map.py（v70）
- scripts/backtest/strategy_adapter.py（v70）

---

## 8. 下一步

- [ ] 实现 `DomainSplitter` 类
- [ ] 统计各域股票分布
- [ ] 中盘域因子IC分析
- [ ] 设计文档评审

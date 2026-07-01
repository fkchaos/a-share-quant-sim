# LLM Alpha挖掘方案设计

> 创建: 2026-06-30
> 状态: 设计阶段
> 来源: 2026年量化策略新方向调研

---

## 1. 背景与动机

### 1.1 问题
- 传统因子挖掘效率低，依赖人工经验
- 我们已尝试55+因子，大部分证伪
- LLM可以自动化、系统化挖掘Alpha

### 1.2 核心逻辑
**LLM + 进化算法 → 自动发现Alpha因子**

- QuantaAlpha框架：IC=0.15，ARR=27.75%
- 零样本跨市场迁移：CSI300→CSI500，超额160%
- 自动化、可解释、可持续进化

### 1.3 参考
- QuantaAlpha论文（arXiv 2602.07085）
- GitHub: QuantaAlpha/QuantaAlpha
- 华泰证券《人工智能系列》

---

## 2. 框架选择

### 2.1 方案对比

| 方案 | 优势 | 劣势 | 可行性 |
|------|------|------|--------|
| **QuantaAlpha** | 开源、效果最好、有WebUI | 需要GPU、数据格式特定 | ✅ 首选 |
| AlphaAgent | 论文效果好 | 未开源 | ❌ 不可行 |
| 自研LLM Agent | 完全可控 | 开发成本高 | ⚠️ 备选 |

### 2.2 推荐方案：QuantaAlpha

**核心优势：**
1. **轨迹级自进化** - 不是单点优化，而是整体进化
2. **三重质量控制** - 语义一致性、复杂度控制、冗余过滤
3. **零样本迁移** - 挖掘的因子可跨市场使用
4. **开源可用** - 有完整代码和文档

---

## 3. 技术架构

### 3.1 QuantaAlpha核心模块

```
QuantaAlpha框架
├── Diversified Planning     # 10个互补研究方向
│   ├── 信号源：价 vs 量
│   ├── 时间尺度：短期(1-5天) vs 长期(20-60天)
│   └── 机制类型：动量/反转/波动率/隔夜信息
│
├── Factor Construction      # 四层翻译
│   ├── 假设（自然语言）
│   ├── 形式化描述
│   ├── 数学表达式
│   └── 可执行代码
│
├── Three-Gate QC            # 三重质量控制
│   ├── 语义一致性门
│   ├── 复杂度控制门
│   └── 冗余过滤门
│
└── Self-Evolution           # 自进化
    ├── 变异（Mutation）
    └── 交叉（Crossover）
```

### 3.2 数据需求

**Qlib市场数据：**
- A股日频数据（2016-2025）
- 包含：OHLCV、行业、市值等

**预计算价量HDF5：**
- 高效因子挖掘用
- 包含：价格、成交量、各种衍生特征

### 3.3 硬件需求

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| GPU | 无（CPU可用） | RTX 3090/4090 |
| 内存 | 16GB | 32GB+ |
| 存储 | 10GB | 50GB+ |
| API | OpenAI/Claude API | GPT-5.2/Claude-4.5 |

---

## 4. 实施方案

### 4.1 Phase 1: 环境搭建（1天）

```bash
# 1. 克隆QuantaAlpha
git clone https://github.com/QuantaAlpha/QuantaAlpha.git
cd QuantaAlpha

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入API密钥

# 4. 下载数据
# 从HuggingFace下载Qlib数据和HDF5文件
```

### 4.2 Phase 2: 数据准备（1天）

```python
# 准备A股数据
# 方案1：使用QuantaAlpha自带数据（CSI300）
# 方案2：转换我们自己的zz1800数据

# 数据格式转换
python scripts/convert_data.py \
    --input data/quant_stocks.db \
    --output quantaalpha_data/cn_data/
```

### 4.3 Phase 3: 因子挖掘（2-3天）

```python
# 运行因子挖掘
python run_mining.py \
    --config configs/mining_csi300.yaml \
    --gpu 0

# 监控进度
# WebUI: http://localhost:8080
```

**挖掘参数：**
```yaml
# configs/mining_csi300.yaml
mining:
  num_trajectories: 10      # 10条轨迹并行
  max_iterations: 50        # 最大迭代次数
  ic_threshold: 0.03        # IC阈值
  
llm:
  model: gpt-5.2            # 或 claude-4.5-sonnet
  temperature: 0.7
  
evolution:
  mutation_rate: 0.3
  crossover_rate: 0.2
```

### 4.4 Phase 4: 因子验证（1天）

```python
# 1. 查看挖掘结果
cat all_factors_library*.json

# 2. 独立回测
python run_backtest.py \
    --factors all_factors_library.json \
    --config configs/backtest.yaml

# 3. 分析因子质量
python analyze_factors.py \
    --factors all_factors_library.json
```

### 4.5 Phase 5: 集成到v39g（1天）

```python
# 将挖掘的因子集成到v39g
# 1. 筛选高质量因子（IC > 0.04, IR > 0.3）
# 2. 转换为我们框架的因子格式
# 3. 集成到v39g评分体系

# 修改 scripts/strategies/v39g_optimized.py
def calc_factors_v39g_with_llm(...):
    # 原有因子
    factors = calc_factors_v39g(...)
    
    # LLM挖掘的因子
    llm_factors = load_llm_factors()
    
    # 组合评分
    final_score = 0.6 * factors + 0.4 * llm_factors
    
    return final_score
```

---

## 5. 架构设计（解耦原则）

### 5.1 模块划分

```
alpha_mining/
├── quantaalpha/              # QuantaAlpha框架（外部）
├── data_converter.py         # 数据格式转换（新）
├── factor_extractor.py       # 因子提取和转换（新）
├── factor_validator.py       # 因子验证（新）
└── integration.py            # 与v39g集成（新）

scripts/
└── alpha_mining/
    ├── setup_env.sh          # 环境搭建脚本
    ├── run_mining.py         # 因子挖掘入口
    ├── convert_data.py       # 数据转换
    └── analyze_results.py    # 结果分析
```

### 5.2 核心接口设计

```python
# alpha_mining/data_converter.py

class DataConverter:
    """数据格式转换器"""
    
    def convert_to_qlib(self, db_path, output_dir):
        """将SQLite数据转换为Qlib格式"""
        pass
    
    def convert_to_hdf5(self, db_path, output_path):
        """将数据转换为HDF5格式"""
        pass
```

```python
# alpha_mining/factor_extractor.py

class FactorExtractor:
    """因子提取器"""
    
    def extract_factors(self, factors_json):
        """从QuantaAlpha输出提取因子"""
        pass
    
    def convert_to_our_format(self, qlib_factors):
        """转换为我们框架的因子格式"""
        pass
    
    def filter_by_quality(self, factors, min_ic=0.04, min_ir=0.3):
        """按质量筛选因子"""
        pass
```

```python
# alpha_mining/integration.py

class LLMFactorIntegrator:
    """LLM因子集成器"""
    
    def __init__(self, factors_path):
        """加载LLM挖掘的因子"""
        pass
    
    def calc_combined_factors(self, base_factors, date):
        """计算组合因子"""
        pass
    
    def get_factor_weights(self):
        """获取因子权重（基于IC）"""
        pass
```

---

## 6. 实验步骤

### Phase 1: 环境搭建（1天）
- [ ] 克隆QuantaAlpha
- [ ] 安装依赖
- [ ] 配置API密钥
- [ ] 下载数据

### Phase 2: 数据准备（1天）
- [ ] 验证数据格式
- [ ] 转换我们自己的数据（可选）
- [ ] 生成HDF5文件

### Phase 3: 因子挖掘（2-3天）
- [ ] 运行CSI300因子挖掘
- [ ] 监控挖掘进度
- [ ] 收集挖掘结果

### Phase 4: 因子验证（1天）
- [ ] 分析因子质量
- [ ] 筛选高质量因子
- [ ] 跨市场迁移测试

### Phase 5: 集成验证（1天）
- [ ] 集成到v39g
- [ ] WF验证
- [ ] 与v39g原版对比

---

## 7. 风险与预期

### 预期收益
- QuantaAlpha IC=0.15，远超我们现有因子
- 零样本迁移可能有效
- 可能提升夏普20-50%

### 主要风险
1. **API成本** - GPT-5.2/Claude-4.5调用费用
2. **计算时间** - 因子挖掘可能需要数天
3. **过拟合风险** - 挖掘的因子可能过拟合历史数据
4. **黑箱因子** - 部分因子可能缺乏经济解释

### 验证重点
- 挖掘因子的IC是否显著
- 与v39g因子相关性是否足够低
- 集成后WF是否提升
- 跨市场迁移是否有效

---

## 8. 文件规划

```
docs/experiments/2026-06-30_llm_alpha_mining_design.md  — 本文档
alpha_mining/                                             — LLM因子挖掘模块
scripts/alpha_mining/                                     — 挖掘脚本
```

---

## 9. 下一步

- [ ] 评估API成本（GPT-5.2/Claude-4.5）
- [ ] 测试QuantaAlpha环境搭建
- [ ] 设计文档评审
- [ ] 确定是否实施（基于成本效益分析）

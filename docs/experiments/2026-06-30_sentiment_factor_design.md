# 舆情因子方案设计

> 创建: 2026-06-30
> 状态: 设计阶段
> 来源: 2026年量化策略新方向调研

---

## 1. 背景与动机

### 1.1 问题
- v39g/v61b基于价量因子，与传统因子高度相关
- 需要低相关性的Alpha来源
- 舆情因子与传统基本面因子相关性极低

### 1.2 核心逻辑
**新闻舆情 → 投资者情绪 → 短期价格波动 → Alpha**

- A股散户占比高，情绪驱动明显
- 新闻因子与传统因子相关性低
- AI语言模型提升情绪识别能力

### 1.3 参考
- 华泰证券《舆情因子和BERT情感分类模型》
- AKShare A股新闻情绪指数接口
- 雪球/东方财富舆情数据爬虫

---

## 2. 数据源设计

### 2.1 数据源选择

| 数据源 | 类型 | 免费 | 质量 | 可行性 |
|--------|------|------|------|--------|
| **AKShare新闻情绪指数** | 市场级 | ✅ | 中 | ✅ 首选 |
| **东方财富股吧** | 个股级 | ✅ | 中 | ✅ 可行 |
| **雪球讨论** | 个股级 | ✅ | 高 | ⚠️ 反爬 |
| **新浪财经新闻** | 个股级 | ✅ | 中 | ✅ 可行 |
| **同花顺iFind** | 专业级 | ❌ | 高 | ❌ 需付费 |

### 2.2 推荐方案：AKShare + 东方财富

**Phase 1: 市场情绪指数（AKShare）**
```python
import akshare as ak

# 获取A股新闻情绪指数
df = ak.stock_news_em()  # 东方财富新闻
# 或
df = ak.stock_market_activity_legulegu()  # 市场情绪
```

**Phase 2: 个股舆情（东方财富股吧）**
```python
# 爬取个股股吧讨论
# 分析情绪倾向
# 构建个股情绪因子
```

### 2.3 数据存储

```
data/
├── sentiment/
│   ├── market_sentiment.db    # 市场情绪指数
│   └── stock_sentiment.db     # 个股舆情数据
```

---

## 3. 因子构建设计

### 3.1 市场情绪因子

**因子定义：**
```python
# market_sentiment_score
# 来源：AKShare市场情绪指数
# 计算：直接使用指数值
# 方向：正向（情绪高=看多）
```

**使用方式：**
- 作为v39g/v61b的择时过滤器
- 情绪 > 阈值 → 正常选股
- 情绪 < 阈值 → 降低仓位

### 3.2 个股舆情因子

**因子定义：**
```python
# stock_sentiment_score
# 来源：东方财富股吧讨论
# 计算：NLP情感分析 → 看多/看空比例
# 方向：正向（看多占比高=看多）
```

**计算流程：**
```
1. 爬取个股股吧最近N天讨论
2. 使用SnowNLP/BERT进行情感分析
3. 统计看多/看空比例
4. 计算情绪得分 = (看多数 - 看空数) / 总数
```

### 3.3 新闻热度因子

**因子定义：**
```python
# news_heat_score
# 来源：个股新闻数量
# 计算：log(新闻数量 + 1)
# 方向：正向（热度高=关注度高）
```

**逻辑：**
- 新闻关注度高的股票，短期波动大
- 配合动量因子，捕捉热点轮动

---

## 4. 架构设计

### 4.1 模块划分（解耦原则）

```
core/
├── sentiment/
│   ├── __init__.py
│   ├── data_fetcher.py      # 数据获取（新）
│   ├── nlp_analyzer.py      # NLP分析（新）
│   └── factor_builder.py    # 因子构建（新）
└── factors.py               # 因子计算（修改，集成舆情因子）

scripts/data/
└── update_sentiment_data.py  # 舆情数据更新脚本（新）

scripts/strategies/
├── v39g_optimized.py        # 修改，集成舆情因子
└── v61_turnover_size.py     # 修改，集成舆情因子
```

### 4.2 核心接口设计

```python
# core/sentiment/data_fetcher.py

class SentimentDataFetcher:
    """舆情数据获取器"""
    
    def fetch_market_sentiment(self, date) -> float:
        """获取市场情绪指数"""
        pass
    
    def fetch_stock_news(self, code, days=7) -> list:
        """获取个股新闻"""
        pass
    
    def fetch_stock_guba(self, code, days=7) -> list:
        """获取股吧讨论"""
        pass
```

```python
# core/sentiment/nlp_analyzer.py

class SentimentAnalyzer:
    """NLP情感分析器"""
    
    def __init__(self, model='snownlp'):
        """
        Args:
            model: 'snownlp' | 'bert' | 'llm'
        """
        pass
    
    def analyze(self, text) -> dict:
        """
        分析单条文本
        
        Returns:
            {'sentiment': 'positive'|'negative'|'neutral', 'score': 0.85}
        """
        pass
    
    def batch_analyze(self, texts) -> list:
        """批量分析"""
        pass
```

```python
# core/sentiment/factor_builder.py

class SentimentFactorBuilder:
    """舆情因子构建器"""
    
    def build_market_factor(self, date) -> float:
        """构建市场情绪因子"""
        pass
    
    def build_stock_factor(self, code, date) -> float:
        """构建个股舆情因子"""
        pass
    
    def build_heat_factor(self, code, date) -> float:
        """构建新闻热度因子"""
        pass
    
    def build_all_factors(self, codes, date) -> pd.Series:
        """构建所有舆情因子"""
        pass
```

### 4.3 与现有策略集成

```python
# 在 v39g 中集成舆情因子

def calc_factors_v39g_with_sentiment(close_panel, volume_panel, ...):
    """计算v39g因子 + 舆情因子"""
    
    # 原有因子
    factors = calc_factors_v39g(close_panel, volume_panel, ...)
    
    # 舆情因子
    sentiment_builder = SentimentFactorBuilder()
    sentiment_factors = sentiment_builder.build_all_factors(codes, date)
    
    # 组合评分
    # 原有权重 80% + 舆情权重 20%
    final_score = 0.8 * factors + 0.2 * sentiment_factors
    
    return final_score
```

---

## 5. 实验步骤

### Phase 1: 数据获取验证（1天）
1. 测试AKShare新闻情绪指数接口
2. 测试东方财富股吧爬虫
3. 验证数据质量和覆盖度

### Phase 2: NLP分析验证（1天）
1. 测试SnowNLP情感分析效果
2. 准备金融领域语料库
3. 验证情感分析准确率

### Phase 3: 因子IC分析（1天）
1. 计算市场情绪因子IC
2. 计算个股舆情因子IC
3. 计算新闻热度因子IC

### Phase 4: 策略集成验证（2天）
1. 将舆情因子集成到v39g
2. WF验证舆情因子增量
3. 与v39g原版对比

---

## 6. 风险与预期

### 预期收益
- 舆情因子与传统因子低相关
- 可能提升夏普5-15%
- 增强热点轮动捕捉能力

### 主要风险
1. **数据获取稳定性** - 爬虫可能被封
2. **NLP准确率** - 通用模型在金融领域效果有限
3. **因子衰减** - 舆情因子可能被市场学习
4. **时效性** - 舆情数据需要高频更新

### 验证重点
- 舆情因子IC是否显著
- 与v39g因子相关性是否足够低
- 集成后WF是否提升

---

## 7. 文件规划

```
docs/experiments/2026-06-30_sentiment_factor_design.md  — 本文档
core/sentiment/__init__.py                              — 舆情模块
core/sentiment/data_fetcher.py                          — 数据获取
core/sentiment/nlp_analyzer.py                          — NLP分析
core/sentiment/factor_builder.py                        — 因子构建
scripts/data/update_sentiment_data.py                   — 数据更新脚本
```

---

## 8. 下一步

- [ ] 测试AKShare新闻情绪指数接口
- [ ] 测试东方财富股吧爬虫
- [ ] 验证SnowNLP情感分析效果
- [ ] 设计文档评审

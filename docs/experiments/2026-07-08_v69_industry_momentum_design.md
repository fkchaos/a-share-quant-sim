# v69: 行业动量追涨策略设计

> 日期：2026-07-08
> 状态：实施中

---

## 一、策略定位

**牛市追高追热点策略**，与v61b（冷门小票）和v68（小票动量）互补。

核心逻辑：
1. 识别近期最强势行业（行业动量排名）
2. 从强势行业中选个股（个股动量+量能过滤）
3. 市场情绪择时（连板晋级率+涨停数）
4. 快速止盈止损（短线持有）

## 二、因子设计

### 因子1：行业动量得分（IndustryMomentum）

用申万一级行业分类（已存在industry_map表），计算各行业的多周期动量：

```python
# 行业收益率 = 行业内成分股平均收益率
industry_ret_5d = 行业内股票5日收益率的均值
industry_ret_10d = 行业内股票10日收益率的均值
industry_ret_20d = 行业内股票20日收益率的均值

# 行业动量 = 加权合成
industry_momentum = 0.5 * rank(industry_ret_5d) + 0.3 * rank(industry_ret_10d) + 0.2 * rank(industry_ret_20d)

# 候选行业 = momentum排名前20%
hot_industries = industry_momentum > 阈值
```

### 因子2：个股强势度（StockStrength）

在热门行业内选个股：

```python
stock_strength = (
    0.50 * rank(mom_5d) +           # 5日动量
    0.30 * rank(vol_ratio) +         # 量比（5日/20日均量）
    0.20 * rank(amount_pct)          # 成交额排名（流动性）
)
```

### 因子3：市场情绪择时（SentimentFilter）

```python
# 情绪指标1：全市场涨停数
limit_up_count = 每日涨停股票数（returns >= 9.5%）
sentiment_ma = limit_up_count.rolling(5).mean()

# 情绪指标2：行业动量离散度（可选）
# 如果所有行业都在涨 → 普涨，追涨有效
# 如果行业分化严重 → 轮动，需要精准选行业
```

## 三、选股流程

```
每日选股：
1. 计算所有行业的5/10/20日动量
2. 按动量排名，选Top3最热行业
3. 取这3个行业的成分股
4. 过滤：排除涨停/ST/科创/市值<20亿
5. 计算个股强势度得分
6. 按得分排序，取Top N
7. 情绪过滤：涨停数<阈值时不开新仓
```

## 四、风控参数

```python
DEFAULT_PARAMS = {
    "STOP_LOSS": -0.06,           # 止损6%
    "TAKE_PROFIT": 0.12,          # 止盈12%
    "HOLD_DAYS_MAX": 5,           # 最长持有5天
    "MAX_HOLDINGS": 5,            # 最多持5只
    "MAX_DAILY_BUY": 3,           # 每天最多买3只
    "MAX_POSITION": 0.25,         # 单只最大25%仓位
    
    # 行业动量参数
    "W_MOM_5D": 0.50,             # 5日动量权重
    "W_MOM_10D": 0.30,            # 10日动量权重
    "W_MOM_20D": 0.20,            # 20日动量权重
    "TOP_INDUSTRY_PCT": 0.20,     # 选前20%行业
    "TOP_INDUSTRIES": 3,          # 最多选3个行业
    
    # 个股强势度权重
    "W_STOCK_MOM": 0.50,          # 个股动量权重
    "W_STOCK_VOL": 0.30,          # 量比权重
    "W_STOCK_AMT": 0.20,          # 成交额权重
    
    # 情绪择时
    "SENTIMENT_THRESHOLD": 20,    # 涨停数阈值
    
    # 过滤
    "EXCLUDE_LIMIT_UP": True,
    "MIN_MARKET_CAP": 2e9,        # 最小市值20亿
    "MIN_AMOUNT": 5e7,            # 最小成交额5000万
}
```

## 五、实施步骤

1. 创建 `scripts/strategies/v69_industry_momentum.py`
2. 注册到 `core/strategy_map.py`
3. 注册到 `scripts/backtest/strategy_adapter.py`
4. 注册到 `scripts/backtest/wf_runner.py`
5. 运行IC分析
6. 运行全量回测
7. 运行WF验证

## 六、与v35的区别

v35也用行业轮动，但：
- v35用**成交额分组代理行业**（大/中/小盘），不是真实行业
- v69用**申万一级行业分类**，是真实的行业维度
- v69更聚焦"追涨"，v35更偏"均衡配置"

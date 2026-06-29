# Alpha158 因子构建公式（Qlib 官方）

> 来源: https://bigquant.com/wiki/doc/nODcNAKYPJ
> 归档: 2026-06-29

## 概览 — 158个因子分8大类

| 类别 | 数量 | 模式 |
|------|------|------|
| K线基础 | 9 | 独立公式 |
| 静态价格 | 4 | open/high/low/vwap / close |
| 趋势 | 25 | 5指标 × 5周期(5/10/20/30/60) |
| 波动 | 30 | 6指标 × 5周期 |
| 极值位置 | 15 | 3指标 × 5周期 |
| 价量统计 | 45 | 9指标 × 5周期 |
| 成交量波动 | 10 | 2指标 × 5周期 |
| 量价加权统计 | 20 | 4指标 × 5周期 |

## 设计规律

1. **多周期统一**: 5/10/20/30/60
2. **归一化**: 价格类 /close，成交量类 /volume
3. **价量分离**: 价格因子和成交量因子独立

## 一、K线基础因子（9个）

```python
KMID  = (close - open) / open
KLEN  = (high - low) / open
KMID2 = (close - open) / (high - low)
KUP   = (high - Max(open, close)) / open
KUP2  = (high - Max(open, close)) / (high - low)
KMin(open, close) - low) / open
KLOW2 = (Min(open, close) - low) / (high - low)
KSFT  = (2 * close - high - low) / open
KSFT2 = (2 * close - high - low) / (high - low)
```

## 二、静态价格因子（4个）

```python
OPEN0  = open / close
HIGH0  = high / close
LOW0   = low / close
VWAP0  = vwap / close
```

## 三、趋势类因子（25个 = 5指标 × 5周期）

| 指标 | 公式 | 含义 |
|------|------|------|
| ROC | Ref(close, N) / close | 收益率 |
| MA | Mean(close, N) / close | 均线偏离 |
| BETA | Slope(close, N) / close | 线性回归斜率 |
| RSQR | Rsquare(close, N) | 趋势拟合度 |
| RESI | Resi(close, N) / close | 线性回归残差 |

## 四、波动类因子（30个 = 6指标 × 5周期）

| 指标 | 公式 | 含义 |
|------|------|------|
| STD | Std(close, N) / close | 标准差 |
| MAX | Max(high, N) / close | N日最高偏移 |
| MIN | Min(low, N) / close | N日最低偏移 |
| QTLU | Quantile(close, N, 0.8) / close | 80%分位偏移 |
| QTLD | Quantile(close, N, 0.2) / close | 20%分位偏移 |
| RSV | (close - Min(low,N)) / (Max(high,N) - Min(low,N)) | KDJ的%K |

## 五、极值位置类因子（15个 = 3指标 × 5周期）

| 指标 | 公式 | 含义 |
|------|------|------|
| IMAX | IdxMax(high, N) / N | 最高价位置（归一化）|
| IMIN | IdxMin(low, N) / N | 最低价位置（归一化）|
| IMXD | (IdxMax(high,N) - IdxMin(low,N)) / N | 极值点距离 |

## 六、价量统计类因子（45个 = 9指标 × 5周期）

| 指标 | 公式 | 含义 |
|------|------|------|
| CORR | Corr(close, Log(volume+1), N) | 价量相关 |
| CORD | Corr(close/Ref(close,1), Log(volume/Ref(volume,1)+1), N) | 价变与量变相关 |
| CNTP | Mean(close > Ref(close,1), N) | 涨天数占比 |
| CNTN | Mean(close < Ref(close,1), N) | 跌天数占比 |
| CNTD | CNTP - CNTN | 涨跌天数差 |
| SUMP | Sum(Max(close-Ref,0),N) / Sum(Abs(close-Ref),N) | 涨幅/振幅（RSI分子）|
| SUMN | Sum(Max(Ref-close,0),N) / Sum(Abs(close-Ref),N) | 跌幅/振幅 |
| SUMD | SUMP - SUMN | 涨跌振幅差 |
| RANK | Rank(close, N) | N日排名 |

## 七、成交量波动类因子（10个 = 2指标 × 5周期）

```python
VMA   = Mean(volume, N) / volume    # 量比倒数
VSTD  = Std(volume, N) / volume      # 成交量波动率
```

## 八、量价加权统计因子（20个 = 4指标 × 5周期）

| 指标 | 公式 | 含义 |
|------|------|------|
| WVMA | Std(\|ret\|*volume, N) / Mean(\|ret\|*volume, N) | 量价比变异系数 |
| VSUMP | Sum(Max(volume-Ref,0),N) / Sum(Abs(volume-Ref),N) | 放量/振幅 |
| VSUMN | Sum(Max(Ref-volume,0),N) / Sum(Abs(volume-Ref),N) | 缩量/振幅 |
| VSUMD | VSUMP - VSUMN | 放量缩量差 |

---

## 对我们的价值

这些因子可以直接用 Python/pandas 实现，输入 OHLCV 面板即可。
和我们的 v39g 因子对比：

| 对比 | v39g | Alpha158 |
|------|------|----------|
| 因子数 | 8个 | 158个 |
| 时间框架 | 纯短周期(5/10/20日) | 多周期(5/10/20/30/60) |
| 价量分离 | 有(size/illiq/fund_flow) | 有(第七八类) |
| 趋势/波动 | 无(BOLL仅门槛) | 有(第三四类25+30个) |
| 价量相关 | pv_corr_10/20 | CORR/CORD/CNTP等 |

**最有价值的新因子方向**:
1. RSV (KDJ的%K) — 价格在N日区间的极值位置，和 RSI 不同
2. SUMP/SUMN/SUMD — RSI 的分解版
3. CORR/CORD — 价量相关(我们已有pv_corr，但可对数变换改进)
4. IMAX/IMIN — 极值位置时间分布(我们没有)
5. WVMA/VSUMD — 量价加权(我们没有)

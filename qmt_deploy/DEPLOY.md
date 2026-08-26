# QMT Adapter 部署指南

## 目录结构

```
qmt_deploy/
├── v61c_qmt.py                 # v61c入口文件（加载这个）
├── v75j_qmt.py                 # v75j入口文件（加载这个）
├── qmt_diagnostic.py           # 环境诊断（先跑这个）
├── qmt_adapter/
│   ├── config.py               # 风控/市场/账户参数
│   ├── data.py                 # 行情数据获取
│   ├── trading.py              # 交易下单封装
│   ├── qmt_runner.py           # 公共逻辑（风控/rebalance/买入）
│   ├── qmt_data.py             # ZZ1800股票池 + FLOAT_SHARES
│   ├── v61c_strategy.py        # v61c选股逻辑
│   └── v75j_strategy.py        # v75j选股逻辑
├── qmt_verify.py               # 本地验证脚本（9项测试）
└── DEPLOY.md                   # 本文档
```

## 部署步骤

### 1. 复制文件到QMT

将整个 `qmt_deploy/` 目录复制到QMT Windows机器上。

```
建议路径: D:\qmt\strategies\a-share-quant-sim\
```

### 2. QMT加载策略

1. 打开QMT客户端
2. 进入「策略交易」→「策略编辑器」
3. 新建策略，选择「导入文件」
4. 加载入口文件：`v61c_qmt.py` 或 `v75j_qmt.py`
5. **不要**直接加载 `qmt_adapter/` 下的文件，入口文件会自动导入

### 3. 配置参数

打开 `qmt_adapter/config.py`：

```python
# 账户配置
ACCOUNT_CONFIG = {
    'account_id': 'SIMTEST',     # ← 回测用SIMTEST，实盘改成真实ID
    'account_type': 'STOCK',
}

# 风控参数（v75j默认）
RISK_CONFIG = {
    'stop_loss': -0.08,
    'take_profit': 0.25,
    'hold_days_max': 20,
}

# v61c独立风控
V61C_RISK_CONFIG = {
    'stop_loss': -0.10,
    'take_profit': 0.20,
    'hold_days_max': 5,
}
```

### 4. 开启DEBUG

编辑入口文件顶部：

```python
# v61c_qmt.py 或 v75j_qmt.py
DEBUG = True   # ← 回测时设True，实盘设False
```

### 5. 运行回测

### 首次使用：先跑环境诊断

1. 加载 `qmt_diagnostic.py`，跑1天回测
2. 查看输出，确认所有 `[OK]` 项通过
3. 如果有 `[FAIL]` 项，参考下方「常见问题」排查

诊断项目：API可用性、行情数据、股票池、账户、行业映射

### 正式回测

1. QMT「策略交易」→「回测」
2. 选择策略文件（v61c_qmt.py 或 v75j_qmt.py）
3. 设置回测参数：
   - 初始资金：100000
   - 回测周期：建议先跑3个月验证
   - K线周期：日线
   - 复权方式：前复权
4. 点击「运行回测」

## 两个策略的区别

| | v61c | v75j |
|--|------|------|
| 选股因子 | 低换手率 + 小市值 | 科技板块流动性 |
| 持仓数 | 5只 | 3只 |
| 单只仓位 | 5% | 11.7% |
| 总仓位上限 | 25% | 35% |
| 超期卖出 | 5天 | 10天 |
| 止损 | -10% | -8% |
| 止盈 | +20% | +25% |
| 板块 | 全市场（排除科创板） | 科技板块（排除科创板） |
| 广度过滤 | 无 | MA20比例<30%不买 |

## 调仓逻辑

**per-stock独立调仓**，没有全局调仓日：

```
每根K线：
1. 所有持仓 hold_days++
2. 风控检查：止损/止盈/最大持仓天数 → 触发即卖
3. 超期检查：hold_days >= rebalance_days → 卖出
4. 空位检查：max_holdings - 当前持仓数 = slots
5. slots > 0 → 选股买入
```

## 常见问题

### Q1: 策略加载报编码错误

**现象**: `UnicodeDecodeError` 或 `GBK codec error`

**原因**: 策略文件使用GBK编码（QMT要求）

**解决**: 
- 不要用UTF-8编辑器修改策略文件
- 用记事本或GBK兼容的编辑器
- 如果文件被转成UTF-8，用 `iconv` 转回GBK：
  ```bash
  iconv -f UTF-8 -t GBK file.py -o file_gbk.py
  ```

### Q2: 策略加载报模块找不到

**现象**: `ModuleNotFoundError: No module named 'qmt_adapter'`

**原因**: 入口文件和qmt_adapter目录不在同一层级

**解决**: 
- 确保目录结构正确：`v61c_qmt.py` 和 `qmt_adapter/` 在同一目录
- 在QMT中加载入口文件时，选择正确的路径

### Q3: 回测不出交易记录

**现象**: 回测完成但没有任何买卖

**可能原因**:
1. `DEBUG=False` → 看不到输出（但交易应该有）
2. 股票池为空 → 检查 `qmt_data.py` 的 `ZZ1800_STOCKS`
3. 数据没下载 → QMT需要先下载历史数据
4. 广度过滤(v75j) → 弱市时策略不买

**排查**:
```python
# 在QMT的Python控制台执行
from qmt_adapter.qmt_data import ZZ1800_STOCKS
print('股票池数量:', len(ZZ1800_STOCKS))
```

### Q4: 买入失败

**现象**: debug显示 `buy targets` 但没有成交

**可能原因**:
1. 资金不足 → 检查 `available cash`
2. 停牌/涨跌停 → 当天无法交易
3. 最小交易单位 → A股最少100股

### Q5: 风控没触发

**现象**: 持仓亏损超过止损线但没卖出

**排查**:
1. 检查debug输出中的 `risk:` 行
2. 确认参数正确（v61c: SL=-0.10, v75j: SL=-0.08）
3. 检查 `get_close_price` 是否返回了有效价格

### Q6: v75j广度一直是1.0

**现象**: debug显示 `breadth: 1.0000 (xxx/xxx above MA20)`

**原因**: 数据不足20天，广度默认返回1.0

**解决**: 确保QMT下载了足够的历史数据（至少25天）

### Q7: 科创板股票被选中

**现象**: 买入了688/689开头的股票

**原因**: `get_instrument_detail` 返回的行业分类可能不包含科创板标记

**解决**: 检查debug输出中的科技股列表，确认过滤逻辑正常

## Debug输出说明

开启 `DEBUG=True` 后，QMT的「策略输出」窗口会显示：

### v61c示例
```
[V61C] init done. pool=1800 rebalance_days=5
[V61C] risk: SL=-0.10 TP=0.20 HD=5
[V61C] risk: 000xxx pnl=-10.5% days=3 -> SELL(SL)
[V61C] time exit: 000yyy days=5 >= 5 -> SELL
[V61C] 2 slots available, selecting stocks...
[V61C] candidates=1523, top 10:
  000034.SZ score=1.9400 turnover=4.83% mcap=943.5亿
  000050.SZ score=1.9100 turnover=4.85% mcap=909.2亿
  ...
[V61C] buy targets:
  000034.SZ weight=0.0500 (50000元)
  000050.SZ weight=0.0500 (50000元)
  ...
```

### v75j示例
```
[V75J] init done. pool=1800 tech=423
[V75J] risk: SL=-0.08 TP=0.25 HD=20
[V75J] breadth: 0.4500 (190/423 above MA20)
[V75J] breadth 0.45 in [0.30, 0.50) -> scale to 2 stocks
[V75J] liquidity candidates=423 top 10:
  600519.SH score=0.9800 avg_amt=45.2亿
  000858.SZ score=0.9500 avg_amt=38.7亿
  ...
[V75J] buy targets:
  600519.SH weight=0.1167 (35000元)
  000858.SZ weight=0.1167 (35000元)
  ...
```

## 本地验证

在Linux机器上运行验证脚本：

```bash
cd /root/a-share-quant-sim
python3 qmt_deploy/qmt_verify.py
```

预期输出：`9 passed, 0 failed`

验证项目：
1. 风控逻辑（止损/止盈/最大天数）
2. 仓位计算（v61c: 5%, v75j: 11.7%）
3. Per-stock超期卖出
4. 广度过滤（v75j）
5. 换手率+市值排名（v61c）
6. 科创板过滤（v75j）
7. 风控参数分离
8. DEBUG开关
9. 有空位才买入

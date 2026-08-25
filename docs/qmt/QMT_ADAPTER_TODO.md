# QMT Adapter TODO - 2026-08-26

## 明天必须完成

### 1. 恢复v61c选股逻辑（优先级：P0）
**问题**：当前v61c_strategy.py的`_select_stocks`只按市值排序，缺少低换手因子
**目标逻辑**：
```python
turnover = volume * 100 / float_shares  # 换手率（volume单位是手）
turn_5 = turnover.rolling(5).mean()      # 5日均换手率
market_cap = close * float_shares        # 市值
# 低换手 + 小市值 → rank评分
```
**实现步骤**：
1. `data.py`添加`get_kline_data(C, stock_list, count=5)`获取多日K线
2. `_select_stocks`里计算5日均换手率
3. 换手率+市值各50%权重rank评分
4. 参考：`scripts/strategies/v61_turnover_size.py`的`calc_factors_v61`

### 2. 恢复v75j选股逻辑（优先级：P0）
**问题**：当前v75j_strategy.py的`_select_stocks`只按float_shares排序
**目标逻辑**：
- 科技趋势：需要动量因子（如5日涨幅）
- 流动性：float_shares作为proxy
- 广度过滤：需要市场宽度指标（如上涨比例）
**实现步骤**：
1. 参考：`scripts/strategies/v75j_liquidity_only.py`
2. 实现科技趋势+流动性+广度过滤的三因子选股

### 3. 验证QMT回测交易记录（优先级：P1）
**现状**：passorder调用成功（有系统WARNING），但回测界面看不到记录
**排查方向**：
- QMT回测界面的初始资金设置是否正确
- 账户配置是否匹配（8890979649）
- 是否需要在QMT界面单独配置回测参数

### 4. config.py更新（优先级：P1）
- 填入主公的真实account_id（已在本地填，但代码默认值还是SIMTEST）
- 确认account_type='STOCK'是否正确

### 5. 清理遗留问题（优先级：P2）
- [ ] 删除v61c_debug_strategy.py的SELECT打印（或保留作为调试工具）
- [ ] 确认qmt_data.py的FLOAT_SHARES和INDUSTRY_MAP数据是否准确
- [ ] 测试v75j的REBALANCE_DAYS=10是否合理

## 参考文件
- 原始v61c策略：`scripts/strategies/v61_turnover_size.py`
- 原始v75j策略：`scripts/strategies/v75j_liquidity_only.py`
- QMT知识库：`docs/qmt/wtsolutions/`
- 当前adapter：`qmt_deploy/qmt_adapter/`

## 已完成的修复（供参考）
- [x] GBK编码+英文注释
- [x] 模块级全局变量（不存C属性）
- [x] entry文件定义init/handlebar函数体
- [x] frame遍历找QMT内置函数
- [x] get_market_data_ex参数修正（field_list在前）
- [x] passorder参数修正（11参数）
- [x] 回测禁用datetime.now()去重
- [x] total_value=0用available cash兜底
- [x] 清理debug打印

# QMT Adapter Implementation TODO

## Status

- [x] P0: v61c选股逻辑恢复 ✅ (2026-08-26)
- [x] P0: v75j选股逻辑恢复 ✅ (2026-08-26)
- [x] P1: config.py更新 ✅ (2026-08-26)
- [x] P1: sell_all验证 ✅ (之前已验证)
- [x] P2: 清理遗留问题 ✅ (2026-08-26)
- [x] P0: strategy_base.py 共享工具提取 ✅ (2026-09-11)
- [x] P0: 订单状态机重写（7状态、3层确认）✅ (2026-09-11)
- [x] P0: 原子JSON写入 ✅ (2026-09-11)
- [x] P1: strategy_name lowercase/UPPERCASE 约定统一 ✅ (2026-09-11)
- [x] P1: 订单成交后自动同步 positions JSON ✅ (2026-09-11)
- [x] P1: PER_STRATEGY_POSITIONS 重新启用 ✅ (2026-09-11)

---

## Completed Changes (2026-08-26)

### 1. data.py - 新增 get_kline_data_multi()
- 获取多日K线数据，供v61c计算换手率、v75j计算流动性
- 支持批量获取，有逐只fallback
- Python 3.6.8兼容

### 2. v61c_strategy.py - 恢复低换手+小市值选股
- 从config读取REBALANCE_CONFIG（不再硬编码5天）
- 计算5日均换手率：volume(股)/float_shares
- 计算市值：close*float_shares
- 等权50/50 rank评分（低换手+小市值）
- 每日缓存K线数据避免重复获取

### 3. v75j_strategy.py - 恢复科技趋势+流动性+广度过滤
- 用C.get_instrument_detail()获取行业（QMT环境不能导入sqlite）
- init时构建行业映射，识别科技板块（电子/计算机/通信/传媒）
- 广度过滤：科技股中收盘价>MA20的比例
  - breadth<0.30: 空仓
  - 0.30<=breadth<0.50: 线性减仓（MAX_HOLDINGS按比例缩减）
  - breadth>=0.50: 满仓运行
- 流动性排序：按float_shares降序（越大越流动）
- 股价过滤：<300元
- 科创板过滤：688/689开头排除

### 4. config.py - 明确回测/实盘区分
- 添加注释：account_id='SIMTEST'仅用于回测
- 部署QMT实盘前需改为真实account_id
- 代码中所有策略从config统一读取，不硬编码

---

## Completed Changes (2026-08-27)

### 5. config.py - 策略参数集中化
- STRATEGIES dict 按策略名分组，所有参数一处管理
- 每策略独立 capital（10万），不再用账户总资产算仓位
- max_per_stock 替代 max_pos（单只上限，不受其他策略影响）
- hold_days_max（风控超时）vs rebalance_days（v61c续持）分清
- v75j 去掉死参数 rebalance_days（adapter不用）

### 6. PER_STRATEGY_POSITIONS 隔离开关
- 两策略共用同一账户时，持仓JSON隔离
- check_risk / max_holdings 只看自己的JSON
- 买入/卖出同时更新QMT账户+本地JSON
- JSON从空开始，不从账户同步（避免误拽其他策略持仓）
- 关闭开关回退到旧逻辑（共享账户持仓）

### 7. 双触发入口
- handlebar() → on_signal()（回测）
- schedule_run + TIMER_TIME='14:50:00'（实盘）
- MODE='BACKTEST'|'LIVE' 控制
- schedule_run time_point 必须传字符串

### 8. hold_days 盘中优化
- date.today() 补偿 daily_kline 数据延迟
- 今天买的 hold_days=0（buy_date < today_str 才加1）

---

## Completed Changes (2026-09-11)

### 9. strategy_base.py — 共享工具提取
- 从 v61c_strategy 和 v75j_strategy 提取 ~90 行重复代码
- `get_bar_date(C)`: 从 ContextInfo 获取当前 bar 日期（禁止 datetime.now()）
- `load_hold_days(strategy_name)`: 从 JSON 加载持仓天数
- `persist_hold_days(strategy_name, hold_days, today)`: 原子写入持仓天数
- `is_limit_up(close, prev_close)`: 涨停检测（9.5% 阈值）
- `apply_limit_up_filter(codes, kline_data)`: 批量涨停过滤

### 10. 订单状态机重写 (trading.py)
- 7 状态：pending → ordered → partial → filled [TERMINAL]
- 3 层确认：ORDER → DEAL → POSITION
- 超时处理：60s pending→rejected, 120s→cancel, 300s→force expired
- 撤单：最多 3 次重试
- 无 order_id 超 30s → force expired
- STATUS 53 (部分撤单) → terminal（已成交部分保留）
- 10s 轮询间隔，自调度（schedule_run）

### 11. 原子JSON写入
- `_hold_days_{strategy}.json`: tmp + os.rename
- `_positions_{strategy}.json`: tmp + os.rename
- 防止写入中断导致文件损坏

### 12. strategy_name 大小写约定
- 内部（config, JSON, 日志）: lowercase (`'v61c'`, `'v75j'`)
- QMT API（passorder, get_trade_detail_data）: UPPERCASE (`'V61C'`, `'V75J'`)
- `QmtAccount.buy/sell()` 接收 lowercase，内部 `.upper()` 转换

### 13. 订单成交后自动同步 positions JSON
- `_order_transition(filled)` → `_sync_strategy_position()`
- `_sync_strategy_position()` → `qmt_runner.strategy_buy/sell()`
- 不再需要在 deal_callback 中手动更新

---

## Key Technical Decisions

1. **QMT volume单位**：QMT返回股（shares），不是手（lots），所以换手率=volume/float_shares
2. **行业映射**：QMT环境不能import sqlite，改用 `qmt_data_static.py` 静态数据
3. **K线缓存**：每日首次获取后缓存，同一天内不重复fetch
4. **Python 3.6.8兼容**：无walrus(:=)、无dict union(|)、无debug f-string(=)
5. **仓位隔离**：共享股票账户无法拆子账户，用本地JSON实现per-strategy持仓
6. **不从账户同步持仓**：JSON空=策略没买过，避免新策略初始化时拽入其他策略持仓
7. **capital静态分配**：每策略固定资金池，不受其他策略买入影响
8. **hold_days vs positions 分离**：更新频率不同（每 bar vs 仅成交），分开管理
9. **三层订单确认**：ORDER 不可靠时回退 DEAL，DEAL 不可靠时回退 POSITION
10. **涨停不卖止盈**：涨停时保留止盈仓位，防止卖出后无法买回

---

## 已知陷阱 (CLAUDE.md 交叉引用)

详见 `CLAUDE.md` 陷阱 #27-34：
- 订单状态机用 userOrderId (remark) 跟踪
- 撤单前必须 can_cancel_order 检查
- 持仓 JSON 必须在 deal_callback 后更新
- 涨停过滤和资金容量过滤必须在选股阶段执行
- T+1：持有首日跳过风控检查
- 涨停不卖止盈
- HOLD_DAYS_EXTEND：盈利>阈值时可延期持有
- MAX_STOCK_PRICE：排除超过价格上限的股票

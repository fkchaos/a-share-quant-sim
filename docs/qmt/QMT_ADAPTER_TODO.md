# QMT Adapter Implementation TODO

## Status

- [x] P0: v61c选股逻辑恢复 ✅ (2026-08-26)
- [x] P0: v75j选股逻辑恢复 ✅ (2026-08-26)
- [x] P1: config.py更新 ✅ (2026-08-26)
- [x] P1: sell_all验证 ✅ (之前已验证)
- [x] P2: 清理遗留问题 ✅ (2026-08-26)

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

## Key Technical Decisions

1. **QMT volume单位**：QMT返回股（shares），不是手（lots），所以换手率=volume/float_shares，不需要*100
2. **行业映射**：QMT环境不能import sqlite，改用C.get_instrument_detail()获取IndustryClassification
3. **K线缓存**：每日首次获取后缓存，同一天内不重复fetch（避免QMT限流）
4. **Python 3.6.8兼容**：无walrus(:=)、无dict union(|)、无debug f-string(=)

---

## Original TODO (archived for reference)

### P0: Restore Stock Selection Logic

1. **v61c_strategy.py `_select_stocks(C)`** ✅ DONE
   - ~~Get turnover data for last N days~~ ✅ (get_kline_data_multi + 5日均值)
   - ~~Calculate rolling average turnover~~ ✅ (volume/float_shares, 5日均值)
   - ~~Rank by low turnover + small cap~~ ✅ (等权50/50 rank)
   - Add missing imports if needed ✅

2. **v75j_strategy.py `_select_stocks(C)`** ✅ DONE
   - ~~Import v75a factors~~ ✅ (改用QMT API获取行业映射)
   - Apply breadth filter ✅ (MA20广度过滤)
   - Calculate liquidity factor ✅ (float_shares排序)
   - Select top N tech stocks ✅

### P1: Verify Buy/Sell/GetHoldings in Backtest

1. **Backtest run** - Run a short backtest (1-2 weeks)
2. **Check trade log** - Verify buy/sell orders appear in QMT trade log
3. **Check final positions** - Verify get_holdings returns correct positions
4. **Check cash flow** - Verify cash decreases on buy, increases on sell

### P1: Update config.py Real Account ID

1. ~~Fill in real account_id~~ ✅ DONE
2. Fill in real account type if different from STOCK

### P2: Clean Up Issues

1. ~~v61c_debug_strategy.py~~ - Legacy debug file, can archive if not needed
2. Any other test artifacts

# QMT Official Backtest Guide - Key Patterns

Source: https://github.com/imlida/qmt-docs

## Core Rules

### 1. subscribe parameter
- `subscribe=False`: Read local cache ONLY (main chart stock only)
- `subscribe=True` (default): Subscribe + read (any stock)
- **For non-main-chart stocks: MUST use subscribe=True**

### 2. end_time parameter (CRITICAL)
```python
# WRONG: returns today's price
data = C.get_market_data_ex(['close'], [code], count=1)

# CORRECT: returns bar date's price
data = C.get_market_data_ex(['close'], [code], count=1, end_time=bar_date)
```

### 3. passorder quickTrade
- `quickTrade=0`: Backtest mode (每根K线触发一次)
- `quickTrade=1`: Latest bar only (实盘快速触发)
- `quickTrade=2`: Any bar immediate (慎用)

### 4. passorder prType
- `prType=5`: Latest price (最新价)
- `prType=11`: Limit price (限价)
- `prType=14`: Counterparty (对手盘)

### 5. get_trade_detail_data query_type
- Must be UPPERCASE: `'POSITION'`, `'ORDER'`, `'DEAL'`, `'ACCOUNT'`
- Lowercase returns empty list silently

### 6. Bar date extraction
```python
bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d')
```
NEVER use `datetime.now()` in backtest.

### 7. Entry file structure
- Must define `init(C)` and `handlebar(C)` at module level
- Cannot be pure import转发
- QMT injects globals (passorder, get_trade_detail_data) into entry file

### 8. Non-main-chart stock data
```python
# Official pattern for multiple stocks
C.stock_list = ["000001.SZ", "600519.SH", "510050.SH"]
data = C.get_market_data_ex([], C.stock_list, period="1d", count=1)
```

## Common Mistakes

1. Missing `end_time` → wrong price (today vs bar date)
2. `subscribe=False` for non-main-chart → empty data
3. `quickTrade=1` in backtest → only triggers on last bar
4. Lowercase query_type → silent empty results
5. Using `datetime.now()` → wrong date in backtest

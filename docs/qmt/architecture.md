# QMT Adapter Architecture

> Source of truth for the QMT trading adapter. Read this before modifying any `qmt_adapter/*.py` file.

## Module Map

```
qmt_adapter/
├── config.py              # All strategy params (STRATEGIES dict)
├── strategy_base.py       # Shared utilities (bar dates, hold_days, limit-up)
├── trading.py             # QmtAccount + order state machine (7 states, 3-layer confirm)
├── qmt_runner.py          # Risk control, buy execution, per-strategy position JSON
├── data.py                # K-line data, close price, market data wrappers
├── qmt_data.py            # ZZ1800 stock pool, FLOAT_SHARES static dict
├── qmt_data_static.py     # INDUSTRY mapping, TECH_SECTORS list
├── v61c_strategy.py       # V61C: low turnover + small cap
├── v75j_strategy.py       # V75J: tech trend + liquidity + breadth
└── strategy_skeleton.py   # Template for new strategies
```

## Call Flow

```
v61c_qmt.py / v75j_qmt.py   (entry files)
    │
    ├─ init(C)
    │   ├─ set_debug(DEBUG)
    │   ├─ set_risk_debug(DEBUG)
    │   └─ strategy.init(C)
    │       ├─ qmt_runner.qmt_init(C)  →  inject QMT built-in functions
    │       ├─ QmtAccount(C)           →  detect account_id, backtest mode
    │       ├─ load_hold_days()         →  _hold_days_{strategy}.json
    │       └─ _build_industry_map(C)  →  v75j only: tech_codes list
    │
    ├─ handlebar(C)  [BACKTEST mode]  ──┐
    │                                    │
    └─ on_timer(C)   [LIVE mode]    ──┘
                                       │
                              strategy.on_signal(C)
                                       │
                      ┌────────────────┼────────────────┐
                      ▼                ▼                ▼
               hold_days++      check_risk()      execute_buy()
                                     │                │
                              QmtAccount       _select_stocks()
                              .sell_all()           │
                                     │         QmtAccount.buy_value()
                                     ▼              │
                              _order_transition     ▼
                              (state machine)  start_order_poll()
                                     │
                                     ▼
                              _sync_strategy_position()
                                     │
                                     ▼
                              strategy_buy/sell()
                                     │
                                     ▼
                              _positions_{strategy}.json
```

## Order State Machine

### States

```
pending ──── ordered ──── partial ──── filled  [TERMINAL]
   │            │            │
   │            │            └──────── cancel_pending ──┐
   │            │                                       │
   │            └───────────────────────────────────────┤
   │                                                    │
   └──────── rejected [TERMINAL]    cancelled [TERMINAL]┘
```

### Transitions

| From | To | Condition |
|------|----|-----------|
| pending | ordered | ORDER found, traded=0 |
| pending | partial | ORDER found, traded>0 |
| pending | filled | traded >= vol (via any layer) |
| pending | rejected | status=57 or 60s timeout |
| ordered | partial | traded increases |
| ordered | filled | traded >= vol |
| ordered | cancelled | status=54 (confirmed cancel) |
| ordered | cancel_pending | status 51/52 |
| partial | filled | traded >= vol |
| partial | cancelled | status=54 or 53 |
| partial | cancel_pending | status 51/52 |
| cancel_pending | partial | traded increases while pending |
| cancel_pending | filled | traded >= vol while pending |
| cancel_pending | ordered | cancel rejected (reverts to 50) |

### Three-Layer Confirmation

1. **ORDER** — `get_trade_detail_data('ORDER')` matched by `m_strRemark`
   - Fast path: most orders appear here
   - Provides `m_strOrderSysID` for cancellation
2. **DEAL** — `get_trade_detail_data('DEAL')` (fallback if ORDER not found)
   - Accumulate all matching `m_strRemark` volumes
3. **POSITION** — `get_trade_detail_data('POSITION')` (final fallback)
   - BUY: check if stock exists in positions
   - SELL: check if position decreased

### Timeouts

| Timer | Duration | Action |
|-------|----------|--------|
| Pending timeout | 60s | → rejected (no record anywhere) |
| No order_id | 30s | → rejected (force expired) |
| Cancel wait | 120s | → cancel (max 3 retries) |
| Max lifetime | 300s | → rejected (force expired) |
| Poll interval | 10s | schedule_run repeating |

### Status Codes (QMT Official)

```python
ORD_ST_48_NOT_REPORTED     = 48
ORD_ST_49_PENDING          = 49
ORD_ST_50_REPORTED         = 50
ORD_ST_51_CANCEL_REQ       = 51   # cancel requested
ORD_ST_52_PARTIAL_CANCEL   = 52   # partial cancel requested
ORD_ST_53_PARTIAL_CANCELLED = 53  # partial cancelled → order done
ORD_ST_54_CANCELLED        = 54   # fully cancelled
ORD_ST_55_PARTIAL_FILLED   = 55
ORD_ST_56_FILLED           = 56
ORD_ST_57_REJECTED         = 57
ORD_ST_86_CONFIRMED        = 86

ORD_TERMINAL_REJECT  = {57}
ORD_TERMINAL_CANCEL  = {54, 53}   # 53 = partial cancel, order done
ORD_CANCEL_PENDING   = {51, 52}
```

## Per-Strategy Position Isolation

### Why?

Two strategies share one QMT account. QMT has no sub-accounts. We use local JSON files to track which stocks belong to which strategy.

### Files

| File | Writer | Update Frequency | Content |
|------|--------|-----------------|---------|
| `_hold_days_{strategy}.json` | strategy `on_signal()` | Every bar | `{hold_days: {code: N}, last_date: "YYYY-MM-DD"}` |
| `_positions_{strategy}.json` | `trading.py` → `_sync_strategy_position()` | On fill only | `{code: {shares, cost_price, added_at}}` |

### Write Path

```
passorder() → QMT
    │
    ▼
order_state_machine polls
    │
    ▼
order_callback (logging only)
deal_callback (logging only)
    │
    ▼
_do_order_check_single() detects fill
    │
    ▼
_order_transition('filled')
    │
    ▼
_sync_strategy_position()
    │
    ├─ BUY → qmt_runner.strategy_buy()  → _positions_{strategy}.json
    └─ SELL → qmt_runner.strategy_sell() → _positions_{strategy}.json
```

### Hold Days Management

```
on_signal() entry:
  1. hold_days[code] += 1  for all held codes
  2. check_risk() → sell triggered codes, pop from hold_days
  3. time exit → sell expired codes, pop from hold_days
  4. execute_buy() → newly bought codes get hold_days = 0
  5. persist_hold_days() → atomic write to JSON
```

### Atomic Write Pattern

```python
def _atomic_write(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f)
    os.rename(tmp, path)  # atomic on POSIX
```

## strategy_name Convention

| Context | Format | Example |
|---------|--------|---------|
| Config keys | lowercase | `'v61c'`, `'v75j'` |
| JSON filenames | lowercase | `_hold_days_v61c.json` |
| Log prefixes | UPPERCASE | `[V61C]`, `[V75J]` |
| passorder() | UPPERCASE | `strategyName='V61C'` |
| get_trade_detail_data() | UPPERCASE | query filter |
| Internal tracking | lowercase | `strategy_name='v61c'` in `_orders` dict |

**Rule**: Strategy files pass lowercase to `QmtAccount.buy()`, which calls `.upper()` when invoking QMT API.

## QmtAccount API

```python
account = QmtAccount(C)

# Position queries
account.get_holdings()         # → [{'code', 'shares', 'available', 'avg_cost', 'name'}]
account.get_position_detail(code)  # → single position dict or None
account.get_cash()             # → float (available cash)
account.get_total_value()      # → float (stock + fund value)

# Trading
account.buy(code, shares, price, reason, strategy_name)    # → remark or None
account.sell(code, shares, price, reason, strategy_name)   # → remark or None
account.sell_all(code, price, reason, strategy_name)       # → remark or None
account.buy_value(code, target_value, price, reason, strategy_name)  # auto lot-size
```

## Strategy File Contract

Every strategy must implement:

```python
def init(C):
    """Initialize strategy. Called once at start."""
    ...

def on_signal(C):
    """Core logic. Called on every bar (backtest) or timer tick (live)."""
    ...
```

The entry file calls these through the module:

```python
from qmt_adapter.{strategy}_strategy import init as _init, on_signal as _on_signal

def init(C):
    _set_debug(DEBUG)
    _set_risk_debug(DEBUG)
    _init(C)

def handlebar(C):      # BACKTEST
    _on_signal(C)

def on_timer(C):       # LIVE
    _on_signal(C)
```

## Pitfalls

### Python 3.6.8 Constraints
- No walrus operator (`:=`)
- No dict union (`|`)
- No debug f-string (`f'{x=}'`)
- No `list | list` type union
- `pd.DataFrame.map()` unavailable (use `df.apply()`)
- All files must have `#coding:gbk` as first line
- String constants should use English (not Chinese) for QMT GBK compatibility

### QMT-Specific Traps
- `get_trade_detail_data()` datatype parameter MUST be UPPERCASE
- `passorder()` strategyName MUST be UPPERCASE
- `get_trade_detail_data()` returns empty in backtest for POSITION
- T+1: `m_nCanUseVolume=0` for today's buy, use `m_nVolume` for total
- `schedule_run` time_point must be string `'YYYYMMDDHHMMSS'`
- QMT volume is in shares (not lots): turnover = volume / float_shares
- `get_bar_timetag()` preferred over `datetime.now()` for bar dates

### Limit-Up Thresholds
- Main board (60xxxx, 00xxxx): 10% → threshold 1.095
- ChiNext (300xxx, 301xxx): 20% → threshold 1.195
- STAR Market (688xxx, 689xxx): 20% → threshold 1.195
- New Third Board (8xxxxx, 4xxxxx): 30% → threshold 1.295

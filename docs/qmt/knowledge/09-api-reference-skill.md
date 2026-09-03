# ⚠️ QMT API 签名对照（2026-08-25 实测修正）

以下是从知识库和实测中确认的**正确API签名**，与之前文档有出入：

## passorder（11个参数）
```python
passorder(opType, orderType, accountid, orderCode, prType, price, volume,
          strategyName, quickOrder, userOrderId, ContextInfo)
```
| 参数 | 值 | 说明 |
|------|-----|------|
| opType | **23**=买入, **24**=卖出 | 股票账户 |
| orderType | **1101** | 按股数委托（股票通用） |
| accountid | 字符串，如 `'88888888'` | **不是StockAccount对象** |
| orderCode | **带后缀** `'600000.SH'` | 不带后缀会报错 |
| prType | **`14`**=对手价（推荐）, **`5`**=最新价, **`11`**=限价 | price仅prType=11时有效 |
| price | -1（prType≠11时）或具体价格 | prType=11时必须填具体价格 |
| quickOrder | **`2`**=立即触发（实盘推荐）, **`0`**=逐K线（回测标准） | 0=下一根K线触发，2=立即触发 |
| ContextInfo | 传C | 最后一个参数 |

```python
# ✅ 正确用法（prType=14对手价 + quickTrade=2立即触发）
passorder(23, 1101, account_id, '600000.SH', 14, -1, 100, '策略名', 2, '备注', C)
```

## get_trade_detail_data（3个参数）
```python
get_trade_detail_data(account_id, account_type, query_type)
```
| 参数 | 说明 |
|------|------|
| account_id | 字符串 `'88888888'`，**不是StockAccount** |
| account_type | **`'STOCK'`**（大写，普通）或 `'CREDIT'`（两融） |
| query_type | **必须大写**: `'ACCOUNT'` / `'POSITION'` / `'ORDER'` / `'DEAL'` / `'TASK'` |

> ⚠️ 官方文档（ThinkTrader v3.3.6）确认：query_type和account_type都是**大写**。
> 用小写（如`'position'`）会**静默返回空列表**，不报错。

```python
# ✅ 正确用法（官方文档确认）
accounts = get_trade_detail_data('88888888', 'STOCK', 'ACCOUNT')
positions = get_trade_detail_data('88888888', 'STOCK', 'POSITION')
orders = get_trade_detail_data('88888888', 'STOCK', 'ORDER')
deals = get_trade_detail_data('88888888', 'STOCK', 'DEAL')

# ❌ 错误！会静默返回空列表（不报错）
positions = get_trade_detail_data('88888888', 'stock', 'stockpositions')  # WRONG
orders = get_trade_detail_data('88888888', 'stock', 'stockorders')  # WRONG
```

### Position 对象字段（官方文档）
| 字段 | 类型 | 说明 |
|------|------|------|
| `m_strInstrumentID` | string | 品种代码（不含后缀，如 `'600519'`） |
| `m_strExchangeID` | string | 交易所（`'SH'`/`'SZ'`） |
| `m_strInstrumentName` | string | 证券名称 |
| `m_nVolume` | int | 总持仓量 |
| `m_nCanUseVolume` | int | 可用数量（A股T+1: 当天买的=0） |
| `m_dOpenPrice` | float | **开仓均价**（不是m_dSettlementPrice） |
| `m_dPositionProfit` | float | 持仓盈亏 |
| `m_dInstrumentValue` | float | 当前市值 |

### Order 对象字段
| 字段 | 说明 |
|------|------|
| `m_strInstrumentID` | 品种代码 |
| `m_nVolumeTotalOriginal` | 委托数量 |
| `m_nVolumeTraded` | 成交数量 |
| `m_dTradedPrice` | 成交均价 |
| `m_nOrderStatus` | 委托状态 |
| `m_strRemark` | 投资备注（对应userOrderId） |

### Deal 对象字段
| 字段 | 说明 |
|------|------|
| `m_strInstrumentID` | 品种代码 |
| `m_dPrice` | 成交价格 |
| `m_nVolume` | 成交数量 |
| `m_dTradeAmount` | 成交金额 |
| `m_nOffsetFlag` | 方向（48=买入, 49=卖出） |
| `m_strRemark` | 投资备注 |

## account 和 accountType（全局变量）
QMT在模型交易界面配置的`account`和`accountType`是**注入到策略文件全局命名空间的变量**，不是C的属性。在被import的模块中获取需要**向上遍历调用栈**：

```python
import sys

def _find_account_from_frames():
    frame = sys._getframe(1)
    while frame is not None:
        if 'account' in frame.f_globals:
            val = frame.f_globals['account']
            if val and val != 'test':
                return val
        frame = frame.f_back
    return None

def _find_account_type_from_frames():
    frame = sys._getframe(1)
    while frame is not None:
        if 'accountType' in frame.f_globals:
            return frame.f_globals['accountType']
        frame = frame.f_back
    return 'STOCK'  # default
```

## C.set_universe()（必须在init调用）
```python
def init(C):
    C.set_universe(['600000.SH', '000001.SZ'])  # 订阅股票池
```
**不调用set_universe，handlebar不会被这些品种触发。** 这是知识库明确说明的。

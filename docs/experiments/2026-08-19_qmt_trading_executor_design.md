# QMT交易执行器架构设计

**日期**: 2026-08-19
**目标**: 策略代码与交易执行解耦，通过配置切换执行器

## 设计原则

1. **策略层无感知** — 因子计算/打分排序不关心数据从哪来、交易怎么执行
2. **执行器可插拔** — 配置文件指定用哪个执行器（模拟盘/QMT/其他）
3. **Python 3.6兼容** — 所有代码兼容3.6.8，向后兼容到3.11+

## 架构分层

```
┌─────────────────────────────────────────────┐
│  策略层 (platform-agnostic)                   │
│  v61c / v75j / 未来的v84+                     │
│  职责: 因子计算、打分排序、信号生成             │
│  依赖: pandas, numpy (无平台依赖)             │
├─────────────────────────────────────────────┤
│  执行器接口 (TradingExecutor)                  │
│  统一接口: buy/sell/get_positions/get_balance  │
├──────────┬──────────┬───────────────────────┤
│ SimExecutor│ QMTExecutor│ FutureExecutor      │
│ 模拟盘     │ QMT实盘    │ 其他平台             │
│ (当前DB)   │ (passorder)│ (PTrade/自研)       │
└──────────┴──────────┴───────────────────────┘
```

## 接口定义

```python
# core/trading_executor.py

class TradingExecutor:
    """交易执行器基类"""
    
    def connect(self, config):
        """连接/初始化"""
        raise NotImplementedError
    
    def buy(self, code, amount, price=None, order_type='limit'):
        """买入
        code: 股票代码 '600000.SH'
        amount: 数量(股)
        price: 价格(None=市价)
        order_type: 'limit'/'market'
        返回: order_id
        """
        raise NotImplementedError
    
    def sell(self, code, amount, price=None, order_type='limit'):
        """卖出"""
        raise NotImplementedError
    
    def cancel(self, order_id):
        """撤单"""
        raise NotImplementedError
    
    def get_positions(self):
        """获取持仓
        返回: [{'code': '600000.SH', 'amount': 100, 'cost': 10.5, 'pnl': 0.02}, ...]
        """
        raise NotImplementedError
    
    def get_balance(self):
        """获取资金
        返回: {'total': 100000, 'available': 50000, 'frozen': 50000}
        """
        raise NotImplementedError
    
    def get_orders(self, status=None):
        """获取委托
        status: 'pending'/'filled'/'cancelled'/None(全部)
        """
        raise NotImplementedError
```

## 执行器实现

### 1. SimExecutor (当前模拟盘，保持不变)

```python
class SimExecutor(TradingExecutor):
    """模拟盘执行器 — 基于SQLite"""
    
    def __init__(self, db_path, account_id):
        self.db_path = db_path
        self.account_id = account_id
    
    def buy(self, code, amount, price=None, order_type='limit'):
        # 现有 account.add_transaction() 逻辑
        pass
    
    def sell(self, code, amount, price=None, order_type='limit'):
        pass
    
    def get_positions(self):
        # 现有 DB查询逻辑
        pass
    
    def get_balance(self):
        pass
```

### 2. QMTExecutor (大QMT，待实现)

```python
class QMTExecutor(TradingExecutor):
    """大QMT执行器 — 基于passorder"""
    
    def __init__(self, account_id):
        self.account_id = account_id
        # QMT环境下的passorder/get_trade_detail_data由框架注入
        # 这里只做参数转换
    
    def buy(self, code, amount, price=None, order_type='limit'):
        if order_type == 'limit' and price:
            # passorder(23=买入, 1101=按股, account, code, 11=限价, price, amount, name, 0, '', C)
            passorder(23, 1101, self.account_id, code, 11, price, amount, 'v61c', 0, '', C)
        else:
            # 市价: prType=5(最新价)
            passorder(23, 1101, self.account_id, code, 5, -1, amount, 'v61c', 0, '', C)
    
    def sell(self, code, amount, price=None, order_type='limit'):
        if order_type == 'limit' and price:
            passorder(24, 1101, self.account_id, code, 11, price, amount, 'v61c', 0, '', C)
        else:
            passorder(24, 1101, self.account_id, code, 5, -1, amount, 'v61c', 0, '', C)
    
    def get_positions(self):
        # get_trade_detail_data('stockpositions')
        pass
    
    def get_balance(self):
        # get_trade_detail_data('stockassets')
        pass
```

### 3. 配置切换

```yaml
# config.yaml
trading:
  executor: sim  # sim / qmt / future
  sim:
    db_path: data/quant_accounts.db
    account_id: v61c
  qmt:
    account_id: '你的资金账号'
    strategy_name: v61c
```

```python
# core/executor_factory.py

def create_executor(config):
    """根据配置创建执行器"""
    executor_type = config['trading']['executor']
    
    if executor_type == 'sim':
        from core.executors.sim_executor import SimExecutor
        return SimExecutor(
            config['trading']['sim']['db_path'],
            config['trading']['sim']['account_id']
        )
    elif executor_type == 'qmt':
        from core.executors.qmt_executor import QMTExecutor
        return QMTExecutor(
            config['trading']['qmt']['account_id']
        )
    else:
        raise ValueError(f"Unknown executor: {executor_type}")
```

## 策略代码改造示例

### 改造前 (硬编码模拟盘)

```python
# v61c_turnover_size.py 中的交易逻辑
account.add_transaction(code, 'buy', shares, price, date, reason)
```

### 改造后 (通过执行器)

```python
# v61c_turnover_size.py
def trade_signals(signals, executor, date):
    """执行交易信号 — 与平台无关"""
    for signal in signals:
        if signal['action'] == 'buy':
            executor.buy(signal['code'], signal['shares'], signal['price'])
        elif signal['action'] == 'sell':
            executor.sell(signal['code'], signal['shares'], signal['price'])
```

## 迁移路径

```
Phase 1: 定义接口 + SimExecutor (保持现有功能不变)
Phase 2: 策略代码改用executor调用 (模拟盘验证)
Phase 3: QMTExecutor实现 (QMT环境验证)
Phase 4: 配置切换 (一键切换模拟盘/实盘)
```

## Python 3.6兼容性清单

| 特性 | 3.6 | 3.11 | 兼容写法 |
|------|-----|------|---------|
| f-string | ✅ | ✅ | `f"{x:.2f}"` |
| f-string `=`调试 | ❌ | ✅ | 删掉`=号` |
| `:=` walrus | ❌ | ✅ | 拆成两行 |
| `dict \| dict` | ❌ | ✅ | `{**d1, **d2}` |
| `dict | dict` merge | ❌ | ✅ | `d1.update(d2)` |
| `pd.DataFrame.map()` | ❌ | ✅ | `df.applymap()` |
| `match/case` | ❌ | ✅ | `if/elif` |
| `asyncio.run()` | ❌ | ✅ | `loop.run_until_complete()` |
| type hints `list[int]` | ❌ | ✅ | `List[int]` (typing) |

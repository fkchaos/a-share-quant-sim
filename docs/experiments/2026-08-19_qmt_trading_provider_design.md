# 交易Provider解耦设计

**日期**: 2026-08-19
**目标**: 策略代码与交易执行完全解耦，QMT调用封装为独立Provider

## 架构

```
策略代码 (v61c/v75j)
    │
    │ 只调用: provider.buy() / provider.sell() / provider.get_positions()
    │
    ▼
TradingProvider 接口 (core/trading_provider.py)
    │
    ├── SimProvider   (core/providers/sim_provider.py)     ← 模拟盘
    ├── QMTProvider   (core/providers/qmt_provider.py)     ← QMT实盘
    └── FutureProvider (core/providers/future_provider.py)  ← 未来扩展
```

**关键原则：QMT的passorder/ContextInfo/get_trade_detail_data等只出现在qmt_provider.py里，其他任何文件看不到。**

## 接口定义

```python
# core/trading_provider.py

class TradingProvider:
    """交易Provider基类 — 所有交易操作的统一接口"""
    
    def initialize(self, config):
        """初始化，config包含账号等信息"""
        raise NotImplementedError
    
    def buy(self, code, shares, price=None):
        """买入
        code: '600000.SH'
        shares: 股数
        price: 限价(None=市价)
        返回: order_id (str)
        """
        raise NotImplementedError
    
    def sell(self, code, shares, price=None):
        """卖出，返回order_id"""
        raise NotImplementedError
    
    def cancel_order(self, order_id):
        """撤单"""
        raise NotImplementedError
    
    def get_positions(self):
        """返回: [{'code': str, 'shares': int, 'cost_price': float, 'market_value': float}]"""
        raise NotImplementedError
    
    def get_balance(self):
        """返回: {'total_assets': float, 'available_cash': float, 'frozen_cash': float}"""
        raise NotImplementedError
    
    def get_pending_orders(self):
        """返回: [{'order_id': str, 'code': str, 'direction': 'buy'/'sell', 'shares': int, 'price': float}]"""
        raise NotImplementedError
    
    def on_bar(self, context):
        """K线回调 — QMT模式下每根K线触发，模拟盘模式下手动调用
        context: 包含当前行情数据
        """
        pass
```

## QMT Provider实现

```python
# core/providers/qmt_provider.py
# ⚠️ 这个文件是唯一允许出现QMT API的地方

class QMTProvider(TradingProvider):
    """QMT实盘Provider — 封装所有QMT特有调用"""
    
    def initialize(self, config):
        self.account_id = config['account_id']
        self.strategy_name = config.get('strategy_name', 'v61c')
        # QMT环境: passorder/get_trade_detail_data由框架全局注入
        # 这里不import，运行时由QMT框架提供
    
    def buy(self, code, shares, price=None):
        if price:
            # 限价买入: opType=23, orderType=1101(按股), prType=11(限价)
            passorder(23, 1101, self.account_id, code, 11, 
                     price, shares, self.strategy_name, 0, '', C)
        else:
            # 市价买入: prType=5(最新价)
            passorder(23, 1101, self.account_id, code, 5,
                     -1, shares, self.strategy_name, 0, '', C)
        return None  # QMT异步，通过回调获取order_id
    
    def sell(self, code, shares, price=None):
        if price:
            passorder(24, 1101, self.account_id, code, 11,
                     price, shares, self.strategy_name, 0, '', C)
        else:
            passorder(24, 1101, self.account_id, code, 5,
                     -1, shares, self.strategy_name, 0, '', C)
        return None
    
    def cancel_order(self, order_id):
        # QMT撤单: 通过passorder特定参数或内置撤单接口
        pass
    
    def get_positions(self):
        # QMT: get_trade_detail_data('stockpositions')
        raw = get_trade_detail_data(self.account_id, 'stock', 'stockpositions')
        positions = []
        for p in raw:
            positions.append({
                'code': p.m_strInstrumentID,
                'shares': p.m_nCanUseVolume,
                'cost_price': p.m_dSettlementPrice,
                'market_value': p.m_dMarketValue,
            })
        return positions
    
    def get_balance(self):
        # QMT: get_trade_detail_data('stockassets')
        raw = get_trade_detail_data(self.account_id, 'stock', 'stockassets')
        if raw:
            a = raw[0]
            return {
                'total_assets': a.m_dBalance + a.m_dFrozenCash,
                'available_cash': a.m_dAvailableCash,
                'frozen_cash': a.m_dFrozenCash,
            }
        return {'total_assets': 0, 'available_cash': 0, 'frozen_cash': 0}
    
    def get_pending_orders(self):
        # QMT: get_trade_detail_data('stockorders')
        raw = get_trade_detail_data(self.account_id, 'stock', 'stockorders')
        orders = []
        for o in raw:
            if o.m_nOrderStatus == 0:  # 未成交
                orders.append({
                    'order_id': str(o.m_nOrderSysID),
                    'code': o.m_strInstrumentID,
                    'direction': 'buy' if o.m_nDirection == 23 else 'sell',
                    'shares': o.m_nVolumeTotalOriginal - o.m_nVolumeTraded,
                    'price': o.m_dLimitPrice,
                })
        return orders
```

## 模拟盘Provider实现

```python
# core/providers/sim_provider.py

class SimProvider(TradingProvider):
    """模拟盘Provider — 基于SQLite，就是现有的逻辑"""
    
    def initialize(self, config):
        self.db_path = config['db_path']
        self.account_id = config['account_id']
    
    def buy(self, code, shares, price=None):
        # 现有 account.add_transaction() 逻辑搬过来
        conn = sqlite3.connect(self.db_path)
        # ... 现有买入逻辑 ...
        conn.close()
        return order_id
    
    def sell(self, code, shares, price=None):
        # 现有卖出逻辑
        pass
    
    def get_positions(self):
        # 现有持仓查询逻辑
        pass
    
    def get_balance(self):
        # 现有余额查询逻辑
        pass
```

## 策略代码调用方式

```python
# v61c_turnover_size.py

def execute_trading(signals, provider, date):
    """执行交易 — 只认provider接口，不碰任何平台细节"""
    for sig in signals:
        if sig['action'] == 'buy':
            provider.buy(sig['code'], sig['shares'], sig.get('price'))
        elif sig['action'] == 'sell':
            provider.sell(sig['code'], sig['shares'], sig.get('price'))
    
    # 获取当前持仓
    positions = provider.get_positions()
    balance = provider.get_balance()
    
    return positions, balance
```

## 配置切换

```yaml
# config.yaml
trading:
  provider: sim        # sim / qmt
  sim:
    db_path: data/quant_accounts.db
    account_id: v61c
  qmt:
    account_id: '你的资金账号'
    strategy_name: v61c
```

```python
# 启动时根据配置创建provider
provider = create_provider(config)  # 返回SimProvider或QMTProvider
```

## 目录结构

```
core/
├── trading_provider.py          # 基类定义
├── provider_factory.py          # 工厂函数: create_provider()
├── providers/
│   ├── __init__.py
│   ├── sim_provider.py          # 模拟盘 (现有逻辑迁移过来)
│   └── qmt_provider.py          # QMT实盘 (唯一出现passorder的地方)
├── strategy_map.py
├── account.py
└── ...
```

## Python 3.6兼容性

所有代码兼容3.6.8：
- 不用 `:=` walrus operator
- 不用 `dict | dict` 合并
- 不用 f-string `=` 调试
- 不用 `match/case`
- pandas用 `applymap()` 不用 `map()`

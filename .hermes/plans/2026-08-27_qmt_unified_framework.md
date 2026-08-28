# QMT 统一策略框架设计

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 创建"策略写一次，回测+实盘都跑"的统一框架，消除代码重复。

**Architecture:** BaseStrategy 类定义统一接口（on_init/on_bar），BacktestEngine 遍历历史数据调用 on_bar，QMT Adapter 将 on_bar 桥接到 QMT 的 handlebar/run_time。

**Tech Stack:** Python 3.6.8 compatible（QMT 限制），numpy，pandas

---

## 核心矛盾分析

| | handlebar（回测） | run_time（实盘定时器） |
|--|-----------------|---------------------|
| 触发方式 | K线走完 | 固定间隔 |
| 回测可用 | ✅ | ❌ |
| 实盘可用 | ✅ | ✅ |

**解决方案：** 策略逻辑只写一份 `on_bar()`，回测引擎和 QMT 适配器分别用自己的触发方式调用它。

---

## 文件结构

```
qmt_deploy/qmt_framework/
  __init__.py           # 导出
  base.py               # BaseStrategy 基类
  context.py            # BacktestContext + LiveContext
  backtest_engine.py    # 回测引擎（遍历历史数据）
  qmt_adapter.py        # QMT适配器（桥接 init/handlebar + run_time）
```

---

## Task 1: BaseStrategy 基类

**Objective:** 定义策略统一接口

**Files:** Create `qmt_deploy/qmt_framework/base.py`

**Step 1: Write the file**

```python
#coding:gbk
"""BaseStrategy - 统一策略接口

所有策略继承此类，实现 on_init() 和 on_bar()。
回测引擎和QMT适配器分别用自己的触发方式调用这些方法。
"""
from abc import ABCMeta, abstractmethod


class BaseStrategy(object):
    """策略基类，定义统一接口"""
    __metaclass__ = ABCMeta

    def __init__(self, params=None):
        self.params = params or {}
        self.state = {}  # 策略可自由存储状态

    @abstractmethod
    def on_init(self, ctx):
        """初始化策略
        
        Args:
            ctx: Context对象，提供 get_date(), get_data(), portfolio 等方法
        """
        pass

    @abstractmethod
    def on_bar(self, ctx):
        """每根K线触发
        
        Args:
            ctx: Context对象
        """
        pass

    def on_stop(self, ctx):
        """策略结束时调用（可选）"""
        pass
```

**Step 2: Verify**

```bash
cd /root/a-share-quant-sim
python3 -c "from qmt_deploy.qmt_framework.base import BaseStrategy; print('OK')"
```

**Step 3: Commit**

```bash
git add qmt_deploy/qmt_framework/
git commit -m "feat: add BaseStrategy base class"
```

---

## Task 2: BacktestContext

**Objective:** 回测环境的 Context 对象，封装数据访问和持仓管理

**Files:** Create `qmt_deploy/qmt_framework/context.py`

**Step 1: Write the file**

```python
#coding:gbk
"""Context - 回测和实盘的统一上下文"""
import numpy as np
import pandas as pd


class BacktestContext(object):
    """回测上下文
    
    模拟 QMT ContextInfo 的核心方法，让策略代码可以
    在回测和实盘中使用相同的 API。
    """
    def __init__(self, date, close_panel, volume_panel, amount_panel,
                 high_panel, low_panel, open_panel, portfolio, params):
        self._date = date
        self._close = close_panel
        self._volume = volume_panel
        self._amount = amount_panel
        self._high = high_panel
        self._low = low_panel
        self._open = open_panel
        self.portfolio = portfolio
        self.params = params
        
        # Position index for current date
        if date in self._close.index:
            self._pos = self._close.index.get_loc(date)
            if isinstance(self._pos, slice):
                self._pos = self._pos.start
        else:
            self._pos = -1

    def get_date(self):
        """当前日期 YYYYMMDD"""
        return str(self._date).replace('-', '')[:8]

    def get_date_dash(self):
        """当前日期 YYYY-MM-DD"""
        return str(self._date)[:10]

    def get_codes(self):
        """当前可用股票列表"""
        return self._close.columns.tolist()

    def get_close(self, code, count=1):
        """获取收盘价"""
        if code not in self._close.columns:
            return np.nan
        arr = self._close[code].values
        if self._pos < 0 or self._pos >= len(arr):
            return np.nan
        if count == 1:
            return arr[self._pos]
        start = max(0, self._pos - count + 1)
        return arr[start:self._pos + 1]

    def get_close_series(self, code, count=20):
        """获取收盘价序列"""
        if code not in self._close.columns:
            return pd.Series(dtype=float)
        arr = self._close[code].values
        if self._pos < 0:
            return pd.Series(dtype=float)
        start = max(0, self._pos - count + 1)
        dates = self._close.index[start:self._pos + 1]
        return pd.Series(arr[start:self._pos + 1], index=dates)

    def get_volume(self, code, count=1):
        """获取成交量"""
        if code not in self._volume.columns:
            return np.nan
        arr = self._volume[code].values
        if self._pos < 0 or self._pos >= len(arr):
            return np.nan
        if count == 1:
            return arr[self._pos]
        start = max(0, self._pos - count + 1)
        return arr[start:self._pos + 1]

    def get_amount(self, code, count=1):
        """获取成交额"""
        if code not in self._amount.columns:
            return np.nan
        arr = self._amount[code].values
        if self._pos < 0 or self._pos >= len(arr):
            return np.nan
        if count == 1:
            return arr[self._pos]
        start = max(0, self._pos - count + 1)
        return arr[start:self._pos + 1]

    def get_panel(self, field, codes=None, count=20):
        """获取面板数据
        
        Args:
            field: 'close', 'volume', 'amount', 'high', 'low', 'open'
            codes: 股票列表（None=全部）
            count: 回溯天数
        Returns:
            DataFrame, index=日期, columns=股票代码
        """
        panels = {
            'close': self._close,
            'volume': self._volume,
            'amount': self._amount,
            'high': self._high,
            'low': self._low,
            'open': self._open,
        }
        panel = panels.get(field)
        if panel is None:
            return pd.DataFrame()
        start = max(0, self._pos - count + 1) if self._pos >= 0 else 0
        end = self._pos + 1 if self._pos >= 0 else 0
        sub = panel.iloc[start:end]
        if codes:
            sub = sub[[c for c in codes if c in sub.columns]]
        return sub

    def get_instrument_detail(self, code):
        """获取股票详情（回测中返回空，策略自行从DB加载）"""
        return {}
```

**Step 2: Verify**

```bash
cd /root/a-share-quant-sim
python3 -c "from qmt_deploy.qmt_framework.context import BacktestContext; print('OK')"
```

**Step 3: Commit**

```bash
git add qmt_deploy/qmt_framework/context.py
git commit -m "feat: add BacktestContext"
```

---

## Task 3: BacktestEngine

**Objective:** 回测引擎，遍历历史数据，调用策略的 on_bar()

**Files:** Create `qmt_deploy/qmt_framework/backtest_engine.py`

**Step 1: Write the file**

```python
#coding:gbk
"""BacktestEngine - 回测引擎

遍历历史K线数据，逐日调用策略的 on_bar()。
模拟简单的买入/卖出执行（按收盘价成交）。
"""
import numpy as np
import pandas as pd
from datetime import datetime


class BacktestPortfolio(object):
    """回测持仓"""
    def __init__(self, initial_cash=100000.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.holdings = {}  # {code: {'shares': n, 'cost': p}}
        self.trade_log = []

    def buy(self, code, shares, price, date):
        """买入"""
        cost = shares * price
        if cost > self.cash:
            # 调整到可买数量
            shares = int(self.cash / price / 100) * 100
            if shares < 100:
                return False
            cost = shares * price
        
        if code in self.holdings:
            old = self.holdings[code]
            total_shares = old['shares'] + shares
            avg_cost = (old['shares'] * old['cost'] + shares * price) / total_shares
            self.holdings[code] = {'shares': total_shares, 'cost': avg_cost}
        else:
            self.holdings[code] = {'shares': shares, 'cost': price}
        
        self.cash -= cost
        self.trade_log.append({
            'date': date, 'code': code, 'action': 'BUY',
            'shares': shares, 'price': price
        })
        return True

    def sell(self, code, price, date):
        """全部卖出"""
        if code not in self.holdings:
            return False
        info = self.holdings[code]
        revenue = info['shares'] * price
        self.cash += revenue
        self.trade_log.append({
            'date': date, 'code': code, 'action': 'SELL',
            'shares': info['shares'], 'price': price
        })
        del self.holdings[code]
        return True

    def get_holdings(self):
        """返回持仓列表"""
        result = []
        for code, info in self.holdings.items():
            result.append({
                'code': code,
                'shares': info['shares'],
                'avg_cost': info['cost'],
            })
        return result

    def get_value(self, close_panel, pos):
        """计算总市值"""
        total = self.cash
        for code, info in self.holdings.items():
            if code in close_panel.columns and pos >= 0:
                price = close_panel[code].values[pos]
                if not np.isnan(price):
                    total += info['shares'] * price
        return total


class BacktestEngine(object):
    """回测引擎
    
    用法:
        engine = BacktestEngine()
        result = engine.run(strategy, close_panel, volume_panel, ...)
    """
    def __init__(self, initial_cash=100000.0, commission=0.0003):
        self.initial_cash = initial_cash
        self.commission = commission

    def run(self, strategy, close_panel, volume_panel, amount_panel,
            high_panel, low_panel, open_panel, params=None,
            start_date=None, end_date=None):
        """运行回测
        
        Args:
            strategy: BaseStrategy 实例
            *_panel: DataFrame (index=日期, columns=股票代码)
            params: 策略参数
            start_date/end_date: 回测区间 'YYYY-MM-DD'
        Returns:
            dict: {total_return, sharpe, max_dd, trades, equity_curve}
        """
        from qmt_deploy.qmt_framework.context import BacktestContext

        # Filter date range
        dates = close_panel.index
        if start_date:
            dates = dates[dates >= start_date]
        if end_date:
            dates = dates[dates <= end_date]
        
        if len(dates) == 0:
            return {'error': 'No data in date range'}

        # Init portfolio and strategy
        portfolio = BacktestPortfolio(self.initial_cash)
        
        # First context for init
        ctx = BacktestContext(
            dates[0], close_panel, volume_panel, amount_panel,
            high_panel, low_panel, open_panel, portfolio, params or {}
        )
        strategy.state = {}
        strategy.on_init(ctx)

        # Run through bars
        equity_curve = []
        for date in dates:
            ctx = BacktestContext(
                date, close_panel, volume_panel, amount_panel,
                high_panel, low_panel, open_panel, portfolio, params or {}
            )
            
            strategy.on_bar(ctx)
            
            # Record equity
            value = portfolio.get_value(close_panel, ctx._pos)
            equity_curve.append({'date': date, 'value': value})

        # Calculate metrics
        eq = pd.DataFrame(equity_curve).set_index('date')['value']
        returns = eq.pct_change().dropna()
        
        total_return = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
        annual_return = total_return * 252 / len(dates)
        sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
        
        # Max drawdown
        cummax = eq.cummax()
        dd = (eq - cummax) / cummax
        max_dd = dd.min() * 100

        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'sharpe': sharpe,
            'max_dd': max_dd,
            'n_bars': len(dates),
            'n_trades': len(portfolio.trade_log),
            'final_value': eq.iloc[-1],
            'equity_curve': eq,
            'trade_log': portfolio.trade_log,
        }
```

**Step 2: Verify**

```bash
cd /root/a-share-quant-sim
python3 -c "from qmt_deploy.qmt_framework.backtest_engine import BacktestEngine; print('OK')"
```

**Step 3: Commit**

```bash
git add qmt_deploy/qmt_framework/backtest_engine.py
git commit -m "feat: add BacktestEngine"
```

---

## Task 4: QMT Adapter

**Objective:** 将 BaseStrategy 桥接到 QMT 的 init/handlebar + run_time

**Files:** Create `qmt_deploy/qmt_framework/qmt_adapter.py`

**Step 1: Write the file**

```python
#coding:gbk
"""QMT Adapter - 将 BaseStrategy 桥接到 QMT

提供两种模式:
1. handlebar模式: 标准K线回调（回测+实盘都可用）
2. run_time模式: 定时器回调（仅实盘，但支持更灵活的触发）

用法:
    # 在 QMT 入口文件中:
    from qmt_framework.qmt_adapter import QmtAdapter
    from my_strategy import MyStrategy
    
    adapter = QmtAdapter(MyStrategy, params={...})
    init = adapter.init
    handlebar = adapter.handlebar  # handlebar模式
    # 或
    init = adapter.init_with_timer  # run_time模式
"""
import json
import os
from datetime import datetime


class QmtAdapter(object):
    """QMT适配器
    
    将 BaseStrategy 的 on_init/on_bar 桥接到 QMT 的 init/handlebar。
    """
    def __init__(self, strategy_class, params=None, debug=False):
        """
        Args:
            strategy_class: BaseStrategy 子类
            params: 策略参数
            debug: 是否打印调试信息
        """
        self._strategy_class = strategy_class
        self._params = params or {}
        self._debug = debug
        self._strategy = None
        self._hold_days = {}
        self._persist_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '_hold_days.json'
        )

    def _log(self, msg):
        if self._debug:
            print('[ADAPTER] %s' % msg)

    def _get_bar_date(self, C):
        """从 ContextInfo 获取当前bar日期"""
        try:
            timetag = C.get_bar_timetag(C.barpos)
            if timetag > 0:
                return datetime.fromtimestamp(timetag / 1000).strftime('%Y%m%d')
        except Exception:
            pass
        return 'unknown'

    def _load_hold_days(self):
        """从文件加载持仓天数"""
        try:
            with open(self._persist_path, 'r') as f:
                data = json.load(f)
                self._hold_days = data.get('hold_days', {})
        except Exception:
            self._hold_days = {}

    def _save_hold_days(self, today):
        """保存持仓天数到文件"""
        try:
            with open(self._persist_path, 'w') as f:
                json.dump({'hold_days': self._hold_days, 'last_date': today}, f)
        except Exception as e:
            self._log('WARN: failed to persist hold_days: %s' % str(e))

    def _make_qmt_context(self, C):
        """创建 QMT Context 对象，提供与 BacktestContext 相同的接口"""
        return QmtContext(C, self._hold_days)

    def init(self, C):
        """QMT init() - handlebar模式"""
        self._load_hold_days()
        self._strategy = self._strategy_class(params=self._params)
        ctx = self._make_qmt_context(C)
        self._strategy.on_init(ctx)
        self._log('init done')

    def handlebar(self, C):
        """QMT handlebar() - 每根K线触发"""
        if self._strategy is None:
            self.init(C)
        
        today = self._get_bar_date(C)
        
        # Increment hold_days
        for code in list(self._hold_days.keys()):
            self._hold_days[code] = self._hold_days.get(code, 0) + 1
        
        ctx = self._make_qmt_context(C)
        self._strategy.on_bar(ctx)
        
        # Save state
        self._save_hold_days(today)

    def init_with_timer(self, C):
        """QMT init() - run_time模式（仅实盘）"""
        self._load_hold_days()
        self._strategy = self._strategy_class(params=self._params)
        ctx = self._make_qmt_context(C)
        self._strategy.on_init(ctx)
        
        # Register timer
        C.run_time('on_timer', '5nSecond', 
                    str(datetime.now().date()) + ' 09:30:00')
        self._log('init done, timer registered')

    def on_timer(self, C):
        """QMT run_time回调 - 定时触发（仅实盘）"""
        if self._strategy is None:
            return
        
        today = self._get_bar_date(C)
        
        # Increment hold_days (only on new day)
        # ... (same logic as handlebar)
        
        ctx = self._make_qmt_context(C)
        self._strategy.on_bar(ctx)
        
        self._save_hold_days(today)


class QmtContext(object):
    """QMT环境的Context对象
    
    桥接 QMT ContextInfo 到 BaseStrategy 期望的接口。
    """
    def __init__(self, C, hold_days):
        self._C = C
        self._hold_days = hold_days
        self.portfolio = QmtPortfolio(C)
        self.params = {}

    def get_date(self):
        """当前日期 YYYYMMDD"""
        try:
            timetag = self._C.get_bar_timetag(self._C.barpos)
            if timetag > 0:
                return datetime.fromtimestamp(timetag / 1000).strftime('%Y%m%d')
        except Exception:
            pass
        return 'unknown'

    def get_codes(self):
        """获取股票池"""
        try:
            return self._C.get_stock_list_in_sector('沪深A股')
        except Exception:
            return []

    def get_close(self, code, count=1):
        """获取收盘价"""
        try:
            data = self._C.get_market_data_ex(
                ['close'], [code], period='1d', count=count, subscribe=True
            )
            if code in data and len(data[code]) > 0:
                values = data[code]['close'].values
                return values[-1] if count == 1 else values
        except Exception:
            pass
        return float('nan')

    def get_volume(self, code, count=1):
        """获取成交量"""
        try:
            data = self._C.get_market_data_ex(
                ['volume'], [code], period='1d', count=count, subscribe=True
            )
            if code in data and len(data[code]) > 0:
                values = data[code]['volume'].values
                return values[-1] if count == 1 else values
        except Exception:
            pass
        return float('nan')

    def get_amount(self, code, count=1):
        """获取成交额"""
        try:
            data = self._C.get_market_data_ex(
                ['amount'], [code], period='1d', count=count, subscribe=True
            )
            if code in data and len(data[code]) > 0:
                values = data[code]['amount'].values
                return values[-1] if count == 1 else values
        except Exception:
            pass
        return float('nan')

    def get_panel(self, field, codes=None, count=20):
        """获取面板数据"""
        if codes is None:
            codes = self.get_codes()[:200]  # QMT限制
        try:
            data = self._C.get_market_data_ex(
                [field], codes, period='1d', count=count, subscribe=True
            )
            result = {}
            for code in codes:
                if code in data and len(data[code]) > 0:
                    result[code] = data[code][field].values
            return result
        except Exception:
            return {}

    def get_instrument_detail(self, code):
        """获取股票详情"""
        try:
            return self._C.get_instrument_detail(code)
        except Exception:
            return {}

    def get_hold_days(self, code):
        """获取持仓天数"""
        return self._hold_days.get(code, 0)

    def set_hold_days(self, code, days):
        """设置持仓天数"""
        self._hold_days[code] = days

    def remove_hold_days(self, code):
        """移除持仓天数"""
        self._hold_days.pop(code, None)


class QmtPortfolio(object):
    """QMT环境的持仓管理
    
    桥接 QMT 交易接口到 BaseStrategy 期望的 buy/sell 接口。
    """
    def __init__(self, C):
        self._C = C

    def buy(self, code, shares, price=None, reason=''):
        """买入"""
        try:
            from qmt_adapter.trading import QmtAccount
            account = QmtAccount(self._C)
            return account.buy(code, shares, reason=reason)
        except Exception as e:
            print('[ADAPTER] buy failed: %s %s' % (code, str(e)))
            return False

    def sell(self, code, reason=''):
        """全部卖出"""
        try:
            from qmt_adapter.trading import QmtAccount
            account = QmtAccount(self._C)
            return account.sell_all(code, reason=reason)
        except Exception as e:
            print('[ADAPTER] sell failed: %s %s' % (code, str(e)))
            return False

    def get_holdings(self):
        """获取持仓列表"""
        try:
            from qmt_adapter.trading import QmtAccount
            account = QmtAccount(self._C)
            return account.get_holdings()
        except Exception:
            return []

    def get_cash(self):
        """获取可用资金"""
        try:
            from qmt_adapter.trading import QmtAccount
            account = QmtAccount(self._C)
            return account.get_cash()
        except Exception:
            return 0.0
```

**Step 2: Verify**

```bash
cd /root/a-share-quant-sim
python3 -c "from qmt_deploy.qmt_framework.qmt_adapter import QmtAdapter; print('OK')"
```

**Step 3: Commit**

```bash
git add qmt_deploy/qmt_framework/qmt_adapter.py
git commit -m "feat: add QMT adapter (handlebar + run_time)"
```

---

## Task 5: 迁移 v61c 策略

**Objective:** 将 v61c 从重复的两套代码迁移到统一框架

**Files:** Create `qmt_deploy/qmt_framework/strategies/v61c.py`

**Step 1: Write the unified strategy**

```python
#coding:gbk
"""v61c - 统一策略（回测+实盘共用）

低换手+小市值+到期续持优化。
从 scripts/strategies/v61c_turnover_size.py 和
qmt_deploy/qmt_adapter/v61c_strategy.py 合并而来。
"""
import numpy as np
import pandas as pd
from qmt_deploy.qmt_framework.base import BaseStrategy


DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,
    'MAX_DAILY_BUY': 5,
    'MAX_POSITION': 0.25,
}


class V61cStrategy(BaseStrategy):
    """v61c: 低换手+小市值+到期续持"""

    def on_init(self, ctx):
        """初始化"""
        self.state['last_buy_date'] = None
        self.state['today_buys'] = 0

    def on_bar(self, ctx):
        """每根K线触发"""
        today = ctx.get_date()
        p = ctx.params or DEFAULT_PARAMS
        
        # 1. Risk control - sell
        self._check_risk(ctx, today, p)
        
        # 2. Time exit with renewal
        self._check_time_exit(ctx, today, p)
        
        # 3. Buy if slots available
        self._check_buy(ctx, today, p)

    def _check_risk(self, ctx, today, params):
        """止损/止盈"""
        portfolio = ctx.portfolio
        for h in portfolio.get_holdings():
            code = h['code']
            current = ctx.get_close(code)
            cost = h.get('avg_cost', 0)
            if cost <= 0 or np.isnan(current):
                continue
            
            pnl = (current - cost) / cost
            if pnl <= params['STOP_LOSS']:
                portfolio.sell(code, current, today)
                ctx.remove_hold_days(code)
            elif pnl >= params['TAKE_PROFIT']:
                portfolio.sell(code, current, today)
                ctx.remove_hold_days(code)

    def _check_time_exit(self, ctx, today, params):
        """到期续持检查"""
        rebalance_days = params.get('REBALANCE_DAYS', 5)
        sell_out_of = params.get('SELL_OUT_OF', 15)
        
        # Get current rankings
        ranked = self._get_rankings(ctx, sell_out_of)
        
        portfolio = ctx.portfolio
        for h in portfolio.get_holdings():
            code = h['code']
            days = ctx.get_hold_days(code)
            if days >= rebalance_days:
                if code in ranked:
                    # Still in top N -> hold, reset days
                    ctx.set_hold_days(code, 0)
                else:
                    # Dropped out -> sell
                    current = ctx.get_close(code)
                    if not np.isnan(current):
                        portfolio.sell(code, current, today)
                    ctx.remove_hold_days(code)

    def _get_rankings(self, ctx, top_n):
        """计算排名（低换手+小市值）"""
        codes = ctx.get_codes()
        turnover = {}
        mcap = {}
        
        for code in codes:
            vol = ctx.get_volume(code, count=5)
            close = ctx.get_close(code)
            if isinstance(vol, np.ndarray) and len(vol) > 0:
                avg_vol = np.mean(vol)
                # TODO: need float_shares for turnover calculation
                turnover[code] = avg_vol
            if not np.isnan(close) and close > 0:
                mcap[code] = close  # simplified
        
        if len(turnover) < 50:
            return set()
        
        # Rank scoring
        scores = pd.Series(0.0, index=list(turnover.keys()))
        ts = pd.Series(turnover)
        scores = scores.add(1 - ts.rank(ascending=True, pct=True), fill_value=0)
        
        ms = pd.Series(mcap)
        if len(ms) > 50:
            scores = scores.add(1 - ms.rank(ascending=True, pct=True), fill_value=0)
        
        return set(scores.sort_values(ascending=False).head(top_n).index)

    def _check_buy(self, ctx, today, params):
        """检查买入"""
        # Skip if already bought today
        if self.state.get('last_buy_date') == today:
            return
        
        portfolio = ctx.portfolio
        max_holdings = params.get('MAX_HOLDINGS', 5)
        current_count = len(portfolio.get_holdings())
        slots = max_holdings - current_count
        
        if slots <= 0:
            return
        
        # Select stocks
        ranked = self._get_rankings(ctx, params.get('SELL_OUT_OF', 15))
        held = set(h['code'] for h in portfolio.get_holdings())
        buy_list = [c for c in ranked if c not in held][:slots]
        
        if not buy_list:
            return
        
        # Buy
        cash = portfolio.get_cash()
        max_pos = params.get('MAX_POSITION', 0.25)
        for code in buy_list:
            price = ctx.get_close(code)
            if np.isnan(price) or price <= 0:
                continue
            per_stock = min(cash / len(buy_list), max_pos * 100000)
            shares = int(per_stock / price / 100) * 100
            if shares >= 100:
                if portfolio.buy(code, shares, price, today):
                    ctx.set_hold_days(code, 0)
                    cash -= shares * price
        
        self.state['last_buy_date'] = today
```

**Step 2: Verify**

```bash
cd /root/a-share-quant-sim
python3 -c "from qmt_deploy.qmt_framework.strategies.v61c import V61cStrategy; print('OK')"
```

**Step 3: Commit**

```bash
git add qmt_deploy/qmt_framework/strategies/
git commit -m "feat: migrate v61c to unified framework"
```

---

## Task 6: 回测入口脚本

**Objective:** 命令行回测入口

**Files:** Create `qmt_deploy/run_backtest.py`

**Step 1: Write the file**

```python
#!/usr/bin/env python3
"""run_backtest.py - 统一框架回测入口

用法:
    python qmt_deploy/run_backtest.py --strategy v61c
    python qmt_deploy/run_backtest.py --strategy v61c --start 2023-01-01 --end 2025-12-31
"""
import sys
import os
import argparse

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.db import load_panel_from_db
from qmt_deploy.qmt_framework.backtest_engine import BacktestEngine

# Strategy registry
STRATEGIES = {
    'v61c': {
        'class': 'qmt_deploy.qmt_framework.strategies.v61c.V61cStrategy',
        'params': {
            'MAX_HOLDINGS': 5,
            'REBALANCE_DAYS': 5,
            'STOP_LOSS': -0.08,
            'TAKE_PROFIT': 0.25,
            'HOLD_DAYS_MAX': 5,
            'SELL_OUT_OF': 15,
        }
    },
    # Add more strategies here
}


def main():
    parser = argparse.ArgumentParser(description='Unified framework backtest')
    parser.add_argument('--strategy', required=True, choices=STRATEGIES.keys())
    parser.add_argument('--start', default='2021-01-01')
    parser.add_argument('--end', default='2026-06-30')
    parser.add_argument('--cash', type=float, default=100000.0)
    parser.add_argument('--full', action='store_true', help='Full backtest (not WF)')
    args = parser.parse_args()

    # Load strategy
    config = STRATEGIES[args.strategy]
    import importlib
    module_path, class_name = config['class'].rsplit('.', 1)
    module = importlib.import_module(module_path)
    strategy_class = getattr(module, class_name)
    strategy = strategy_class(params=config['params'])

    # Load data
    print('Loading data...')
    close, vol, amt, open_, high, low = load_panel_from_db(
        'data/quant_stocks.db',
        start_date=args.start,
        end_date=args.end,
    )
    print('Data loaded: %d days, %d stocks' % (len(close), len(close.columns)))

    # Run backtest
    engine = BacktestEngine(initial_cash=args.cash)
    result = engine.run(
        strategy, close, vol, amt, high, low, open_,
        params=config['params'],
        start_date=args.start,
        end_date=args.end,
    )

    # Print results
    print('\n' + '=' * 50)
    print('%s Backtest Result' % args.strategy.upper())
    print('=' * 50)
    print('Total Return: %+.2f%%' % result['total_return'])
    print('Annual Return: %+.2f%%' % result['annual_return'])
    print('Sharpe Ratio: %.3f' % result['sharpe'])
    print('Max Drawdown: %.1f%%' % result['max_dd'])
    print('Total Trades: %d' % result['n_trades'])
    print('Final Value: %.2f' % result['final_value'])


if __name__ == '__main__':
    main()
```

**Step 2: Verify**

```bash
cd /root/a-share-quant-sim
python3 qmt_deploy/run_backtest.py --strategy v61c --start 2024-01-01 --end 2024-06-30
```

**Step 3: Commit**

```bash
git add qmt_deploy/run_backtest.py
git commit -m "feat: add unified backtest entry point"
```

---

## Task 7: __init__.py 导出

**Objective:** 包初始化

**Files:** Create `qmt_deploy/qmt_framework/__init__.py`, `qmt_deploy/qmt_framework/strategies/__init__.py`

**Step 1: Write files**

```python
# qmt_deploy/qmt_framework/__init__.py
from qmt_deploy.qmt_framework.base import BaseStrategy
from qmt_deploy.qmt_framework.backtest_engine import BacktestEngine
from qmt_deploy.qmt_framework.context import BacktestContext
from qmt_deploy.qmt_framework.qmt_adapter import QmtAdapter

__all__ = ['BaseStrategy', 'BacktestEngine', 'BacktestContext', 'QmtAdapter']
```

```python
# qmt_deploy/qmt_framework/strategies/__init__.py
from qmt_deploy.qmt_framework.strategies.v61c import V61cStrategy

__all__ = ['V61cStrategy']
```

**Step 2: Verify**

```bash
cd /root/a-share-quant-sim
python3 -c "from qmt_deploy.qmt_framework import BaseStrategy, BacktestEngine, QmtAdapter; print('All imports OK')"
```

**Step 3: Commit**

```bash
git add qmt_deploy/qmt_framework/__init__.py qmt_deploy/qmt_framework/strategies/__init__.py
git commit -m "feat: add package init files"
```

---

## 验证清单

- [ ] `python3 -c "from qmt_deploy.qmt_framework import BaseStrategy"` — 导入成功
- [ ] `python3 qmt_deploy/run_backtest.py --strategy v61c` — 能跑回测
- [ ] QMT 入口文件可以 `from qmt_framework.qmt_adapter import QmtAdapter` — 能桥接

## 迁移路径

1. 新策略用框架写（继承 BaseStrategy）
2. 旧策略逐步迁移（v61c → v75j → ...）
3. 旧的重复代码标记 deprecated

## 风险

- QMT Python 3.6.8 兼容性（不能用 walrus、dict union 等新语法）
- BacktestContext 与 QMT ContextInfo 的 API 差异（需要逐步对齐）
- run_time 回测不可验证（只能实盘验证）

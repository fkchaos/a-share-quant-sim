"""
A股 v3 策略模拟交易系统
======================
初始资金: 100万
交易规则:
- 买入: 等权分配, 每只约10%
- 止损: 单只 -20%
- 调仓: 每20个交易日
- 交易成本: 佣金0.03% + 印花税0.1%(卖出)
"""
import sys, os, pandas as pd, numpy as np, json, time
from datetime import datetime, timedelta

DATA_DIR = "data"
DAILY_DIR = os.path.join(DATA_DIR, "daily")
SIGNAL_DIR = os.path.join(DATA_DIR, "signals")
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

INITIAL_CAPITAL = 1_000_000
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.001
STOP_LOSS = 0.20
TOP_N = 10
REBAL_FREQ = 20

class SimAccount:
    def __init__(self):
        self.cash = INITIAL_CAPITAL
        self.initial_capital = INITIAL_CAPITAL
        self.holdings = {}  # {code: {'shares': int, 'cost_price': float, 'entry_date': str}}
        self.trade_log = []
        self.nav_history = []
        self.initialized = False
    
    def portfolio_value(self, date, price_data):
        """计算当日净值"""
        total = self.cash
        for code, info in self.holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    total += info['shares'] * p
        return total
    
    def buy(self, code, price, date, shares=None):
        """买入"""
        if shares is None:
            # 计算可买入股数
            available = self.cash * 0.98  # 预留佣金
            target_value = self.cash / len(self.holdings | {code: 1}) if self.holdings else self.cash / TOP_N
            target_value = min(target_value, self.cash * 0.12)  # 单只最大12%
            adj_price = price * (1 + SLIPPAGE_RATE)
            shares = int(target_value / adj_price / 100) * 100
        
        if shares <= 0:
            return False
        
        adj_price = price * (1 + SLIPPAGE_RATE)
        cost = shares * adj_price
        commission = cost * COMMISSION_RATE
        
        if self.cash < cost + commission:
            # 减少股数
            shares = int((self.cash * 0.98) / adj_price / 100) * 100
            if shares <= 0:
                return False
            cost = shares * adj_price
            commission = cost * COMMISSION_RATE
        
        self.cash -= (cost + commission)
        
        # 更新持仓（加仓时加权平均成本）
        if code in self.holdings:
            old = self.holdings[code]
            total_shares = old['shares'] + shares
            total_cost = old['shares'] * old['cost_price'] + shares * price
            self.holdings[code] = {
                'shares': total_shares,
                'cost_price': total_cost / total_shares,
                'entry_date': old['entry_date']
            }
        else:
            self.holdings[code] = {
                'shares': shares,
                'cost_price': price,
                'entry_date': str(date)
            }
        
        self.trade_log.append({
            'date': str(date), 'code': code, 'action': 'BUY',
            'shares': shares, 'price': price, 'cost': commission
        })
        
        return True
    
    def sell(self, code, price, date, reason='SELL'):
        """卖出"""
        if code not in self.holdings:
            return False
        
        info = self.holdings[code]
        adj_price = price * (1 - SLIPPAGE_RATE)
        revenue = info['shares'] * adj_price
        commission = revenue * COMMISSION_RATE
        stamp_tax = revenue * STAMP_TAX_RATE if reason != 'STOP_LOSS' else 0
        
        self.cash += (revenue - commission - stamp_tax)
        
        pnl = (price - info['cost_price']) / info['cost_price']
        
        self.trade_log.append({
            'date': str(date), 'code': code, 'action': reason,
            'shares': info['shares'], 'price': price,
            'cost': commission + stamp_tax, 'pnl': round(pnl, 4)
        })
        
        del self.holdings[code]
        return True
    
    def check_stop_loss(self, date, price_data):
        """检查止损"""
        to_sell = []
        for code, info in self.holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    loss = (info['cost_price'] - p) / info['cost_price']
                    if loss >= STOP_LOSS:
                        to_sell.append((code, p, loss))
        return to_sell
    
    def status_report(self, date, price_data):
        """生成状态报告"""
        total_value = self.portfolio_value(date, price_data)
        
        holdings_report = []
        total_invested = 0
        for code, info in self.holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    market_value = info['shares'] * p
                    weight = market_value / total_value if total_value > 0 else 0
                    pnl = (p - info['cost_price']) / info['cost_price']
                    total_invested += market_value
                    
                    # 获取股票名称
                    name = code
                    
                    holdings_report.append({
                        'code': code,
                        'name': name,
                        'shares': info['shares'],
                        'cost_price': info['cost_price'],
                        'current_price': p,
                        'market_value': market_value,
                        'weight': weight,
                        'pnl': pnl,
                        'entry_date': info['entry_date']
                    })
        
        total_ret = (total_value / self.initial_capital) - 1
        
        return {
            'date': str(date),
            'cash': self.cash,
            'portfolio_value': total_value,
            'total_return': total_ret,
            'holdings_count': len(self.holdings),
            'holdings': holdings_report,
            'total_trades': len(self.trade_log)
        }


def calc_factors_for_signal(df):
    """计算单只股票的因子"""
    close = df['close']
    volume = df.get('volume', pd.Series(1, index=df.index))
    amount = df.get('amount', close * volume)
    returns = close.pct_change()
    
    factors = {}
    eps = 1e-10
    
    for w in [5, 10, 20, 60, 120]:
        factors[f'mom_{w}'] = close.iloc[-1] / close.iloc[-w] - 1 if len(close) >= w else 0
    for w in [3, 5, 10]:
        factors[f'rev_{w}'] = -(close.iloc[-1] / close.iloc[-w] - 1) if len(close) >= w else 0
    for w in [10, 20, 60]:
        if len(returns) >= w:
            factors[f'vol_{w}'] = returns.iloc[-w:].std()
    if len(volume) >= 20:
        factors['vol_ratio_5'] = volume.iloc[-1] / (volume.iloc[-5:].mean() + eps)
        factors['vol_ratio_20'] = volume.iloc[-1] / (volume.iloc[-20:].mean() + eps)
        factors['amount_ratio'] = amount.iloc[-1] / (amount.iloc[-20:].mean() + eps)
    for w in [6, 14, 28]:
        if len(returns) >= w:
            g = returns.clip(lower=0).iloc[-w:].mean()
            l = (-returns.clip(upper=0)).iloc[-w:].mean()
            rs = g / (l + eps)
            factors[f'rsi_{w}'] = 100 - (100 / (1 + rs))
    if len(close) >= 26:
        ema12 = close.ewm(span=12).mean().iloc[-1]
        ema26 = close.ewm(span=26).mean().iloc[-1]
        factors['macd'] = (ema12 - ema26) * 0.2
    if len(close) >= 20:
        ma20 = close.iloc[-20:].mean()
        std20 = close.iloc[-20:].std()
        factors['boll_pos'] = (close.iloc[-1] - ma20 + 2*std20) / (4*std20 + eps)
    if len(returns) >= 20:
        factors['skew'] = returns.iloc[-20:].skew()
    factors['rel_strength'] = factors.get('mom_20', 0)
    
    return factors


def generate_scores():
    """计算所有股票评分"""
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    all_factors = {}
    
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if len(df) > 120:
            all_factors[code] = calc_factors_for_signal(df)
    
    # 标准化
    std_factors = {}
    for code in all_factors:
        std_factors[code] = {}
        for fname in all_factors[code]:
            vals = [all_factors[c].get(fname, np.nan) for c in all_factors]
            vals = [v for v in vals if not np.isnan(v) and not np.isinf(v)]
            if len(vals) < 10:
                std_factors[code][fname] = 0
                continue
            mean, std = np.mean(vals), np.std(vals)
            my_val = all_factors[code].get(fname, mean)
            std_factors[code][fname] = (my_val - mean) / std if std > 0 else 0
    
    weights = {
        'mom_5': 0.05, 'mom_10': 0.10, 'mom_20': 0.10, 'mom_60': 0.08, 'mom_120': 0.05,
        'rev_3': 0.05, 'rev_5': 0.08, 'rev_10': 0.05,
        'vol_10': -0.03, 'vol_20': -0.05, 'vol_60': -0.05,
        'vol_ratio_5': 0.05, 'vol_ratio_20': 0.05, 'amount_ratio': 0.05,
        'rsi_6': 0.03, 'rsi_14': 0.05, 'rsi_28': 0.02,
        'macd': 0.08,
        'boll_pos': 0.03,
        'skew': 0.02,
        'rel_strength': 0.08,
    }
    
    scores = {}
    for code, sf in std_factors.items():
        score = sum(sf.get(n, 0) * w for n, w in weights.items())
        scores[code] = score
    
    return scores


def load_hs300_names():
    """加载股票名称"""
    try:
        hs300 = pd.read_csv("/root/hs300_constituents.csv")
        return dict(zip(hs300['品种代码'].astype(str).str.zfill(6), hs300['品种名称']))
    except:
        return {}

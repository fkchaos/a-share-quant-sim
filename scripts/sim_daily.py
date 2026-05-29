"""
模拟盘交易 - 每日操作脚本
========================
"""
import sys, os, pandas as pd, numpy as np, json, time
from datetime import datetime

sys.path.insert(0, "/root")
from sim_account import SimAccount, generate_scores, load_hs300_names, DAILY_DIR

DATA_DIR = "data"
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")
SIGNAL_DIR = os.path.join(DATA_DIR, "signals")
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

REBAL_FREQ = 20
STOP_LOSS = 0.20
TOP_N = 10
SLIPPAGE_RATE = 0.001

def load_account():
    """加载账户状态"""
    account_file = os.path.join(PORTFOLIO_DIR, "account.json")
    if os.path.exists(account_file):
        with open(account_file) as f:
            data = f.load(f)
        # 暂时返回 None，用 pickle 保存
        return None
    return None

def save_account(account):
    """保存账户状态"""
    data = {
        'cash': account.cash,
        'holdings': account.holdings,
        'trade_log': account.trade_log,
        'nav_history': account.nav_history,
        'last_update': str(datetime.now())
    }
    with open(os.path.join(PORTFOLIO_DIR, "account.json"), 'w') as f:
        json.dump(data, f, indent=2, default=str)

def daily_operation():
    """每日操作"""
    print("=" * 70)
    print(f"v3 模拟交易 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    
    # 0. 先更新行情数据到最新
    print(f"\n📥 更新行情数据...")
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.expanduser("~"), "update_daily_data.py")],
        capture_output=True, text=True, timeout=300
    )
    # 只打印关键行
    for line in result.stdout.split('\n'):
        if any(k in line for k in ['📋', '📅', '✅', '🔄', '📊', '最新', '失败', '新增']):
            print(f"  {line.strip()}")
    if result.returncode != 0:
        print(f"  ⚠️  数据更新可能有问题: {result.stderr[:200]}")
    else:
        print(f"  ✅ 数据更新完成")
    
    # 1. 加载或初始化账户
    account = SimAccount()
    account_file = os.path.join(PORTFOLIO_DIR, "account.json")
    trade_count_file = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    
    if os.path.exists(account_file):
        with open(account_file) as f:
            data = json.load(f)
        account.cash = data['cash']
        account.holdings = data['holdings']
        account.trade_log = data.get('trade_log', [])
        account.nav_history = data.get('nav_history', [])
        # 转换 holdings 的 shares 为 int
        for code in account.holdings:
            account.holdings[code]['shares'] = int(account.holdings[code]['shares'])
        loaded = True
        print(f"  已加载账户: 现金 ¥{account.cash:,.0f}, 持仓 {len(account.holdings)} 只")
    else:
        loaded = False
        print(f"  初始资金: ¥{account.cash:,.0f}")
    
    # 2. 获取最新交易日数据
    # 读取第一只股票确定最新日期
    sample_file = os.path.join(DAILY_DIR, os.listdir(DAILY_DIR)[0])
    sample_df = pd.read_csv(sample_file, index_col='date', parse_dates=True)
    latest_date = sample_df.index[-1]
    print(f"  最新数据日期: {latest_date.date()}")
    
    # 3. 读取交易计数（用于判断调仓日）
    trade_count = 0
    if os.path.exists(trade_count_file):
        with open(trade_count_file) as f:
            trade_count = int(f.read().strip())
    
    # 4. 构建当日价格数据
    price_data = pd.Series(dtype=float)
    high_data = pd.Series(dtype=float)
    low_data = pd.Series(dtype=float)
    
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if latest_date in df.index:
            price_data[code] = df.loc[latest_date, 'close']
            if 'high' in df.columns:
                high_data[code] = df.loc[latest_date, 'high']
            if 'low' in df.columns:
                low_data[code] = df.loc[latest_date, 'low']
    
    # 5. 计算当前净值
    pv = account.portfolio_value(latest_date, price_data)
    total_ret = (pv / account.initial_capital) - 1
    
    print(f"\n  {'账户状态':=^60}")
    print(f"  现金:       ¥{account.cash:>12,.0f}")
    print(f"  持仓市值:   ¥{pv - account.cash:>12,.0f}")
    print(f"  总净值:     ¥{pv:>12,.0f}")
    print(f"  总收益率:   {total_ret:>11.2%}")
    print(f"  持仓数量:   {len(account.holdings)} 只")
    print(f"  已交易次数: {len(account.trade_log)}")
    print(f"  调仓计数:   {trade_count}/{REBAL_FREQ}")
    
    # 6. 持仓详情
    names = load_hs300_names()
    
    if account.holdings:
        print(f"\n  {'持仓明细':=^60}")
        print(f"  {'代码':<8} {'名称':<10} {'持仓':>8} {'成本价':>8} {'现价':>8} {'盈亏':>8} {'权重':>8}")
        print("  " + "-" * 58)
        
        holdings_list = []
        for code, info in account.holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    mv = info['shares'] * p
                    w = mv / pv if pv > 0 else 0
                    pnl = (p - info['cost_price']) / info['cost_price']
                    name = names.get(code, '—')
                    print(f"  {code:<8} {name:<10} {info['shares']:>8} {info['cost_price']:>8.2f} {p:>8.2f} {pnl:>7.2%} {w:>7.2%}")
                    holdings_list.append({
                        'code': code, 'name': name, 'shares': info['shares'],
                        'cost': info['cost_price'], 'price': p, 'pnl': pnl, 'weight': w
                    })
    
    # 7. 检查止损
    stop_loss_triggers = account.check_stop_loss(latest_date, price_data)
    if stop_loss_triggers:
        print(f"\n  ⚠️  止损触发!")
        for code, price, loss in stop_loss_triggers:
            name = names.get(code, code)
            print(f"    {code} {name}: 亏损 {loss:.2%}")
            account.sell(code, price, latest_date, 'STOP_LOSS')
    
    # 8. 判断是否需要调仓
    need_rebalance = (trade_count % REBAL_FREQ == 0) or not loaded
    
    if need_rebalance:
        print(f"\n  {'🔄 调仓日':=^60}")
        
        # 生成评分
        scores = generate_scores()
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_stocks = [code for code, _ in sorted_scores[:TOP_N]]
        
        print(f"  目标持仓 (Top {TOP_N}):")
        for i, code in enumerate(top_stocks):
            name = names.get(code, '—')
            s = scores.get(code, 0)
            p = price_data.get(code, 0)
            print(f"    {i+1}. {code} {name:<10} 评分={s:.3f} 价格={p:.2f}")
        
        # 卖出不在目标的
        to_sell = [c for c in list(account.holdings.keys()) if c not in top_stocks]
        if to_sell:
            print(f"\n  卖出 {len(to_sell)} 只:")
            for code in to_sell:
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        info = account.holdings[code]
                        pnl = (p - info['cost_price']) / info['cost_price']
                        name = names.get(code, code)
                        account.sell(code, p, latest_date, 'SELL')
                        print(f"    ❌ {code} {name} 盈亏={pnl:.2%}")
        
        # 买入新的
        current_pv = account.portfolio_value(latest_date, price_data)
        
        # 计算目标权重
        existing_target = [c for c in top_stocks if c in account.holdings]
        new_targets = [c for c in top_stocks if c not in account.holdings]
        
        # 等权分配
        weight_per_stock = 1.0 / TOP_N
        
        # 先补仓到目标权重
        for code in top_stocks:
            if code in account.holdings and code in price_data.index:
                p = price_data[code]
                if pd.isna(p) or p <= 0:
                    continue
                info = account.holdings[code]
                current_mv = info['shares'] * p
                current_w = current_mv / current_pv if current_pv > 0 else 0
                
                if current_w < weight_per_stock * 0.8:  # 低于目标的80%就补仓
                    target_mv = current_pv * weight_per_stock
                    add_mv = target_mv - current_mv
                    if add_mv > 10000:  # 至少买1万
                        adj_p = p * (1 + SLIPPAGE_RATE)
                        add_shares = int(add_mv / adj_p / 100) * 100
                        if add_shares > 0:
                            account.buy(code, p, latest_date, add_shares)
                            name = names.get(code, code)
                            print(f"    🔺 {code} {name} 补仓 {add_shares} 股")
        
        # 买入新股票
        for code in new_targets:
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    name = names.get(code, code)
                    success = account.buy(code, p, latest_date)
                    if success:
                        print(f"    ✅ {code} {name} 买入 @ {p:.2f}")
                    else:
                        print(f"    ⏭️  {code} {name} 资金不足跳过")
        
        trade_count = 0  # 重置计数
    else:
        print(f"\n  ⏸️  非调仓日 (距下次调仓 {REBAL_FREQ - trade_count % REBAL_FREQ} 天)")
    
    # 9. 保存状态
    save_account(account)
    with open(trade_count_file, 'w') as f:
        f.write(str(trade_count + 1))
    
    # 10. 收盘后报告
    final_pv = account.portfolio_value(latest_date, price_data)
    final_ret = (final_pv / account.initial_capital) - 1
    
    # 计算今日收益
    if len(account.nav_history) > 0:
        prev_nav = account.nav_history[-1]['nav']
        daily_ret = (final_pv / prev_nav) - 1 if prev_nav > 0 else 0
    else:
        daily_ret = 0
    
    account.nav_history.append({
        'date': str(latest_date),
        'nav': final_pv,
        'daily_return': daily_ret,
        'total_return': final_ret
    })
    
    # 重新保存（包含 nav_history）
    save_account(account)
    
    # 日报
    print(f"\n  {'📊 收盘报告':=^60}")
    print(f"  日期:       {latest_date.date()}")
    print(f"  总净值:     ¥{final_pv:,.0f}")
    print(f"  今日收益:   {daily_ret:+.2%}")
    print(f"  总收益率:   {final_ret:+.2%}")
    print(f"  持仓数量:   {len(account.holdings)} 只")
    print(f"  现金占比:   {account.cash/final_pv:.1%}")
    
    # 10. 明日操作计划
    print(f"\n  {'📋 明日操作计划':=^60}")
    
    next_trade_count = trade_count + 1
    days_to_rebal = REBAL_FREQ - next_trade_count % REBAL_FREQ
    is_rebal_day = (next_trade_count % REBAL_FREQ == 0)
    
    if is_rebal_day:
        print(f"  ⚡ 明天是调仓日！")
        
        # 用最新评分预演明天的调仓
        scores = generate_scores()
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_stocks = [code for code, _ in sorted_scores[:TOP_N]]
        
        # 计算当前持仓现值
        current_pv = account.portfolio_value(latest_date, price_data)
        
        # 当前持仓中需要卖出的
        to_sell = [c for c in list(account.holdings.keys()) if c not in top_stocks]
        # 当前持仓中需要保留/补仓的
        to_keep = [c for c in top_stocks if c in account.holdings]
        # 需要新买入的
        to_buy = [c for c in top_stocks if c not in account.holdings]
        
        print(f"\n  预期操作:")
        
        if to_sell:
            print(f"\n  📉 卖出 ({len(to_sell)} 只):")
            sell_total = 0
            for code in to_sell:
                if code in price_data.index and code in account.holdings:
                    p = price_data[code]
                    info = account.holdings[code]
                    mv = info['shares'] * p
                    sell_total += mv
                    pnl = (p - info['cost_price']) / info['cost_price']
                    name = names.get(code, code)
                    print(f"    ❌ {code} {name:<10} {info['shares']:>6}股  市值¥{mv:>10,.0f}  盈亏{pnl:>7.2%}")
            print(f"    预计回款: ¥{sell_total:,.0f}")
        
        if to_keep:
            print(f"\n  🔄 保留/补仓 ({len(to_keep)} 只):")
            weight_per = 1.0 / TOP_N
            for code in to_keep:
                if code in price_data.index:
                    p = price_data[code]
                    info = account.holdings[code]
                    current_mv = info['shares'] * p
                    current_w = current_mv / current_pv if current_pv > 0 else 0
                    target_mv = current_pv * weight_per
                    diff = target_mv - current_mv
                    name = names.get(code, code)
                    action = f"补仓¥{diff:,.0f}" if diff > 10000 else "持有不动"
                    print(f"    🔺 {code} {name:<10} 当前权重{current_w:.1%}  目标{weight_per:.1%}  {action}")
        
        if to_buy:
            print(f"\n  ✅ 新买入 ({len(to_buy)} 只):")
            # 计算可用资金
            avail = account.cash
            # 估算卖出回款
            for code in to_sell:
                if code in price_data.index and code in account.holdings:
                    p = price_data[code]
                    avail += account.holdings[code]['shares'] * p * 0.998  # 扣除交易成本
            # 扣除保留股票需要补仓的钱
            for code in to_keep:
                if code in price_data.index:
                    p = price_data[code]
                    info = account.holdings[code]
                    current_mv = info['shares'] * p
                    target_mv = current_pv * weight_per
                    if target_mv > current_mv + 10000:
                        avail -= (target_mv - current_mv)
            
            buy_budget = avail / max(len(to_buy), 1) if to_buy else 0
            for code in to_buy:
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        name = names.get(code, code)
                        score = scores.get(code, 0)
                        est_shares = int(buy_budget / p / 100) * 100
                        est_cost = est_shares * p if est_shares > 0 else 0
                        print(f"    ✅ {code} {name:<10} 评分={score:.3f}  预估买入{est_shares:>6}股  约¥{est_cost:>10,.0f}")
        
        # 预计交易后持仓
        print(f"\n  预计调仓后:")
        all_target = to_keep + to_buy
        for i, code in enumerate(all_target):
            if code in price_data.index:
                name = names.get(code, code)
                p = price_data[code]
                score = scores.get(code, 0)
            elif code in account.holdings:
                name = names.get(code, code)
                p = account.holdings[code]['cost_price']
                score = scores.get(code, 0)
            else:
                name = names.get(code, code)
                p = 0
                score = scores.get(code, 0)
            print(f"    {i+1}. {code} {name:<10} 评分={score:.3f}  参考价={p:.2f}")
    else:
        print(f"  ⏸️  非调仓日，无交易计划")
        print(f"  距下次调仓: {days_to_rebal} 个交易日（约 {days_to_rebal // 5} 周后）")
        
        # 非调仓日：检查是否有止损风险（如果明天大幅下跌可能触发）
        print(f"\n  ⚠️  止损风险预警:")
        has_risk = False
        for code, info in account.holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    loss = (info['cost_price'] - p) / info['cost_price']
                    # 如果当前亏损超过15%（接近20%止损线），提醒
                    if loss > 0.15:
                        name = names.get(code, code)
                        warn_pct = (loss - 0.15) / 0.05 * 100  # 距离止损线的百分比
                        print(f"    🔴 {code} {name:<10} 当前亏损{loss:.1%}  距止损线{0.20-loss:.1%}  高风险!")
                        has_risk = True
                    elif loss > 0.10:
                        name = names.get(code, code)
                        print(f"    🟡 {code} {name:<10} 当前亏损{loss:.1%}  注意观察")
                        has_risk = True
        
        if not has_risk:
            print(f"    ✅ 所有持仓安全，无止损风险")
        
        # 提示关注的持仓变动
        if account.holdings:
            print(f"\n  📌 关注持仓:")
            sorted_holdings = sorted(
                account.holdings.items(),
                key=lambda x: (price_data.get(x[0], 0) - x[1]['cost_price']) / x[1]['cost_price'] if x[1]['cost_price'] > 0 else 0
            )
            # 亏最多的和赚最多的
            if len(sorted_holdings) >= 2:
                worst_code, worst_info = sorted_holdings[0]
                best_code, best_info = sorted_holdings[-1]
                if worst_code in price_data.index:
                    name = names.get(worst_code, worst_code)
                    loss = (price_data[worst_code] - worst_info['cost_price']) / worst_info['cost_price']
                    print(f"    📉 跌幅最大: {worst_code} {name} ({loss:+.2%})")
                if best_code in price_data.index:
                    name = names.get(best_code, best_code)
                    gain = (price_data[best_code] - best_info['cost_price']) / best_info['cost_price']
                    print(f"    📈 涨幅最大: {best_code} {name} ({gain:+.2%})")
    
    print()  # 空行分隔
    
    # 保存日报
    daily_report = {
        'date': str(latest_date),
        'nav': final_pv,
        'daily_return': daily_ret,
        'total_return': final_ret,
        'holdings_count': len(account.holdings),
        'cash_ratio': account.cash / final_pv,
        'trades': [t for t in account.trade_log if str(latest_date) in str(t.get('date', ''))],
        'next_rebal_days': days_to_rebal,
        'is_rebal_tomorrow': is_rebal_day,
    }
    
    report_file = os.path.join(PORTFOLIO_DIR, f"daily_{latest_date.strftime('%Y%m%d')}.json")
    with open(report_file, 'w') as f:
        json.dump(daily_report, f, indent=2, default=str)
    
    print(f"\n  报告已保存: {report_file}")
    print("=" * 70)
    
    return daily_report


if __name__ == "__main__":
    try:
        report = daily_operation()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()

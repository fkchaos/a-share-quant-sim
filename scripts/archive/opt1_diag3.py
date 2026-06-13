"""
opt-1 诊断3：分析 v11b vs v11b_style 的持仓差异
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir('/root/a-share-quant-sim')

import numpy as np
import pandas as pd
from core.config import STRATEGY_PROFILES, MarketFilter
from core.data import load_and_build_panel
from core.factors import calc_factors_panel
from core.scoring import ensemble_union_score
import time

def main():
    start = "2021-01-01"
    end = "2026-06-11"
    print(f"=== 持仓诊断 ({start} ~ {end}) ===\n")

    close, volume, amount, _, high, low = load_and_build_panel(
        start, end, need_open=False, need_hl=True, market_filter=MarketFilter()
    )
    print(f"面板: {close.shape}")

    factors = calc_factors_panel(close, volume, amount, None, high, low)

    for label in ["v11b_zz800_union", "v11b_zz800_union_style"]:
        profile = STRATEGY_PROFILES[label]
        groups = profile.ensemble_groups
        top_n = profile.ensemble_group_top_n

        score = ensemble_union_score(factors, groups, top_n)

        # 统计每天被多少组选中的股票数
        dates = score.index
        stocks_per_day = []
        group_dist = {g: 0 for g in groups}
        multi_group = 0
        single_group = 0
        total_holdings = 0

        for date in dates:
            day_scores = score.loc[date].dropna()
            selected = day_scores[day_scores > 0]
            stocks_per_day.append(len(selected))
            total_holdings += len(selected)
            if len(selected) > 0:
                multi_group += (day_scores > 1).sum()
                single_group += (day_scores == 1).sum()

        avg_stocks = np.mean(stocks_per_day)
        print(f"\n--- {label} ---")
        print(f"  平均持仓: {avg_stocks:.1f} 只")
        print(f"  最大持仓: {max(stocks_per_day)} 只")
        print(f"  最小持仓: {min(stocks_per_day)} 只")
        print(f"  多组选中(>=2组)天数占比: {multi_group/len(dates):.1%}")
        print(f"  单组选中天数占比: {single_group/len(dates):.1%}")
        print(f"  零持仓天数: {sum(1 for s in stocks_per_day if s == 0)}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
v21_industry_rotation — 行业轮动两层选股策略
==============================================

架构：
  第一层：行业轮动打分 → 选 top N 行业
    - 行业动量：过去 20 日行业平均收益
    - 行业反转：过去 5 日行业平均收益（避免追高）
    - 行业拥挤度：行业内股票换手率标准差（高拥挤 = 过热）
    - 综合轮动分 = 动量 - 反转 - 拥挤度惩罚

  第二层：在 top 行业内选股
    - 沿用 v13 量价因子（反转+放量+缩量企稳+振幅收窄）
    - 只在 top 行业内的股票中评分排序

参数：
  - mom_window=20, rev_window=5
  - top_industries=3~8（可调）
  - 行业内选股数：按仓位分配

与 v13 的关系：
  - v13 全市场选股（715只）
  - v21 先在行业内过滤，再在行业内选股（减少选股池，提高信号密度）
  - 当行业轮动失效时（所有行业评分接近），退化为全市场选股
"""

import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")


def load_industry_map():
    """加载行业分类映射 {code: industry_name}（从 DB）"""
    from core.db import load_industry_map as _load
    return _load()


class IndustryRotationScorer:
    """行业轮动打分引擎"""

    def __init__(self, close_panel, volume_panel, amount_panel,
                 industry_map, mom_window=20, rev_window=5,
                 crowding_penalty=0.3):
        self.close_panel = close_panel
        self.volume_panel = volume_panel
        self.amount_panel = amount_panel
        self.industry_map = industry_map
        self.mom_window = mom_window
        self.rev_window = rev_window
        self.crowding_penalty = crowding_penalty

        # 预计算行业指标
        self._industry_mom = None   # 行业动量
        self._industry_rev = None   # 行业反转
        self._industry_crowd = None # 行业拥挤度
        self._stock_industry = None # stock -> industry
        self._precompute()

    def _precompute(self):
        """预计算行业动量/反转/拥挤度"""
        codes = self.close_panel.columns.tolist()
        industries = pd.Series(
            [self.industry_map.get(c, "") for c in codes],
            index=codes
        )
        # 过滤有行业分类的股票
        valid = industries[industries != ""].index.tolist()
        if not valid:
            return

        self._stock_industry = industries

        # 个股收益率
        returns = self.close_panel[valid].pct_change()

        # 按行业分组
        ind_groups = industries[valid]
        unique_inds = ind_groups.unique()

        mom_dict = {}
        rev_dict = {}
        crowd_dict = {}

        for ind in unique_inds:
            ind_codes = ind_groups[ind_groups == ind].index.tolist()
            if len(ind_codes) < 3:
                continue

            # 行业平均收益
            ind_ret = returns[ind_codes].mean(axis=1)
            mom_dict[ind] = ind_ret.rolling(self.mom_window, min_periods=5).mean()
            rev_dict[ind] = ind_ret.rolling(self.rev_window, min_periods=2).mean()

            # 拥挤度：行业内股票换手率的标准差（标准化后的分散度）
            if self.amount_panel is not None and self.volume_panel is not None:
                amt = self.amount_panel[ind_codes].reindex(columns=ind_codes).fillna(0)
                vol = self.volume_panel[ind_codes].reindex(columns=ind_codes).fillna(0)
                # 换手率 = amount / (close * volume) 的代理：amount / close
                close_sub = self.close_panel[ind_codes].reindex(columns=ind_codes).ffill()
                turnover = amt / close_sub.replace(0, np.nan)
                crowd_dict[ind] = turnover.std(axis=1).rolling(10, min_periods=3).mean()
            else:
                # 用收益率截面标准差代替
                crowd_dict[ind] = returns[ind_codes].std(axis=1).rolling(10, min_periods=3).mean()

        if mom_dict:
            self._industry_mom = pd.DataFrame(mom_dict)
            self._industry_rev = pd.DataFrame(rev_dict)
            self._industry_crowd = pd.DataFrame(crowd_dict)

    def get_industry_scores(self, date):
        """
        计算行业轮动评分。
        score = 动量分 - 反转分 - 拥挤度惩罚
        动量强 + 短期超跌 + 不拥挤 = 最佳
        """
        scores = {}
        for df in [self._industry_mom, self._industry_rev, self._industry_crowd]:
            if df is None or date not in df.index:
                return pd.Series(dtype=float)

        mom = self._industry_mom.loc[date]
        rev = self._industry_rev.loc[date]
        crowd = self._industry_crowd.loc[date] if self._industry_crowd is not None else pd.Series(0, index=mom.index)

        # 标准化
        def zscore(s):
            return (s - s.mean()) / s.std() if s.std() > 0 else pd.Series(0, index=s.index)

        mom_z = zscore(mom.dropna())
        rev_z = zscore(rev.dropna())
        crowd_z = zscore(crowd.dropna())

        # 取交集
        common = mom_z.index.intersection(rev_z.index).intersection(crowd_z.index)
        scores = mom_z[common] - rev_z[common] - self.crowding_penalty * crowd_z[common]

        return scores.dropna()

    def get_top_industries(self, date, top_n=5, min_score=None):
        """获取 top N 行业"""
        scores = self.get_industry_scores(date)
        if scores.empty:
            return []
        if min_score is not None:
            scores = scores[scores >= min_score]
        return scores.nlargest(top_n).index.tolist()

    def get_industry_mask(self, date, top_n=5, min_score=None):
        """获取 top 行业的股票布尔掩码"""
        top_inds = self.get_top_industries(date, top_n=top_n, min_score=min_score)
        if not top_inds:
            return pd.Series(False, index=self.close_panel.columns)
        mask = self._stock_industry.isin(top_inds)
        return mask.reindex(self.close_panel.columns).fillna(False)


# ============================================================
# 回测测试
# ============================================================
def run_backtest_test():
    """简单回测验证行业轮动 + v13 选股"""
    from core.db import load_panel_from_db
    from core.config import RiskLimits, TradingCosts, MarketFilter

    print("=" * 60)
    print("v21 行业轮动策略 — 回测验证")
    print("=" * 60)

    # 加载数据
    tpl, codes = load_panel_from_db("2022-01-01", "2026-05-31",
                                     need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    print(f"数据: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只股票")

    # 加载行业映射
    industry_map = load_industry_map()
    print(f"行业映射: {len(industry_map)} 只股票")
    mapped = sum(1 for c in close_panel.columns if c in industry_map)
    print(f"选股池中有行业分类: {mapped}/{len(close_panel.columns)} 只")

    # 初始化行业轮动引擎
    scorer = IndustryRotationScorer(
        close_panel, volume_panel, amount_panel, industry_map,
        mom_window=20, rev_window=5, crowding_penalty=0.3
    )

    # 选一个测试日期，检查行业评分
    test_date = close_panel.index[-1]
    scores = scorer.get_industry_scores(test_date)
    print(f"\n最新日期 {test_date.date()} 行业轮动评分 Top 10:")
    for ind, s in scores.nlargest(10).items():
        print(f"  {ind}: {s:.3f}")

    top_inds = scorer.get_top_industries(test_date, top_n=5)
    print(f"\nTop 5 行业: {top_inds}")

    mask = scorer.get_industry_mask(test_date, top_n=5)
    print(f"Top 行业股票数: {mask.sum()}/{len(mask)}")

    return scorer, close_panel, volume_panel, amount_panel


if __name__ == "__main__":
    run_backtest_test()

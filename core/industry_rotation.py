"""
行业轮动策略模块。

核心逻辑：
1. 行业动量：过去 20 日行业收益排名前 N 的行业
2. 行业反转：过去 5 日行业收益排名后 N 的行业（避免追高）
3. 行业轮动评分 = 动量分 - 反转分
4. 在 top 行业内选股（用 v13 量价因子）

用法：
    from industry_rotation import IndustryRotation

    rot = IndustryRotation(close_panel, industry_map)
    top_industries = rot.get_top_industries(date, top_n=5)
    industry_scores = rot.get_industry_scores(date)
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
INDUSTRY_CACHE = os.path.join(DATA_DIR, "industry_map.csv")


def load_industry_map():
    """加载行业分类映射。"""
    if not os.path.exists(INDUSTRY_CACHE):
        return {}
    df = pd.read_csv(INDUSTRY_CACHE, dtype={"code": str})
    return dict(zip(df["code"], df["industry"]))


class IndustryRotation:
    """行业轮动引擎。

    Parameters
    ----------
    close_panel : pd.DataFrame
        收盘价面板 (dates × stocks)
    industry_map : dict
        {股票代码: 行业名称}
    mom_window : int
        动量窗口（默认 20 日）
    rev_window : int
        反转窗口（默认 5 日）
    top_industries : int
        选取 top N 行业
    """

    def __init__(self, close_panel, industry_map, mom_window=20, rev_window=5, top_industries=5):
        self.close_panel = close_panel
        self.industry_map = industry_map
        self.mom_window = mom_window
        self.rev_window = rev_window
        self.top_industries = top_industries

        # 预计算行业收益面板
        self._industry_returns = None
        self._stock_industry_df = None
        self._precompute()

    def _precompute(self):
        """预计算行业收益。"""
        # 构建股票-行业映射表
        codes = self.close_panel.columns.tolist()
        industries = [self.industry_map.get(c, "") for c in codes]
        self._stock_industry_df = pd.Series(industries, index=codes)

        # 过滤有行业分类的股票
        valid_mask = self._stock_industry_df != ""
        valid_codes = self._stock_industry_df[valid_mask].index.tolist()

        if not valid_codes:
            return

        # 计算个股收益
        returns = self.close_panel[valid_codes].pct_change()

        # 按行业分组计算平均收益
        industry_groups = self._stock_industry_df[valid_mask]
        unique_industries = industry_groups.unique()

        industry_ret_dict = {}
        for ind in unique_industries:
            if not ind:
                continue
            ind_codes = industry_groups[industry_groups == ind].index.tolist()
            if len(ind_codes) >= 3:  # 至少 3 只股票才计算行业收益
                industry_ret_dict[ind] = returns[ind_codes].mean(axis=1)

        if industry_ret_dict:
            self._industry_returns = pd.DataFrame(industry_ret_dict)

    def get_industry_scores(self, date):
        """计算行业轮动评分。

        Returns
        -------
        pd.Series : {行业名: 轮动评分}
        """
        if self._industry_returns is None:
            return pd.Series(dtype=float)

        # 动量分：过去 mom_window 日行业收益
        mom_start = max(0, self._industry_returns.index.get_loc(date) - self.mom_window) if date in self._industry_returns.index else 0
        mom_end = self._industry_returns.index.get_loc(date) + 1 if date in self._industry_returns.index else len(self._industry_returns)

        mom_returns = self._industry_returns.iloc[mom_start:mom_end].sum()

        # 反转分：过去 rev_window 日行业收益（反转 = 负向）
        rev_start = max(0, self._industry_returns.index.get_loc(date) - self.rev_window) if date in self._industry_returns.index else 0
        rev_returns = self._industry_returns.iloc[rev_start:mom_end].sum()

        # 轮动评分 = 动量 - 反转（动量强 + 短期超跌 = 最佳）
        scores = mom_returns - rev_returns

        # 标准化
        if scores.std() > 0:
            scores = (scores - scores.mean()) / scores.std()

        return scores

    def get_top_industries(self, date, top_n=None):
        """获取 top N 行业。"""
        top_n = top_n or self.top_industries
        scores = self.get_industry_scores(date)
        if scores.empty:
            return []
        return scores.nlargest(top_n).index.tolist()

    def get_industry_mask(self, date, top_n=None):
        """获取 top 行业的股票掩码。

        Returns
        -------
        pd.Series : {股票代码: bool}，True 表示在 top 行业内
        """
        top_inds = self.get_top_industries(date, top_n)
        if not top_inds:
            return pd.Series(False, index=self.close_panel.columns)

        mask = self._stock_industry_df.isin(top_inds)
        return mask

    def filter_scores_by_industry(self, date, scores):
        """将评分过滤为仅包含 top 行业的股票。"""
        mask = self.get_industry_mask(date)
        filtered = scores.copy()
        filtered[~mask.reindex(filtered.index).fillna(False)] = np.nan
        return filtered


def calc_industry_rotation_scores(close_panel, industry_map, mom_window=20, rev_window=5):
    """计算行业轮动因子（截面评分）。

    对每个日期，计算每只股票的行业轮动分：
    - 股票所属行业过去 mom_window 日收益排名
    - 减去过去 rev_window 日收益排名（反转）

    Returns
    -------
    pd.DataFrame : (dates × stocks) 行业轮动因子值
    """
    rot = IndustryRotation(close_panel, industry_map, mom_window=mom_window, rev_window=rev_window)

    scores_dict = {}
    for date in close_panel.index:
        ind_scores = rot.get_industry_scores(date)
        if ind_scores.empty:
            continue

        # 将行业评分映射到个股
        stock_scores = {}
        for code in close_panel.columns:
            ind = industry_map.get(code, "")
            if ind in ind_scores.index:
                stock_scores[code] = ind_scores[ind]

        scores_dict[date] = stock_scores

    return pd.DataFrame(scores_dict).T

"""
core/industry_momentum.py — 行业动量因子

计算申万一级行业的动量得分（行业指数5/21/60日收益率加权），
并为每只股票赋予其所属行业的动量得分。

接口:
    compute_industry_momentum(panel, industry_map, date) -> pd.Series
    compute_industry_momentum_rank(panel, industry_map, date, top_n) -> pd.Series
    compute_industry_rotation_speed(panel, industry_map, date) -> pd.Series
"""
import numpy as np
import pandas as pd


def _extract_close(panel):
    """从 panel 中提取 close DataFrame（兼容 tuple 和 dict）"""
    if isinstance(panel, (tuple, list)):
        return panel[0]
    elif isinstance(panel, dict):
        return panel.get('close', panel.get('close_panel'))
    raise ValueError(f"不支持的 panel 类型: {type(panel)}")


def compute_industry_momentum(panel, industry_map, date=None,
                              weights=(0.4, 0.3, 0.3)):
    """
    计算每只股票的行业动量因子。
    
    参数:
        panel: load_panel_from_db 返回的面板数据 (tuple 或 dict)
        industry_map: {stock_code: industry_name} 映射
        date: 截止日期（可选，None=用全部数据）
        weights: (w5, w21, w60) 三个动量窗口的权重
    
    返回:
        pd.Series: index=股票代码, 值=行业动量得分
    """
    close = _extract_close(panel)
    
    if date is not None:
        close = close[close.index <= date]
    
    if len(close) < 6:
        return pd.Series(0.0, index=close.columns)
    
    # 计算各窗口收益率
    latest = close.iloc[-1]
    mom_5 = close.iloc[-6] / latest - 1 if len(close) >= 6 else 0.0
    mom_21 = close.iloc[-22] / latest - 1 if len(close) >= 22 else 0.0
    mom_60 = close.iloc[-61] / latest - 1 if len(close) >= 61 else 0.0
    
    # 加权合成行业动量（截面）
    w5, w21, w60 = weights
    industry_momentum_panel = latest * (w5 * mom_5 + w21 * mom_21 + w60 * mom_60)
    
    # 按行业聚合
    codes = close.columns.tolist()
    stock_industry = pd.Series(
        [industry_map.get(str(c), '') for c in codes], index=codes
    )
    
    unique_industries = stock_industry.unique()
    industry_scores = {}
    for ind in unique_industries:
        if not ind:
            continue
        mask = stock_industry == ind
        industry_scores[ind] = industry_momentum_panel[mask].mean()
    
    result = stock_industry.map(industry_scores)
    return result.fillna(0.0)


def compute_industry_momentum_rank(panel, industry_map, date=None, top_n=10):
    """
    行业动量排名因子：只保留动量 Top N 行业的股票，其余为 NaN。
    
    返回:
        pd.Series: Top N 行业中的股票动量得分，其余为 NaN
    """
    momentum = compute_industry_momentum(panel, industry_map, date)
    close = _extract_close(panel)
    
    if date is not None:
        close = close[close.index <= date]
    
    codes = close.columns.tolist()
    stock_industry = pd.Series(
        [industry_map.get(str(c), '') for c in codes], index=codes
    )
    
    industry_avg = momentum.groupby(stock_industry).mean()
    top_industries = industry_avg.nlargest(top_n).index.tolist()
    
    result = momentum.copy()
    mask = stock_industry.isin(top_industries)
    result[~mask] = np.nan
    
    return result


def compute_industry_rotation_speed(panel, industry_map, date=None, window=20):
    """
    行业轮动速度因子：行业排名变化率。
    
    返回:
        pd.Series: 每只股票所属行业当前的轮动速度（标准化）
    """
    close = _extract_close(panel)
    
    if date is not None:
        close = close[close.index <= date]
    
    if len(close) < window * 2:
        return pd.Series(0.0, index=close.columns)
    
    codes = close.columns.tolist()
    stock_industry = pd.Series(
        [industry_map.get(str(c), '') for c in codes], index=codes
    )
    
    ret_recent = close.iloc[-1] / close.iloc[-window] - 1
    ret_prev = close.iloc[-window] / close.iloc[-2 * window] - 1
    
    industry_ret_recent = ret_recent.groupby(stock_industry).mean()
    industry_ret_prev = ret_prev.groupby(stock_industry).mean()
    
    rank_recent = industry_ret_recent.rank()
    rank_prev = industry_ret_prev.rank()
    
    rank_change = (rank_recent - rank_prev).abs()
    
    result = stock_industry.map(rank_change).fillna(0.0)
    
    if result.std() > 0:
        result = (result - result.mean()) / result.std()
    
    return result

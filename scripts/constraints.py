"""
A股交易约束模块
================

在买卖前检查真实 A 股交易规则约束：
  - 涨跌停限制（主板 ±10%，创业板/科创板 ±20%，ST ±5%）
  - 一字板（涨停/跌停封板，无法成交）
  - 停牌（成交量为 0 或全部价格缺失）
  - T+1 制度（当日买入的持仓当日不能卖出）

使用方法：
  from constraints import build_trade_context, TradeContext

  ctx = build_trade_context(code, df, latest_date)
  blocked, reason = ctx.is_buy_blocked()
  if blocked:
      print(f"{code} 无法买入: {reason}")
"""
from __future__ import annotations

import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────
# 涨停比例（按股票代码前缀判断）
# ──────────────────────────────────────────────
MAIN_LIMIT_PCT = 0.10          # 主板 ±10%
CHINEXT_STAR_PCT = 0.20        # 创业板/科创板 ±20%
ST_PCT = 0.05                  # ST ±5%

CHINEXT_PREFIXES = ("300", "301")      # 创业板
STAR_MARKET_PREFIXES = ("688", "689")  # 科创板
ST_KEYWORDS = ("ST", "*ST")


def _limit_pct_for_symbol(code: str) -> float:
    """根据股票代码前缀判断涨停比例"""
    # 简单判断：300/301 开头是创业板，688/689 开头是科创板
    # 注意：这里用 code 前缀判断，不含 ST 检测（ST 需要名称信息）
    for prefix in CHINEXT_PREFIXES:
        if code.startswith(prefix):
            return CHINEXT_STAR_PCT
    for prefix in STAR_MARKET_PREFIXES:
        if code.startswith(prefix):
            return CHINEXT_STAR_PCT
    return MAIN_LIMIT_PCT


def _round_price(raw: float) -> float:
    """A股价格精度：分（2位小数）"""
    return round(raw, 2)


@dataclass
class TradeContext:
    """单只股票当日交易上下文"""
    code: str
    day: str                                          # YYYYMMDD
    prev_close: Optional[float] = None               # 昨收价
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None                   # 成交量（手）
    limit_up: Optional[float] = None                 # 涨停价
    limit_down: Optional[float] = None               # 跌停价
    suspended: bool = False                          # 是否停牌
    is_one_word_up: bool = False                     # 一字涨停
    is_one_word_down: bool = False                   # 一字跌停

    def is_buy_blocked(self) -> tuple[bool, str]:
        """返回 (是否被阻塞, 原因)"""
        if self.suspended:
            return True, "s停牌"
        if self.is_one_word_up:
            return True, "一字涨停(封板买不到)"
        if self.limit_up is not None and self.close is not None:
            if self.close >= self.limit_up - 1e-6:
                return True, f"涨停({self.close:.2f}>=涨停价{self.limit_up:.2f})"
        # 买入价达到涨停价也视为难以买入
        return False, ""

    def is_sell_blocked(self) -> tuple[bool, str]:
        """返回 (是否被阻塞, 原因)"""
        if self.suspended:
            return True, "s停牌"
        if self.is_one_word_down:
            return True, "一字跌停(封板卖不出)"
        if self.limit_down is not None and self.close is not None:
            if self.close <= self.limit_down + 1e-6:
                return True, f"跌停({self.close:.2f}<=跌停价{self.limit_down:.2f})"
        return False, ""


def build_trade_context(
    code: str,
    frame: pd.DataFrame,
    as_of,
    limit_pct: Optional[float] = None,
) -> Optional[TradeContext]:
    """
    从本地 K 线 DataFrame 构建 TradeContext。

    参数:
        code:   股票代码
        frame:  含 date/open/high/low/close/volume 列的 DataFrame
        as_of:  截止日期（Timestamp 或可被 pd.to_datetime 解析的值）
        limit_pct: 涨停比例，None 则自动根据代码前缀判断

    返回:
        TradeContext 或 None（数据不足时）
    """
    if frame is None or len(frame) < 2:
        return None

    # 兼容 date 列是 index 还是普通列
    if "date" in frame.columns:
        rows = frame.loc[frame["date"] <= as_of].copy()
    else:
        # date 是 index
        rows = frame.loc[frame.index <= as_of].copy()

    if len(rows) < 2:
        return None

    today_row = rows.iloc[-1]
    prev_row = rows.iloc[-2]

    as_of_ts = pd.to_datetime(as_of)
    day_str = as_of_ts.strftime("%Y%m%d")

    def _safe(val):
        if val is None:
            return None
        try:
            v = float(val)
            if pd.isna(v):
                return None
            return v
        except (TypeError, ValueError):
            return None

    open_p = _safe(today_row.get("open"))
    high_p = _safe(today_row.get("high"))
    low_p = _safe(today_row.get("low"))
    close_p = _safe(today_row.get("close"))
    volume = _safe(today_row.get("volume"))
    prev_close = _safe(prev_row.get("close"))

    if prev_close is None or prev_close <= 0:
        return None

    # 判断涨停比例
    pct = limit_pct if limit_pct is not None else _limit_pct_for_symbol(code)

    # 计算涨停价 / 跌停价（精确到分）
    limit_up = _round_price(prev_close * (1 + pct))
    limit_down = _round_price(prev_close * (1 - pct))

    # 判断是否停牌
    suspended = False
    if volume is not None and volume <= 0:
        suspended = True
    if all(v is None for v in [open_p, high_p, low_p, close_p]):
        suspended = True

    # 判断一字涨停：开盘=最高=最低=收盘=涨停价
    is_one_word_up = False
    is_one_word_down = False
    if not suspended and close_p is not None and limit_up is not None:
        if (open_p is not None and high_p is not None
                and low_p is not None
                and abs(open_p - limit_up) < 1e-6
                and abs(high_p - limit_up) < 1e-6
                and abs(low_p - limit_up) < 1e-6
                and abs(close_p - limit_up) < 1e-6):
            is_one_word_up = True

    if not suspended and close_p is not None and limit_down is not None:
        if (open_p is not None and high_p is not None
                and low_p is not None
                and abs(open_p - limit_down) < 1e-6
                and abs(high_p - limit_down) < 1e-6
                and abs(low_p - limit_down) < 1e-6
                and abs(close_p - limit_down) < 1e-6):
            is_one_word_down = True

    return TradeContext(
        code=code,
        day=day_str,
        prev_close=prev_close,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
        limit_up=limit_up,
        limit_down=limit_down,
        suspended=suspended,
        is_one_word_up=is_one_word_up,
        is_one_word_down=is_one_word_down,
    )


def batch_build_contexts(
    price_data: dict[str, pd.DataFrame],
    as_of,
) -> dict[str, TradeContext]:
    """
    批量构建所有股票的 TradeContext。

    参数:
        price_data: {code: DataFrame}，每只股票的 K 线数据
        as_of:      截止日期

    返回:
        {code: TradeContext}
    """
    contexts = {}
    for code, df in price_data.items():
        ctx = build_trade_context(code, df, as_of)
        if ctx is not None:
            contexts[code] = ctx
    return contexts

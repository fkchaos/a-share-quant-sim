"""
数据质量门禁模块
==================

在每日交易逻辑前运行，检查数据质量：
  - 数据过期（最新日期 > 5 天前）
  - 空值检查（最近窗口内 close/volume 为空）
  - 异常涨跌（单日涨跌幅超过阈值）
  - 复权异常（价格跳变 > 30%，可能缺失除权除息调整）

使用方法：
  from data_quality import DataQualityAuditor, DataQualityResult

  auditor = DataQualityAuditor(code_list, daily_dir, as_of=latest_date)
  result = auditor.audit()
  if not result.approved:
      print("数据质量问题:", result.blocking_issues)
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

# ──────────────────────────────────────────────
# 质量阈值
# ──────────────────────────────────────────────
MAX_STALE_DAYS = 5          # 超过 N 天未更新视为过期
RECENT_WINDOW = 20          # 检查最近 N 天
MAIN_BOARD_LIMIT = 0.10     # 主板单日涨跌超过 10% 视为异常
JUMP_THRESHOLD = 0.30       # 价格跳变 >30% 视为复权异常

@dataclass
class SymbolQuality:
    """单只股票的质量检查结果"""
    code: str
    latest_date: str
    is_stale: bool = False
    has_null_close: bool = False
    has_null_volume: bool = False
    has_abnormal_move: bool = False
    has_jump: bool = False
    issues: list = field(default_factory=list)

@dataclass
class DataQualityResult:
    """数据质量门禁结果"""
    approved: bool                        # False = 有阻塞问题
    risk_level: str                       # "low" / "medium" / "high"
    stale_count: int = 0
    null_close_count: int = 0
    abnormal_move_count: int = 0
    jump_count: int = 0
    blocking_issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    per_symbol: list = field(default_factory=list)

class DataQualityAuditor:
    """数据质量门禁检查器"""

    def __init__(
        self,
        code_list: list[str],
        daily_dir: str = None,
        as_of=None,
        max_stale_days: int = MAX_STALE_DAYS,
        recent_window: int = RECENT_WINDOW,
        data_source: str = "csv",  # "csv" | "db"
    ):
        self.code_list = code_list
        self.daily_dir = daily_dir
        self.as_of = pd.to_datetime(as_of) if as_of else None
        self.max_stale_days = max_stale_days
        self.recent_window = recent_window
        self.data_source = data_source

    def audit(self) -> DataQualityResult:
        """
        执行数据质量检查。
        返回 DataQualityResult，approved=False 表示存在阻塞问题。
        """
        results: list[SymbolQuality] = []
        reference_date = self.as_of  # 若有指定日期就用，否则用今天

        for code in self.code_list:
            if self.data_source == "db":
                # ── 从 DB 读取 ──
                try:
                    from core.db import get_kline
                    kl = get_kline(code)
                except Exception as e:
                    sq = SymbolQuality(code=code, latest_date="N/A",
                                       issues=[f"DB 读取失败: {e}"])
                    results.append(sq)
                    continue
                if not kl:
                    sq = SymbolQuality(code=code, latest_date="N/A",
                                       issues=["DB 中无数据"])
                    results.append(sq)
                    continue
                df = pd.DataFrame(kl)
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
            else:
                # ── 从 CSV 读取 ──
                csv_path = os.path.join(self.daily_dir, f"{code}.csv")
                if not os.path.exists(csv_path):
                    sq = SymbolQuality(code=code, latest_date="N/A",
                                       issues=["数据文件不存在"])
                    results.append(sq)
                    continue
                try:
                    df = pd.read_csv(csv_path, index_col="date", parse_dates=True)
                except Exception as e:
                    sq = SymbolQuality(code=code, latest_date="N/A",
                                       issues=[f"CSV 读取失败: {e}"])
                    results.append(sq)
                    continue

            if len(df) == 0:
                sq = SymbolQuality(code=code, latest_date="N/A",
                                   issues=["CSV 文件为空"])
                results.append(sq)
                continue

            latest_date = df.index[-1]
            latest_date_str = str(latest_date.date())

            # 确定参考日期
            ref = reference_date if reference_date is not None else latest_date

            sq = SymbolQuality(code=code, latest_date=latest_date_str)

            # 1. 过期检查：最新数据距离参考日期 > max_stale_days
            try:
                days_gap = (ref.Date() - latest_date.date()).days
            except Exception:
                days_gap = (ref - latest_date).days
            if days_gap > self.max_stale_days:
                sq.is_stale = True
                sq.issues.append(f"数据过期 {days_gap} 天（最新 {latest_date_str}）")

            # 取最近_WINDOW行做进一步检查
            recent = df.tail(self.recent_window).copy()

            # 2. 空值检查
            if "close" in recent.columns:
                null_close = recent["close"].isna().sum()
                if null_close > 0:
                    sq.has_null_close = True
                    sq.issues.append(f"近{self.recent_window}天有{null_close}天 close 为空")

            if "volume" in recent.columns:
                null_vol = recent["volume"].isna().sum()
                if null_vol > 0:
                    sq.has_null_volume = True
                    sq.issues.append(f"近{self.recent_window}天有{null_vol}天 volume 为空")

            # 3. 异常涨跌检查（单日涨跌幅超过主板涨停线）
            if "close" in recent.columns and len(recent) >= 2:
                pct_chg = recent["close"].pct_change().dropna()
                abnormal = pct_chg[pct_chg.abs() > MAIN_BOARD_LIMIT]
                if len(abnormal) > 0:
                    sq.has_abnormal_move = True
                    dates_str = ",".join(str(d.date()) for d in abnormal.index[:3])
                    sq.issues.append(f"异常涨跌 {len(abnormal)} 次（日期: {dates_str}）")

            # 4. 复权异常检查（价格跳变 > 30%）
            if "close" in recent.columns and len(recent) >= 2:
                abs_chg = recent["close"].pct_change().dropna().abs()
                jumps = abs_chg[abs_chg > JUMP_THRESHOLD]
                if len(jumps) > 0:
                    sq.has_jump = True
                    sq.issues.append(f"复权异常 {len(jumps)} 次（价格跳变 >{JUMP_THRESHOLD:.0%}）")

            results.append(sq)

        # 汇总
        return self._summarize(results)

    def _summarize(self, results: list[SymbolQuality]) -> DataQualityResult:
        total = len(results)
        stale_count = sum(1 for r in results if r.is_stale)
        null_close_count = sum(1 for r in results if r.has_null_close)
        abnormal_count = sum(1 for r in results if r.has_abnormal_move)
        jump_count = sum(1 for r in results if r.has_jump)

        blocking: list[str] = []
        warnings: list[str] = []

        # 阻塞问题
        if stale_count > 0:
            blocking.append(f"{stale_count}/{total} 只股票数据过期 >{self.max_stale_days} 天")
        if null_close_count > total * 0.1:
            blocking.append(f"{null_close_count}/{total} 只股票 close 空值率 >10%")

        # 警告（不影响执行）
        if abnormal_count > 0:
            warnings.append(f"{abnormal_count} 只股票存在异常涨跌（可能数据质量问题）")
        if jump_count > 0:
            warnings.append(f"{jump_count} 只股票存在复权异常（价格跳变 >{JUMP_THRESHOLD:.0%}）")

        # 风险评级
        if len(blocking) > 0:
            risk = "high"
        elif len(warnings) > 2:
            risk = "medium"
        else:
            risk = "low"

        approved = len(blocking) == 0

        per_symbol = [
            {
                "code": r.code,
                "latest_date": r.latest_date,
                "issues": r.issues,
            }
            for r in results if r.issues
        ]

        return DataQualityResult(
            approved=approved,
            risk_level=risk,
            stale_count=stale_count,
            null_close_count=null_close_count,
            abnormal_move_count=abnormal_count,
            jump_count=jump_count,
            blocking_issues=blocking,
            warnings=warnings,
            per_symbol=per_symbol,
        )

    def save_report(self, result: DataQualityResult, output_dir: str):
        """保存质量报告到 JSON"""
        os.makedirs(output_dir, exist_ok=True)
        date_str = self.as_of.strftime("%Y%m%d") if self.as_of else datetime.now().strftime("%Y%m%d")
        path = os.path.join(output_dir, f"quality_{date_str}.json")
        data = {
            "date": date_str,
            "approved": result.approved,
            "risk_level": result.risk_level,
            "stale_count": result.stale_count,
            "null_close_count": result.null_close_count,
            "abnormal_move_count": result.abnormal_move_count,
            "jump_count": result.jump_count,
            "blocking_issues": result.blocking_issues,
            "warnings": result.warnings,
            "details": result.per_symbol,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path

def print_quality_report(result: DataQualityResult):
    """打印可读的质量报告"""
    status = "✅ 通过" if result.approved else "❌ 阻塞"
    print(f"\n  {'数据质量门禁':=^60}")
    print(f"  状态: {status}    风险级别: {result.risk_level}")
    print(f"  过期: {result.stale_count}  空值: {result.null_close_count}  "
          f"异常涨跌: {result.abnormal_move_count}  复权异常: {result.jump_count}")
    if result.blocking_issues:
        print(f"\n  ❌ 阻塞问题:")
        for issue in result.blocking_issues:
            print(f"    - {issue}")
    if result.warnings:
        print(f"\n  ⚠️  警告:")
        for w in result.warnings:
            print(f"    - {w}")
    if result.per_symbol:
        # 只打印前 10 个有问题的
        print(f"\n  问题个股（前10）:")
        for item in result.per_symbol[:10]:
            print(f"    {item['code']} ({item['latest_date']}): {'; '.join(item['issues'])}")
        if len(result.per_symbol) > 10:
            print(f"    ... 还有 {len(result.per_symbol) - 10} 只有问题")

#!/usr/bin/env python3
"""
一键记录回测结果到 docs/RESULTS_LOG.md

用法:
    python scripts/log_backtest_result.py \
        --label v5_tp_decay \
        --params "top12,rf20,sl0.20,TP+decay" \
        --metrics return=0.2397 sharpe=1.37 maxdd=-0.2005 \
        --notes "分级止盈+持有期decay，无行业限制"

也可以从 run_backtest 的 metrics dict 自动提取:
    python scripts/log_backtest_result.py --auto
"""

import argparse
import subprocess
import sys
import os
import re
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_LOG = os.path.join(REPO_ROOT, "docs", "RESULTS_LOG.md")


def get_git_commit():
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def get_data_range():
    """从数据目录获取日期范围"""
    import glob
    data_dir = os.environ.get("BACKTEST_DATA_DIR", "/root/data/daily")
    files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    if not files:
        return "unknown"
    first = os.path.basename(files[0]).replace(".csv", "")
    last = os.path.basename(files[-1]).replace(".csv", "")
    count = len(files)
    return f"{first}~{last} ({count}只)"


def parse_metrics(metrics_list):
    """Parse 'key=value' strings into dict"""
    d = {}
    for item in metrics_list:
        k, v = item.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def append_row(label, params, metrics, notes=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = get_git_commit()
    data_range = get_data_range()

    ret = metrics.get("return", metrics.get("annual_return", "-"))
    sharpe = metrics.get("sharpe", metrics.get("sharpe_ratio", "-"))
    maxdd = metrics.get("maxdd", metrics.get("max_drawdown", "-"))
    calmar = metrics.get("calmar", "-")
    trades = metrics.get("trades", metrics.get("trade_count", "-"))

    # 确保百分比格式统一
    def fmt_pct(v):
        if v == "-":
            return "-"
        try:
            vf = float(v)
            return f"{vf:.2%}"
        except (ValueError, TypeError):
            return str(v)

    row = (
        f"| {now} | {commit} | {data_range} | {label} | {params} | "
        f"{fmt_pct(ret)} | {sharpe} | {fmt_pct(maxdd)} | {calmar} | {trades} | {notes} |"
    )

    # 追加到文件（在最后的表格行之后）
    with open(RESULTS_LOG, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到最后一个表格行，在其后追加
    lines = content.splitlines()
    last_table_line = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("|"):
            last_table_line = i

    if last_table_line >= 0:
        lines.insert(last_table_line + 1, row)
    else:
        lines.append(row)

    with open(RESULTS_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"✅ 已记录到 {RESULTS_LOG}")
    print(f"   {row}")


def main():
    parser = argparse.ArgumentParser(description="记录回测结果到 RESULTS_LOG.md")
    parser.add_argument("--label", required=True, help="策略标签，如 v5_tp_decay")
    parser.add_argument("--params", required=True, help="参数字符串，如 top12,rf20,sl0.20")
    parser.add_argument("--metrics", nargs="+", required=True,
                        help="指标 key=value 对，如 return=0.2397 sharpe=1.37 maxdd=-0.2005")
    parser.add_argument("--notes", default="", help="备注")
    args = parser.parse_args()

    metrics = parse_metrics(args.metrics)
    append_row(args.label, args.params, metrics, args.notes)


if __name__ == "__main__":
    main()

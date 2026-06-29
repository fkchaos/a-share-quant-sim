#!/usr/bin/env python3
"""
scripts/tools/format_report.py — 解析脚本输出 JSON → 格式化报告文本

用法（命令行）：
  python3 scripts/tools/format_report.py --type signal --account 2 --data '{"date":"2026-06-29",...}'
  python3 scripts/tools/format_report.py --type data_update --data '{"updated":800,...}'

用法（管道）：
  python3 scripts/sim/account_runner.py run --account-id 2 intraday_signal 2>/dev/null | \
    python3 scripts/tools/format_report.py --type signal --account 2

输入：JSON（--data 参数 或 stdin 管道）
输出：格式化报告文本到 stdout

send 部分（通知推送）不在此脚本中，由 cron 的 agent prompt 负责。
"""
import sys
import os
import json
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def format_signal(data: dict, account_id: int) -> str:
    """格式化信号报告"""
    date = data.get("date", "未知")
    is_trading = data.get("is_trading_day", True)
    status = data.get("status", "ok")
    cash = data.get("cash", 0)
    holdings = data.get("holdings", 0) or data.get("holdings_count", 0)
    buys = data.get("buys", [])
    sells = data.get("sells", [])
    holds = data.get("holds", [])
    skipped = data.get("skipped", 0)
    reason = data.get("reason", "")

    trading_tag = "📅" if is_trading else "🚫 非交易日"
    lines = [
        f"📊 账户{account_id} 信号 — {date} {trading_tag}",
    ]

    if status == "skip":
        lines.append(f"⏭️ 跳过 — {reason}")
        return "\n".join(lines)
    if status == "empty":
        lines.append(f"⚪ 无交易计划")
        return "\n".join(lines)

    lines.append(f"现金: ¥{cash:,.0f}  持仓: {holdings} 只")
    lines.append("─" * 30)

    if sells:
        lines.append(f"🔴 卖出 {len(sells)} 只:")
        for s in sells:
            lines.append(f"  {s['code']} {s.get('name','')} — {s.get('shares','')}股 ({s.get('reason','')})")

    if buys:
        lines.append(f"🟢 买入 {len(buys)} 只:")
        for b in buys:
            lines.append(f"  {b['code']} {b.get('name','')} — {b.get('shares','')}股 @ {b.get('price',0):.2f}")

    if holds:
        lines.append(f"🟡 持有 {len(holds)} 只:")
        for h in holds:
            lines.append(f"  {h['code']} {h.get('name','')} — {h.get('shares','')}股 @ {h.get('price',0):.2f}")

    if skipped:
        lines.append(f"⏭️ 跳过 {skipped} 只（资金不足/风控）")

    if not buys and not sells and not holds:
        lines.append("⚪ 无操作")

    return "\n".join(lines)


def format_execute(data: dict, account_id: int) -> str:
    """格式化执行报告"""
    date = data.get("date", "未知")
    is_trading = data.get("is_trading_day", True)
    status = data.get("status", "ok")
    executed = data.get("executed", 0)
    skipped = data.get("skipped", 0)
    details = data.get("details", [])
    reason = data.get("reason", "")

    trading_tag = "📅" if is_trading else "🚫 非交易日"
    lines = [
        f"⚡ 账户{account_id} 执行 — {date} {trading_tag}",
    ]

    if status == "skip":
        lines.append(f"⏭️ 跳过 — {reason}")
        return "\n".join(lines)

    lines.append(f"执行: {executed} 笔  跳过: {skipped} 笔")
    lines.append("─" * 30)

    buys = [d for d in details if d.get("action") == "BUY"]
    sells = [d for d in details if d.get("action") == "SELL"]
    skips = [d for d in details if d.get("action") == "SKIP"]

    if sells:
        lines.append(f"🔴 卖出 {len(sells)} 笔:")
        for d in sells:
            lines.append(f"  {d.get('code','')} {d.get('name','')} — {d.get('shares','')}股 ({d.get('reason','')})")

    if buys:
        lines.append(f"🟢 买入 {len(buys)} 笔:")
        for d in buys:
            lines.append(f"  {d.get('code','')} {d.get('name','')} — {d.get('shares','')}股 @ {d.get('price',0):.2f}")

    if skips:
        lines.append(f"⏭️ 跳过 {len(skips)} 笔:")
        for d in skips:
            lines.append(f"  {d.get('code','')} {d.get('name','')} — {d.get('reason','')}")

    if not details:
        lines.append("⚪ 无交易")

    holdings = data.get("holdings", [])
    if holdings:
        lines.append("─" * 30)
        lines.append(f"📦 执行后持仓 ({len(holdings)} 只):")
        for h in holdings:
            code = h.get("code", "")
            name = h.get("name", "")
            shares = h.get("shares", 0)
            cost = h.get("cost_price", 0)
            mv = h.get("market_value", 0)
            pnl_i_pct = h.get("pnl_pct", 0)
            lines.append(f"  {code} {name} — {shares}股 成本{cost:.2f} 市值¥{mv:,.0f} ({pnl_i_pct:+.1f}%)")

    return "\n".join(lines)


def format_report(data: dict, account_id: int) -> str:
    """格式化收盘报告"""
    date = data.get("date", "未知")
    is_trading = data.get("is_trading_day", True)
    nav = data.get("nav", 0)
    cash = data.get("cash", 0)
    pnl = data.get("pnl", 0)
    pnl_pct = data.get("pnl_pct", 0)
    holdings = data.get("holdings", [])
    position_scale = data.get("position_scale", 1.0)

    trading_tag = "📅" if is_trading else "🚫 非交易日"
    lines = [
        f"📋 账户{account_id} 收盘报告 — {date} {trading_tag}",
        f"现金: ¥{cash:,.0f}  持仓: {len(holdings)} 只",
        f"净值: ¥{nav:,.0f}  总收益: ¥{pnl:+,.0f} ({pnl_pct:+.2f}%)",
        f"仓位控制: POSITION_SCALE={position_scale:.2f}",
        "─" * 30,
    ]

    if holdings:
        lines.append("持仓明细:")
        for h in holdings:
            code = h.get("code", "")
            name = h.get("name", "")
            shares = h.get("shares", 0)
            cost = h.get("cost_price", 0)
            mv = h.get("market_value", 0)
            pnl_i_pct = h.get("pnl_pct", 0)
            lines.append(f"  {code} {name} — {shares}股 成本{cost:.2f} 市值¥{mv:,.0f} ({pnl_i_pct:+.1f}%)")

    return "\n".join(lines)


def format_data_update(data: dict) -> str:
    """格式化数据更新报告"""
    updated = data.get("updated", 0)
    failed = data.get("failed", 0)
    duration = data.get("duration", 0)
    records = data.get("records", 0)
    index_ok = data.get("index_ok", False)

    lines = [
        f"📥 数据更新完成",
        f"更新: {updated} 只  失败: {failed} 只  K线: {records} 条  耗时: {duration:.1f}s",
        f"上证指数: {'✅' if index_ok else '⚠️ 更新失败'}",
    ]

    if failed > 0:
        skipped_codes = data.get("skipped_codes", [])
        if skipped_codes:
            lines.append(f"跳过代码: {', '.join(skipped_codes[:10])}")
            if len(skipped_codes) > 10:
                lines.append(f"  ...共 {len(skipped_codes)} 只")

    return "\n".join(lines)


def format_error(data: dict, account_id: int) -> str:
    """格式化错误报告"""
    task = data.get("task", "未知任务")
    error = data.get("error", "未知错误")

    return (
        f"❌ 账户{account_id} {task} 报错\n"
        f"错误: {error}\n"
        f"时间: {data.get('date', '未知')}"
    )


FORMATTERS = {
    "signal": format_signal,
    "execute": format_execute,
    "report": format_report,
    "data_update": format_data_update,
    "error": format_error,
}


def main():
    parser = argparse.ArgumentParser(description="格式化脚本输出 JSON → 报告文本")
    parser.add_argument("--type", required=True, choices=FORMATTERS.keys(), help="报告类型")
    parser.add_argument("--account", type=int, default=0, help="账户ID（data_update 不需要）")
    parser.add_argument("--data", default=None, help="JSON 格式的报告数据（不传则从 stdin 读取）")
    args = parser.parse_args()

    # 读取 JSON
    if args.data:
        raw = args.data
    else:
        raw = sys.stdin.read().strip()

    # 取最后一个 JSON 行（兼容脚本输出混合文本的情况）
    data = None
    for line in reversed(raw.split("\n")):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    if data is None:
        print(f"❌ 未找到有效 JSON 输入", file=sys.stderr)
        sys.exit(1)

    # 格式化
    formatter = FORMATTERS[args.type]
    if args.type == "data_update":
        message = formatter(data)
    else:
        message = formatter(data, args.account)

    # 输出到 stdout（send 由 cron 的 agent prompt 负责）
    print(message)


if __name__ == "__main__":
    main()

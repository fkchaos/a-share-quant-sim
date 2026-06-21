#!/usr/bin/env python3
"""
scripts/tools/send_report.py — 发送报告到 QQ

用法：
  python3 scripts/tools/send_report.py --type signal --account 2 --data '{"date":"2026-06-21","cash":78018,"holdings":11,"buys":[...],"sells":[...]}'
  python3 scripts/tools/send_report.py --type execute --account 2 --data '{"date":"2026-06-21","executed":5,"skipped":2}'
  python3 scripts/tools/send_report.py --type report --account 2 --data '{"date":"2026-06-21","nav":195000,"pnl_pct":-2.5,"holdings":[...]}'
  python3 scripts/tools/send_report.py --type data_update --data '{"updated":800,"failed":0,"duration":12.5}'
  python3 scripts/tools/send_report.py --type error --account 2 --data '{"task":"intraday_signal","error":"..."}'

输出：发送结果 JSON 到 stdout
"""
import sys
import os
import json
import argparse

# 添加项目根目录到 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def format_signal(data: dict, account_id: int) -> str:
    """格式化信号报告"""
    date = data.get("date", "未知")
    cash = data.get("cash", 0)
    holdings = data.get("holdings", 0)
    buys = data.get("buys", [])
    sells = data.get("sells", [])
    skipped = data.get("skipped", 0)

    lines = [
        f"📊 账户{account_id} 信号 — {date}",
        f"现金: ¥{cash:,.0f}  持仓: {holdings} 只",
        "─" * 30,
    ]

    if sells:
        lines.append(f"🔴 卖出 {len(sells)} 只:")
        for s in sells:
            lines.append(f"  {s['code']} {s.get('name','')} — {s.get('shares','')}股 ({s.get('reason','')})")

    if buys:
        lines.append(f"🟢 买入 {len(buys)} 只:")
        for b in buys:
            lines.append(f"  {b['code']} {b.get('name','')} — {b.get('shares','')}股 @ {b.get('price',0):.2f}")

    if skipped:
        lines.append(f"⏭️ 跳过 {skipped} 只（资金不足/风控）")

    if not buys and not sells:
        lines.append("⚪ 无操作")

    return "\n".join(lines)


def format_execute(data: dict, account_id: int) -> str:
    """格式化执行报告"""
    date = data.get("date", "未知")
    executed = data.get("executed", 0)
    skipped = data.get("skipped", 0)
    details = data.get("details", [])

    lines = [
        f"⚡ 账户{account_id} 执行 — {date}",
        f"执行: {executed} 笔  跳过: {skipped} 笔",
        "─" * 30,
    ]

    for d in details:
        action = d.get("action", "")
        code = d.get("code", "")
        name = d.get("name", "")
        shares = d.get("shares", "")
        price = d.get("price", 0)
        reason = d.get("reason", "")

        if action == "BUY":
            lines.append(f"🟢 买入 {code} {name} — {shares}股 @ {price:.2f}")
        elif action == "SELL":
            lines.append(f"🔴 卖出 {code} {name} — {shares}股 ({reason})")
        elif action == "SKIP":
            lines.append(f"⏭️ 跳过 {code} {name} — {reason}")

    if not details:
        lines.append("⚪ 无交易")

    return "\n".join(lines)


def format_report(data: dict, account_id: int) -> str:
    """格式化收盘报告"""
    date = data.get("date", "未知")
    nav = data.get("nav", 0)
    cash = data.get("cash", 0)
    pnl = data.get("pnl", 0)
    pnl_pct = data.get("pnl_pct", 0)
    holdings = data.get("holdings", [])
    position_scale = data.get("position_scale", 1.0)

    lines = [
        f"📋 账户{account_id} 收盘报告 — {date}",
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


def send_to_qq(message: str) -> dict:
    """发送消息到 QQ"""
    try:
        # hermes_tools 只在 cron agent 环境中可用
        from hermes_tools import send_message
        result = send_message(
            action="send",
            target="qqbot",
            message=message,
        )
        return {"success": True, "result": result}
    except ImportError:
        # 非 agent 环境，用命令行发送
        import subprocess
        try:
            # 尝试用 hermes CLI 发送
            r = subprocess.run(
                ["hermes", "message", "send", "--target", "qqbot", "--message", message],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                return {"success": True, "result": r.stdout.strip()}
            return {"success": False, "error": r.stderr.strip() or "hermes CLI 发送失败"}
        except FileNotFoundError:
            return {"success": False, "error": "hermes CLI 未安装，无法发送 QQ 消息"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "发送超时"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="发送报告到 QQ")
    parser.add_argument("--type", required=True, choices=FORMATTERS.keys(), help="报告类型")
    parser.add_argument("--account", type=int, default=0, help="账户ID（data_update 不需要）")
    parser.add_argument("--data", required=True, help="JSON 格式的报告数据")
    parser.add_argument("--dry-run", action="store_true", help="只打印不发送")
    args = parser.parse_args()

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"JSON 解析失败: {e}"}))
        sys.exit(1)

    # 格式化消息
    formatter = FORMATTERS[args.type]
    if args.type == "data_update":
        message = formatter(data)
    else:
        message = formatter(data, args.account)

    result = {
        "success": True,
        "type": args.type,
        "account": args.account,
        "message": message,
        "sent": False,
    }

    if args.dry_run:
        print(f"[dry-run] 消息内容:\n{message}")
    else:
        send_result = send_to_qq(message)
        result["sent"] = send_result.get("success", False)
        if not result["sent"]:
            result["error"] = send_result.get("error", "发送失败")

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

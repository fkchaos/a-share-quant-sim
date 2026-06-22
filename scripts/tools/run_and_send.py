#!/usr/bin/env python3
"""
scripts/tools/run_and_send.py — 执行脚本 + 发送报告一条龙

用法：
  python3 scripts/tools/run_and_send.py --task data_update
  python3 scripts/tools/run_and_send.py --task signal --account 2
  python3 scripts/tools/run_and_send.py --task execute --account 2
  python3 scripts/tools/run_and_send.py --task report --account 2

流程：
  1. 执行对应的脚本（数据更新/信号生成/执行交易/收盘报告）
  2. 捕获输出的最后一行 JSON
  3. 调用 send_report.py 格式化并发送 QQ
"""
import sys
import os
import json
import subprocess
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

SEND_SCRIPT = os.path.join(PROJECT_ROOT, "tools", "send_report.py")


def run_command(cmd: list[str], task: str, account_id: int = 0, dry_run: bool = False) -> dict:
    """执行命令，捕获最后一行 JSON 输出，调用 send_report 发送"""
    print(f"[run_and_send] 执行任务: {task} {'账户'+str(account_id) if account_id else ''}")
    print(f"[run_and_send] 命令: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if stdout:
        print(f"[run_and_send] 脚本输出:\n{stdout}")
    if stderr:
        print(f"[run_and_send] 脚本日志:\n{stderr}")

    # 取最后一行作为 JSON
    lines = [l for l in stdout.split("\n") if l.strip()]
    data = None
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    if data is None:
        # JSON 解析失败，发送错误消息
        error_msg = f"❌ {task} 未返回有效 JSON\nstdout: {stdout[:500]}\nstderr: {stderr[:500]}"
        print(f"[run_and_send] {error_msg}")
        if not dry_run:
            subprocess.run([sys.executable, SEND_SCRIPT, "--type", "error", "--account", str(account_id),
                           "--data", json.dumps({"task": task, "error": "未返回有效 JSON", "date": str(datetime.now().date())})],
                          capture_output=True, timeout=30)
        return {"success": False, "error": "未返回有效 JSON"}

    # 调用 send_report.py
    send_type = data.get("type", task)
    send_data = json.dumps(data, ensure_ascii=False)

    if dry_run:
        print(f"[run_and_send] [dry-run] 将发送: type={send_type}, data={send_data[:200]}...")
        send_result = {"success": True, "dry_run": True}
    else:
        send_cmd = [sys.executable, SEND_SCRIPT, "--type", send_type, "--data", send_data]
        if account_id:
            send_cmd.extend(["--account", str(account_id)])
        send_proc = subprocess.run(send_cmd, capture_output=True, text=True, timeout=30)
        try:
            send_result = json.loads(send_proc.stdout)
        except Exception:
            send_result = {"success": False, "error": send_proc.stderr or send_proc.stdout}

    print(f"[run_and_send] 发送结果: {send_result}")
    return {"success": True, "data": data, "sent": send_result.get("success", False)}


def main():
    parser = argparse.ArgumentParser(description="执行脚本 + 发送报告")
    parser.add_argument("--task", required=True, choices=["data_update", "signal", "execute", "report"])
    parser.add_argument("--account", type=int, default=0, help="账户ID（data_update 不需要）")
    parser.add_argument("--date", default=None, help="日期（默认今天）")
    parser.add_argument("--dry-run", action="store_true", help="只执行不发送")
    args = parser.parse_args()

    date = args.date or datetime.now().strftime("%Y-%m-%d")

    # 构建命令
    if args.task == "data_update":
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "update_daily_data_async.py")]
    elif args.task == "signal":
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "..", "sim", "account_runner.py"),
               "run", "--account-id", str(args.account), "intraday_signal", "--date", date]
    elif args.task == "execute":
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "..", "sim", "account_runner.py"),
               "run", "--account-id", str(args.account), "intraday_execute", "--date", date]
    elif args.task == "report":
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "..", "sim", "account_runner.py"),
               "run", "--account-id", str(args.account), "report_only", "--date", date]
    else:
        print(f"未知任务: {args.task}")
        sys.exit(1)

    result = run_command(cmd, args.task, args.account, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

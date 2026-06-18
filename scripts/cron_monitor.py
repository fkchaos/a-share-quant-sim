#!/usr/bin/env python3
"""
cron_monitor.py — Cron 任务执行监控脚本

功能：
1. 读取 jobs.json 获取所有 job 配置
2. 扫描每个 job 的最新输出文件，解析 [CRON_STATUS] 标记
3. 检测异常：漏执行、执行失败、超时
4. 异常时通过 QQ 告警，正常时静默
5. 每天 16:00 发送一次心跳报告

用法：
  python3 cron_monitor.py          # 单次检查
  python3 cron_monitor.py --daemon # 每 10 分钟循环（由 cron job 调度，不需要 daemon 模式）
"""

import json
import os
import re
import glob
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── 配置 ───────────────────────────────────────────────────────────────────

JOBS_JSON = os.path.expanduser("~/.hermes/cron/jobs.json")
OUTPUT_DIR = os.path.expanduser("~/.hermes/cron/output")
HEARTBEAT_FILE = os.path.expanduser("~/.hermes/cron/.monitor_heartbeat")
SUPPRESS_FILE = os.path.expanduser("~/.hermes/cron/.monitor_suppress")

# 告警抑制时间（分钟）
SUPPRESS_MINUTES = 30

# 漏执行容忍时间（分钟）
MISS_TOLERANCE_MIN = 10

# 超时倍数阈值（执行时间超过历史均值 N 倍则预警）
SLOW_THRESHOLD = 2.0

# 连续失败告警阈值
CONSECUTIVE_ERROR_THRESHOLD = 2

# QQ 目标
QQ_TARGET = "qqbot:95A0E5858A112E7A030CAE1094512D2F"

# ─── 工具函数 ───────────────────────────────────────────────────────────────

def load_jobs():
    """加载 jobs.json"""
    with open(JOBS_JSON, "r") as f:
        return json.load(f)["jobs"]

def parse_cron_status(output_text):
    """
    从输出文本中解析 [CRON_STATUS] 标记
    返回 dict: {job_id, status, duration, ts} 或 None
    """
    pattern = r"\[CRON_STATUS\]\s+job_id=(\w+)\s+status=(\w+)\s+duration=(\d+)\s+ts=(\S+ \S+)"
    match = re.search(pattern, output_text)
    if match:
        return {
            "job_id": match.group(1),
            "status": match.group(2),
            "duration": int(match.group(3)),
            "ts": match.group(4),
        }
    return None

def get_latest_output(job_id):
    """
    获取 job 的最新输出文件内容
    返回 (filepath, content) 或 (None, None)
    """
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    if not os.path.isdir(job_dir):
        return None, None

    files = sorted(glob.glob(os.path.join(job_dir, "*.md")))
    if not files:
        return None, None

    latest = files[-1]
    with open(latest, "r") as f:
        return latest, f.read()

def get_history_durations(job_id, days=5):
    """
    获取 job 最近 N 天的执行耗时列表
    """
    durations = []
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    if not os.path.isdir(job_dir):
        return durations

    files = sorted(glob.glob(os.path.join(job_dir, "*.md")))
    for fpath in files[-days:]:
        with open(fpath, "r") as f:
            content = f.read()
        status = parse_cron_status(content)
        if status and status["status"] == "ok":
            durations.append(status["duration"])

    return durations

def is_suppressed(job_id):
    """检查 job 是否处于告警抑制期"""
    if not os.path.exists(SUPPRESS_FILE):
        return False

    try:
        with open(SUPPRESS_FILE, "r") as f:
            suppress = json.load(f)
        if job_id in suppress:
            ts = datetime.fromisoformat(suppress[job_id])
            if datetime.now() - ts < timedelta(minutes=SUPPRESS_MINUTES):
                return True
    except Exception:
        pass
    return False

def set_suppressed(job_id):
    """设置 job 告警抑制"""
    suppress = {}
    if os.path.exists(SUPPRESS_FILE):
        try:
            with open(SUPPRESS_FILE, "r") as f:
                suppress = json.load(f)
        except Exception:
            pass
    suppress[job_id] = datetime.now().isoformat()
    with open(SUPPRESS_FILE, "w") as f:
        json.dump(suppress, f)

def is_heartbeat_sent_today():
    """检查今天是否已发送心跳"""
    if not os.path.exists(HEARTBEAT_FILE):
        return False
    try:
        with open(HEARTBEAT_FILE, "r") as f:
            last = f.read().strip()
        return last == datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return False

def mark_heartbeat_sent():
    """标记今天已发送心跳"""
    with open(HEARTBEAT_FILE, "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d"))

def send_message(target, message):
    """通过 hermes CLI 发送消息"""
    import subprocess
    cmd = [
        "hermes", "send",
        "-t", target,
        message
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[ERROR] hermes send 失败: {result.stderr}", file=sys.stderr)
        return result.returncode == 0
    except Exception as e:
        print(f"[ERROR] 发送消息失败: {e}", file=sys.stderr)
        return False

# ─── 核心检查逻辑 ───────────────────────────────────────────────────────────

def check_jobs():
    """
    检查所有启用的 job
    返回 (alerts, summary)
    alerts: list of str — 告警消息列表
    summary: dict — 各 job 状态汇总
    """
    jobs = load_jobs()
    alerts = []
    summary = {}

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    weekday = now.weekday()  # 0=Mon, 6=Sun

    for job in jobs:
        job_id = job["id"]
        name = job["name"]
        enabled = job.get("enabled", False)
        state = job.get("state", "")

        # 跳过暂停的 job
        if state == "paused" or not enabled:
            summary[job_id] = {"name": name, "state": "paused"}
            continue

        # 跳过非工作日 job（cron 表达式含 1-5 的）
        schedule = job.get("schedule", {}).get("expr", "")
        if "1-5" in schedule and weekday >= 5:
            summary[job_id] = {"name": name, "state": "skip_weekend"}
            continue

        # 获取最新输出
        filepath, content = get_latest_output(job_id)

        if content is None:
            # 从未执行过
            summary[job_id] = {"name": name, "state": "never_run"}
            continue

        # 解析状态标记
        status = parse_cron_status(content)

        if status is None:
            # 输出存在但没有状态标记（旧格式或 agent 未写入）
            # 检查文件是否是今天的
            file_date = os.path.basename(filepath)[:10] if filepath else ""
            if file_date == today:
                # 检查输出内容是否包含错误信息
                has_error = any(kw in content for kw in ["Error", "FAILED", "RuntimeError", "Traceback", "HTTP 429", "Provider returned error"])
                if has_error:
                    summary[job_id] = {"name": name, "state": "error_no_marker"}
                    if not is_suppressed(job_id):
                        alerts.append(f"🔴 执行失败（agent 异常）：{name} — 今日输出含错误信息，无 [CRON_STATUS] 标记")
                        set_suppressed(job_id)
                else:
                    summary[job_id] = {"name": name, "state": "no_marker_today"}
            else:
                summary[job_id] = {"name": name, "state": "no_marker"}
            continue

        # 检查是否是今天的执行
        # 方法1：CRON_STATUS 的 ts（可能被 agent 写死，不可靠）
        ts_is_today = status["ts"].startswith(today) if status else False
        # 方法2：输出文件的 mtime（更可靠，文件今天被修改过说明今天执行了）
        file_is_today = False
        if filepath:
            try:
                from datetime import datetime as _dt
                mtime = _dt.fromtimestamp(os.path.getmtime(filepath))
                file_is_today = mtime.strftime("%Y-%m-%d") == today
            except Exception:
                pass

        if not ts_is_today and not file_is_today:
            # 今天还没执行，检查是否漏执行
            # 解析计划时间
            scheduled_time = parse_schedule_time(schedule, now)
            if scheduled_time and now > scheduled_time + timedelta(minutes=MISS_TOLERANCE_MIN):
                if not is_suppressed(job_id):
                    alerts.append(f"🔴 漏执行：{name}（计划 {schedule}，已过 {MISS_TOLERANCE_MIN} 分钟未执行）")
                    set_suppressed(job_id)
            summary[job_id] = {"name": name, "state": "not_today", "last": status["ts"]}
            continue

        # 今天的执行 — 检查状态
        job_summary = {
            "name": name,
            "status": status["status"],
            "duration": status["duration"],
            "ts": status["ts"],
        }

        if status["status"] == "error":
            # 检查连续失败次数
            consecutive = count_consecutive_errors(job_id)
            if consecutive >= CONSECUTIVE_ERROR_THRESHOLD:
                if not is_suppressed(job_id):
                    alerts.append(f"🔴 连续失败 {consecutive} 次：{name}")
                    set_suppressed(job_id)
            job_summary["state"] = "error"
        else:
            # 检查执行时间是否异常
            history = get_history_durations(job_id)
            if history:
                avg_duration = sum(history) / len(history)
                if status["duration"] > avg_duration * SLOW_THRESHOLD:
                    if not is_suppressed(job_id):
                        alerts.append(
                            f"🟡 超时预警：{name}（耗时 {status['duration']}s，"
                            f"历史均值 {avg_duration:.0f}s）"
                        )
                        set_suppressed(job_id)
            job_summary["state"] = "ok"

        summary[job_id] = job_summary

    return alerts, summary

def parse_schedule_time(schedule_expr, now):
    """
    简化的 cron 时间解析，只处理 '分 时 * * 星期' 格式
    返回今天的计划 datetime 或 None
    """
    parts = schedule_expr.strip().split()
    if len(parts) < 5:
        return None

    try:
        minute = int(parts[0])
        hour = int(parts[1])
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, IndexError):
        return None

def count_consecutive_errors(job_id):
    """计算最近连续失败次数"""
    job_dir = os.path.join(OUTPUT_DIR, job_id)
    if not os.path.isdir(job_dir):
        return 0

    files = sorted(glob.glob(os.path.join(job_dir, "*.md")))
    consecutive = 0
    for fpath in reversed(files):
        with open(fpath, "r") as f:
            content = f.read()
        status = parse_cron_status(content)
        if status and status["status"] == "error":
            consecutive += 1
        else:
            break
    return consecutive

def format_heartbeat_report(summary):
    """格式化心跳报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"📋 Cron 任务心跳报告 — {now}", ""]

    ok_jobs = []
    error_jobs = []
    paused_jobs = []
    miss_jobs = []

    for job_id, info in summary.items():
        state = info.get("state", "unknown")
        name = info["name"]
        if state == "ok":
            duration = info.get("duration", "?")
            ok_jobs.append(f"  ✅ {name}  {duration}s")
        elif state == "error":
            error_jobs.append(f"  ❌ {name}")
        elif state == "error_no_marker":
            error_jobs.append(f"  ❌ {name}（agent 异常，无状态标记）")
        elif state == "paused":
            paused_jobs.append(f"  ⏸️ {name}")
        elif state in ("not_today", "no_marker", "no_marker_today"):
            miss_jobs.append(f"  ⚠️ {name}（今日未执行）")
        else:
            paused_jobs.append(f"  ❓ {name}（{state}）")

    if ok_jobs:
        lines.append(f"✅ 正常 ({len(ok_jobs)})")
        lines.extend(ok_jobs)
        lines.append("")

    if error_jobs:
        lines.append(f"❌ 异常 ({len(error_jobs)})")
        lines.extend(error_jobs)
        lines.append("")

    if miss_jobs:
        lines.append(f"⚠️ 未执行 ({len(miss_jobs)})")
        lines.extend(miss_jobs)
        lines.append("")

    if paused_jobs:
        lines.append(f"⏸️ 暂停/跳过 ({len(paused_jobs)})")
        lines.extend(paused_jobs)
        lines.append("")

    total = len(summary)
    ok_count = len(ok_jobs)
    lines.append(f"总计：{ok_count}/{total} 正常")

    return "\n".join(lines)

# ─── 主入口 ─────────────────────────────────────────────────────────────────

def main():
    """主函数"""
    import argparse
    parser = argparse.ArgumentParser(description="Cron 任务监控")
    parser.add_argument("--heartbeat", action="store_true", help="发送心跳报告")
    parser.add_argument("--quiet", action="store_true", help="静默模式（不发送消息）")
    args = parser.parse_args()

    alerts, summary = check_jobs()

    # 处理告警
    if alerts:
        alert_msg = "⚠️ Cron 任务异常告警\n\n" + "\n".join(alerts)
        print(alert_msg)
        if not args.quiet:
            send_message(QQ_TARGET, alert_msg)
    else:
        print("[OK] 所有任务正常")

    # 处理心跳报告（16:00 左右由 cron 触发）
    if args.heartbeat:
        if not is_heartbeat_sent_today():
            report = format_heartbeat_report(summary)
            print("\n" + report)
            if not args.quiet:
                send_message(QQ_TARGET, report)
            mark_heartbeat_sent()
        else:
            print("[INFO] 今天已发送心跳报告")

if __name__ == "__main__":
    main()

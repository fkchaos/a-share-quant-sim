#!/usr/bin/env python3
"""
ai_factor_analyzer — AI 辅助因子分析工具
=========================================

功能：
1. 从 DB 加载数据，计算所有因子 IC/IR（替代 ic_analysis_zz800.py）
2. 将 IC 结果发送给 DeepSeek，获取因子分析报告
3. AI 输出：因子有效性排名、冗余识别、权重方向建议、新因子思路

用法：
    python scripts/ai_factor_analyzer.py --calc-only    # 只计算IC
    python scripts/ai_factor_analyzer.py --analyze       # 计算+AI分析（需 DEEPSEEK_API_KEY）
    python scripts/ai_factor_analyzer.py --top-n 10      # 只看top10因子
"""

import sys, os
import json
import time
import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(os.environ.get('BACKTEST_DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')))
REPORT_DIR = DATA_DIR / 'backtest_results'
REPORT_DIR.mkdir(exist_ok=True)

# ── 因子面板计算（从 DB）─────────────────────────────────────────
def calc_factor_panels_from_db(start_date='2022-01-01', end_date='2026-05-31'):
    from core.db import load_panel_from_db
    from core.factors import calc_factors_panel

    print("加载数据...")
    tpl, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel = tpl[3] if len(tpl) > 3 else None
    high_panel = tpl[4] if len(tpl) > 4 else None
    low_panel = tpl[5] if len(tpl) > 5 else None

    print("  数据: %d 天 x %d 只" % (close_panel.shape[0], close_panel.shape[1]))

    print("计算因子面板...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel,
                                  open_panel=open_panel,
                                  high_panel=high_panel, low_panel=low_panel)
    print("  因子数: %d" % len(factors))
    return factors, close_panel

# ── IC 计算 ─────────────────────────────────────────────────────
def calc_all_ic(factors, close_panel, fwd_period=5):
    """向量化 IC 计算"""
    import numpy as np
    print("\n计算 IC (forward=%d天)..." % fwd_period)
    fwd_ret = close_panel.pct_change(fwd_period).shift(-fwd_period)

    # 对齐到共同索引
    common_idx = fwd_ret.index.intersection(close_panel.index)
    fwd_arr = fwd_ret.loc[common_idx].values  # (T, N)
    T = len(common_idx)

    results = {}
    total = len(factors)
    for idx, (name, panel) in enumerate(factors.items()):
        aligned = panel.reindex(index=common_idx).values  # (T, N) with NaN
        ics = []
        for t in range(T):
            fv = aligned[t]
            rv = fwd_arr[t]
            mask = ~(np.isnan(fv) | np.isnan(rv))
            if mask.sum() < 20:
                continue
            c = np.corrcoef(fv[mask], rv[mask])[0, 1]
            if not np.isnan(c):
                ics.append(c)
        if ics:
            results[name] = {
                'IC_mean': float(np.mean(ics)),
                'IC_std': float(np.std(ics)),
                'IR': float(np.mean(ics) / np.std(ics)) if np.std(ics) > 0 else 0,
                'IC_positive_pct': float(np.mean(np.array(ics) > 0)),
                'n': len(ics),
            }
        if (idx + 1) % 10 == 0:
            print("  %d/%d 因子..." % (idx + 1, total))

    df = pd.DataFrame(results).T
    if not df.empty:
        df = df.sort_values('IR', ascending=False)
    return df

# ── 报告格式化 ──────────────────────────────────────────────────
def format_ic_report(ic_df, top_n=20):
    NL = "\n"
    lines = ["# 因子 IC/IR 分析报告", ""]
    lines.append("数据范围: 2022-01 ~ 2026-05, 前视周期: 5天")
    lines.append("")
    lines.append("## Top %d 因子（按 IR 排序）" % top_n)
    lines.append("")
    lines.append("| 排名 | 因子 | IC均值 | IC_std | IR | 正IC占比 |")
    lines.append("|------|------|--------|--------|-----|---------|")

    for i, (name, row) in enumerate(ic_df.head(top_n).iterrows()):
        lines.append("| %d | %s | %.4f | %.4f | %.3f | %.1f%% |" % (
            i+1, name, row['IC_mean'], row['IC_std'], row['IR'], row['IC_positive_pct']*100))

    lines.append("")
    lines.append("## 负 IR 因子（可能反向有效）")
    lines.append("")
    neg = ic_df[ic_df['IR'] < 0].tail(10)
    lines.append("| 因子 | IC均值 | IR | 正IC占比 |")
    lines.append("|------|--------|-----|---------|")
    for name, row in neg.iterrows():
        lines.append("| %s | %.4f | %.3f | %.1f%% |" % (
            name, row['IC_mean'], row['IR'], row['IC_positive_pct']*100))

    lines.append("")
    lines.append("## 全部因子 IC 数据")
    lines.append("")
    lines.append(ic_df.to_string())

    return NL.join(lines)

# ── DeepSeek AI 分析 ────────────────────────────────────────────
def get_ai_analysis(ic_report_text, api_key=None):
    key = api_key or os.environ.get('DEEPSEEK_API_KEY', '')
    if not key:
        print("未设置 DEEPSEEK_API_KEY，跳过 AI 分析")
        print("  export DEEPSEEK_API_KEY=your_key_here")
        return None

    import requests

    prompt = """你是一位专业的量化因子分析师。以下是 A 股中证800成分股的因子 IC/IR 分析结果（2022-01~2026-05，前视5天）。

请完成以下分析（每个问题不超过3句话，简洁专业）：

1. **最有价值的5个因子**：选出 |IR| 最大的5个，说明经济逻辑
2. **因子冗余识别**：mom/mom/rev 系列哪些高度相关？建议保留哪个周期？
3. **权重方向建议**：IR>0 的因子正权重还是负权重？为什么？
4. **负 IR 因子**：IR<0 的因子是否应该反向使用？
5. **新因子思路**：基于现有因子覆盖的不足（量价/技术/波动率/动量/反转），建议2个可能有效的另类因子方向
6. **与 v13 策略关联**：当前 v13 策略（反转+量价+短周期）用了哪些因子？IC 分析支持这个方向吗？

以下是 IC 分析结果：

%s""" % ic_report_text

    print("\n调用 DeepSeek API...")
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 3000,
            },
            timeout=60,
        )
        result = resp.json()
        if 'choices' in result:
            return result['choices'][0]['message']['content']
        else:
            print("API 返回异常: %s" % str(result)[:500])
            return None
    except Exception as e:
        print("API 调用失败: %s" % e)
        return None

# ── 主函数 ──────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="AI 辅助因子分析")
    parser.add_argument("--calc-only", action="store_true", help="只计算 IC")
    parser.add_argument("--analyze", action="store_true", help="计算 + AI 分析")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--fwd", type=int, default=5)
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    args = parser.parse_args()

    t0 = time.time()

    # 1. 因子面板
    factors, close_panel = calc_factor_panels_from_db(args.start, args.end)

    # 2. IC 计算
    ic_df = calc_all_ic(factors, close_panel, fwd_period=args.fwd)

    # 3. 报告
    report = format_ic_report(ic_df, top_n=args.top_n)
    print("\n" + report)

    # 保存 CSV
    ic_path = REPORT_DIR / "ic_analysis_db.csv"
    ic_df.to_csv(ic_path)
    print("\nIC 结果已保存: %s" % ic_path)

    # 4. AI 分析
    if args.analyze or (not args.calc_only):
        ai_result = get_ai_analysis(report)
        if ai_result:
            print("\n" + "=" * 60)
            print("DeepSeek AI 因子分析")
            print("=" * 60)
            print(ai_result)

            ai_path = REPORT_DIR / "ai_factor_analysis.txt"
            with open(ai_path, 'w') as f:
                f.write("# AI 因子分析报告\n")
                f.write("# 生成时间: %s\n" % time.strftime('%Y-%m-%d %H:%M:%S'))
                f.write("# 数据范围: %s ~ %s\n\n" % (args.start, args.end))
                f.write(report)
                f.write("\n\n--- AI 分析 ---\n\n")
                f.write(ai_result)
            print("\nAI 分析已保存: %s" % ai_path)

    print("\n总耗时: %.1fs" % (time.time()-t0))

if __name__ == "__main__":
    main()

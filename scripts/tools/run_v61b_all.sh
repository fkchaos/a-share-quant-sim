#!/bin/bash
# v61b 参数扫描 — 批量运行所有组
# 用法: bash run_v61b_all.sh [组号]
# 不带参数运行全部，带组号只运行指定组

SCRIPT="/root/a-share-quant-sim/scripts/tools/v61b_param_scan_v2.py"
PYTHON="/root/.hermes/hermes-agent/venv/bin/python3"

if [ -n "$1" ]; then
    echo "=== 运行组 $1 ==="
    cd /root/a-share-quant-sim && $PYTHON $SCRIPT --group $1
else
    echo "=== 运行全部12组 ==="
    for i in $(seq 0 11); do
        echo ""
        echo "############################"
        echo "# 组 $i / 11"
        echo "############################"
        cd /root/a-share-quant-sim && $PYTHON $SCRIPT --group $i
    done
    echo ""
    echo "=== 全部完成 ==="
    echo "结果文件: /root/a-share-quant-sim/scripts/tools/v61b_results.json"
fi

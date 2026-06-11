#!/bin/bash
# run_all_scans.sh — 串行运行 v13 和 v20 选股参数扫描
# v13 跑完后自动跑 v20，避免内存不足

echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] 开始 v13 选股参数扫描 =====" | tee /root/data/backtest_results/all_scans.log

cd /root/a-share-quant-sim
python scripts/v13_select_param_scan.py > /root/data/backtest_results/v13_select_scan.log 2>&1
V13_EXIT=$?

echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] v13 扫描完成 (exit=$V13_EXIT) =====" | tee -a /root/data/backtest_results/all_scans.log

echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] 开始 v20 选股参数扫描 =====" | tee -a /root/data/backtest_results/all_scans.log

python scripts/v20_select_param_scan.py > /root/data/backtest_results/v20_select_scan.log 2>&1
V20_EXIT=$?

echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] v20 扫描完成 (exit=$V20_EXIT) =====" | tee -a /root/data/backtest_results/all_scans.log
echo "===== 全部完成 =====" | tee -a /root/data/backtest_results/all_scans.log

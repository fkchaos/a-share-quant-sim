# A股量化模拟交易系统

> 基于多因子评分的 A 股量化模拟交易系统，腾讯行情接口，回测与模拟盘共享交易逻辑。

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 快速开始

```bash
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim
pip install -e .                  # 安装依赖（pandas/numpy/requests）
python scripts/tools/init_project.py   # 一键初始化（建表+股票池+K线+账户）
python scripts/backtest/run_backtest.py --strategy v27  # 跑回测
python scripts/sim/account_runner.py --account-id 2 intraday_signal  # 模拟盘信号
```

## 文档

- [DEPLOY.md](docs/DEPLOY.md) — 部署指南
- [USER_MANUAL.md](docs/USER_MANUAL.md) — 用户手册
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 系统架构
- [RELEASE_NOTES.md](docs/RELEASE_NOTES.md) — 版本记录
- [TODO.md](docs/TODO.md) — 待办事项
- [策略注册表](docs/strategy/STRATEGY_REGISTRY.md) — 策略列表与状态
- [实验记录](docs/experiments/) — 因子调研与实验日志
- [QMT 实盘调研](docs/experiments/2026-06-21_qmt_research.md) — QMT 接入方案与 Linux 兼容性

## License

MIT — use freely, modify as needed. Not financial advice. Simulation only.

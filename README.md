# A股量化模拟交易系统

> 基于多因子评分的A股量化模拟交易系统，使用腾讯行情接口获取数据，支持止损、调仓、每日报告。

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 特性

- **多因子策略**: 31个技术因子（动量、反转、成交量、波动率、RSI、MACD、布林带等）等权合成评分
- **风控机制**: 单只止损 -20%，每20个交易日调仓一次
- **数据源**: 腾讯行情接口（不依赖 AKShare/Eastmoney，适用于网络受限环境）
- **完整交易模拟**: 包含佣金(0.03%)、印花税(0.1%)、滑点(0.1%)
- **自动报告**: 每日生成持仓报告 + 明日操作计划
- **定时执行**: 每工作日 18:00 自动运行（cron job）

## 策略表现（沪深300成分股回测）

| 指标 | 值 |
|------|-----|
| 年化收益率 | 21.86% |
| 夏普比率 | 1.11 |
| 最大回撤 | -24.37% |
| 持仓数量 | 10只（等权） |

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 初始化数据

```bash
# 1. 准备沪深300成分股列表（CSV格式，含"品种代码"和"品种名称"列）
cp hs300_constituents.csv data/

# 2. 首次运行会自动初始化日K线数据（需要网络访问 gtimg.cn）
cd scripts && python update_daily_data.py
```

### 运行模拟交易

```bash
# 手动执行一天的模拟交易
python scripts/sim_daily.py

# 仅更新行情数据
python scripts/update_daily_data.py

# 检查数据状态
python scripts/update_daily_data.py --check

# 更新到指定日期
python scripts/update_daily_data.py --date 20260529
```

### 配置定时任务

```bash
# 每工作日 18:00 自动执行
crontab -e
# 添加: 0 18 * * 1-5 cd /path/to/project && python scripts/sim_daily.py
```

## 项目结构

```
a-share-quant-sim/
├── scripts/
│   ├── sim_account.py          # 核心引擎: SimAccount类, 因子计算, 评分
│   ├── sim_daily.py            # 每日交易脚本: 止损/调仓/报告
│   ├── update_daily_data.py    # 数据更新: 腾讯行情接口 → 本地CSV
│   └── hs300_constituents.csv  # 沪深300成分股
├── data/
│   ├── daily/                  # 日K线数据 (每只股票一个CSV)
│   │   ├── 000001.csv
│   │   ├── 000725.csv
│   │   └── ...
│   ├── portfolio/              # 账户状态
│   │   ├── account.json        # 持仓/现金/交易记录/净值历史
│   │   ├── trade_count.txt     # 调仓计数器
│   │   └── daily_YYYYMMDD.json # 每日报告
│   └── signals/                # 因子信号缓存
│       └── signal_YYYYMMDD.json
├── references/
│   └── api-notes.md            # API接口笔记
├── requirements.txt
├── LICENSE
└── README.md
```

## 数据格式

### 日K线 CSV (`data/daily/{code}.csv`)

```csv
date,open,high,low,close,volume,amount
2026-01-04,10.50,10.80,10.30,10.65,1234567,1.31e+09
```

### 账户状态 (`data/portfolio/account.json`)

```json
{
  "cash": 487033.86,
  "holdings": {
    "603986": {"shares": 100, "cost_price": 514.18, "entry_date": "2026-05-27"}
  },
  "trade_log": [...],
  "nav_history": [...]
}
```

## 因子列表（31个）

| 类别 | 因子 |
|------|------|
| 动量 | mom_5, mom_10, mom_20, mom_60, mom_120 |
| 反转 | rev_3, rev_5, rev_10 |
| 波动率 | vol_10, vol_20, vol_60 (负权重) |
| 成交量 | vol_ratio_5, vol_ratio_20, amount_ratio |
| RSI | rsi_6, rsi_14, rsi_28 |
| 趋势 | macd, boll_pos, rel_strength |
| 其他 | skew |

自定义因子权重: 修改 `scripts/sim_account.py` → `generate_scores()` 中的 `weights` 字典。

## 自定义参数

编辑 `scripts/sim_daily.py`:

```python
REBAL_FREQ = 20        # 调仓频率（交易日）
STOP_LOSS = 0.20       # 止损线
TOP_N = 10             # 持仓数量
SLIPPAGE_RATE = 0.001  # 滑点
```

## 每日报告示例

```
======================================================================
v3 模拟交易 - 2026-05-28 18:00
======================================================================

📥 更新行情数据...
  ✅ 数据更新完成

  ============================账户状态============================
  现金:       ¥   487,034
  持仓市值:   ¥   510,740
  总净值:     ¥   997,774
  总收益率:       -0.22%
  持仓数量:   7 只
  已交易次数: 14
  调仓计数:   19/20

  ⚠️  止损风险预警:
    ✅ 所有持仓安全，无止损风险

  📌 关注持仓:
    📉 跌幅最大: 000725 京东方A (-3.51%)
    📈 涨幅最大: 603986 兆易创新 (+1.23%)

  📊 收盘报告
  日期:       2026-05-28
  总净值:     ¥997,774
  今日收益:   -0.16%
  总收益率:   -0.22%
  持仓数量:   7 只
  现金占比:   48.8%
======================================================================
```

## 注意事项

- **仅供学习研究，不构成投资建议**
- 模拟交易，不涉及真实资金
- 数据源为腾讯行情接口，可能存在延迟
- 因子策略基于历史数据回测，不代表未来收益

## License

MIT License - 详见 [LICENSE](LICENSE)

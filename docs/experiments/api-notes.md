# API 接口笔记

## 腾讯行情接口

### 日K线（前复权）

```
http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx_code},day,,,{days},qfq
```

- `tx_code` = `sh{code}` 上交所（6/9开头）, `sz{code}` 深交所（0/2/3开头）
- 返回 JSON，key 是 `qfqday`（不是 `day`）
- 嵌套路径: `data[不带前缀的stock_code]['qfqday']`
- 每行: `[日期, 开盘, 收盘, 最高, 最低, 成交量(手)]`

### 实时行情

```
http://qt.gtimg.cn/q={tx_code}
```

返回 `~` 分隔的文本格式，字段索引：
- [1] 名称, [2] 代码, [3] 现价, [4] 昨收, [5] 今开
- [6] 成交量(手), [32] 涨跌幅, [33] 最高, [34] 最低, [37] 成交额(万)

## 常见坑

1. **Eastmoney/AKShare 被墙**: 服务器环境 AKShare 连接 push2his.eastmoney.com 时返回 RemoteDisconnected，腾讯接口可用
2. **qfqday vs day**: 必须用 `qfqday`（前复权），不能用 `day`（不复权）
3. **代码前缀**: 返回的 JSON key 是不带 sh/sz 前缀的纯数字代码，查询时需要加前缀
4. **频率限制**: 建议 0.15s 间隔，280只股票约 5 分钟

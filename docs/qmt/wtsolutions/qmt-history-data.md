# 讯投QMT使用小技巧: 历史行情数据下载与本地缓存方案

> 来源: https://invest.wtsolutions.cn/posts/qmt-history-data/

## 概述

写策略离不开历史数据：算指标要 K 线，做回测要序列，分析 tick 要分笔。但 QMT 的历史数据不是天然就躺在本地的——它需要你主动「下载」并「订阅」。很多新手第一次跑策略报「数据长度不足」或者拿到一堆空值，十有八九就是没下载历史数据，或者用错了取数函数。

本文把下载、订阅、取数、缓存四件事一次讲清楚。

## 一、下载历史数据：download_history_data

QMT 的历史数据是按需下载的。你不去下载，`get_history_data` / `get_market_data_ex` 拿到的可能就是空或者很短的片段。

```python
def init(C):
    stock = '600000.SH'
    # 下载日线历史数据，从2020-01-01到今天
    ContextInfo.download_history_data(stock, '1d', '2020-01-01', '')
```

```
ContextInfo.download_history_data(stockcode, period, start_time, end_time)
```

### 下载时机

建议在 `init` 中下载，而非 `handlebar` 中。

### 批量下载的小技巧

下载是同步且较慢的操作，几百只股票一个个下会很卡。可以做成「下载进度可恢复」：

```python
def download_batch(C, stocks, period='1d', start='2020-01-01'):
    done = []
    for i, s in enumerate(stocks):
        try:
            C.download_history_data(s, period, start, '')
            done.append(s)
        except Exception as e:
            print(f"下载失败 {s}: {e}")
        if i % 50 == 0:
            print(f"进度 {i}/{len(stocks)}")
    return done
```

## 二、订阅行情：set_universe vs subscribe_quote

下载的是**历史**数据，实盘要拿**实时**行情还需要订阅。

### set_universe（订阅K线行情）

```python
def init(C):
    C.set_universe(['600000.SH', '000001.SZ'])
```

`set_universe` 订阅之后，`handlebar` 才会被这些品种的 K 线推动，`get_market_data_ex` 才能拿到实时值。这是绝大多数策略用的方式。

### subscribe_quote（订阅实时tick/盘口）

```python
def init(C):
    C.subscribe_quote('600000.SH', 'tick', count=10)
```

适合需要逐笔或盘口深度数据的策略。注意全市场订阅 tick（全推）数据量巨大，按需订阅即可。

订阅和下载是两件事：下载管历史，订阅管实时。实盘策略通常两者都要。

## 三、取数：get_history_data 与 get_market_data_ex

### get_history_data（取单个品种的某个字段序列）

来自官方示例的写法：

```python
data = C.get_history_data(30, '1d', 'close', dividend_type='front_ratio')
close_list = data['600000.SH']   # 长度30的收盘价序列
```

### get_market_data_ex（多品种多字段的 DataFrame）

```python
bars = C.get_market_data_ex(['close', 'volume'], ['600000.SH', '000001.SZ'],
                            period='1d', count=60)
df = bars['600000.SH']   # 一个DataFrame，含close、volume列
```

适合一次取多只股票、多个字段、要 DataFrame 形态的场景。

## 四、本地缓存：避免重复取数

`get_history_data` 每次调用都会走一次数据接口，在 `handlebar` 高频路径里反复取 60 日数据非常浪费。建议把算好的指标或取好的序列缓存起来，只在需要时刷新。

```python
class a(): pass
A = a()

def init(C):
    A.cache = {}          # stock -> {'data':..., 'barpos':...}
    C.set_universe(['600000.SH'])

def handlebar(C):
    if not C.is_last_bar():
        return
    stock = '600000.SH'
    barpos = C.barpos
    # 同一根bar只取一次数
    if A.cache.get(stock, {}).get('barpos') != barpos:
        close = C.get_history_data(60, '1d', 'close', dividend_type='front_ratio')[stock]
        A.cache[stock] = {'data': close, 'barpos': barpos}
    close = A.cache[stock]['data']
    ma60 = sum(close) / len(close)
    # ...
```

这种「按 barpos 缓存」的写法，保证一根 K 线内多次访问只取数一次，是实盘性能优化的关键。

### 落地到本地文件/数据库

如果数据量更大（比如全市场日线、多年 tick），可以下载后落到本地 parquet/csv 或 SQLite，下次直接读本地，不再依赖 QMT 接口：

```python
import pandas as pd

def save_local(stock, df, path='./data/'):
    df.to_parquet(f"{path}{stock.replace('.','_')}.parquet")

def load_local(stock, path='./data/'):
    try:
        return pd.read_parquet(f"{path}{stock.replace('.','_')}.parquet")
    except FileNotFoundError:
        return None
```

## 五、常见坑

* `get_history_data` 不会自动下载数据，必须先调用 `download_history_data`
* 下载应在 `init` 中完成，而非 `handlebar` 中

## 总结

历史数据的流程是：**download（下载历史） → set_universe/subscribe（订阅实时） → get_history_data/get_market_data_ex（取数） → 缓存（按 barpos 或落本地）**。把这四步顺序记牢，再配上按 barpos 的缓存，策略的数据层就稳了。

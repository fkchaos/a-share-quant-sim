# 数据源中间层设计

> 日期：2026-08-04
> 目标：标准化数据格式，支持多数据源 fallback

---

## 1. 标准化字段定义

所有 provider 输出必须符合以下标准：

### 1.1 日K线（daily_kline）

| 字段 | 标准单位 | 类型 | 说明 |
|------|----------|------|------|
| `code` | 6位数字 | str | 如 `600519`，不含市场前缀 |
| `date` | `YYYY-MM-DD` | str | 如 `2024-01-02` |
| `open` | 元（人民币） | float | 开盘价 |
| `high` | 元（人民币） | float | 最高价 |
| `low` | 元（人民币） | float | 最低价 |
| `close` | 元（人民币） | float | 收盘价 |
| `volume` | **股** | int | 成交量 |
| `amount` | **元** | float | 成交额 |
| `turnover` | **百分比** | float | 换手率，如 1.5 表示 1.5% |
| `pct_change` | **百分比** | float | 涨跌幅，如 3.2 表示 3.2% |
| `tradestatus` | 0/1 | int | 1=正常交易，0=停牌 |
| `is_st` | 0/1 | int | 1=ST股，0=正常 |

### 1.2 流通股本（float_shares）

| 字段 | 标准单位 | 类型 | 说明 |
|------|----------|------|------|
| `code` | 6位数字 | str | 股票代码 |
| `float_shares` | **股** | int | 流通股本 |

### 1.3 指数成分股（index_components）

| 字段 | 标准单位 | 类型 | 说明 |
|------|----------|------|------|
| `code` | 6位数字 | str | 股票代码 |
| `index_code` | 指数代码 | str | 如 `000852`=中证1000 |
| `in_date` | `YYYY-MM-DD` | str | 调入日期（可选） |
| `out_date` | `YYYY-MM-DD` | str | 调出日期（可选） |

---

## 2. Provider 接口定义

```python
# core/data_provider.py

from abc import ABC, abstractmethod
from typing import Optional, List
from dataclasses import dataclass
import pandas as pd

@dataclass
class KlineData:
    """标准化K线数据"""
    code: str
    date: str
    open: float      # 元
    high: float      # 元
    low: float       # 元
    close: float     # 元
    volume: int      # 股
    amount: float    # 元
    turnover: float  # 百分比
    pct_change: float # 百分比
    tradestatus: int # 0=停牌, 1=正常
    is_st: int       # 0=正常, 1=ST

class DataProvider(ABC):
    """数据源抽象接口"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称"""
        pass
    
    @abstractmethod
    def get_daily_kline(
        self, 
        codes: List[str], 
        start_date: str, 
        end_date: str
    ) -> pd.DataFrame:
        """获取日K线数据
        
        Returns:
            DataFrame，列名符合标准字段定义
            index: date (str, YYYY-MM-DD)
            columns: code (str, 6位)
            每个单元格是 KlineData 或 dict
        """
        pass
    
    @abstractmethod
    def get_float_shares(
        self, 
        codes: Optional[List[str]] = None,
        date: Optional[str] = None  # 指定日期获取（point-in-time）
    ) -> pd.DataFrame:
        """获取流通股本
        
        Returns:
            DataFrame: code, float_shares (股)
        """
        pass
    
    @abstractmethod
    def get_index_components(
        self, 
        index_code: str, 
        date: Optional[str] = None
    ) -> List[str]:
        """获取指数成分股"""
        pass
    
    def health_check(self) -> bool:
        """检查数据源是否可用"""
        try:
            self.get_daily_kline(['000001'], '2024-01-02', '2024-01-02')
            return True
        except Exception:
            return False
```

---

## 3. 数据格式转换层

每个 provider 内部负责将原始数据转换为标准格式：

### 3.1 TencentProvider 转换规则

```python
# 原始格式
volume = raw_volume * 100          # 手 → 股
amount = raw_amount                # 元（已是标准）
turnover = None                    # 腾讯无此字段
tradestatus = None                 # 腾讯无此字段
is_st = None                       # 腾讯无此字段
```

### 3.2 BaoStockProvider 转换规则

```python
# 原始格式
code = raw_code.split('.')[1]      # sh.600519 → 600519
volume = raw_volume                # 股（已是标准）
amount = raw_amount                # 元（已是标准）
turnover = raw_turn                # 百分比（已是标准）
tradestatus = int(raw_tradestatus) # 字符串转int
is_st = int(raw_isST)              # 字符串转int
close = float(raw_close)           # 字符串转float
```

---

## 4. Provider 管理器

```python
# core/provider_manager.py

from enum import Enum
from typing import Optional
import yaml

class ProviderRole(Enum):
    PRIMARY = "primary"      # 主数据源
    BACKUP = "backup"        # 备用数据源
    OVERRIDE = "override"    # 手动指定（优先级最高）

class ProviderManager:
    """数据源管理器"""
    
    def __init__(self, config_path: str = None):
        self.providers = {}  # name -> DataProvider
        self.config = self._load_config(config_path)
        self._register_builtin()
    
    def register(self, name: str, provider: DataProvider, role: ProviderRole = None):
        """注册数据源"""
        self.providers[name] = provider
        if role:
            self.config[role.value] = name
    
    def get_provider(self, role: ProviderRole = None) -> DataProvider:
        """获取数据源
        
        优先级：override > primary > backup
        """
        if role and role.value in self.config:
            name = self.config[role.value]
            return self.providers.get(name)
        
        # fallback 顺序
        for r in [ProviderRole.OVERRIDE, ProviderRole.PRIMARY, ProviderRole.BACKUP]:
            name = self.config.get(r.value)
            if name and name in self.providers:
                provider = self.providers[name]
                if provider.health_check():
                    return provider
        
        raise RuntimeError("No available data provider")
    
    def get_daily_kline(self, codes, start_date, end_date) -> pd.DataFrame:
        """带 fallback 的数据获取"""
        last_error = None
        for provider in self._get_fallback_chain():
            try:
                return provider.get_daily_kline(codes, start_date, end_date)
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(f"All providers failed: {last_error}")
```

---

## 5. 配置文件

```yaml
# config/data_sources.yaml

primary: tencent
backup: baostock
# override: null  # 手动指定时设置

providers:
  tencent:
    module: core.providers.tencent
    class: TencentProvider
    enabled: true
    options:
      timeout: 30
      retry: 3
  
  baostock:
    module: core.providers.baostock
    class: BaoStockProvider
    enabled: true
    options:
      cache_ttl: 3600  # 缓存1小时
  
  akshare:
    module: core.providers.akshare
    class: AkShareProvider
    enabled: false  # 暂不启用
    options:
      rate_limit: 0.5  # 请求间隔500ms
```

---

## 6. 使用示例

```python
# 6.1 默认用 primary，fallback 到 backup
from core.provider_manager import ProviderManager

pm = ProviderManager()
df = pm.get_daily_kline(['600519', '000001'], '2024-01-01', '2024-12-31')

# 6.2 手动指定 provider
pm.set_override('baostock')
df = pm.get_daily_kline(...)

# 6.3 临时切换（不改全局配置）
df = pm.get_daily_kline(..., provider='baostock')

# 6.4 在策略中使用（推荐）
# v61b_risk_scan.py
data = pm.get_daily_kline(codes, start_date, end_date)
# data 已经包含标准格式的 turnover 字段
turn_5 = data['turnover'].rolling(5).mean()  # 直接用
```

---

## 7. 实现计划

### Phase 1：接口定义 + BaoStock Provider（1天）
- [ ] 创建 `core/data_provider.py`（接口）
- [ ] 创建 `core/providers/baostock.py`（实现）
- [ ] 单元测试

### Phase 2：Tencent Provider 封装（0.5天）
- [ ] 创建 `core/providers/tencent.py`
- [ ] 将现有腾讯 API 代码封装
- [ ] 格式转换

### Phase 3：Provider Manager（0.5天）
- [ ] 创建 `core/provider_manager.py`
- [ ] 配置文件解析
- [ ] Fallback 逻辑

### Phase 4：集成到模拟盘（1天）
- [ ] 修改 `v61b_risk_scan.py`
- [ ] 修改 `account_runner.py`
- [ ] 验证信号一致性

---

## 8. 验证标准

- [ ] BaoStock 返回的数据符合标准字段定义
- [ ] Tencent 返回的数据符合标准字段定义（缺失字段标记为 None）
- [ ] Fallback 机制在主源失败时自动切换
- [ ] 模拟盘信号结果与改造前一致（turnover 有差异可接受）
- [ ] 历史数据对比：两种数据源的 close/volume 一致

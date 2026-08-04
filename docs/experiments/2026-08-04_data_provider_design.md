# 设计文档：DataProvider 抽象层 + 双数据源

> 日期：2026-08-04
> 优先级：P0
> 目标：消除数据源单一风险，接入 Tushare/AkShare 双源 + fallback

---

## 1. 背景

当前项目数据全靠自写腾讯行情 requests，存在系统性风险：
- 腾讯没有公开官方 API，本质逆向/爬虫
- 随时可能改版、限流、封 IP
- 已踩 volume 单位"手 vs 股"坑

## 2. 方案设计

### 2.1 接口定义

```python
# core/data_provider.py

from abc import ABC, abstractmethod
from typing import Optional, List
import pandas as pd

class DataProvider(ABC):
    """数据源抽象接口"""
    
    @abstractmethod
    def get_daily_kline(self, codes: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """获取日K线数据
        
        Returns:
            DataFrame with columns: [code, date, open, high, low, close, volume, amount]
            volume 单位: 股（非手）
            amount 单位: 元
        """
        pass
    
    @abstractmethod
    def get_float_shares(self, codes: Optional[List[str]] = None) -> pd.Series:
        """获取流通股本（股）
        
        Returns:
            Series: index=code, value=float_shares（股）
        """
        pass
    
    @abstractmethod
    def get_index_components(self, index_code: str, date: str) -> List[str]:
        """获取指数成分股（point-in-time）
        
        Args:
            index_code: 指数代码（如 '000905' = 中证500, '000852' = 中证1000）
            date: 日期（如 '2024-06-30'）
        
        Returns:
            成分股代码列表
        """
        pass
```

### 2.2 实现类

```
TencentProvider（当前）  →  主用，已验证
AkShareProvider          →  备用，东方财富源
BaoStockProvider         →  备用，学术级，稳定
TushareProvider          →  可选，需积分
```

### 2.3 Fallback 机制

```python
class CompositeDataProvider(DataProvider):
    """组合数据源，支持自动 fallback"""
    
    def __init__(self, providers: List[DataProvider]):
        self.providers = providers
    
    def get_daily_kline(self, codes, start_date, end_date):
        for provider in self.providers:
            try:
                df = provider.get_daily_kline(codes, start_date, end_date)
                if df is not None and len(df) > 0:
                    return df
            except Exception as e:
                print(f"[Fallback] {provider.__class__.__name__} failed: {e}")
                continue
        raise RuntimeError("All data providers failed")
```

### 2.4 数据验证层

```python
class DataValidator:
    """数据质量验证"""
    
    @staticmethod
    def validate_kline(df: pd.DataFrame) -> bool:
        """验证K线数据质量"""
        checks = [
            df['volume'].min() >= 0,           # 成交量非负
            df['close'].min() > 0,             # 收盘价正数
            df['volume'].max() < 1e12,         # 成交量上限（防止手/股混淆）
            not df.duplicated(subset=['code', 'date']).any(),  # 无重复
        ]
        return all(checks)
    
    @staticmethod
    def validate_volume_unit(df: pd.DataFrame, expected_unit='股') -> bool:
        """验证成交量单位"""
        if expected_unit == '股':
            # 全市场单日总成交量应在 100亿-500亿股之间
            daily_total = df.groupby('date')['volume'].sum()
            return daily_total.between(1e10, 5e11).all()
        return True
```

## 3. 迁移路径

### Phase 1：接口抽象（1-2天）
- 创建 `core/data_provider.py` 定义接口
- 将现有腾讯 API 封装为 `TencentProvider`
- 不改动现有代码，仅封装

### Phase 2：双源接入（2-3天）
- 实现 `AkShareProvider`（行情 + 成分股）
- 实现 `CompositeDataProvider`（fallback）
- 在 `update_daily_data_async.py` 中支持多源

### Phase 3：验证与切换（1-2天）
- 对比双源数据一致性
- 添加数据验证层
- 逐步切换到新接口

## 4. 验证标准

- [ ] 新接口返回的数据与腾讯源一致（差异 < 0.01%）
- [ ] fallback 机制在主源失败时自动切换
- [ ] volume 单位统一为"股"
- [ ] 数据验证层能检测手/股混淆

## 5. 风险

- AkShare 可能有频率限制 → 需要延时控制
- BaoStock 数据可能有延迟 → 作为 fallback 而非主源
- 迁移期间需要保持旧接口可用 → 渐进式切换

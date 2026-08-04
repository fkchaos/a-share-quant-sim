"""数据源管理器

支持：
- primary: 主数据源
- backup: 备用数据源（primary失败时fallback）
- override: 手动指定（优先级最高）
"""
import os
import yaml
from enum import Enum
from typing import Optional, List, Dict
import pandas as pd

from core.data_provider import DataProvider


class ProviderRole(Enum):
    PRIMARY = "primary"
    BACKUP = "backup"
    OVERRIDE = "override"


class ProviderManager:
    """数据源管理器"""
    
    def __init__(self, config_path: str = None):
        self.providers: Dict[str, DataProvider] = {}
        self.config: Dict[str, str] = {
            'primary': 'tencent',
            'backup': 'baostock',
            'override': None,
        }
        self._fallback_chain = None
        
        # 加载配置
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                'config', 'data_sources.yaml'
            )
        self._load_config(config_path)
    
    def _load_config(self, config_path: str):
        """加载配置文件"""
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = yaml.safe_load(f)
                if cfg:
                    for role in ['primary', 'backup', 'override']:
                        if role in cfg:
                            self.config[role] = cfg[role]
            except Exception:
                pass
    
    def register(self, name: str, provider: DataProvider):
        """注册数据源"""
        self.providers[name] = provider
        self._fallback_chain = None  # 重置缓存
    
    def set_override(self, name: Optional[str]):
        """设置手动指定的数据源（优先级最高）"""
        self.config['override'] = name
        self._fallback_chain = None
    
    def clear_override(self):
        """清除手动指定"""
        self.config['override'] = None
        self._fallback_chain = None
    
    def _get_fallback_chain(self) -> List[DataProvider]:
        """获取 fallback 链（带缓存）"""
        if self._fallback_chain is not None:
            return self._fallback_chain
        
        chain = []
        seen = set()
        
        # 优先级：override > primary > backup
        for role in [ProviderRole.OVERRIDE, ProviderRole.PRIMARY, ProviderRole.BACKUP]:
            name = self.config.get(role.value)
            if name and name in self.providers and name not in seen:
                provider = self.providers[name]
                chain.append(provider)
                seen.add(name)
        
        self._fallback_chain = chain
        return chain
    
    def get_provider(self, name: Optional[str] = None) -> DataProvider:
        """获取指定数据源
        
        Args:
            name: 数据源名称，None=按 fallback 链顺序
        """
        if name:
            if name in self.providers:
                return self.providers[name]
            raise ValueError(f"Provider '{name}' not registered")
        
        # 按 fallback 链获取可用的
        for provider in self._get_fallback_chain():
            try:
                if provider.health_check():
                    return provider
            except Exception:
                continue
        
        raise RuntimeError("No available data provider")
    
    def get_daily_kline(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        provider: Optional[str] = None
    ) -> pd.DataFrame:
        """带 fallback 的数据获取
        
        Args:
            codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            provider: 指定数据源，None=自动fallback
        """
        if provider:
            return self.get_provider(provider).get_daily_kline(codes, start_date, end_date)
        
        last_error = None
        for p in self._get_fallback_chain():
            try:
                if p.health_check():
                    return p.get_daily_kline(codes, start_date, end_date)
            except Exception as e:
                last_error = e
                continue
        
        raise RuntimeError(f"All providers failed. Last error: {last_error}")
    
    def get_float_shares(
        self,
        codes: Optional[List[str]] = None,
        date: Optional[str] = None,
        provider: Optional[str] = None
    ) -> pd.DataFrame:
        """带 fallback 的流通股本获取"""
        if provider:
            return self.get_provider(provider).get_float_shares(codes, date)
        
        last_error = None
        for p in self._get_fallback_chain():
            try:
                if p.health_check():
                    df = p.get_float_shares(codes, date)
                    if df is not None and len(df) > 0:
                        return df
            except Exception as e:
                last_error = e
                continue
        
        raise RuntimeError(f"All providers failed for float_shares. Last error: {last_error}")
    
    def health_check_all(self) -> Dict[str, bool]:
        """检查所有已注册数据源的健康状态"""
        results = {}
        for name, provider in self.providers.items():
            try:
                results[name] = provider.health_check()
            except Exception:
                results[name] = False
        return results
    
    def list_providers(self) -> List[str]:
        """列出所有已注册的数据源"""
        return list(self.providers.keys())

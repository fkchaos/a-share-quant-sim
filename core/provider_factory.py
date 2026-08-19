# -*- coding: utf-8 -*-
"""
core/provider_factory.py — Provider工厂函数
=============================================
根据配置创建对应的交易Provider。

Python 3.6.8 兼容。
"""

import logging

logger = logging.getLogger(__name__)


def create_provider(config):
    """根据配置创建交易Provider
    
    config: {
        'provider': 'sim' | 'qmt',
        'sim': { 'account_id': ..., 'portfolio_dir': ..., 'initial_cash': ... },
        'qmt': { ... },  # 未来QMT配置
    }
    
    返回: TradingProvider实例
    """
    provider_type = config.get('provider', 'sim')

    if provider_type == 'sim':
        from core.providers.sim_provider import SimProvider
        provider = SimProvider()
        sim_config = config.get('sim', {})
        provider.initialize(sim_config)
        return provider

    elif provider_type == 'qmt':
        raise NotImplementedError(
            "QMT Provider 尚未实现。请先确认券商政策后再开发。"
        )

    else:
        raise ValueError("Unknown provider: {}".format(provider_type))

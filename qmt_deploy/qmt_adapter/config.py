#coding:gbk
"""
QMT公共配置
所有策略文件import这个模块获取统一配置
"""

# 行情数据配置
MARKET_CONFIG = {
    'period': '1d',        # K线周期
    'count': 30,           # 获取K线数量
    'subscribe': True,     # 自动下载数据（False=只读本地）
    'dividend_type': 'front',  # 复权方式：front=前复权
}

# 默认账户配置
ACCOUNT_CONFIG = {
    'account_id': 'testS',
    'account_type': 'stock',
}

"""
Position — 单一持仓的领域模型。

替代裸 dict {'shares': int, 'cost_price': float, 'entry_date': str}，
提供类型安全、IDE 补全、封装加权平均成本计算。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    code: str
    shares: int
    cost_price: float
    entry_date: str

    @property
    def is_empty(self) -> bool:
        return self.shares <= 0

    @property
    def total_cost(self) -> float:
        return self.shares * self.cost_price

    def market_value(self, price: float) -> float:
        return self.shares * price

    def pnl(self, price: float) -> float:
        if self.cost_price <= 0:
            return 0.0
        return (price - self.cost_price) / self.cost_price

    def add_shares(self, new_shares: int, price: float) -> 'Position':
        """加仓 — 返回新 Position（加权平均成本）。"""
        if new_shares <= 0:
            return self
        total_shares = self.shares + new_shares
        avg_price = (self.shares * self.cost_price + new_shares * price) / total_shares
        return Position(
            code=self.code,
            shares=total_shares,
            cost_price=avg_price,
            entry_date=self.entry_date,
        )

    def remove_all(self) -> 'Position':
        """清仓。"""
        return Position(
            code=self.code,
            shares=0,
            cost_price=self.cost_price,
            entry_date=self.entry_date,
        )

    # ── 与旧 dict 格式的兼容转换 ──

    def to_dict(self) -> dict:
        return {
            'shares': self.shares,
            'cost_price': self.cost_price,
            'entry_date': self.entry_date,
        }

    @staticmethod
    def from_dict(code: str, d: dict) -> 'Position':
        return Position(
            code=code,
            shares=int(d['shares']),
            cost_price=float(d['cost_price']),
            entry_date=str(d['entry_date']),
        )


# ── Holdings 辅助函数 ──

def holdings_to_dict(holdings: dict) -> dict:
    """{code: Position} → {code: dict}（序列化用）"""
    return {code: pos.to_dict() for code, pos in holdings.items()}


def holdings_from_dict(d: dict) -> dict:
    """{code: dict} → {code: Position}（反序列化用）"""
    return {code: Position.from_dict(code, v) for code, v in d.items()}


def copy_holdings(holdings: dict) -> dict:
    """浅拷贝 holdings dict，每个 Position 是不可变的（重建即可）。"""
    return {code: Position(code=pos.code, shares=pos.shares,
                           cost_price=pos.cost_price, entry_date=pos.entry_date)
            for code, pos in holdings.items()}

"""StrategyValidator 接口（本轮只留缝，不实现）。

数据契约：任何要"上升为策略"的逻辑（固化的 prompt 版本、阈值规则、
factor 组合）在启用前必须过一个 StrategyValidator 实现——未来由量化层 P3
（backend/quant/backtest.py 的 walk-forward）或外部项目提供。多重检验校正
（试了多少次必须计入）是验证器实现方的责任，不是调用方的。

strategy_spec 契约（dict）:
    name: str                     策略名
    prompt_version: str | None    若为 prompt 固化
    rules: list                   结构化规则/因子描述（由实现方定义粒度）
    sample_window: str | None     声明的样本窗口
verdict ∈ {'pass', 'fail', 'not_validated'}
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ValidationReport:
    verdict: str                  # pass | fail | not_validated
    detail: str = ""
    metrics: dict = field(default_factory=dict)


class StrategyValidator(Protocol):
    def validate(self, strategy_spec: dict) -> ValidationReport: ...


class NullValidator:
    """占位实现：显式拒绝背书。存在的意义是让调用方今天就能写依赖注入代码。"""

    def validate(self, strategy_spec: dict) -> ValidationReport:
        return ValidationReport(
            verdict="not_validated",
            detail="尚无验证器实现——策略逻辑未经 walk-forward 验证，不得视为已确认",
        )

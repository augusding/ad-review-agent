"""
Agent 编排层数据结构。

ToolResultSet — 聚合所有 Tool 执行结果
Decision     — 决策引擎输出
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.schemas.result import ReviewVerdict
from src.schemas.violation import ViolationItem, ViolationDimension
from src.schemas.tool_io import (
    TextCheckerOutput,
    ImageCheckerOutput,
    LandingPageCheckerOutput,
    QualificationCheckerOutput,
    PlatformRuleCheckerOutput,
    ConsistencyCheckOutput,
)


@dataclass
class ToolResultSet:
    """聚合所有 Tool 的执行结果，供决策引擎和结果构建器使用。"""

    # ── 各 Tool 输出（None 表示该维度未执行） ──
    text: TextCheckerOutput | None = None
    image: ImageCheckerOutput | None = None
    landing_page: LandingPageCheckerOutput | None = None
    qualification: QualificationCheckerOutput | None = None
    platform_rule: PlatformRuleCheckerOutput | None = None
    consistency: ConsistencyCheckOutput | None = None

    # ── 维度执行状态 ──
    checked_dimensions: list[ViolationDimension] = field(default_factory=list)
    skipped_dimensions: list[ViolationDimension] = field(default_factory=list)
    skip_reasons: dict[str, str] = field(default_factory=dict)

    # ── 汇总信息 ──
    all_violations: list[ViolationItem] = field(default_factory=list)
    has_fallback: bool = False
    fallback_reasons: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


@dataclass
class Decision:
    """决策引擎输出：综合判定结论、置信度和理由列表。"""

    verdict: ReviewVerdict = ReviewVerdict.REVIEW
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)

"""
审核结果数据模型。
"""
from enum import Enum
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from .violation import ViolationItem, ViolationDimension


class ReviewVerdict(str, Enum):
    PASS = "pass"
    REVIEW = "review"      # 需要人工复核
    RETURNED = "returned"  # 退回修改（有明确修改建议）
    REJECT = "reject"      # 保留用于极严重违规（涉政/色情/暴力），由人工标记


class ReviewResult(BaseModel):
    """广告素材审核结果。"""
    request_id: str = Field(description="对应请求的 ID")
    verdict: ReviewVerdict = Field(description="审核结论")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 0-1")
    violations: list[ViolationItem] = Field(
        default_factory=list,
        description="违规项列表，pass 时为空"
    )
    reason: str = Field(description="面向广告主的说明（中文）")
    checked_dimensions: list[ViolationDimension] = Field(
        default_factory=list,
        description="实际执行了检测的维度"
    )
    skipped_dimensions: list[ViolationDimension] = Field(
        default_factory=list,
        description="被跳过的维度"
    )
    skip_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="维度被跳过的原因"
    )
    processing_ms: int = Field(default=0, description="处理耗时（毫秒）")
    model_used: str = Field(default="", description="使用的模型")
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)
    is_fallback: bool = Field(default=False, description="是否为降级结果")
    fallback_reason: Optional[str] = Field(default=None, description="降级原因")
    reviewer_hint: str = Field(
        default="",
        description="面向审核员的复核提示，包含各维度检测结果摘要和推荐结论"
    )

    @classmethod
    def human_review_required(
        cls,
        request_id: str,
        reason: str,
        fallback_reason: str,
    ) -> "ReviewResult":
        """创建一个要求人工复核的降级结果。"""
        return cls(
            request_id=request_id,
            verdict=ReviewVerdict.REVIEW,
            confidence=0.0,
            reason=reason,
            is_fallback=True,
            fallback_reason=fallback_reason,
        )

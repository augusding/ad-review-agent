"""
违规项数据模型。
"""
from enum import Enum
from pydantic import BaseModel, Field


class ViolationDimension(str, Enum):
    TEXT_VIOLATION = "text_violation"
    IMAGE_SAFETY = "image_safety"
    LANDING_PAGE = "landing_page"
    QUALIFICATION = "qualification"
    PLATFORM_RULE = "platform_rule"
    CONSISTENCY = "consistency"


class ViolationSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionRequired(str, Enum):
    """面向广告主的问题分级。"""
    BLOCKING = "blocking"        # 阻断投放，必须修改（违反广告法/无资质）
    RECOMMENDED = "recommended"  # 建议修改（平台规范/一致性问题）
    ADVISORY = "advisory"        # 提示关注（边界案例/可能误导用户）


# severity → action_required 映射
_SEVERITY_TO_ACTION = {
    ViolationSeverity.HIGH: ActionRequired.BLOCKING,
    ViolationSeverity.MEDIUM: ActionRequired.RECOMMENDED,
    ViolationSeverity.LOW: ActionRequired.ADVISORY,
}


class ViolationItem(BaseModel):
    """单个违规项。"""
    dimension: ViolationDimension = Field(description="违规所属的审核维度")
    description: str = Field(description="违规内容的具体描述")
    regulation_ref: str = Field(description="所违反的规定条款引用，如《广告法》第九条")
    severity: ViolationSeverity = Field(description="违规严重程度")
    evidence: str = Field(default="", description="违规的具体证据（违规文字或图片区域描述）")
    action_required: ActionRequired = Field(
        default=ActionRequired.BLOCKING,
        description="面向广告主的问题分级：blocking/recommended/advisory",
    )

    def model_post_init(self, __context) -> None:
        """根据 severity 自动设置 action_required（如未显式设置）。"""
        if self.action_required == ActionRequired.BLOCKING and self.severity != ViolationSeverity.HIGH:
            self.action_required = _SEVERITY_TO_ACTION.get(
                self.severity, ActionRequired.BLOCKING
            )

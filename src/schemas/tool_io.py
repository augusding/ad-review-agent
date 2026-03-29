"""
各 Tool 的输入输出 Schema。
每添加一个新 Tool，在这里追加对应的 Input/Output 模型。
"""
from typing import Optional
from pydantic import BaseModel, Field

from .violation import ViolationItem


# ==================== TextViolationChecker ====================

class TextCheckerInput(BaseModel):
    text_content: str = Field(description="待检测的广告文案全文（标题+描述+CTA 拼接）")
    ad_category: str = Field(description="广告品类，影响专项规则的应用")
    request_id: str = Field(description="请求 ID，用于日志追踪")


class TextCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)


# ==================== ImageContentChecker ====================

class ImageCheckerInput(BaseModel):
    image_urls: list[str] = Field(default_factory=list, description="图片 URL 列表")
    video_url: Optional[str] = Field(default=None, description="视频 URL")
    ad_category: str = Field(description="广告品类")
    request_id: str = Field(description="请求 ID")


class ImageCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)
    image_descriptions: list[str] = Field(
        default_factory=list,
        description="每张图片的内容摘要（10-20字）"
    )
    has_brand_in_image: bool = Field(
        default=False,
        description="图片中是否包含明显品牌标识"
    )


# ==================== LandingPageChecker ====================

class LandingPageCheckerInput(BaseModel):
    landing_page_url: str = Field(description="落地页 URL")
    creative_summary: str = Field(description="素材内容摘要，用于一致性比对")
    request_id: str = Field(description="请求 ID")


class LandingPageCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    page_accessible: bool = Field(default=True, description="落地页是否可访问")
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)
    page_title: str = Field(default="", description="落地页标题")
    page_content_summary: str = Field(
        default="",
        description="落地页主要文字内容摘要（前600字）"
    )


# ==================== QualificationChecker ====================

class QualificationCheckerInput(BaseModel):
    ad_category: str = Field(description="广告品类")
    qualification_ids: list[str] = Field(description="广告主提交的资质 ID 列表")
    request_id: str = Field(description="请求 ID")


class QualificationCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    missing_qualifications: list[str] = Field(
        default_factory=list,
        description="缺少的资质名称"
    )
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)


# ==================== PlatformRuleChecker ====================

class PlatformRuleCheckerInput(BaseModel):
    ad_category: str = Field(description="广告品类")
    creative_type: str = Field(description="素材类型")
    title: Optional[str] = Field(default=None, description="广告标题")
    description: Optional[str] = Field(default=None, description="广告描述")
    image_urls: list[str] = Field(default_factory=list, description="图片 URL 列表")
    video_url: Optional[str] = Field(default=None, description="视频 URL")
    platform: str = Field(default="vivo_app_store", description="投放平台")
    request_id: str = Field(description="请求 ID")


class PlatformRuleCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)


# ==================== AudioChecker ====================

class AudioCheckOutput(BaseModel):
    transcript: str = Field(default="", description="语音转写文字")
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    skipped: bool = Field(default=False)
    skip_reason: str = Field(default="")


# ==================== ConsistencyChecker ====================

class ConsistencyCheckInput(BaseModel):
    ad_title: str = Field(default="", description="广告标题")
    ad_description: str = Field(default="", description="广告描述")
    ad_cta: str = Field(default="", description="CTA 文案")
    ad_category: str = Field(default="", description="广告品类")
    image_urls: list[str] = Field(
        default_factory=list,
        description="原始图片URL列表，供Qwen直接分析"
    )
    image_descriptions: list[str] = Field(
        default_factory=list,
        description="每张图片的内容摘要，由 ImageChecker 生成"
    )
    has_brand_in_image: bool = Field(
        default=False,
        description="图片中是否包含明显品牌标识，由 ImageChecker 标记"
    )
    video_summary: str = Field(
        default="",
        description="视频关键帧内容摘要，由 TaskWorker 生成"
    )
    landing_page_content: str = Field(
        default="",
        description="落地页主要文字内容，由 LandingPageChecker 提取"
    )
    landing_page_title: str = Field(default="", description="落地页标题")
    request_id: str = Field(default="", description="请求 ID")


class ConsistencyCheckOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    checked_pairs: list[str] = Field(
        default_factory=list,
        description="已检测的素材对列表"
    )
    is_fallback: bool = Field(default=False)
    fallback_reason: str = Field(default="")

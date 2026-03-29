"""
审核请求数据模型。
"""
from enum import Enum
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class AdCategory(str, Enum):
    GAME = "game"
    TOOL_APP = "tool_app"
    ECOMMERCE = "ecommerce"
    FINANCE = "finance"
    HEALTH = "health"
    EDUCATION = "education"
    OTHER = "other"


class CreativeType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    TEXT = "text"
    LANDING_PAGE = "landing_page"
    MIXED = "mixed"


class AdPlatform(str, Enum):
    APP_STORE = "vivo_app_store"
    GAME_CENTER = "vivo_game_center"
    BROWSER = "vivo_browser"
    SCREEN_ON = "vivo_screen_on"
    OTHER = "other"


class CreativeContent(BaseModel):
    title: Optional[str] = Field(default=None, description="广告标题")
    description: Optional[str] = Field(default=None, description="广告描述文案")
    cta_text: Optional[str] = Field(default=None, description="行动召唤按钮文字")
    image_urls: list[str] = Field(default_factory=list, description="图片素材 URL 列表")
    video_url: Optional[str] = Field(default=None, description="视频素材 URL")
    landing_page_url: Optional[str] = Field(default=None, description="落地页 URL")


class ReviewRequest(BaseModel):
    """广告素材审核请求。"""
    request_id: str = Field(description="唯一请求 ID（UUID）")
    advertiser_id: str = Field(description="广告主 ID")
    ad_category: AdCategory = Field(description="广告品类")
    creative_type: CreativeType = Field(description="素材类型")
    content: CreativeContent = Field(description="素材内容")
    advertiser_qualification_ids: list[str] = Field(
        default_factory=list,
        description="广告主已提交的资质证明 ID 列表"
    )
    platform: AdPlatform = Field(
        default=AdPlatform.OTHER,
        description="投放平台"
    )
    submitted_at: datetime = Field(default_factory=datetime.utcnow)

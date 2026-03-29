"""
pytest 全局配置和公共 fixtures。
"""
import pytest
from src.schemas.request import ReviewRequest, AdCategory, CreativeType, CreativeContent


@pytest.fixture
def sample_game_request() -> ReviewRequest:
    """游戏类广告请求样本（用于测试，不使用真实数据）。"""
    return ReviewRequest(
        request_id="test-001",
        advertiser_id="advertiser-test-001",
        ad_category=AdCategory.GAME,
        creative_type=CreativeType.TEXT,
        content=CreativeContent(
            title="测试游戏广告标题",
            description="这是一个用于测试的游戏描述文案",
            cta_text="立即下载",
        ),
        advertiser_qualification_ids=["qual-001"],
    )


@pytest.fixture
def sample_finance_request() -> ReviewRequest:
    """金融类广告请求样本。"""
    return ReviewRequest(
        request_id="test-002",
        advertiser_id="advertiser-test-002",
        ad_category=AdCategory.FINANCE,
        creative_type=CreativeType.TEXT,
        content=CreativeContent(
            title="理财产品广告",
            description="年化收益8%，安全稳健",
            cta_text="立即投资",
        ),
    )

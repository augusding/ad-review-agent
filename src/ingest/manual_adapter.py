"""
手动上传适配器。

复用现有的 POST /review/upload 接口逻辑，
套上 BaseIngestAdapter 接口以统一管理。
"""
import uuid

from loguru import logger

from src.agents.review_agent import ReviewAgent
from src.ingest.base import BaseIngestAdapter
from src.schemas.request import CreativeContent, ReviewRequest


class ManualUploadAdapter(BaseIngestAdapter):
    """
    手动上传适配器。

    将管理后台或 API 的文件上传请求转换为 ReviewRequest。
    实际路由处理仍由 api.py 的 /review/upload 负责，
    本适配器提供统一的 transform 接口。
    """

    name: str = "manual_upload"

    async def start(self) -> None:
        """手动上传适配器始终可用，无需额外启动。"""
        logger.info("Manual upload adapter ready", adapter=self.name)

    async def stop(self) -> None:
        """手动上传适配器无需停止。"""
        logger.info("Manual upload adapter stopped", adapter=self.name)

    def transform(self, raw: dict) -> ReviewRequest:
        """
        将上传数据转换为 ReviewRequest。

        Args:
            raw: 包含以下字段的字典：
                - advertiser_id: 广告主 ID
                - ad_category: 广告品类
                - creative_type: 素材类型
                - title: 广告标题（可选）
                - description: 广告描述（可选）
                - cta_text: CTA 文案（可选）
                - image_paths: 本地图片路径列表（可选）
                - video_path: 本地视频路径（可选）
                - landing_page_url: 落地页 URL（可选）

        Returns:
            标准化的 ReviewRequest
        """
        request_id = raw.get("request_id", f"upload-{uuid.uuid4().hex[:12]}")

        content = CreativeContent(
            title=raw.get("title"),
            description=raw.get("description"),
            cta_text=raw.get("cta_text"),
            image_urls=raw.get("image_paths", []),
            video_url=raw.get("video_path"),
            landing_page_url=raw.get("landing_page_url"),
        )

        return ReviewRequest(
            request_id=request_id,
            advertiser_id=raw.get("advertiser_id", "unknown"),
            ad_category=raw.get("ad_category", "other"),
            creative_type=raw.get("creative_type", "text"),
            content=content,
        )

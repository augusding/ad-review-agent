"""
HTTP Webhook 推送接收适配器。

接收外部系统主动推送的素材审核请求，
支持 HMAC-SHA256 签名验证（可选），
字段映射可通过配置自定义。
"""
import hashlib
import hmac
import json
import uuid

from loguru import logger

from src.agents.review_agent import ReviewAgent
from src.config import settings
from src.ingest.base import BaseIngestAdapter
from src.schemas.request import (
    AdCategory,
    CreativeContent,
    CreativeType,
    ReviewRequest,
)


# 默认字段映射：外部字段名 → ReviewRequest 字段名
_DEFAULT_MAPPING = {
    "request_id": "request_id",
    "advertiser_id": "advertiser_id",
    "ad_category": "ad_category",
    "creative_type": "creative_type",
    "title": "content.title",
    "description": "content.description",
    "cta_text": "content.cta_text",
    "image_urls": "content.image_urls",
    "video_url": "content.video_url",
    "landing_page_url": "content.landing_page_url",
}


class HttpWebhookAdapter(BaseIngestAdapter):
    """
    HTTP Webhook 推送接收适配器。

    外部系统通过 POST /ingest/webhook 推送素材数据，
    适配器验证签名后将数据转换为 ReviewRequest。
    路由注册由 api.py 负责，本类仅提供 transform 和签名验证。
    """

    name: str = "http_webhook"

    def __init__(self, agent: ReviewAgent) -> None:
        """
        初始化 HTTP Webhook 适配器。

        Args:
            agent: ReviewAgent 实例
        """
        super().__init__(agent)
        self._secret = settings.ingest_webhook_secret
        self._field_mapping = self._load_field_mapping()

    def _load_field_mapping(self) -> dict:
        """
        从配置加载字段映射。

        Returns:
            合并后的字段映射字典
        """
        mapping = dict(_DEFAULT_MAPPING)
        custom = settings.ingest_field_mapping.strip()
        if custom and custom != "{}":
            try:
                custom_map = json.loads(custom)
                mapping.update(custom_map)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Invalid field mapping JSON, using defaults",
                    error=str(e),
                )
        return mapping

    async def start(self) -> None:
        """HTTP 适配器由 FastAPI 路由驱动，无需额外启动。"""
        logger.info(
            "HTTP webhook adapter ready",
            adapter=self.name,
            signature_required=bool(self._secret),
        )

    async def stop(self) -> None:
        """HTTP 适配器无需停止。"""
        logger.info("HTTP webhook adapter stopped", adapter=self.name)

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """
        验证 HMAC-SHA256 签名。

        Args:
            body: 原始请求体
            signature: X-Webhook-Signature Header 值

        Returns:
            签名是否有效
        """
        if not self._secret:
            return True  # 未配置密钥时跳过验证

        expected = hmac.new(
            self._secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    def transform(self, raw: dict) -> ReviewRequest:
        """
        将 Webhook 推送的 JSON 映射为 ReviewRequest。

        通过字段映射配置支持不同外部系统的数据格式。

        Args:
            raw: 原始 JSON 数据

        Returns:
            标准化的 ReviewRequest

        Raises:
            ValueError: 必要字段缺失
        """
        def _get(key: str, default=None):
            """从 raw 中按映射获取字段值。"""
            mapped_key = self._field_mapping.get(key, key)
            return raw.get(mapped_key, default)

        request_id = _get("request_id") or f"webhook-{uuid.uuid4().hex[:12]}"
        advertiser_id = _get("advertiser_id", "unknown")
        ad_category = _get("ad_category", "other")
        creative_type = _get("creative_type", "text")

        content = CreativeContent(
            title=_get("title"),
            description=_get("description"),
            cta_text=_get("cta_text"),
            image_urls=_get("image_urls") or [],
            video_url=_get("video_url"),
            landing_page_url=_get("landing_page_url"),
        )

        return ReviewRequest(
            request_id=request_id,
            advertiser_id=advertiser_id,
            ad_category=ad_category,
            creative_type=creative_type,
            content=content,
        )

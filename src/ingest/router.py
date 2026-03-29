"""
接入路由管理器。

根据配置注册和启动对应的素材接入适配器，
统一管理适配器生命周期。
"""
from loguru import logger

from src.agents.review_agent import ReviewAgent
from src.config import settings
from src.ingest.base import BaseIngestAdapter
from src.ingest.http_adapter import HttpWebhookAdapter
from src.ingest.manual_adapter import ManualUploadAdapter
from src.ingest.mq_adapter import MQAdapter


class IngestRouter:
    """
    接入路由管理器。

    根据配置自动注册并启动对应的适配器：
    - manual: 始终启动
    - http_webhook: webhook_secret 配置后启动
    - mq: mq_type != "none" 时启动
    """

    def __init__(self, agent: ReviewAgent) -> None:
        """
        初始化路由管理器。

        Args:
            agent: ReviewAgent 实例，传递给所有适配器
        """
        self._agent = agent
        self.adapters: dict[str, BaseIngestAdapter] = {}

    def register(self, name: str, adapter: BaseIngestAdapter) -> None:
        """
        注册适配器。

        Args:
            name: 适配器名称
            adapter: 适配器实例
        """
        self.adapters[name] = adapter
        logger.debug("Adapter registered", name=name)

    async def start_all(self) -> None:
        """
        根据配置启动对应的适配器。

        - manual 始终启动
        - http_webhook 始终注册（路由由 FastAPI 管理）
        - mq 仅在 mq_type != "none" 时启动
        """
        # Manual adapter（始终启动）
        manual = ManualUploadAdapter(self._agent)
        self.register("manual", manual)
        await manual.start()

        # HTTP Webhook adapter（始终注册）
        webhook = HttpWebhookAdapter(self._agent)
        self.register("http_webhook", webhook)
        await webhook.start()

        # MQ adapter（按配置启动）
        if settings.mq_type != "none":
            mq = MQAdapter(self._agent)
            self.register("mq", mq)
            await mq.start()
        else:
            logger.info("MQ adapter disabled (mq_type=none)")

        logger.info(
            "IngestRouter started",
            active_adapters=list(self.adapters.keys()),
        )

    async def stop_all(self) -> None:
        """停止所有已注册的适配器。"""
        for name, adapter in self.adapters.items():
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning(
                    "Error stopping adapter",
                    name=name,
                    error=str(e),
                )
        logger.info("IngestRouter stopped")

    def get_adapter(self, name: str) -> BaseIngestAdapter | None:
        """
        获取指定适配器。

        Args:
            name: 适配器名称

        Returns:
            适配器实例，不存在返回 None
        """
        return self.adapters.get(name)

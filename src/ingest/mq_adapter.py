"""
消息队列适配器。

支持 Kafka 和 RocketMQ 两种模式，通过 MQ_TYPE 配置切换。
具体客户端库懒加载，未安装时记录警告不阻塞启动。
灰度消费通过 MQ_CONSUME_RATIO 控制。
"""
import asyncio
import json
import random
import uuid
from typing import Any

from loguru import logger

from src.agents.review_agent import ReviewAgent
from src.config import settings
from src.ingest.base import BaseIngestAdapter
from src.schemas.request import (
    CreativeContent,
    ReviewRequest,
)


class MQAdapter(BaseIngestAdapter):
    """
    消息队列适配器。

    根据 mq_type 配置选择 Kafka 或 RocketMQ，
    消费消息后按 consume_ratio 灰度比例决定是否处理。
    """

    name: str = "mq_consumer"

    def __init__(self, agent: ReviewAgent) -> None:
        """
        初始化 MQ 适配器。

        Args:
            agent: ReviewAgent 实例
        """
        super().__init__(agent)
        self._mq_type = settings.mq_type
        self._brokers = settings.mq_brokers
        self._topic = settings.mq_topic
        self._group_id = settings.mq_group_id
        self._consume_ratio = settings.mq_consume_ratio
        self._consumer: Any = None
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """
        启动消息消费。

        根据 mq_type 初始化对应客户端并开始消费。
        客户端库未安装时记录警告并跳过。
        """
        if self._mq_type == "none":
            logger.info("MQ adapter disabled (mq_type=none)")
            return

        logger.info(
            "Starting MQ adapter",
            adapter=self.name,
            mq_type=self._mq_type,
            brokers=self._brokers,
            topic=self._topic,
            consume_ratio=self._consume_ratio,
        )

        if self._mq_type == "kafka":
            self._consumer = self._create_kafka_consumer()
        elif self._mq_type == "rocketmq":
            self._consumer = self._create_rocketmq_consumer()
        else:
            logger.warning(
                "Unknown mq_type, MQ adapter not started",
                mq_type=self._mq_type,
            )
            return

        if self._consumer is None:
            return

        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("MQ consumer started", adapter=self.name)

    async def stop(self) -> None:
        """停止消息消费并关闭连接。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            try:
                self._consumer.close()
            except Exception as e:
                logger.warning(
                    "Error closing MQ consumer",
                    error=str(e),
                )
        logger.info("MQ adapter stopped", adapter=self.name)

    def transform(self, raw: dict) -> ReviewRequest:
        """
        将 MQ 消息体映射为 ReviewRequest。

        Args:
            raw: 消息体 JSON 解析后的字典

        Returns:
            标准化的 ReviewRequest
        """
        request_id = raw.get("request_id", f"mq-{uuid.uuid4().hex[:12]}")

        content = CreativeContent(
            title=raw.get("title"),
            description=raw.get("description"),
            cta_text=raw.get("cta_text"),
            image_urls=raw.get("image_urls", []),
            video_url=raw.get("video_url"),
            landing_page_url=raw.get("landing_page_url"),
        )

        return ReviewRequest(
            request_id=request_id,
            advertiser_id=raw.get("advertiser_id", "unknown"),
            ad_category=raw.get("ad_category", "other"),
            creative_type=raw.get("creative_type", "text"),
            content=content,
        )

    def _should_consume(self) -> bool:
        """
        根据灰度比例决定是否消费当前消息。

        Returns:
            True 表示处理，False 表示跳过
        """
        return random.random() < self._consume_ratio

    def _create_kafka_consumer(self) -> Any:
        """
        创建 Kafka 消费者。

        Returns:
            KafkaConsumer 实例，或 None（库未安装时）
        """
        try:
            from kafka import KafkaConsumer
            return KafkaConsumer(
                self._topic,
                bootstrap_servers=self._brokers.split(","),
                group_id=self._group_id,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                auto_offset_reset="latest",
                enable_auto_commit=True,
                consumer_timeout_ms=1000,
            )
        except ImportError:
            logger.warning(
                "kafka-python not installed, MQ adapter disabled. "
                "Install with: uv add kafka-python"
            )
            return None

    def _create_rocketmq_consumer(self) -> Any:
        """
        创建 RocketMQ 消费者（占位实现）。

        Returns:
            消费者实例，或 None
        """
        try:
            from rocketmq.client import PushConsumer
            consumer = PushConsumer(self._group_id)
            consumer.set_name_server_address(self._brokers)
            logger.info("RocketMQ consumer created")
            return consumer
        except ImportError:
            logger.warning(
                "rocketmq-client-python not installed, MQ adapter disabled. "
                "Install with: uv add rocketmq-client-python"
            )
            return None

    async def _consume_loop(self) -> None:
        """
        消息消费主循环。

        在后台持续拉取消息，按灰度比例处理。
        """
        logger.info("MQ consume loop started", topic=self._topic)
        while self._running:
            try:
                # 非阻塞拉取（在线程池中执行同步消费）
                messages = await asyncio.get_event_loop().run_in_executor(
                    None, self._poll_messages
                )
                for msg in messages:
                    if not self._should_consume():
                        logger.debug(
                            "MQ message skipped (consume ratio)",
                            adapter=self.name,
                        )
                        continue
                    try:
                        await self.process(msg)
                    except Exception as e:
                        logger.error(
                            "MQ message processing failed",
                            adapter=self.name,
                            error=str(e),
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "MQ consume loop error",
                    adapter=self.name,
                    error=str(e),
                )
                await asyncio.sleep(5)

    def _poll_messages(self) -> list[dict]:
        """
        同步拉取消息（在线程池中调用）。

        Returns:
            消息体列表
        """
        if not self._consumer:
            return []
        try:
            # kafka-python 的 poll 模式
            if hasattr(self._consumer, "poll"):
                records = self._consumer.poll(timeout_ms=1000)
                messages = []
                for tp, msgs in records.items():
                    for msg in msgs:
                        if isinstance(msg.value, dict):
                            messages.append(msg.value)
                return messages
        except Exception:
            pass
        return []

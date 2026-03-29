"""
素材接入适配器抽象基类。

所有接入方式（HTTP Webhook / 消息队列 / 手动上传）
均继承此类，实现 transform() 将原始数据转换为 ReviewRequest，
由基类 process() 统一调用 ReviewAgent 审核并写入数据库。
"""
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from src.agents.review_agent import ReviewAgent
from src.schemas.request import ReviewRequest
from src.schemas.result import ReviewResult


class BaseIngestAdapter(ABC):
    """
    素材接入适配器抽象基类。

    子类必须实现：
    - name: 适配器名称
    - start(): 启动监听
    - stop(): 停止监听
    - transform(): 将原始消息转换为 ReviewRequest
    """

    name: str = "base_adapter"

    def __init__(self, agent: ReviewAgent) -> None:
        """
        初始化适配器。

        Args:
            agent: ReviewAgent 实例，所有适配器共享同一个 Agent
        """
        self._agent = agent

    @abstractmethod
    async def start(self) -> None:
        """启动适配器（开始监听或注册路由）。"""
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        """停止适配器（关闭连接或取消注册）。"""
        raise NotImplementedError

    @abstractmethod
    def transform(self, raw: dict) -> ReviewRequest:
        """
        将原始消息转换为 ReviewRequest。

        Args:
            raw: 原始数据（HTTP body / MQ 消息体 / 上传数据）

        Returns:
            标准化的 ReviewRequest

        Raises:
            ValueError: 数据格式不合法
        """
        raise NotImplementedError

    async def process(self, raw: dict) -> ReviewResult:
        """
        完整处理流程：转换格式 → 审核 → 返回结果。

        Args:
            raw: 原始数据

        Returns:
            审核结果 ReviewResult
        """
        request = self.transform(raw)

        logger.info(
            "Ingest adapter processing",
            adapter=self.name,
            request_id=request.request_id,
            ad_category=request.ad_category.value,
        )

        result = await self._agent.review(request)

        logger.info(
            "Ingest adapter completed",
            adapter=self.name,
            request_id=result.request_id,
            verdict=result.verdict.value,
            confidence=result.confidence,
        )

        return result

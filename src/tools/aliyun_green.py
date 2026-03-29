"""
阿里云内容安全 API 封装。

支持图片和视频内容检测，与 Qwen VL 并行运行做双重验证。
API Key 为空时自动禁用，不影响现有审核流程。
"""
import asyncio
import json

from loguru import logger

from src.config import settings


_LABEL_MAP = {
    "porn": "色情内容",
    "sexy": "性感低俗内容",
    "terrorism": "暴力恐怖内容",
    "terrorist": "暴力恐怖内容",
    "contraband": "违禁违法内容",
    "ad": "广告违规内容",
    "abuse": "辱骂内容",
    "flood": "垃圾信息",
    "meaningless": "无意义内容",
    "political": "政治敏感内容",
    "violence": "暴力内容",
    "logo": "品牌Logo检测",
    "qrcode": "二维码检测",
}


class AliyunGreenChecker:
    """
    阿里云内容安全 API 封装。

    通过 baselineCheck 服务检测图片和视频中的违规内容，
    与 Qwen VL 视觉模型并行执行做双重验证。

    API Key 未配置时所有方法返回跳过结果，不阻塞审核流程。
    """

    def __init__(self) -> None:
        """
        初始化阿里云内容安全客户端。

        未配置 AccessKey 时标记为禁用状态。
        """
        self._enabled = (
            settings.aliyun_green_enabled
            and bool(settings.aliyun_access_key_id)
            and bool(settings.aliyun_access_key_secret)
        )
        self._client = None

        if self._enabled:
            try:
                self._init_client()
                logger.info("Aliyun Green content safety checker initialized")
            except Exception as e:
                logger.warning(
                    "Aliyun Green init failed, disabling",
                    error=str(e),
                )
                self._enabled = False
        else:
            logger.info("Aliyun Green disabled (no AccessKey configured)")

    def _init_client(self) -> None:
        """初始化 SDK 客户端。"""
        from alibabacloud_green20220302.client import Client
        from alibabacloud_tea_openapi.models import Config

        config = Config(
            access_key_id=settings.aliyun_access_key_id,
            access_key_secret=settings.aliyun_access_key_secret,
            endpoint=settings.aliyun_green_endpoint,
            connect_timeout=3000,
            read_timeout=6000,
        )
        self._client = Client(config)

    @property
    def enabled(self) -> bool:
        """是否已启用。"""
        return self._enabled

    async def check_image(self, image_url: str, request_id: str) -> dict:
        """
        检测单张图片。

        Args:
            image_url: 图片 URL（必须为公网可访问地址）
            request_id: 请求 ID

        Returns:
            {passed: bool, violations: list, skipped: bool}
        """
        if not self._enabled:
            return {"passed": True, "violations": [], "skipped": True}

        # 本地文件路径无法被阿里云访问，跳过
        if not image_url.startswith("http"):
            logger.debug(
                "Aliyun green skipped local file",
                request_id=request_id,
                url=image_url[:50],
            )
            return {"passed": True, "violations": [], "skipped": True}

        try:
            from alibabacloud_green20220302.models import ImageModerationRequest

            request = ImageModerationRequest(
                service="baselineCheck",
                service_parameters=json.dumps({
                    "imageUrl": image_url,
                    "dataId": request_id,
                }),
            )

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.image_moderation(request),
            )

            return self._parse_image_response(response, request_id)

        except Exception as e:
            logger.warning(
                "Aliyun green image check failed",
                request_id=request_id,
                error=str(e),
            )
            return {"passed": True, "violations": [], "error": str(e), "skipped": True}

    async def check_video_submit(self, video_url: str, request_id: str) -> str | None:
        """
        提交视频异步检测任务。

        Args:
            video_url: 视频 URL
            request_id: 请求 ID

        Returns:
            阿里云任务 ID，失败返回 None
        """
        if not self._enabled or not video_url.startswith("http"):
            return None

        try:
            from alibabacloud_green20220302.models import VideoModerationRequest

            request = VideoModerationRequest(
                service="baselineCheck",
                service_parameters=json.dumps({
                    "url": video_url,
                    "dataId": request_id,
                }),
            )

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.video_moderation(request),
            )

            if response.body and response.body.data:
                task_id = response.body.data.task_id
                logger.info(
                    "Aliyun video task submitted",
                    request_id=request_id,
                    aliyun_task_id=task_id,
                )
                return task_id
            return None

        except Exception as e:
            logger.warning(
                "Aliyun green video submit failed",
                request_id=request_id,
                error=str(e),
            )
            return None

    async def check_video_result(self, task_id: str) -> dict:
        """
        查询视频检测结果。

        Args:
            task_id: 阿里云任务 ID

        Returns:
            {status: pending/completed/error, passed: bool, violations: list}
        """
        if not self._enabled or not task_id:
            return {"status": "completed", "passed": True, "violations": [], "skipped": True}

        try:
            from alibabacloud_green20220302.models import VideoModerationResultRequest

            request = VideoModerationResultRequest(task_id=task_id)

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.video_moderation_result(request),
            )

            return self._parse_video_response(response)

        except Exception as e:
            logger.warning("Aliyun green video result failed", error=str(e))
            return {"status": "error", "passed": True, "violations": [], "skipped": True}

    def _parse_image_response(self, response, request_id: str) -> dict:
        """
        解析图片检测响应。

        Args:
            response: SDK 响应对象
            request_id: 请求 ID

        Returns:
            统一格式结果字典
        """
        try:
            body = response.body
            if not body or body.code != 200:
                logger.debug(
                    "Aliyun image check non-200",
                    request_id=request_id,
                    code=getattr(body, "code", None),
                )
                return {"passed": True, "violations": [], "skipped": True}

            data = body.data
            raw_result = getattr(data, "result", "unknown") if data else "unknown"
            passed = raw_result != "block"

            violations = []
            if data and hasattr(data, "result") and data.result:
                # result 可能是 JSON 字符串
                try:
                    result_list = json.loads(data.result) if isinstance(data.result, str) else []
                    if isinstance(result_list, list):
                        for item in result_list:
                            label = item.get("label", "")
                            conf = item.get("confidence", 0)
                            if label and label != "nonLabel" and conf > 50:
                                passed = False
                                violations.append({
                                    "label": label,
                                    "confidence": conf,
                                    "description": _LABEL_MAP.get(label, f"违规内容（{label}）"),
                                })
                except (json.JSONDecodeError, TypeError):
                    pass

            logger.info(
                "Aliyun image check done",
                request_id=request_id,
                passed=passed,
                violations_count=len(violations),
                raw_result=str(raw_result)[:100],
            )

            return {"passed": passed, "violations": violations, "skipped": False}

        except Exception as e:
            logger.warning("Parse aliyun response failed", error=str(e))
            return {"passed": True, "violations": [], "skipped": True}

    def _parse_video_response(self, response) -> dict:
        """解析视频检测响应。"""
        try:
            body = response.body
            if not body or body.code != 200:
                return {"status": "error", "passed": True, "violations": []}

            data = body.data
            if not data:
                return {"status": "error", "passed": True, "violations": []}

            # 检查任务状态
            if hasattr(data, "data_id"):
                # 结果已返回
                passed = True
                violations = []

                if hasattr(data, "result") and data.result:
                    try:
                        result_list = json.loads(data.result) if isinstance(data.result, str) else []
                        if isinstance(result_list, list):
                            for item in result_list:
                                label = item.get("label", "")
                                conf = item.get("confidence", 0)
                                if label and label != "nonLabel" and conf > 50:
                                    passed = False
                                    violations.append({
                                        "label": label,
                                        "confidence": conf,
                                        "description": _LABEL_MAP.get(label, f"违规内容（{label}）"),
                                    })
                    except (json.JSONDecodeError, TypeError):
                        pass

                return {"status": "completed", "passed": passed, "violations": violations}

            return {"status": "pending", "passed": True, "violations": []}

        except Exception as e:
            return {"status": "error", "passed": True, "violations": [], "skipped": True}

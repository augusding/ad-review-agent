"""
图片/视频内容安全检测 Tool。

检测策略：
1. 图片：下载并 base64 编码，逐张审核
2. 视频：关键帧抽样（首帧+尾帧+均匀分布3帧，共最多5帧），逐帧审核
3. 调用通义千问视觉模型（OpenAI 兼容接口）进行视觉理解

所有帧统一审核，结果合并后输出。
"""
import asyncio
import base64
import tempfile
import time
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base_tool import BaseTool, ToolExecutionError, ToolTimeoutError
from src.schemas.tool_io import ImageCheckerInput, ImageCheckerOutput
from src.schemas.violation import ViolationItem, ViolationDimension, ViolationSeverity
from src.config import settings

# 视频文件大小上限（500MB）
_MAX_VIDEO_SIZE_BYTES = 500 * 1024 * 1024
# 视频下载超时（秒）
_VIDEO_DOWNLOAD_TIMEOUT = 10.0


class _LLMViolation(BaseModel):
    """Qwen 返回的单个违规项（内部解析用）。"""
    dimension: str = "image_safety"
    description: str
    regulation_ref: str
    severity: str
    evidence: str


class _LLMResponse(BaseModel):
    """Qwen 完整响应（内部解析用）。"""
    violations: list[_LLMViolation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    image_description: str = Field(
        default="",
        description="图片内容的10-20字简短描述"
    )
    has_brand: bool = Field(
        default=False,
        description="图片中是否有明显的品牌Logo或产品名称"
    )


class _FrameInfo:
    """待审核的帧信息（图片或视频帧）。"""

    def __init__(
        self, b64: str, media_type: str, label: str, index: int
    ) -> None:
        """
        Args:
            b64: base64 编码
            media_type: MIME 类型
            label: 帧标签（如"图片第1张"、"视频第1帧(首帧)"）
            index: 在所有帧中的全局序号（从 1 开始）
        """
        self.b64 = b64
        self.media_type = media_type
        self.label = label
        self.index = index


class ImageContentChecker(BaseTool):
    """
    图片/视频内容安全检测。

    执行流程：
    1. 收集所有待审核帧（图片直接下载，视频提取关键帧）
    2. 逐帧调用通义千问视觉模型进行视觉审核
    3. 合并所有帧的审核结果，取最低置信度

    Prompt 模板在 __init__ 时加载并缓存。
    """

    name: str = "image_content_checker"
    description: str = "检测广告图片/视频中的违规内容"

    def __init__(self) -> None:
        """
        初始化 ImageContentChecker，加载 Prompt 模板和阿里云检测器。

        Raises:
            ToolExecutionError: Prompt 文件加载失败
        """
        self._prompt_path = Path("src/prompts/image_checker.txt")
        self._prompt_template: str = self._load_prompt_file()

        # 阿里云内容安全（双重验证）
        from src.tools.aliyun_green import AliyunGreenChecker
        self._aliyun_checker = AliyunGreenChecker()

    def _load_prompt_file(self) -> str:
        """
        从 image_checker.txt 加载 Prompt 模板。

        Returns:
            Prompt 模板字符串

        Raises:
            ToolExecutionError: 文件不存在或读取失败
        """
        try:
            return self._prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise ToolExecutionError(
                f"Prompt file not found: {self._prompt_path}"
            ) from e

    async def execute(self, input: ImageCheckerInput) -> ImageCheckerOutput:
        """
        执行图片/视频内容安全检测（双重验证）。

        并行运行 Qwen VL 视觉模型和阿里云内容安全 API，合并结果。
        任一发现违规即标记，任一出错则用另一方结果。

        Args:
            input: ImageCheckerInput，包含图片 URL 列表、视频 URL、品类和请求 ID

        Returns:
            ImageCheckerOutput，包含违规项列表和置信度
        """
        start_ms = time.monotonic()

        # 并行运行 Qwen 检测和阿里云检测
        qwen_task = self._qwen_check(input)
        aliyun_task = self._aliyun_check(input)

        results = await asyncio.gather(qwen_task, aliyun_task, return_exceptions=True)
        qwen_result, aliyun_result = results

        # 合并双重验证结果
        merged = self._merge_results(qwen_result, aliyun_result, input.request_id)

        duration_ms = (time.monotonic() - start_ms) * 1000
        logger.info(
            "Image/video dual check completed",
            tool=self.name,
            request_id=input.request_id,
            total_violations=len(merged.violations),
            confidence=merged.confidence,
            duration_ms=round(duration_ms, 1),
            aliyun_enabled=self._aliyun_checker.enabled,
        )

        return merged

    async def _qwen_check(self, input: ImageCheckerInput) -> ImageCheckerOutput:
        """
        Qwen VL 视觉模型检测（原有逻辑）。

        收集所有待审核帧，逐帧调用视觉模型。

        Args:
            input: ImageCheckerInput

        Returns:
            ImageCheckerOutput
        """
        # 收集所有待审核帧
        frames: list[_FrameInfo] = []

        for idx, image_url in enumerate(input.image_urls, start=1):
            image_b64, media_type = await self._download_image(
                image_url, input.request_id
            )
            frames.append(_FrameInfo(
                b64=image_b64,
                media_type=media_type,
                label=f"图片第{idx}张",
                index=len(frames) + 1,
            ))

        if input.video_url:
            video_frames = await self._extract_keyframes(
                input.video_url, input.request_id
            )
            for vf in video_frames:
                vf.index = len(frames) + 1
                frames.append(vf)

        if not frames:
            return ImageCheckerOutput(
                violations=[], confidence=0.98, is_fallback=False,
            )

        all_violations: list[ViolationItem] = []
        min_confidence: float = 1.0
        total_frames = len(frames)
        image_descriptions: list[str] = []
        any_has_brand: bool = False

        for frame in frames:
            violations, confidence, description, has_brand = (
                await self._analyze_image(
                    image_b64=frame.b64,
                    media_type=frame.media_type,
                    ad_category=input.ad_category,
                    image_index=frame.index,
                    total_images=total_frames,
                    request_id=input.request_id,
                )
            )

            for v in violations:
                if "视频" in frame.label:
                    v.evidence = f"[{frame.label}] {v.evidence}"

            all_violations.extend(violations)
            min_confidence = min(min_confidence, confidence)

            if description:
                image_descriptions.append(description)
            if has_brand:
                any_has_brand = True

            logger.info(
                "Frame analyzed (Qwen)",
                tool=self.name,
                request_id=input.request_id,
                frame_label=frame.label,
                violations_count=len(violations),
                confidence=confidence,
                image_description=description,
                has_brand=has_brand,
            )

        return ImageCheckerOutput(
            violations=all_violations,
            confidence=min_confidence,
            is_fallback=False,
            image_descriptions=image_descriptions,
            has_brand_in_image=any_has_brand,
        )

    async def _aliyun_check(self, input: ImageCheckerInput) -> list[dict]:
        """
        阿里云内容安全检测。

        对每张图片 URL 调用阿里云 API，返回违规结果列表。

        Args:
            input: ImageCheckerInput

        Returns:
            阿里云检测结果列表
        """
        if not self._aliyun_checker.enabled:
            return []

        results = []
        for url in input.image_urls:
            result = await self._aliyun_checker.check_image(url, input.request_id)
            results.append(result)
        return results

    def _merge_results(
        self,
        qwen_result: ImageCheckerOutput | Exception,
        aliyun_result: list[dict] | Exception,
        request_id: str,
    ) -> ImageCheckerOutput:
        """
        合并 Qwen 和阿里云的双重验证结果。

        规则：
        - 两者都通过 → pass
        - 任一发现违规 → 合并违规项，取较低置信度
        - 任一出错 → 用另一方结果
        - 两者都出错 → fallback

        Args:
            qwen_result: Qwen 检测结果或异常
            aliyun_result: 阿里云检测结果列表或异常
            request_id: 请求 ID

        Returns:
            合并后的 ImageCheckerOutput
        """
        # 处理 Qwen 异常
        qwen_ok = isinstance(qwen_result, ImageCheckerOutput)
        if not qwen_ok:
            logger.warning(
                "Qwen check failed in dual mode",
                request_id=request_id,
                error=str(qwen_result),
            )

        # 处理阿里云异常
        aliyun_ok = isinstance(aliyun_result, list)
        if not aliyun_ok:
            logger.warning(
                "Aliyun check failed in dual mode",
                request_id=request_id,
                error=str(aliyun_result),
            )

        # 两者都失败 → fallback
        if not qwen_ok and not aliyun_ok:
            return ImageCheckerOutput(
                violations=[], confidence=0.5, is_fallback=True,
                fallback_reason="Both Qwen and Aliyun checks failed",
            )

        # 基础结果从 Qwen 取（或用空结果）
        if qwen_ok:
            violations = list(qwen_result.violations)
            confidence = qwen_result.confidence
            is_fallback = qwen_result.is_fallback
            image_descriptions = qwen_result.image_descriptions
            has_brand_in_image = qwen_result.has_brand_in_image
        else:
            violations = []
            confidence = 0.8
            is_fallback = True
            image_descriptions = []
            has_brand_in_image = False

        # 合并阿里云违规项
        if aliyun_ok:
            for result in aliyun_result:
                if result.get("skipped"):
                    continue
                for av in result.get("violations", []):
                    violations.append(ViolationItem(
                        dimension=ViolationDimension.IMAGE_SAFETY,
                        description=f"阿里云内容安全检测：{av['description']}",
                        regulation_ref="阿里云内容安全基线检测",
                        severity=ViolationSeverity.HIGH,
                        evidence=f"阿里云标签：{av['label']}（置信度{av.get('confidence', '-')}）",
                    ))
                if not result.get("passed", True):
                    confidence = min(confidence, 0.95)

            aliyun_violations = sum(
                len(r.get("violations", []))
                for r in aliyun_result if not r.get("skipped")
            )
            logger.info(
                "Aliyun check merged",
                request_id=request_id,
                aliyun_violations=aliyun_violations,
            )

        return ImageCheckerOutput(
            violations=violations,
            confidence=confidence,
            is_fallback=is_fallback,
            image_descriptions=image_descriptions,
            has_brand_in_image=has_brand_in_image,
        )

    async def _fallback(self, error: Exception, input: Any) -> ImageCheckerOutput:
        """
        降级处理：返回低置信度结果，让流程走人工复核。

        Args:
            error: 导致降级的异常
            input: 原始 ImageCheckerInput

        Returns:
            降级的 ImageCheckerOutput（is_fallback=True, confidence < 0.7）
        """
        logger.warning(
            "Image checker falling back",
            tool=self.name,
            request_id=getattr(input, "request_id", "unknown"),
            error=str(error),
        )

        return ImageCheckerOutput(
            violations=[],
            confidence=0.5,
            is_fallback=True,
            fallback_reason=f"Tool fallback due to: {type(error).__name__}: {error}",
        )

    # ==================== 图片下载 ====================

    def _is_local_path(self, path: str) -> bool:
        """
        判断路径是否为本地文件路径（非 HTTP URL）。

        Args:
            path: 图片路径或 URL

        Returns:
            True 表示本地文件路径
        """
        return (
            path.startswith("/")
            or path.startswith("./")
            or path.startswith("\\")
            or (len(path) >= 3 and path[1] == ":" and path[2] in "/\\")
        )

    async def _download_image(
        self, image_url: str, request_id: str
    ) -> tuple[str, str]:
        """
        获取图片并返回 base64 编码和 MIME 类型。

        支持两种来源：
        1. HTTP/HTTPS URL：通过 httpx 下载
        2. 本地文件路径：直接读取文件

        Args:
            image_url: 图片 URL 或本地文件路径
            request_id: 请求 ID

        Returns:
            (base64 编码字符串, MIME 类型如 "image/png")

        Raises:
            ToolExecutionError: 下载/读取失败或非图片文件
        """
        if self._is_local_path(image_url):
            return self._read_local_image(image_url, request_id)

        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout
            ) as client:
                response = await client.get(image_url)
                response.raise_for_status()
        except httpx.TimeoutException as e:
            raise ToolExecutionError(
                f"Image download timeout: {image_url}"
            ) from e
        except httpx.HTTPError as e:
            raise ToolExecutionError(
                f"Image download failed: {image_url}: {e}"
            ) from e

        content_type = response.headers.get("content-type", "")
        if "image/" not in content_type:
            raise ToolExecutionError(
                f"URL did not return an image: {image_url}, "
                f"content-type: {content_type}"
            )

        media_type = content_type.split(";")[0].strip()
        image_b64 = base64.b64encode(response.content).decode("utf-8")

        logger.debug(
            "Image downloaded",
            tool=self.name,
            request_id=request_id,
            url=image_url,
            media_type=media_type,
            size_bytes=len(response.content),
        )

        return image_b64, media_type

    def _read_local_image(
        self, file_path: str, request_id: str
    ) -> tuple[str, str]:
        """
        从本地文件读取图片并返回 base64 编码和 MIME 类型。

        Args:
            file_path: 本地图片文件路径
            request_id: 请求 ID

        Returns:
            (base64 编码字符串, MIME 类型如 "image/jpeg")

        Raises:
            ToolExecutionError: 文件不存在或非图片格式
        """
        _EXT_TO_MIME = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
        }

        p = Path(file_path)
        if not p.exists():
            raise ToolExecutionError(f"Local image file not found: {file_path}")

        ext = p.suffix.lower()
        media_type = _EXT_TO_MIME.get(ext)
        if not media_type:
            raise ToolExecutionError(
                f"Unsupported local image format: {ext}, file: {file_path}"
            )

        content = p.read_bytes()
        image_b64 = base64.b64encode(content).decode("utf-8")

        logger.debug(
            "Local image loaded",
            tool=self.name,
            request_id=request_id,
            path=file_path,
            media_type=media_type,
            size_bytes=len(content),
        )

        return image_b64, media_type

    # ==================== 视频关键帧提取 ====================

    async def _extract_keyframes(
        self, video_url: str, request_id: str
    ) -> list[_FrameInfo]:
        """
        下载视频并提取关键帧（首帧+尾帧+均匀分布3帧，共最多5帧）。

        使用 httpx 流式下载到临时文件，cv2.VideoCapture 提取帧，
        每帧编码为 base64 JPEG。

        Args:
            video_url: 视频 URL
            request_id: 请求 ID

        Returns:
            关键帧 _FrameInfo 列表

        Raises:
            ToolExecutionError: 下载失败、视频过大或无法解码
        """
        tmp_path: str | None = None
        try:
            # 流式下载视频，检查大小
            tmp_path = await self._download_video(video_url, request_id)

            # 用 OpenCV 提取关键帧
            frames = self._extract_frames_from_file(tmp_path, request_id)
            return frames

        finally:
            # 清理临时文件
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError as e:
                    logger.warning(
                        "Failed to delete temp video file",
                        tool=self.name,
                        request_id=request_id,
                        path=tmp_path,
                        error=str(e),
                    )

    async def _download_video(
        self, video_url: str, request_id: str
    ) -> str:
        """
        流式下载视频到临时文件。

        Args:
            video_url: 视频 URL
            request_id: 请求 ID

        Returns:
            临时文件路径

        Raises:
            ToolExecutionError: 下载失败、超时或视频过大
        """
        try:
            async with httpx.AsyncClient(
                timeout=_VIDEO_DOWNLOAD_TIMEOUT
            ) as client:
                # 先 HEAD 检查大小（如果服务端支持）
                try:
                    head_resp = await client.head(video_url)
                    content_length = int(
                        head_resp.headers.get("content-length", 0)
                    )
                    if content_length > _MAX_VIDEO_SIZE_BYTES:
                        raise ToolExecutionError(
                            f"Video too large: {content_length} bytes "
                            f"(max {_MAX_VIDEO_SIZE_BYTES}), "
                            f"url: {video_url}"
                        )
                except httpx.HTTPError:
                    pass  # HEAD 不支持则跳过，下载时再检查

                # 流式下载
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".mp4", delete=False
                )
                tmp_path = tmp.name
                downloaded = 0

                async with client.stream("GET", video_url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        downloaded += len(chunk)
                        if downloaded > _MAX_VIDEO_SIZE_BYTES:
                            tmp.close()
                            Path(tmp_path).unlink(missing_ok=True)
                            raise ToolExecutionError(
                                f"Video too large: exceeded {_MAX_VIDEO_SIZE_BYTES} bytes "
                                f"during download, url: {video_url}"
                            )
                        tmp.write(chunk)

                tmp.close()

                logger.debug(
                    "Video downloaded",
                    tool=self.name,
                    request_id=request_id,
                    url=video_url,
                    size_bytes=downloaded,
                )

                return tmp_path

        except ToolExecutionError:
            raise
        except httpx.TimeoutException as e:
            raise ToolExecutionError(
                f"Video download timeout: {video_url}"
            ) from e
        except httpx.HTTPError as e:
            raise ToolExecutionError(
                f"Video download failed: {video_url}: {e}"
            ) from e

    def _extract_frames_from_file(
        self, video_path: str, request_id: str
    ) -> list[_FrameInfo]:
        """
        从视频文件提取关键帧：首帧+尾帧+均匀分布3帧（共最多5帧）。

        Args:
            video_path: 视频文件路径
            request_id: 请求 ID

        Returns:
            _FrameInfo 列表

        Raises:
            ToolExecutionError: 视频无法打开或无帧可读
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ToolExecutionError(
                f"Cannot open video file: {video_path}"
            )

        try:
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames <= 0:
                raise ToolExecutionError(
                    f"Video has no frames: {video_path}"
                )

            # 计算要提取的帧索引
            frame_indices = self._compute_keyframe_indices(total_frames)

            logger.debug(
                "Extracting keyframes",
                tool=self.name,
                request_id=request_id,
                total_frames=total_frames,
                selected_indices=frame_indices,
            )

            # 帧标签映射
            label_map = {
                frame_indices[0]: "首帧",
                frame_indices[-1]: "尾帧",
            }

            frames: list[_FrameInfo] = []
            for i, frame_idx in enumerate(frame_indices):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                if not ret:
                    logger.warning(
                        "Failed to read frame, skipping",
                        tool=self.name,
                        request_id=request_id,
                        frame_index=frame_idx,
                    )
                    continue

                # 编码为 JPEG base64
                success, buf = cv2.imencode(".jpg", frame)
                if not success:
                    logger.warning(
                        "Failed to encode frame, skipping",
                        tool=self.name,
                        request_id=request_id,
                        frame_index=frame_idx,
                    )
                    continue

                b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

                if frame_idx in label_map:
                    label = f"视频第{i+1}帧({label_map[frame_idx]})"
                else:
                    label = f"视频第{i+1}帧"

                frames.append(_FrameInfo(
                    b64=b64,
                    media_type="image/jpeg",
                    label=label,
                    index=0,  # 由 execute() 重新赋值
                ))

            if not frames:
                raise ToolExecutionError(
                    f"Failed to extract any frames from video: {video_path}"
                )

            logger.info(
                "Keyframes extracted",
                tool=self.name,
                request_id=request_id,
                total_video_frames=total_frames,
                extracted_count=len(frames),
            )

            return frames

        finally:
            cap.release()

    @staticmethod
    def _compute_keyframe_indices(total_frames: int) -> list[int]:
        """
        计算要提取的关键帧索引：首帧+尾帧+均匀分布3帧。

        Args:
            total_frames: 视频总帧数

        Returns:
            去重后的帧索引列表（升序）
        """
        if total_frames <= 5:
            return list(range(total_frames))

        first = 0
        last = total_frames - 1

        # 均匀分布3帧（在首尾之间）
        step = last / 4  # 分成4段取中间3个点
        mid_indices = [int(step * i) for i in range(1, 4)]

        all_indices = sorted(set([first] + mid_indices + [last]))
        return all_indices

    # ==================== 帧分析 ====================

    async def _analyze_image(
        self,
        image_b64: str,
        media_type: str,
        ad_category: str,
        image_index: int,
        total_images: int,
        request_id: str,
    ) -> tuple[list[ViolationItem], float, str, bool]:
        """
        调用通义千问视觉模型分析单帧。

        Args:
            image_b64: 帧的 base64 编码
            media_type: MIME 类型（如 "image/jpeg"）
            ad_category: 广告品类
            image_index: 当前帧序号（从 1 开始）
            total_images: 帧总数
            request_id: 请求 ID

        Returns:
            (违规项列表, 置信度, 图片内容描述, 是否含品牌标识)

        Raises:
            ToolTimeoutError: API 超时
            ToolExecutionError: 响应解析失败
        """
        user_prompt = self._prompt_template.format(
            ad_category=ad_category,
            image_index=image_index,
            total_images=total_images,
        )

        try:
            raw_response = await self._call_qwen_vision(
                image_b64=image_b64,
                media_type=media_type,
                user_prompt=user_prompt,
                request_id=request_id,
            )
        except httpx.TimeoutException as e:
            raise ToolTimeoutError(
                f"Qwen VL API timeout after {settings.llm_timeout}s"
            ) from e
        except httpx.HTTPError as e:
            raise ToolExecutionError(f"Qwen VL API HTTP error: {e}") from e

        # 解析响应（Qwen VL 可能返回 ```json ... ``` 包裹的内容，需剥离）
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            llm_result = _LLMResponse.model_validate_json(cleaned)
        except Exception as e:
            raise ToolExecutionError(
                f"Failed to parse Qwen VL response: {e}\nRaw: {raw_response[:500]}"
            ) from e

        # 转换为标准 ViolationItem
        violations: list[ViolationItem] = []
        for v in llm_result.violations:
            try:
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.IMAGE_SAFETY,
                        description=v.description,
                        regulation_ref=v.regulation_ref,
                        severity=ViolationSeverity(v.severity),
                        evidence=v.evidence,
                    )
                )
            except ValueError:
                logger.warning(
                    "Invalid severity in Qwen VL response, defaulting to medium",
                    tool=self.name,
                    request_id=request_id,
                    raw_severity=v.severity,
                )
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.IMAGE_SAFETY,
                        description=v.description,
                        regulation_ref=v.regulation_ref,
                        severity=ViolationSeverity.MEDIUM,
                        evidence=v.evidence,
                    )
                )

        return (
            violations,
            llm_result.confidence,
            llm_result.image_description,
            llm_result.has_brand,
        )

    async def _call_qwen_vision(
        self,
        image_b64: str,
        media_type: str,
        user_prompt: str,
        request_id: str,
    ) -> str:
        """
        调用通义千问视觉模型（OpenAI 兼容接口），带超时和重试。

        Args:
            image_b64: 帧 base64 编码
            media_type: MIME 类型
            user_prompt: 用户 Prompt
            request_id: 请求 ID

        Returns:
            模型响应的文本内容（应为 JSON 字符串）

        Raises:
            httpx.TimeoutException: 超时
            httpx.HTTPError: HTTP 错误
            ToolExecutionError: 响应格式异常
        """
        url = f"{settings.dashscope_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }

        data_uri = f"data:{media_type};base64,{image_b64}"

        payload = {
            "model": settings.qwen_vl_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一位专业的广告视觉内容安全审核专家。请严格按照要求输出 JSON 格式。",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_uri,
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                },
            ],
            "temperature": 0.1,
        }

        last_error: Exception | None = None
        for attempt in range(1, settings.llm_max_retries + 1):
            try:
                logger.debug(
                    "Calling Qwen VL API",
                    tool=self.name,
                    request_id=request_id,
                    attempt=attempt,
                )
                async with httpx.AsyncClient(
                    timeout=settings.llm_timeout
                ) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return content

            except httpx.TimeoutException:
                logger.warning(
                    "Qwen VL API timeout",
                    tool=self.name,
                    request_id=request_id,
                    attempt=attempt,
                )
                if attempt == settings.llm_max_retries:
                    raise
                last_error = httpx.TimeoutException(
                    f"Timeout on attempt {attempt}"
                )

            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Qwen VL API HTTP error",
                    tool=self.name,
                    request_id=request_id,
                    attempt=attempt,
                    status_code=e.response.status_code,
                )
                if attempt == settings.llm_max_retries:
                    raise
                last_error = e

        raise ToolExecutionError(f"All retries exhausted: {last_error}")

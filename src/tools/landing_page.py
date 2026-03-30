"""
落地页一致性核查 Tool。

检测策略：
1. httpx 异步抓取落地页 HTML
2. 提取 <title> 和 <body> 前 2000 字符作为页面内容摘要
3. 调用 DeepSeek 对比素材摘要与落地页内容的一致性
4. 落地页不可访问时标注 page_accessible=False，降级不阻塞流程
"""
import re
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base_tool import BaseTool, ToolExecutionError, ToolTimeoutError
from src.schemas.tool_io import LandingPageCheckerInput, LandingPageCheckerOutput
from src.schemas.violation import ViolationItem, ViolationDimension, ViolationSeverity
from src.config import settings
from src.harness.model_router import get_router


_BODY_MAX_CHARS = 2000


class _LLMViolation(BaseModel):
    """LLM 返回的单个违规项（内部解析用）。"""
    dimension: str = "landing_page"
    description: str
    regulation_ref: str
    severity: str
    evidence: str


class _LLMResponse(BaseModel):
    """LLM 完整响应（内部解析用）。"""
    violations: list[_LLMViolation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class LandingPageChecker(BaseTool):
    """
    落地页一致性核查。

    执行流程：
    1. 抓取落地页 HTML，提取标题和正文摘要
    2. 调用 DeepSeek 对比素材摘要与落地页内容
    3. 不可访问时标注 page_accessible=False 并降级

    Prompt 模板在 __init__ 时加载并缓存。
    """

    name: str = "landing_page_checker"
    description: str = "检测落地页内容与素材的一致性"

    def __init__(self) -> None:
        """
        初始化 LandingPageChecker，加载并缓存 Prompt 模板。

        Raises:
            ToolExecutionError: Prompt 文件加载失败
        """
        self._prompt_path = Path("src/prompts/landing_page.txt")
        self._prompt_template: str = self._load_prompt_file()

    def _load_prompt_file(self) -> str:
        """
        从 landing_page.txt 加载 Prompt 模板。

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

    async def execute(
        self, input: LandingPageCheckerInput
    ) -> LandingPageCheckerOutput:
        """
        执行落地页一致性检测。

        抓取落地页 → 提取内容 → 调用 DeepSeek 比对 → 输出结果。
        落地页不可访问时直接返回降级结果（page_accessible=False），
        不抛异常，不阻塞流程。

        Args:
            input: LandingPageCheckerInput，包含落地页 URL、素材摘要和请求 ID

        Returns:
            LandingPageCheckerOutput

        Raises:
            ToolExecutionError: LLM 输出解析失败
            ToolTimeoutError: LLM 调用超时
        """
        start_ms = time.monotonic()

        # Phase 1: 抓取落地页
        fetch_result = await self._fetch_page(
            input.landing_page_url, input.request_id
        )
        if fetch_result is None:
            # 落地页不可访问，降级处理
            logger.warning(
                "Landing page inaccessible, marking for human review",
                tool=self.name,
                request_id=input.request_id,
                url=input.landing_page_url,
            )
            return LandingPageCheckerOutput(
                violations=[],
                confidence=0.5,
                page_accessible=False,
                is_fallback=True,
                fallback_reason=(
                    f"落地页无法访问: {input.landing_page_url}，"
                    "已标注待人工复查"
                ),
            )

        page_content, page_title, content_summary = fetch_result

        logger.info(
            "Landing page fetched",
            tool=self.name,
            request_id=input.request_id,
            content_length=len(page_content),
            page_title=page_title,
        )

        # Phase 2: 调用 DeepSeek 比对一致性
        violations, confidence = await self._analyze_consistency(
            creative_summary=input.creative_summary,
            page_content=page_content,
            request_id=input.request_id,
        )

        duration_ms = (time.monotonic() - start_ms) * 1000
        logger.info(
            "Landing page check completed",
            tool=self.name,
            request_id=input.request_id,
            total_violations=len(violations),
            confidence=confidence,
            duration_ms=round(duration_ms, 1),
        )

        return LandingPageCheckerOutput(
            violations=violations,
            confidence=confidence,
            page_accessible=True,
            is_fallback=False,
            page_title=page_title,
            page_content_summary=content_summary,
        )

    async def _fallback(
        self, error: Exception, input: Any
    ) -> LandingPageCheckerOutput:
        """
        降级处理：返回低置信度结果，让流程走人工复核。

        Args:
            error: 导致降级的异常
            input: 原始 LandingPageCheckerInput

        Returns:
            降级的 LandingPageCheckerOutput（is_fallback=True, confidence < 0.7）
        """
        logger.warning(
            "Landing page checker falling back",
            tool=self.name,
            request_id=getattr(input, "request_id", "unknown"),
            error=str(error),
        )

        return LandingPageCheckerOutput(
            violations=[],
            confidence=0.5,
            page_accessible=False,
            is_fallback=True,
            fallback_reason=f"Tool fallback due to: {type(error).__name__}: {error}",
        )

    async def _fetch_page(
        self, url: str, request_id: str
    ) -> tuple[str, str, str] | None:
        """
        异步抓取落地页 HTML，提取标题和正文摘要。

        跟随重定向（httpx 默认行为）。不可访问时返回 None 而非抛异常，
        让调用方决定降级策略。

        Args:
            url: 落地页 URL
            request_id: 请求 ID

        Returns:
            (完整提取文本, 页面标题, 正文摘要前600字)，不可访问时返回 None
        """
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(
                "Landing page fetch failed",
                tool=self.name,
                request_id=request_id,
                url=url,
                error=str(e),
            )
            return None

        html = response.text
        return self._extract_content(html)

    def _extract_content(self, html: str) -> tuple[str, str, str]:
        """
        从 HTML 中提取 <title> 和 <body> 前 2000 字符的文本内容。

        使用正则提取，不依赖第三方 HTML 解析库。
        去除 <script>/<style> 标签内容和 HTML 标签后取纯文本。

        Args:
            html: 原始 HTML 字符串

        Returns:
            (完整提取文本, 页面标题, 正文摘要前600字)
        """
        parts: list[str] = []
        page_title = ""

        # 提取 <title>
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL
        )
        if title_match:
            page_title = title_match.group(1).strip()
            parts.append(f"页面标题: {page_title}")

        # 提取 <body> 内容
        body_match = re.search(
            r"<body[^>]*>(.*)</body>", html, re.IGNORECASE | re.DOTALL
        )
        body_text = body_match.group(1) if body_match else html

        # 去除 <script> 和 <style>
        body_text = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            "",
            body_text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # 去除所有 HTML 标签
        body_text = re.sub(r"<[^>]+>", " ", body_text)

        # 合并空白
        body_text = re.sub(r"\s+", " ", body_text).strip()

        # 截取前 _BODY_MAX_CHARS 字符
        parts.append(f"页面正文: {body_text[:_BODY_MAX_CHARS]}")

        full_text = "\n".join(parts)
        content_summary = body_text[:600]

        return full_text, page_title, content_summary

    async def _analyze_consistency(
        self,
        creative_summary: str,
        page_content: str,
        request_id: str,
    ) -> tuple[list[ViolationItem], float]:
        """
        调用 DeepSeek 对比素材摘要与落地页内容的一致性。

        Args:
            creative_summary: 素材内容摘要
            page_content: 提取后的落地页内容
            request_id: 请求 ID

        Returns:
            (违规项列表, 置信度)

        Raises:
            ToolTimeoutError: LLM 调用超时
            ToolExecutionError: LLM 输出解析失败
        """
        user_prompt = self._prompt_template.format(
            creative_summary=creative_summary,
            page_content=page_content,
        )

        try:
            raw_response = await self._call_deepseek(user_prompt, request_id)
        except httpx.TimeoutException as e:
            raise ToolTimeoutError(
                f"DeepSeek API timeout after {settings.llm_timeout}s"
            ) from e
        except httpx.HTTPError as e:
            raise ToolExecutionError(f"DeepSeek API HTTP error: {e}") from e

        # 解析 LLM 输出
        try:
            llm_result = _LLMResponse.model_validate_json(raw_response)
        except Exception as e:
            raise ToolExecutionError(
                f"Failed to parse LLM response: {e}\nRaw: {raw_response[:500]}"
            ) from e

        # 转换为标准 ViolationItem
        violations: list[ViolationItem] = []
        for v in llm_result.violations:
            try:
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.LANDING_PAGE,
                        description=v.description,
                        regulation_ref=v.regulation_ref,
                        severity=ViolationSeverity(v.severity),
                        evidence=v.evidence,
                    )
                )
            except ValueError:
                logger.warning(
                    "Invalid severity in LLM response, defaulting to medium",
                    tool=self.name,
                    request_id=request_id,
                    raw_severity=v.severity,
                )
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.LANDING_PAGE,
                        description=v.description,
                        regulation_ref=v.regulation_ref,
                        severity=ViolationSeverity.MEDIUM,
                        evidence=v.evidence,
                    )
                )

        return violations, llm_result.confidence

    async def _call_deepseek(self, user_prompt: str, request_id: str) -> str:
        """
        通过 ModelRouter 调用 DeepSeek Chat API。

        Args:
            user_prompt: 用户 Prompt
            request_id: 请求 ID

        Returns:
            LLM 响应的文本内容（应为 JSON 字符串）

        Raises:
            httpx.TimeoutException: 超时
            httpx.HTTPError: HTTP 错误
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一位专业的广告合规审核专家。"
                    "请严格按照要求输出 JSON 格式。"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        result = await get_router().call(
            "landing_page_analysis",
            messages=messages,
            response_format={"type": "json_object"},
        )
        return result["content"]

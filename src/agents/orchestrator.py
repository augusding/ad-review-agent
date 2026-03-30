"""
Tool 编排器：按优先级调度所有审核 Tool，收集结果到 ToolResultSet。

从 ReviewAgent 中提取的纯编排逻辑，不包含决策和结果构建。
"""
import time
from typing import Optional

from loguru import logger

from src.harness.tracer import ReviewTrace, set_current_trace

from src.schemas.request import ReviewRequest, AdCategory
from src.schemas.violation import (
    ViolationItem,
    ViolationDimension,
    ViolationSeverity,
)
from src.schemas.tool_io import (
    TextCheckerInput,
    TextCheckerOutput,
    ImageCheckerInput,
    ImageCheckerOutput,
    LandingPageCheckerInput,
    LandingPageCheckerOutput,
    QualificationCheckerInput,
    QualificationCheckerOutput,
    PlatformRuleCheckerInput,
    PlatformRuleCheckerOutput,
    ConsistencyCheckInput,
    ConsistencyCheckOutput,
)
from src.tools.text_checker import TextViolationChecker
from src.tools.image_checker import ImageContentChecker
from src.tools.landing_page import LandingPageChecker
from src.tools.qualification import QualificationChecker
from src.tools.platform_rule import PlatformRuleChecker
from src.tools.consistency_checker import ConsistencyChecker
from src.agents.types import ToolResultSet

# 需要资质检测的品类
_QUALIFICATION_CATEGORIES = {
    AdCategory.GAME,
    AdCategory.FINANCE,
    AdCategory.HEALTH,
    AdCategory.EDUCATION,
}


class ReviewOrchestrator:
    """
    Tool 编排器，按优先级顺序调度所有审核 Tool。

    编排流程（按优先级）：
    1. TextViolationChecker — 每条素材必跑
    2. ImageContentChecker — 有图片时执行
    3. LandingPageChecker — 有落地页 URL 时执行
    4. QualificationChecker — 特殊行业时执行
    5. PlatformRuleChecker — 所有素材执行
    6. ConsistencyChecker — 跨维度一致性检测

    提前终止：任意 Tool 发现 high 违规且 confidence > 0.9 时跳过后续。
    """

    def __init__(self) -> None:
        """实例化所有 6 个审核 Tool。"""
        self._text_checker = TextViolationChecker()
        self._image_checker = ImageContentChecker()
        self._landing_page_checker = LandingPageChecker()
        self._qualification_checker = QualificationChecker()
        self._platform_rule_checker = PlatformRuleChecker()
        self._consistency_checker = ConsistencyChecker()

    async def run(self, request: ReviewRequest) -> ToolResultSet:
        """
        按优先级执行所有审核 Tool，返回聚合结果。

        Args:
            request: ReviewRequest，包含素材内容和广告主信息

        Returns:
            ToolResultSet，包含所有 Tool 执行结果和汇总信息
        """
        start_ms = time.monotonic()
        rs = ToolResultSet()

        # 创建追踪上下文
        trace = ReviewTrace(request_id=request.request_id)
        set_current_trace(trace)

        logger.info(
            "Review started",
            request_id=request.request_id,
            ad_category=request.ad_category.value,
            creative_type=request.creative_type.value,
        )

        # ── Step 1: 文案检测（必跑） ──
        text_content = self._build_text_content(request)
        if text_content:
            rs.text = await self._run_text_check(
                text_content, request.ad_category.value, request.request_id
            )
            rs.checked_dimensions.append(ViolationDimension.TEXT_VIOLATION)
            rs.all_violations.extend(rs.text.violations)

            if rs.text.is_fallback:
                rs.has_fallback = True
                rs.fallback_reasons.append(rs.text.fallback_reason or "text_checker_fallback")
        else:
            rs.skipped_dimensions.append(ViolationDimension.TEXT_VIOLATION)
            rs.skip_reasons["text_violation"] = "无文案内容"

        # ── Step 2: 图片/视频检测（有图片或视频时） ──
        has_visual = bool(request.content.image_urls or request.content.video_url)
        if has_visual:
            rs.image = await self._run_image_check(
                request.content.image_urls,
                request.content.video_url,
                request.ad_category.value,
                request.request_id,
            )
            rs.checked_dimensions.append(ViolationDimension.IMAGE_SAFETY)
            rs.all_violations.extend(rs.image.violations)

            if rs.image.is_fallback:
                rs.has_fallback = True
                rs.fallback_reasons.append(rs.image.fallback_reason or "image_checker_fallback")
        else:
            rs.skipped_dimensions.append(ViolationDimension.IMAGE_SAFETY)
            rs.skip_reasons["image_safety"] = "无图片/视频素材"

        # ── Step 3: 落地页检测（有 URL 时） ──
        if request.content.landing_page_url:
            creative_summary = self._build_creative_summary(request)
            rs.landing_page = await self._run_landing_page_check(
                request.content.landing_page_url, creative_summary, request.request_id
            )
            rs.checked_dimensions.append(ViolationDimension.LANDING_PAGE)
            rs.all_violations.extend(rs.landing_page.violations)

            if rs.landing_page.is_fallback:
                rs.has_fallback = True
                rs.fallback_reasons.append(rs.landing_page.fallback_reason or "landing_page_fallback")
        else:
            rs.skipped_dimensions.append(ViolationDimension.LANDING_PAGE)
            rs.skip_reasons["landing_page"] = "无落地页URL"

        # ── Step 4: 资质检测（特殊行业时） ──
        if request.ad_category in _QUALIFICATION_CATEGORIES:
            rs.qualification = await self._run_qualification_check(
                request.ad_category.value,
                request.advertiser_qualification_ids,
                request.request_id,
            )
            rs.checked_dimensions.append(ViolationDimension.QUALIFICATION)
            rs.all_violations.extend(rs.qualification.violations)

            if rs.qualification.is_fallback:
                rs.has_fallback = True
                rs.fallback_reasons.append(rs.qualification.fallback_reason or "qualification_fallback")
        else:
            rs.skipped_dimensions.append(ViolationDimension.QUALIFICATION)
            rs.skip_reasons["qualification"] = "该品类无特殊资质要求"

        # ── Step 5: 平台专项规范检测（所有素材执行，优先级最低） ──
        rs.platform_rule = await self._run_platform_rule_check(request)
        rs.checked_dimensions.append(ViolationDimension.PLATFORM_RULE)
        rs.all_violations.extend(rs.platform_rule.violations)

        if rs.platform_rule.is_fallback:
            rs.has_fallback = True
            rs.fallback_reasons.append(
                rs.platform_rule.fallback_reason or "platform_rule_fallback"
            )

        # ── Step 6: 跨维度素材一致性检测 ──
        rs.consistency = await self._run_consistency_check(
            request=request,
            image_result=rs.image,
            landing_result=rs.landing_page,
            video_summary="",
            request_id=request.request_id,
        )
        if rs.consistency.checked_pairs:
            rs.checked_dimensions.append(ViolationDimension.CONSISTENCY)
            rs.all_violations.extend(rs.consistency.violations)

            if rs.consistency.is_fallback:
                rs.has_fallback = True
                rs.fallback_reasons.append(
                    rs.consistency.fallback_reason or "consistency_fallback"
                )
        else:
            rs.skipped_dimensions.append(ViolationDimension.CONSISTENCY)
            rs.skip_reasons["consistency"] = "单一素材类型，无需比对"

        rs.elapsed_ms = int((time.monotonic() - start_ms) * 1000)
        rs.trace = trace

        logger.info(
            "Review trace completed",
            request_id=request.request_id,
            **trace.to_structured_log(),
        )

        return rs

    # ==================== Tool 调用封装 ====================

    async def _run_text_check(
        self, text_content: str, ad_category: str, request_id: str
    ) -> TextCheckerOutput:
        """
        调用 TextViolationChecker。

        Args:
            text_content: 拼接后的文案全文
            ad_category: 广告品类
            request_id: 请求 ID

        Returns:
            TextCheckerOutput
        """
        input_data = TextCheckerInput(
            text_content=text_content,
            ad_category=ad_category,
            request_id=request_id,
        )
        return await self._text_checker.run(input_data)

    async def _run_image_check(
        self,
        image_urls: list[str],
        video_url: str | None,
        ad_category: str,
        request_id: str,
    ) -> ImageCheckerOutput:
        """
        调用 ImageContentChecker（图片+视频）。

        Args:
            image_urls: 图片 URL 列表
            video_url: 视频 URL（可选）
            ad_category: 广告品类
            request_id: 请求 ID

        Returns:
            ImageCheckerOutput
        """
        input_data = ImageCheckerInput(
            image_urls=image_urls,
            video_url=video_url,
            ad_category=ad_category,
            request_id=request_id,
        )
        return await self._image_checker.run(input_data)

    async def _run_landing_page_check(
        self, landing_page_url: str, creative_summary: str, request_id: str
    ) -> LandingPageCheckerOutput:
        """
        调用 LandingPageChecker。

        Args:
            landing_page_url: 落地页 URL
            creative_summary: 素材内容摘要
            request_id: 请求 ID

        Returns:
            LandingPageCheckerOutput
        """
        input_data = LandingPageCheckerInput(
            landing_page_url=landing_page_url,
            creative_summary=creative_summary,
            request_id=request_id,
        )
        return await self._landing_page_checker.run(input_data)

    async def _run_qualification_check(
        self, ad_category: str, qualification_ids: list[str], request_id: str
    ) -> QualificationCheckerOutput:
        """
        调用 QualificationChecker。

        Args:
            ad_category: 广告品类
            qualification_ids: 广告主提交的资质 ID 列表
            request_id: 请求 ID

        Returns:
            QualificationCheckerOutput
        """
        input_data = QualificationCheckerInput(
            ad_category=ad_category,
            qualification_ids=qualification_ids,
            request_id=request_id,
        )
        return await self._qualification_checker.run(input_data)

    async def _run_platform_rule_check(
        self, request: ReviewRequest
    ) -> PlatformRuleCheckerOutput:
        """
        调用 PlatformRuleChecker。

        Args:
            request: 审核请求

        Returns:
            PlatformRuleCheckerOutput
        """
        input_data = PlatformRuleCheckerInput(
            ad_category=request.ad_category.value,
            creative_type=request.creative_type.value,
            title=request.content.title,
            description=request.content.description,
            image_urls=request.content.image_urls,
            video_url=request.content.video_url,
            platform=request.platform.value,
            request_id=request.request_id,
        )
        return await self._platform_rule_checker.run(input_data)

    async def _run_consistency_check(
        self,
        request: ReviewRequest,
        image_result: ImageCheckerOutput | None,
        landing_result: LandingPageCheckerOutput | None,
        video_summary: str,
        request_id: str,
    ) -> ConsistencyCheckOutput:
        """
        调用 ConsistencyChecker 执行跨维度一致性检测。

        Args:
            request: 审核请求
            image_result: 图片检测结果（可能为 None）
            landing_result: 落地页检测结果（可能为 None）
            video_summary: 视频内容摘要
            request_id: 请求 ID

        Returns:
            ConsistencyCheckOutput
        """
        input_data = ConsistencyCheckInput(
            ad_title=request.content.title or "",
            ad_description=request.content.description or "",
            ad_cta=request.content.cta_text or "",
            ad_category=request.ad_category.value,
            image_urls=request.content.image_urls or [],
            image_descriptions=(
                image_result.image_descriptions if image_result else []
            ),
            has_brand_in_image=(
                image_result.has_brand_in_image if image_result else False
            ),
            video_summary=video_summary,
            landing_page_content=(
                landing_result.page_content_summary if landing_result else ""
            ),
            landing_page_title=(
                landing_result.page_title if landing_result else ""
            ),
            request_id=request_id,
        )
        return await self._consistency_checker.run(input_data)

    # ==================== 辅助方法 ====================

    def _build_text_content(self, request: ReviewRequest) -> str:
        """
        从 ReviewRequest 拼接文案全文（标题+描述+CTA）。

        Args:
            request: 审核请求

        Returns:
            拼接后的文案字符串，各部分用换行分隔
        """
        parts = []
        if request.content.title:
            parts.append(request.content.title)
        if request.content.description:
            parts.append(request.content.description)
        if request.content.cta_text:
            parts.append(request.content.cta_text)
        return "\n".join(parts)

    def _build_creative_summary(self, request: ReviewRequest) -> str:
        """
        构建素材内容摘要，用于落地页一致性比对。

        Args:
            request: 审核请求

        Returns:
            素材摘要文本
        """
        parts = [f"品类：{request.ad_category.value}"]
        if request.content.title:
            parts.append(f"标题：{request.content.title}")
        if request.content.description:
            parts.append(f"描述：{request.content.description}")
        if request.content.cta_text:
            parts.append(f"CTA：{request.content.cta_text}")
        return "\n".join(parts)

    def _should_early_terminate(
        self, violations: list[ViolationItem], confidence: float
    ) -> bool:
        """
        判断是否应提前终止后续 Tool 执行。

        条件：存在 high 严重度违规且置信度 > 0.9。

        Args:
            violations: 当前 Tool 的违规项列表
            confidence: 当前 Tool 的置信度

        Returns:
            是否应提前终止
        """
        if confidence <= 0.9:
            return False
        return any(v.severity == ViolationSeverity.HIGH for v in violations)

    def _remaining_dimensions(
        self,
        request: ReviewRequest,
        checked: list[ViolationDimension],
    ) -> list[ViolationDimension]:
        """
        计算因提前终止而被跳过的维度。

        Args:
            request: 审核请求（用于判断哪些维度本应执行）
            checked: 已执行的维度列表

        Returns:
            被跳过的维度列表
        """
        all_applicable: list[ViolationDimension] = [ViolationDimension.TEXT_VIOLATION]
        if request.content.image_urls or request.content.video_url:
            all_applicable.append(ViolationDimension.IMAGE_SAFETY)
        if request.content.landing_page_url:
            all_applicable.append(ViolationDimension.LANDING_PAGE)
        if request.ad_category in _QUALIFICATION_CATEGORIES:
            all_applicable.append(ViolationDimension.QUALIFICATION)
        all_applicable.append(ViolationDimension.PLATFORM_RULE)
        all_applicable.append(ViolationDimension.CONSISTENCY)

        return [d for d in all_applicable if d not in checked]

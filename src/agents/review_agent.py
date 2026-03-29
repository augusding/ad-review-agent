"""
Agent 主循环和编排逻辑。

按优先级顺序编排 Tool 调用，执行提前终止策略，
计算综合置信度，根据阈值输出最终审核结论。
"""
import time
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger

from src.config import settings
from src.schemas.request import ReviewRequest, AdCategory
from src.schemas.result import ReviewResult, ReviewVerdict
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

# 需要资质检测的品类
_QUALIFICATION_CATEGORIES = {
    AdCategory.GAME,
    AdCategory.FINANCE,
    AdCategory.HEALTH,
    AdCategory.EDUCATION,
}


class ReviewAgent:
    """
    广告素材合规审核 Agent。

    编排流程（按优先级）：
    1. TextViolationChecker — 每条素材必跑
    2. ImageContentChecker — 有图片时执行
    3. LandingPageChecker — 有落地页 URL 时执行
    4. QualificationChecker — 特殊行业时执行

    提前终止：任意 Tool 发现 high 违规且 confidence > 0.9 时跳过后续。
    综合置信度：取所有执行过 Tool 中最低的 confidence。
    """

    def __init__(self) -> None:
        """
        初始化 ReviewAgent，实例化所有 Tool 并加载 System Prompt。

        Tool 在 __init__ 时创建，复用规则库缓存和 Prompt 缓存。
        System Prompt 从 src/prompts/system_prompt.txt 加载并缓存。
        """
        self._text_checker = TextViolationChecker()
        self._image_checker = ImageContentChecker()
        self._landing_page_checker = LandingPageChecker()
        self._qualification_checker = QualificationChecker()
        self._platform_rule_checker = PlatformRuleChecker()
        self._consistency_checker = ConsistencyChecker()
        self._system_prompt: str = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """
        从 system_prompt.txt 加载主 System Prompt。

        Returns:
            Prompt 文本内容

        Raises:
            FileNotFoundError: 文件不存在时记录警告并返回兜底文本
        """
        prompt_path = Path("src/prompts/system_prompt.txt")
        try:
            return prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "system_prompt.txt not found, using fallback",
                path=str(prompt_path),
            )
            return "你是广告平台的审核助手，请用简洁友好的中文回答。"

    async def review(self, request: ReviewRequest) -> ReviewResult:
        """
        执行完整的广告素材合规审核。

        Args:
            request: ReviewRequest，包含素材内容和广告主信息

        Returns:
            ReviewResult，包含审核结论、违规项和面向广告主的说明
        """
        start_ms = time.monotonic()

        all_violations: list[ViolationItem] = []
        normal_confidences: list[float] = []  # 正常结果的置信度
        has_fallback = False
        fallback_reasons: list[str] = []
        checked_dimensions: list[ViolationDimension] = []
        skipped_dimensions: list[ViolationDimension] = []
        skip_reasons: dict[str, str] = {}

        # 保存中间结果用于一致性检测和 reviewer_hint 生成
        image_result: ImageCheckerOutput | None = None
        lp_result: LandingPageCheckerOutput | None = None
        text_result: TextCheckerOutput | None = None
        qual_result: QualificationCheckerOutput | None = None
        platform_result: PlatformRuleCheckerOutput | None = None
        consistency_result: ConsistencyCheckOutput | None = None

        logger.info(
            "Review started",
            request_id=request.request_id,
            ad_category=request.ad_category.value,
            creative_type=request.creative_type.value,
        )

        # ── Step 1: 文案检测（必跑） ──
        text_content = self._build_text_content(request)
        if text_content:
            text_result = await self._run_text_check(
                text_content, request.ad_category.value, request.request_id
            )
            checked_dimensions.append(ViolationDimension.TEXT_VIOLATION)
            all_violations.extend(text_result.violations)

            if text_result.is_fallback:
                has_fallback = True
                fallback_reasons.append(text_result.fallback_reason or "text_checker_fallback")
            else:
                normal_confidences.append(text_result.confidence)
        else:
            skipped_dimensions.append(ViolationDimension.TEXT_VIOLATION)
            skip_reasons["text_violation"] = "无文案内容"

        # ── Step 2: 图片/视频检测（有图片或视频时） ──
        has_visual = bool(request.content.image_urls or request.content.video_url)
        if has_visual:
            image_result = await self._run_image_check(
                request.content.image_urls,
                request.content.video_url,
                request.ad_category.value,
                request.request_id,
            )  # image_result 同时供一致性检测使用
            checked_dimensions.append(ViolationDimension.IMAGE_SAFETY)
            all_violations.extend(image_result.violations)

            if image_result.is_fallback:
                has_fallback = True
                fallback_reasons.append(image_result.fallback_reason or "image_checker_fallback")
            else:
                normal_confidences.append(image_result.confidence)
        else:
            skipped_dimensions.append(ViolationDimension.IMAGE_SAFETY)
            skip_reasons["image_safety"] = "无图片/视频素材"

        # ── Step 3: 落地页检测（有 URL 时） ──
        if request.content.landing_page_url:
            creative_summary = self._build_creative_summary(request)
            lp_result = await self._run_landing_page_check(
                request.content.landing_page_url, creative_summary, request.request_id
            )  # lp_result 同时供一致性检测使用
            checked_dimensions.append(ViolationDimension.LANDING_PAGE)
            all_violations.extend(lp_result.violations)

            if lp_result.is_fallback:
                has_fallback = True
                fallback_reasons.append(lp_result.fallback_reason or "landing_page_fallback")
            else:
                normal_confidences.append(lp_result.confidence)
        else:
            skipped_dimensions.append(ViolationDimension.LANDING_PAGE)
            skip_reasons["landing_page"] = "无落地页URL"

        # ── Step 4: 资质检测（特殊行业时） ──
        if request.ad_category in _QUALIFICATION_CATEGORIES:
            qual_result = await self._run_qualification_check(
                request.ad_category.value,
                request.advertiser_qualification_ids,
                request.request_id,
            )
            checked_dimensions.append(ViolationDimension.QUALIFICATION)
            all_violations.extend(qual_result.violations)

            if qual_result.is_fallback:
                has_fallback = True
                fallback_reasons.append(qual_result.fallback_reason or "qualification_fallback")
            else:
                normal_confidences.append(qual_result.confidence)
        else:
            skipped_dimensions.append(ViolationDimension.QUALIFICATION)
            skip_reasons["qualification"] = "该品类无特殊资质要求"

        # ── Step 5: 平台专项规范检测（所有素材执行，优先级最低） ──
        platform_result = await self._run_platform_rule_check(request)
        checked_dimensions.append(ViolationDimension.PLATFORM_RULE)
        all_violations.extend(platform_result.violations)

        if platform_result.is_fallback:
            has_fallback = True
            fallback_reasons.append(
                platform_result.fallback_reason or "platform_rule_fallback"
            )
        else:
            normal_confidences.append(platform_result.confidence)

        # ── Step 6: 跨维度素材一致性检测 ──
        consistency_result = await self._run_consistency_check(
            request=request,
            image_result=image_result,
            landing_result=lp_result,
            video_summary="",
            request_id=request.request_id,
        )
        if consistency_result.checked_pairs:
            checked_dimensions.append(ViolationDimension.CONSISTENCY)
            all_violations.extend(consistency_result.violations)

            if consistency_result.is_fallback:
                has_fallback = True
                fallback_reasons.append(
                    consistency_result.fallback_reason or "consistency_fallback"
                )
            else:
                normal_confidences.append(consistency_result.confidence)
        else:
            skipped_dimensions.append(ViolationDimension.CONSISTENCY)
            skip_reasons["consistency"] = "单一素材类型，无需比对"

        # 综合置信度计算（双轨策略，降级维度不参与）
        dim_to_result = {
            ViolationDimension.TEXT_VIOLATION: text_result,
            ViolationDimension.IMAGE_SAFETY: image_result,
            ViolationDimension.LANDING_PAGE: lp_result,
            ViolationDimension.QUALIFICATION: qual_result,
            ViolationDimension.PLATFORM_RULE: platform_result,
            ViolationDimension.CONSISTENCY: consistency_result,
        }

        if all_violations and normal_confidences:
            # 双轨：取发现违规的维度中的最高置信度
            # 只要任一维度高置信度发现严重违规，就足够退回
            violation_dims = set(v.dimension for v in all_violations)
            violation_confs = []
            for dim in violation_dims:
                r = dim_to_result.get(dim)
                if r and not getattr(r, "is_fallback", False):
                    violation_confs.append(r.confidence)
            # 用最高违规置信度（任一维度确信即可退回）
            min_confidence = max(violation_confs) if violation_confs else min(normal_confidences)
        elif normal_confidences:
            # 无违规：排除「无违规且 confidence 偏低」的辅助维度（图片/一致性），
            # 这些维度无违规时不应拉低整体置信度阻止 pass
            _NON_DRAG_TYPES = ("ImageCheckerOutput", "ConsistencyCheckOutput")
            core_confs = [
                c for dim, c in zip(
                    [ViolationDimension.TEXT_VIOLATION, ViolationDimension.IMAGE_SAFETY,
                     ViolationDimension.LANDING_PAGE, ViolationDimension.QUALIFICATION,
                     ViolationDimension.PLATFORM_RULE, ViolationDimension.CONSISTENCY],
                    normal_confidences,
                )
            ]
            # 重新计算：只用核心维度（文案/资质/平台/落地页）的置信度
            # 图片/一致性无违规时不参与 min 计算
            filtered_confs = []
            for dim_key in [ViolationDimension.TEXT_VIOLATION, ViolationDimension.IMAGE_SAFETY,
                            ViolationDimension.LANDING_PAGE, ViolationDimension.QUALIFICATION,
                            ViolationDimension.PLATFORM_RULE, ViolationDimension.CONSISTENCY]:
                r = dim_to_result.get(dim_key)
                if r is None or getattr(r, "is_fallback", False):
                    continue
                # 图片/一致性无违规时跳过（不拉低）
                if r.__class__.__name__ in _NON_DRAG_TYPES and not getattr(r, "violations", []):
                    continue
                filtered_confs.append(r.confidence)
            min_confidence = min(filtered_confs) if filtered_confs else min(normal_confidences)
        else:
            min_confidence = 0.5

        return await self._build_result(
            request=request,
            violations=all_violations,
            confidence=min_confidence,
            has_fallback=has_fallback,
            fallback_reasons=fallback_reasons,
            checked_dimensions=checked_dimensions,
            skipped_dimensions=skipped_dimensions,
            skip_reasons=skip_reasons,
            start_ms=start_ms,
            tool_results={
                "text": text_result,
                "image": image_result,
                "landing_page": lp_result,
                "qualification": qual_result,
                "platform_rule": platform_result,
                "consistency": consistency_result,
            },
        )

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

    def _determine_verdict(
        self,
        violations: list[ViolationItem],
        confidence: float,
        has_fallback: bool,
    ) -> ReviewVerdict:
        """
        确定最终 verdict。

        规则（confidence 已排除降级维度）：
        - confidence < 0.70 → review
        - 0.70 ≤ confidence < 0.92 → review
        - confidence ≥ 0.92 且有违规 → returned（退回修改）
        - confidence ≥ 0.92 且无违规且无降级 → pass
        - confidence ≥ 0.92 且无违规但有降级 → review（降级维度待补查）

        注意：reject 保留用于极严重违规（涉政/色情/暴力），
        由人工审核员手动标记，系统自动输出统一用 returned。

        Args:
            violations: 所有违规项
            confidence: 综合置信度（已排除降级维度）
            has_fallback: 是否有 Tool 降级

        Returns:
            ReviewVerdict 枚举值
        """
        if confidence < settings.human_review_lower:
            logger.debug(
                "Verdict path: low confidence → review",
                confidence=confidence,
            )
            return ReviewVerdict.REVIEW

        if confidence < settings.auto_pass_threshold:
            logger.debug(
                "Verdict path: medium confidence → review",
                confidence=confidence,
            )
            return ReviewVerdict.REVIEW

        # confidence ≥ 0.92
        if violations:
            logger.debug("Verdict path: high confidence + violations → returned")
            return ReviewVerdict.RETURNED

        if has_fallback:
            # 无违规+高置信度：降级的维度不阻塞通过
            # （如落地页超时但文案/资质/平台全部合规 → 允许 pass）
            if not violations and confidence >= settings.auto_pass_threshold:
                logger.debug(
                    "Verdict path: high confidence + no violations + non-critical fallback → pass",
                    confidence=confidence,
                )
                return ReviewVerdict.PASS
            logger.debug("Verdict path: high confidence + fallback → review")
            return ReviewVerdict.REVIEW

        logger.debug("Verdict path: high confidence + no violations → pass")
        return ReviewVerdict.PASS

    async def _generate_reason(
        self,
        violations: list[ViolationItem],
        verdict: ReviewVerdict,
        request: ReviewRequest,
    ) -> str:
        """
        调用 DeepSeek 生成面向广告主的中文说明和修改建议。

        Args:
            violations: 违规项列表
            verdict: 审核结论
            request: 原始请求

        Returns:
            面向广告主的中文说明文字
        """
        if verdict == ReviewVerdict.PASS and not violations:
            return (
                "素材各维度审核通过，符合 vivo 广告平台投放规范"
                "及《中华人民共和国广告法》相关要求，可以正常投放。"
                "如有疑问请联系您的客户经理。"
            )

        if verdict == ReviewVerdict.REVIEW and not violations:
            return (
                "您的广告素材正在进行人工复核，"
                "预计在2小时内完成审核。"
                "如有疑问请联系您的客户经理。"
            )

        if not violations:
            return "素材需要人工复核，请耐心等待审核结果。"

        # 构建违规摘要（按 action_required 分组）
        from src.schemas.violation import ActionRequired
        blocking = [v for v in violations if v.action_required == ActionRequired.BLOCKING]
        recommended = [v for v in violations if v.action_required == ActionRequired.RECOMMENDED]
        advisory = [v for v in violations if v.action_required == ActionRequired.ADVISORY]

        violation_lines = []
        for i, v in enumerate(violations, start=1):
            violation_lines.append(
                f"{i}. [{v.action_required.value}] {v.description}\n"
                f"   证据：{v.evidence}\n"
                f"   依据：{v.regulation_ref}"
            )
        violation_text = "\n".join(violation_lines)

        user_prompt = (
            f"根据以下审核发现，生成面向广告主的修改指南。\n\n"
            f"品类：{request.ad_category.value}\n"
            f"审核发现的问题（共{len(violations)}处）：\n{violation_text}\n\n"
            f"其中必须修改{len(blocking)}处，建议修改{len(recommended)}处，"
            f"提示关注{len(advisory)}处。\n\n"
            f"要求：\n"
            f"1. 开头用一句话说明「已完成审核，发现X处需要调整」\n"
            f"2. 按严重程度分组列出每个问题：\n"
            f"   - 必须修改（违反广告法/平台强制要求）\n"
            f"   - 建议修改（影响投放效果或用户体验）\n"
            f"3. 每个问题给出：具体问题、法规依据（一句话）、修改建议（1-2个可替换表达）\n"
            f"4. 结尾告知：修改完成后重新提交，如有疑问请联系客户经理\n"
            f"5. 语气：像专业的广告合规顾问在帮助客户，不要像在宣判处罚\n"
            f"输出纯文本，300字以内，不使用 JSON。"
        )

        try:
            reason = await self._call_deepseek_for_reason(user_prompt, request.request_id)
            return reason
        except Exception as e:
            logger.warning(
                "Failed to generate reason via LLM, using fallback",
                request_id=request.request_id,
                error=str(e),
            )
            return f"已完成审核，发现{len(violations)}处需要调整，请修改后重新提交：\n{violation_text}"

    async def _call_deepseek_for_reason(
        self, user_prompt: str, request_id: str
    ) -> str:
        """
        调用 DeepSeek 生成 reason 文本（轻量调用，不需 JSON 格式）。

        Args:
            user_prompt: 用户 Prompt
            request_id: 请求 ID

        Returns:
            生成的中文说明文字

        Raises:
            Exception: API 调用失败
        """
        url = f"{settings.deepseek_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        reason_system_prompt = (
            "你是 vivo 广告平台的合规服务专员，"
            "你的工作是帮助广告主理解问题并顺利完成投放，"
            "而不是拒绝他们。语气专业、友好、具体。"
        )
        payload = {
            "model": settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": reason_system_prompt,
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 800,
        }

        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    def _build_reviewer_hint(
        self,
        verdict: ReviewVerdict,
        confidence: float,
        violations: list[ViolationItem],
        checked_dimensions: list[ViolationDimension],
        skipped_dimensions: list[ViolationDimension],
        skip_reasons: dict[str, str],
        has_fallback: bool,
        fallback_reasons: list[str],
        tool_results: dict,
    ) -> str:
        """
        构建面向审核员的复核提示，使用模板拼接，不调用 LLM。

        包含各维度检测结果摘要、推人工复核原因和审核建议。

        Args:
            verdict: 审核结论
            confidence: 综合置信度
            violations: 所有违规项
            checked_dimensions: 已执行的检测维度
            skipped_dimensions: 被跳过的维度
            skip_reasons: 维度被跳过的原因
            has_fallback: 是否有 Tool 降级
            fallback_reasons: 降级原因列表
            tool_results: 各 Tool 的中间结果

        Returns:
            格式化的审核员提示文本
        """
        lines: list[str] = ["【各维度检测结果】"]

        # 维度名称映射
        dim_labels = {
            ViolationDimension.TEXT_VIOLATION: "文案检测",
            ViolationDimension.IMAGE_SAFETY: "图片检测",
            ViolationDimension.LANDING_PAGE: "落地页检测",
            ViolationDimension.QUALIFICATION: "资质检测",
            ViolationDimension.PLATFORM_RULE: "平台规范",
            ViolationDimension.CONSISTENCY: "一致性检测",
        }

        # 各维度结果
        text_r = tool_results.get("text")
        image_r = tool_results.get("image")
        lp_r = tool_results.get("landing_page")
        qual_r = tool_results.get("qualification")
        platform_r = tool_results.get("platform_rule")
        consistency_r = tool_results.get("consistency")

        # 文案检测
        if ViolationDimension.TEXT_VIOLATION in checked_dimensions:
            if text_r and text_r.violations:
                v_count = len(text_r.violations)
                lines.append(
                    f"❌ 文案检测：发现{v_count}项违规，"
                    f"置信度 {text_r.confidence:.2f}"
                )
            elif text_r and text_r.is_fallback:
                lines.append(
                    f"⚠️ 文案检测：降级处理（{text_r.fallback_reason}）"
                )
            elif text_r:
                lines.append(
                    f"✅ 文案检测：通过（无违禁词），"
                    f"置信度 {text_r.confidence:.2f}"
                )
        elif ViolationDimension.TEXT_VIOLATION in skipped_dimensions:
            reason = skip_reasons.get("text_violation", "无文案内容")
            if "提前终止" in reason:
                lines.append(f"⏭️ 文案检测：跳过（{reason}）")
            else:
                lines.append(f"— 文案检测：跳过（{reason}）")

        # 图片检测
        if ViolationDimension.IMAGE_SAFETY in checked_dimensions:
            if image_r and image_r.violations:
                v_count = len(image_r.violations)
                lines.append(
                    f"❌ 图片检测：发现{v_count}项违规，"
                    f"置信度 {image_r.confidence:.2f}"
                )
            elif image_r and image_r.is_fallback:
                lines.append(
                    f"⚠️ 图片检测：降级处理（{image_r.fallback_reason}）"
                )
            elif image_r:
                lines.append(
                    f"✅ 图片检测：通过，"
                    f"置信度 {image_r.confidence:.2f}"
                )
            # 图片描述信息
            if image_r and image_r.image_descriptions:
                descs = "；".join(image_r.image_descriptions)
                lines.append(f"   图片内容：{descs}")
            if image_r and image_r.has_brand_in_image:
                lines.append("   品牌标识：检测到图片包含品牌Logo")
        elif ViolationDimension.IMAGE_SAFETY in skipped_dimensions:
            reason = skip_reasons.get("image_safety", "无图片/视频素材")
            if "提前终止" in reason:
                lines.append(f"⏭️ 图片检测：跳过（{reason}）")
            else:
                lines.append(f"— 图片检测：跳过（{reason}）")

        # 落地页检测
        if ViolationDimension.LANDING_PAGE in checked_dimensions:
            if lp_r and lp_r.violations:
                v_count = len(lp_r.violations)
                lines.append(
                    f"❌ 落地页检测：发现{v_count}项不一致，"
                    f"置信度 {lp_r.confidence:.2f}"
                )
            elif lp_r and not lp_r.page_accessible:
                lines.append("⚠️ 落地页检测：页面无法访问，已标记待复查")
            elif lp_r and lp_r.is_fallback:
                lines.append(
                    f"⚠️ 落地页检测：降级处理（{lp_r.fallback_reason}）"
                )
            elif lp_r:
                lines.append(
                    f"✅ 落地页检测：通过，"
                    f"置信度 {lp_r.confidence:.2f}"
                )
            if lp_r and lp_r.page_title:
                lines.append(f"   落地页标题：{lp_r.page_title}")
        elif ViolationDimension.LANDING_PAGE in skipped_dimensions:
            reason = skip_reasons.get("landing_page", "未提供落地页URL")
            if "提前终止" in reason:
                lines.append(f"⏭️ 落地页检测：跳过（{reason}）")
            else:
                lines.append(f"— 落地页检测：跳过（{reason}）")

        # 资质检测
        if ViolationDimension.QUALIFICATION in checked_dimensions:
            if qual_r and qual_r.violations:
                missing = "、".join(qual_r.missing_qualifications) if qual_r.missing_qualifications else "未知"
                lines.append(
                    f"❌ 资质检测：缺少{missing}，"
                    f"置信度 {qual_r.confidence:.2f}"
                )
            elif qual_r and qual_r.is_fallback:
                lines.append(
                    f"⚠️ 资质检测：降级处理（{qual_r.fallback_reason}）"
                )
            elif qual_r:
                lines.append(
                    f"✅ 资质检测：通过，"
                    f"置信度 {qual_r.confidence:.2f}"
                )
        elif ViolationDimension.QUALIFICATION in skipped_dimensions:
            reason = skip_reasons.get("qualification", "该品类无资质要求")
            if "提前终止" in reason:
                lines.append(f"⏭️ 资质检测：跳过（{reason}）")
            else:
                lines.append(f"— 资质检测：跳过（{reason}）")

        # 平台规范
        if ViolationDimension.PLATFORM_RULE in checked_dimensions:
            if platform_r and platform_r.violations:
                v_count = len(platform_r.violations)
                lines.append(
                    f"❌ 平台规范：发现{v_count}项违规，"
                    f"置信度 {platform_r.confidence:.2f}"
                )
            elif platform_r and platform_r.is_fallback:
                lines.append(
                    f"⚠️ 平台规范：降级处理（{platform_r.fallback_reason}）"
                )
            elif platform_r:
                lines.append(
                    f"✅ 平台规范：通过，"
                    f"置信度 {platform_r.confidence:.2f}"
                )

        # 一致性检测
        if ViolationDimension.CONSISTENCY in checked_dimensions:
            if consistency_r and consistency_r.violations:
                v_count = len(consistency_r.violations)
                pairs = "、".join(consistency_r.checked_pairs)
                lines.append(
                    f"❌ 一致性检测：发现{v_count}项不一致（{pairs}），"
                    f"置信度 {consistency_r.confidence:.2f}"
                )
            elif consistency_r and consistency_r.is_fallback:
                lines.append(
                    f"⚠️ 一致性检测：降级处理（{consistency_r.fallback_reason}）"
                )
            elif consistency_r:
                pairs = "、".join(consistency_r.checked_pairs)
                lines.append(
                    f"✅ 一致性检测：{pairs}均一致，"
                    f"置信度 {consistency_r.confidence:.2f}"
                )
        elif ViolationDimension.CONSISTENCY in skipped_dimensions:
            reason = skip_reasons.get("consistency", "单一素材类型，无需比对")
            if "提前终止" in reason:
                lines.append(f"⏭️ 一致性检测：跳过（{reason}）")
            else:
                lines.append(f"— 一致性检测：跳过（{reason}）")

        # 推人工复核原因
        if verdict == ReviewVerdict.REVIEW:
            lines.append("")
            lines.append("【推人工复核原因】")

            if has_fallback:
                for fr in fallback_reasons:
                    lines.append(f"- 部分检测降级：{fr}")

            if confidence < settings.auto_pass_threshold:
                lines.append(
                    f"- 综合置信度 {confidence:.2f}，"
                    f"未达自动通过阈值 {settings.auto_pass_threshold}"
                )
                # 找出哪些维度拉低了置信度
                low_dims: list[str] = []
                for name, result in tool_results.items():
                    if result and hasattr(result, "confidence"):
                        if result.confidence < settings.auto_pass_threshold:
                            label = {
                                "text": "文案检测",
                                "image": "图片检测",
                                "landing_page": "落地页检测",
                                "qualification": "资质检测",
                                "platform_rule": "平台规范",
                                "consistency": "一致性检测",
                            }.get(name, name)
                            low_dims.append(
                                f"{label}（{result.confidence:.2f}）"
                            )
                if low_dims:
                    lines.append(
                        f"- 置信度不足维度：{'、'.join(low_dims)}"
                    )

            if violations:
                lines.append(
                    f"- 发现{len(violations)}项疑似违规但置信度不足以自动拒绝"
                )

        # 审核建议
        lines.append("")
        lines.append("【审核建议】")
        if verdict == ReviewVerdict.REVIEW:
            violation_dims = set(v.dimension.value for v in violations)
            if not violations and not has_fallback:
                lines.append("各维度均无明确违规，仅置信度不足。建议：通过")
            elif not violations and has_fallback:
                lines.append(
                    "部分检测维度执行异常，无法自动判断。"
                    "建议：重点核查降级维度对应的素材内容"
                )
            elif violations:
                v_summary = []
                for v in violations:
                    v_summary.append(f"{v.description}")
                lines.append(
                    f"发现疑似问题，建议重点核查：\n"
                    + "\n".join(f"  · {s}" for s in v_summary)
                )
        elif verdict in (ReviewVerdict.RETURNED, ReviewVerdict.REJECT):
            from src.schemas.violation import ActionRequired
            blocking = sum(1 for v in violations if v.action_required == ActionRequired.BLOCKING)
            recommended = sum(1 for v in violations if v.action_required == ActionRequired.RECOMMENDED)
            advisory = sum(1 for v in violations if v.action_required == ActionRequired.ADVISORY)
            lines.append(
                f"Agent 高置信度（{confidence:.2f}）判定需退回修改，"
                f"共{len(violations)}项问题"
                f"（必须修改{blocking}项，建议修改{recommended}项，提示关注{advisory}项）"
            )
        elif verdict == ReviewVerdict.PASS:
            lines.append("各维度检测均通过，无违规项")

        return "\n".join(lines)

    async def _build_result(
        self,
        request: ReviewRequest,
        violations: list[ViolationItem],
        confidence: float,
        has_fallback: bool,
        fallback_reasons: list[str],
        checked_dimensions: list[ViolationDimension],
        skipped_dimensions: list[ViolationDimension],
        skip_reasons: dict[str, str],
        start_ms: float,
        tool_results: dict | None = None,
    ) -> ReviewResult:
        """
        构建最终 ReviewResult，包含 verdict 计算和 reason 生成。

        Args:
            request: 原始审核请求
            violations: 所有违规项
            confidence: 综合置信度
            has_fallback: 是否有 Tool 降级
            fallback_reasons: 降级原因列表
            checked_dimensions: 已执行的检测维度
            skipped_dimensions: 被跳过的维度
            skip_reasons: 维度被跳过的原因
            start_ms: 起始时间戳（monotonic）
            tool_results: 各 Tool 的中间结果（用于生成 reviewer_hint）

        Returns:
            完整的 ReviewResult
        """
        verdict = self._determine_verdict(violations, confidence, has_fallback)

        reason = await self._generate_reason(violations, verdict, request)

        reviewer_hint = self._build_reviewer_hint(
            verdict=verdict,
            confidence=confidence,
            violations=violations,
            checked_dimensions=checked_dimensions,
            skipped_dimensions=skipped_dimensions,
            skip_reasons=skip_reasons,
            has_fallback=has_fallback,
            fallback_reasons=fallback_reasons,
            tool_results=tool_results or {},
        )

        processing_ms = int((time.monotonic() - start_ms) * 1000)

        fallback_reason_text = "; ".join(fallback_reasons) if fallback_reasons else None

        logger.info(
            "Review completed",
            request_id=request.request_id,
            verdict=verdict.value,
            confidence=confidence,
            total_violations=len(violations),
            checked_dimensions=[d.value for d in checked_dimensions],
            skipped_dimensions=[d.value for d in skipped_dimensions],
            processing_ms=processing_ms,
            is_fallback=has_fallback,
        )

        return ReviewResult(
            request_id=request.request_id,
            verdict=verdict,
            confidence=confidence,
            violations=violations,
            reason=reason,
            reviewer_hint=reviewer_hint,
            checked_dimensions=checked_dimensions,
            skipped_dimensions=skipped_dimensions,
            skip_reasons=skip_reasons,
            processing_ms=processing_ms,
            model_used=settings.deepseek_model,
            reviewed_at=datetime.utcnow(),
            is_fallback=has_fallback,
            fallback_reason=fallback_reason_text,
        )

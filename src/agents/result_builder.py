"""
结果构建器：将 ToolResultSet + Decision 组装为最终 ReviewResult。

职责：
- 生成面向广告主的中文说明（reason）
- 生成面向审核员的复核提示（reviewer_hint）
- 组装 ReviewResult
"""
import time
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger

from src.config import settings
from src.schemas.request import ReviewRequest
from src.schemas.result import ReviewResult, ReviewVerdict
from src.schemas.violation import (
    ViolationItem,
    ViolationDimension,
    ActionRequired,
)
from src.agents.types import ToolResultSet, Decision


class ResultBuilder:
    """
    将 ToolResultSet 和 Decision 组装为最终 ReviewResult。

    包含：reason 生成（LLM 调用）、reviewer_hint 模板拼接、
    system_prompt 加载。
    """

    def __init__(self) -> None:
        """初始化 ResultBuilder，加载 System Prompt。"""
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

    async def build(
        self,
        request: ReviewRequest,
        tool_results: ToolResultSet,
        decision: Decision,
        start_ms: float,
    ) -> ReviewResult:
        """
        构建最终 ReviewResult。

        Args:
            request: 原始审核请求
            tool_results: 所有 Tool 的聚合执行结果
            decision: 决策引擎输出
            start_ms: 起始时间戳（monotonic）

        Returns:
            完整的 ReviewResult
        """
        violations = tool_results.all_violations
        verdict = decision.verdict
        confidence = decision.confidence

        tool_results_dict = {
            "text": tool_results.text,
            "image": tool_results.image,
            "landing_page": tool_results.landing_page,
            "qualification": tool_results.qualification,
            "platform_rule": tool_results.platform_rule,
            "consistency": tool_results.consistency,
        }

        reason = await self._generate_reason(violations, verdict, request)

        reviewer_hint = self._build_reviewer_hint(
            verdict=verdict,
            confidence=confidence,
            violations=violations,
            checked_dimensions=tool_results.checked_dimensions,
            skipped_dimensions=tool_results.skipped_dimensions,
            skip_reasons=tool_results.skip_reasons,
            has_fallback=tool_results.has_fallback,
            fallback_reasons=tool_results.fallback_reasons,
            tool_results=tool_results_dict,
        )

        processing_ms = int((time.monotonic() - start_ms) * 1000)

        fallback_reason_text = (
            "; ".join(tool_results.fallback_reasons)
            if tool_results.fallback_reasons
            else None
        )

        logger.info(
            "Review completed",
            request_id=request.request_id,
            verdict=verdict.value,
            confidence=confidence,
            total_violations=len(violations),
            checked_dimensions=[d.value for d in tool_results.checked_dimensions],
            skipped_dimensions=[d.value for d in tool_results.skipped_dimensions],
            processing_ms=processing_ms,
            is_fallback=tool_results.has_fallback,
        )

        return ReviewResult(
            request_id=request.request_id,
            verdict=verdict,
            confidence=confidence,
            violations=violations,
            reason=reason,
            reviewer_hint=reviewer_hint,
            checked_dimensions=tool_results.checked_dimensions,
            skipped_dimensions=tool_results.skipped_dimensions,
            skip_reasons=tool_results.skip_reasons,
            processing_ms=processing_ms,
            model_used=settings.deepseek_model,
            reviewed_at=datetime.utcnow(),
            is_fallback=tool_results.has_fallback,
            fallback_reason=fallback_reason_text,
        )

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

"""
跨维度素材一致性检测 Tool。

检测策略：
1. 文案 vs 图片/视频：品牌身份冲突、产品类别错位
2. 文案 vs 落地页：价格承诺、产品身份、核心功能兑现
3. 图片 vs 落地页：品牌标识一致性（仅在图片含品牌标识时触发）

所有判断标准从 consistency_rules.json 读取，支持热更新。
"""
import json
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base_tool import BaseTool, ToolExecutionError, ToolTimeoutError
from src.schemas.tool_io import ConsistencyCheckInput, ConsistencyCheckOutput
from src.schemas.violation import ViolationItem, ViolationDimension, ViolationSeverity
from src.config import settings


class _ConsistencyLLMResult(BaseModel):
    """LLM 一致性判断响应（内部解析用）。"""
    consistent: bool
    confidence: float = Field(ge=0.0, le=1.0)
    violated_dimension: str = ""
    reason: str = ""
    specific_evidence: str = ""
    severity: str = "medium"
    brand_conflict: bool = False
    brand_conflict_confidence: float = 0.0


class ConsistencyChecker(BaseTool):
    """
    跨维度素材一致性检测。

    执行流程：
    1. 加载 consistency_rules.json 规则（支持热更新）
    2. 按规则启用状态依次检测各素材对
    3. 调用 DeepSeek 进行语义一致性判断
    4. 合并所有检测结果

    判断标准全部从规则文件读取，不硬编码。
    """

    name: str = "consistency_checker"
    description: str = "跨维度素材一致性检测"

    def __init__(self) -> None:
        """
        初始化 ConsistencyChecker，设置规则文件路径。

        规则文件在首次访问 rules 属性时加载，支持热更新。
        """
        self._rules: dict | None = None
        self._rules_loaded_at: float = 0.0
        self._rules_path = Path(settings.rules_dir) / "consistency_rules.json"

    @property
    def rules(self) -> dict:
        """
        获取一致性检测规则，支持热更新。

        通过比较文件修改时间实现热更新，无需重启服务。

        Returns:
            规则字典

        Raises:
            ToolExecutionError: 规则文件不存在或解析失败
        """
        try:
            mtime = self._rules_path.stat().st_mtime
        except FileNotFoundError as e:
            raise ToolExecutionError(
                f"Consistency rules file not found: {self._rules_path}"
            ) from e

        if self._rules is None or mtime > self._rules_loaded_at:
            try:
                self._rules = json.loads(
                    self._rules_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as e:
                raise ToolExecutionError(
                    f"Failed to load consistency rules: {e}"
                ) from e
            self._rules_loaded_at = mtime
            logger.info(
                "Consistency rules reloaded",
                tool=self.name,
                version=self._rules.get("version"),
            )
        return self._rules

    async def execute(
        self, input: ConsistencyCheckInput
    ) -> ConsistencyCheckOutput:
        """
        执行跨维度素材一致性检测。

        依次检测文案vs图片、文案vs视频、文案vs落地页、图片vs落地页，
        每对检测的启用状态和判断标准均从规则文件读取。

        Args:
            input: ConsistencyCheckInput，包含各维度的素材内容摘要

        Returns:
            ConsistencyCheckOutput，包含违规项列表和已检测的素材对
        """
        start_ms = time.monotonic()

        violations: list[ViolationItem] = []
        checked_pairs: list[str] = []
        confidences: list[float] = []

        # 检测1：文案 vs 图片（优先视觉直判，降级文字方案）
        cc_rules = self.rules.get("creative_consistency", {})
        if cc_rules.get("enabled") and (input.ad_title or input.ad_description):
            vision_done = False
            # 优先：Qwen VL 直接看图+文案（单跳，更准确）
            if input.image_urls:
                result = await self._check_creative_consistency_with_vision(
                    ad_title=input.ad_title,
                    ad_description=input.ad_description,
                    image_urls=input.image_urls,
                    pair_name="文案vs图片",
                    rules=cc_rules,
                    request_id=input.request_id,
                )
                if result is not None:
                    violations.extend(result["violations"])
                    if result.get("confidence") is not None:
                        confidences.append(result["confidence"])
                    checked_pairs.append("文案vs图片")
                    vision_done = True
            # 降级：用文字描述方案（两跳）
            if not vision_done and input.image_descriptions:
                result = await self._check_creative_consistency(
                    content_a=f"广告文案 标题：{input.ad_title} 描述：{input.ad_description}",
                    content_b=f"图片内容描述：{'; '.join(input.image_descriptions)}",
                    pair_name="文案vs图片",
                    rules=cc_rules,
                    request_id=input.request_id,
                )
                if result:
                    violations.extend(result["violations"])
                    if result.get("confidence") is not None:
                        confidences.append(result["confidence"])
                    checked_pairs.append("文案vs图片")

        # 检测2：文案 vs 视频
        if (cc_rules.get("enabled")
                and input.video_summary
                and (input.ad_title or input.ad_description)):
            result = await self._check_creative_consistency(
                content_a=f"广告文案 标题：{input.ad_title} 描述：{input.ad_description}",
                content_b=f"视频内容描述：{input.video_summary}",
                pair_name="文案vs视频",
                rules=cc_rules,
                request_id=input.request_id,
            )
            if result:
                violations.extend(result["violations"])
                if result.get("confidence") is not None:
                    confidences.append(result["confidence"])
                checked_pairs.append("文案vs视频")

        # 检测3：文案 vs 落地页
        lc_rules = self.rules.get("landing_consistency", {})
        if (lc_rules.get("enabled")
                and input.landing_page_content
                and (input.ad_title or input.ad_description)):
            result = await self._check_landing_consistency(
                ad_content=(
                    f"标题：{input.ad_title}\n"
                    f"描述：{input.ad_description}\n"
                    f"CTA：{input.ad_cta}"
                ),
                landing_content=(
                    f"落地页标题：{input.landing_page_title}\n"
                    f"内容：{input.landing_page_content[:600]}"
                ),
                rules=lc_rules,
                request_id=input.request_id,
            )
            if result:
                violations.extend(result["violations"])
                if result.get("confidence") is not None:
                    confidences.append(result["confidence"])
                checked_pairs.append("文案vs落地页")

        # 检测4：图片 vs 落地页（仅在图片含品牌标识时触发）
        il_rules = self.rules.get("image_landing_consistency", {})
        if (il_rules.get("enabled")
                and input.image_descriptions
                and input.landing_page_content
                and input.has_brand_in_image):
            result = await self._check_creative_consistency(
                content_a=f"图片品牌/内容：{'; '.join(input.image_descriptions)}",
                content_b=(
                    f"落地页：{input.landing_page_title} "
                    f"{input.landing_page_content[:300]}"
                ),
                pair_name="图片vs落地页",
                rules=il_rules,
                request_id=input.request_id,
            )
            if result:
                violations.extend(result["violations"])
                if result.get("confidence") is not None:
                    confidences.append(result["confidence"])
                checked_pairs.append("图片vs落地页")

        duration_ms = (time.monotonic() - start_ms) * 1000
        final_confidence = min(confidences) if confidences else 1.0

        logger.info(
            "Consistency check completed",
            tool=self.name,
            request_id=input.request_id,
            checked_pairs=checked_pairs,
            total_violations=len(violations),
            confidence=final_confidence,
            duration_ms=round(duration_ms, 1),
        )

        return ConsistencyCheckOutput(
            violations=violations,
            confidence=final_confidence,
            checked_pairs=checked_pairs,
        )

    async def _check_creative_consistency(
        self,
        content_a: str,
        content_b: str,
        pair_name: str,
        rules: dict,
        request_id: str,
    ) -> dict | None:
        """
        检测内容描述一致性（文案vs图片/视频，图片vs落地页）。

        判断标准从 rules 参数读取，包括阈值、检测维度、正反例。

        Args:
            content_a: 素材内容 A 的描述
            content_b: 素材内容 B 的描述
            pair_name: 素材对名称（用于日志和违规描述）
            rules: 对应的规则配置
            request_id: 请求 ID

        Returns:
            包含 violations 和 confidence 的字典，检测失败时返回 None
        """
        threshold_reject = rules.get("confidence_threshold_reject", 0.85)
        threshold_review = rules.get("confidence_threshold_review", 0.70)

        dimensions = rules.get("check_dimensions", [])
        enabled_dims = [d for d in dimensions if d.get("enabled")]

        violation_examples: list[str] = []
        normal_examples: list[str] = []
        for dim in enabled_dims:
            violation_examples.extend(dim.get("examples_violation", []))
            normal_examples.extend(dim.get("examples_normal", []))

        non_violation_patterns = rules.get("non_violation_patterns", [])

        prompt = (
            "你是广告合规审核专家，判断以下两部分广告内容是否存在明显不一致。\n\n"
            f"【内容A】\n{content_a}\n\n"
            f"【内容B】\n{content_b}\n\n"
            "【需要检测的违规情形】（以下情形应判为不一致）：\n"
            + "\n".join(f"- {e}" for e in violation_examples)
            + "\n\n【正常情形，不应判为违规】：\n"
            + "\n".join(f"- {e}" for e in normal_examples + non_violation_patterns)
            + "\n\n【判断原则】：\n"
            "- 只检测明显的品牌混淆和产品类别欺诈\n"
            "- 图文不需要完全对应，广告允许用氛围图/场景图\n"
            "- 模糊情况宁可放行，避免误拒合规素材\n"
            "- confidence 反映你的确信程度，不确定时给低分\n\n"
            "输出严格JSON（不含markdown）：\n"
            "{\n"
            '  "consistent": true或false,\n'
            '  "confidence": 0.0到1.0,\n'
            '  "violated_dimension": "brand_identity或product_category或空字符串",\n'
            '  "reason": "不一致的具体描述，consistent=true时为空字符串",\n'
            '  "specific_evidence": "具体证据文字，consistent=true时为空字符串",\n'
            '  "severity": "high或medium或low"\n'
            "}"
        )

        try:
            raw = await self._call_deepseek(prompt, request_id)
            result = _ConsistencyLLMResult.model_validate_json(raw)

            if not result.consistent:
                if result.confidence >= threshold_reject:
                    severity = ViolationSeverity.HIGH
                elif result.confidence >= threshold_review:
                    severity = ViolationSeverity.MEDIUM
                else:
                    return {"violations": [], "confidence": result.confidence}

                violation = ViolationItem(
                    dimension=ViolationDimension.CONSISTENCY,
                    description=f"[{pair_name}不一致] {result.reason}",
                    regulation_ref=(
                        "《互联网广告管理办法》第九条：广告内容应当真实，"
                        "不得含有虚假或引人误解的内容"
                    ),
                    severity=severity,
                    evidence=f"{pair_name}：{result.specific_evidence}",
                )
                return {
                    "violations": [violation],
                    "confidence": result.confidence,
                }

            return {"violations": [], "confidence": result.confidence}

        except Exception as e:
            logger.warning(
                "Creative consistency check failed",
                tool=self.name,
                pair_name=pair_name,
                error=str(e),
                request_id=request_id,
            )
            return None

    async def _check_landing_consistency(
        self,
        ad_content: str,
        landing_content: str,
        rules: dict,
        request_id: str,
    ) -> dict | None:
        """
        检测承诺兑现一致性（文案 vs 落地页）。

        判断标准从 rules 参数读取，包括阈值、检测维度、允许的正常情形。

        Args:
            ad_content: 广告内容摘要
            landing_content: 落地页内容摘要
            rules: 对应的规则配置
            request_id: 请求 ID

        Returns:
            包含 violations 和 confidence 的字典，检测失败时返回 None
        """
        threshold_reject = rules.get("confidence_threshold_reject", 0.80)
        threshold_review = rules.get("confidence_threshold_review", 0.65)

        dimensions = rules.get("check_dimensions", [])
        enabled_dims = [d for d in dimensions if d.get("enabled")]
        allowed_patterns = rules.get("allowed_patterns", [])

        violation_examples: list[str] = []
        for dim in enabled_dims:
            violation_examples.extend(dim.get("examples_violation", []))

        prompt = (
            "你是广告合规审核专家，判断广告承诺与落地页实际内容是否一致。\n\n"
            f"【广告内容】\n{ad_content}\n\n"
            f"【落地页内容】\n{landing_content}\n\n"
            "【必须检测的违规情形】：\n"
            + "\n".join(f"- {e}" for e in violation_examples)
            + "\n\n【允许的正常情形，不应判为违规】：\n"
            + "\n".join(f"- {p}" for p in allowed_patterns)
            + "\n\n【判断原则】：\n"
            "- 重点检测价格欺诈（免费变收费）和产品身份欺诈\n"
            "- 落地页信息比广告多是正常的\n"
            "- 广告和落地页设计风格不同不算违规\n"
            "- 只有明确的承诺被违背才判为不一致\n\n"
            "输出严格JSON（不含markdown）：\n"
            "{\n"
            '  "consistent": true或false,\n'
            '  "confidence": 0.0到1.0,\n'
            '  "violated_dimension": "price_promise或product_identity或core_function或空字符串",\n'
            '  "reason": "不一致的具体描述",\n'
            '  "specific_evidence": "具体证据",\n'
            '  "severity": "high或medium或low"\n'
            "}"
        )

        try:
            raw = await self._call_deepseek(prompt, request_id)
            result = _ConsistencyLLMResult.model_validate_json(raw)

            if not result.consistent:
                if result.confidence >= threshold_reject:
                    severity = ViolationSeverity.HIGH
                elif result.confidence >= threshold_review:
                    severity = ViolationSeverity.MEDIUM
                else:
                    return {"violations": [], "confidence": result.confidence}

                violation = ViolationItem(
                    dimension=ViolationDimension.CONSISTENCY,
                    description=f"[文案vs落地页不一致] {result.reason}",
                    regulation_ref=(
                        "《广告法》第二十八条：广告内容与实际情况不符"
                        "构成虚假广告"
                    ),
                    severity=severity,
                    evidence=f"广告承诺与落地页不符：{result.specific_evidence}",
                )
                return {
                    "violations": [violation],
                    "confidence": result.confidence,
                }

            return {"violations": [], "confidence": result.confidence}

        except Exception as e:
            logger.warning(
                "Landing consistency check failed",
                tool=self.name,
                error=str(e),
                request_id=request_id,
            )
            return None

    async def _check_creative_consistency_with_vision(
        self,
        ad_title: str,
        ad_description: str,
        image_urls: list[str],
        pair_name: str,
        rules: dict,
        request_id: str,
    ) -> dict | None:
        """
        使用 Qwen VL 直接看图+文案，单跳判断图文一致性。

        比两跳方案（Qwen描述→DeepSeek判断）更准确，
        因为视觉模型直接看到原始图片，不损失信息。

        Args:
            ad_title: 广告标题
            ad_description: 广告描述
            image_urls: 原始图片URL列表
            pair_name: 素材对名称
            rules: 规则配置
            request_id: 请求 ID

        Returns:
            包含 violations 和 confidence 的字典，失败时返回 None
        """
        images_b64 = []
        for url in image_urls[:3]:
            try:
                b64, _ = await self._download_image_b64(url, request_id)
                images_b64.append(b64)
            except Exception as e:
                logger.warning(
                    "Image download failed for consistency vision check",
                    url=url, error=str(e), request_id=request_id,
                )
                continue

        if not images_b64:
            return None

        threshold_reject = rules.get("confidence_threshold_reject", 0.85)
        threshold_review = rules.get("confidence_threshold_review", 0.70)

        dimensions = rules.get("check_dimensions", [])
        violation_examples = []
        normal_examples = []
        for dim in dimensions:
            if dim.get("enabled"):
                violation_examples.extend(dim.get("examples_violation", []))
                normal_examples.extend(dim.get("examples_normal", []))
        non_violation_patterns = rules.get("non_violation_patterns", [])

        text_prompt = (
            "你是广告合规审核专家。请同时查看图片和广告文案，判断两者是否存在明显不一致。\n\n"
            f"【广告文案】\n标题：{ad_title}\n描述：{ad_description}\n\n"
            "【需要检测的违规情形】（以下情形应判为不一致）：\n"
            + "\n".join(f"- {e}" for e in violation_examples)
            + "\n\n【正常情形，不应判为违规】：\n"
            + "\n".join(f"- {e}" for e in normal_examples + non_violation_patterns)
            + "\n\n【判断原则】：\n"
            "- 你直接看图片，不依赖文字描述，判断更准确\n"
            "- 只检测明显的品牌混淆和产品类别欺诈\n"
            "- 图文不需要完全对应，广告允许用氛围图/场景图\n"
            "- 图片品牌与文案品牌明显不同 → 不一致\n"
            "- 图片产品类别与文案产品类别完全不同 → 不一致\n"
            "- 图片是使用场景/氛围图，文案是功能介绍 → 正常，一致\n"
            "- 合规素材请给 confidence >= 0.92\n\n"
            "输出严格JSON（不含markdown）：\n"
            "{\n"
            '  "consistent": true或false,\n'
            '  "confidence": 0.0到1.0,\n'
            '  "brand_conflict": true或false,\n'
            '  "brand_conflict_confidence": 0.0到1.0,\n'
            '  "reason": "不一致的具体描述，consistent=true时为空字符串",\n'
            '  "specific_evidence": "具体证据，consistent=true时为空字符串",\n'
            '  "severity": "high或medium或low"\n'
            "}"
        )

        try:
            url = f"{settings.dashscope_base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
            }

            content = []
            for b64 in images_b64:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            content.append({"type": "text", "text": text_prompt})

            payload = {
                "model": settings.qwen_vl_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.1,
            }

            async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]

            raw = raw.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw = "\n".join(lines)

            result = _ConsistencyLLMResult.model_validate_json(raw)

            brand_threshold = rules.get("brand_conflict_threshold", 0.75)
            if result.brand_conflict and result.brand_conflict_confidence >= brand_threshold:
                violation = ViolationItem(
                    dimension=ViolationDimension.CONSISTENCY,
                    description=f"[{pair_name}品牌冲突] {result.reason}",
                    regulation_ref=(
                        "《互联网广告管理办法》第九条：广告内容应当真实，"
                        "不得含有虚假或引人误解的内容"
                    ),
                    severity=ViolationSeverity.HIGH,
                    evidence=f"品牌冲突：{result.specific_evidence}",
                )
                return {"violations": [violation], "confidence": result.brand_conflict_confidence}

            if not result.consistent:
                if result.confidence >= threshold_reject:
                    severity = ViolationSeverity.HIGH
                elif result.confidence >= threshold_review:
                    severity = ViolationSeverity.MEDIUM
                else:
                    return {"violations": [], "confidence": result.confidence}
                violation = ViolationItem(
                    dimension=ViolationDimension.CONSISTENCY,
                    description=f"[{pair_name}不一致] {result.reason}",
                    regulation_ref="《互联网广告管理办法》第九条",
                    severity=severity,
                    evidence=f"{pair_name}：{result.specific_evidence}",
                )
                return {"violations": [violation], "confidence": result.confidence}

            return {"violations": [], "confidence": result.confidence}

        except Exception as e:
            logger.warning(
                "Qwen vision consistency check failed, will fallback to text",
                tool=self.name, error=str(e), request_id=request_id,
            )
            return None

    async def _download_image_b64(
        self, image_url: str, request_id: str
    ) -> tuple[str, str]:
        """
        下载图片并返回 base64 编码。

        Args:
            image_url: 图片 URL
            request_id: 请求 ID

        Returns:
            (base64 编码字符串, MIME 类型)

        Raises:
            Exception: 下载失败
        """
        import base64

        async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
            response = await client.get(image_url)
            response.raise_for_status()

        content_type = response.headers.get("content-type", "image/jpeg")
        media_type = content_type.split(";")[0].strip()
        image_b64 = base64.b64encode(response.content).decode("utf-8")
        return image_b64, media_type

    async def _fallback(
        self, error: Exception, input: Any
    ) -> ConsistencyCheckOutput:
        """
        降级处理：返回空结果，不阻塞主流程。

        Args:
            error: 导致降级的异常
            input: 原始 ConsistencyCheckInput

        Returns:
            降级的 ConsistencyCheckOutput（is_fallback=True）
        """
        logger.warning(
            "ConsistencyChecker fallback",
            tool=self.name,
            request_id=getattr(input, "request_id", "unknown"),
            error=str(error),
        )
        return ConsistencyCheckOutput(
            violations=[],
            confidence=1.0,
            checked_pairs=[],
            is_fallback=True,
            fallback_reason=f"一致性检测失败：{type(error).__name__}: {error}",
        )

    async def _call_deepseek(self, prompt: str, request_id: str) -> str:
        """
        调用 DeepSeek Chat API（OpenAI 兼容接口），带超时和重试。

        Args:
            prompt: 用户 Prompt
            request_id: 请求 ID

        Returns:
            LLM 响应的文本内容（应为 JSON 字符串）

        Raises:
            ToolTimeoutError: 超时
            ToolExecutionError: HTTP 错误或响应格式异常
        """
        url = f"{settings.deepseek_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是广告合规审核专家，严格按要求输出JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        last_error: Exception | None = None
        for attempt in range(1, settings.llm_max_retries + 1):
            try:
                logger.debug(
                    "Calling DeepSeek API for consistency check",
                    tool=self.name,
                    request_id=request_id,
                    attempt=attempt,
                )
                async with httpx.AsyncClient(
                    timeout=settings.llm_timeout
                ) as client:
                    resp = await client.post(
                        url, headers=headers, json=payload
                    )
                    resp.raise_for_status()

                data = resp.json()
                return data["choices"][0]["message"]["content"]

            except httpx.TimeoutException:
                logger.warning(
                    "DeepSeek API timeout in consistency check",
                    tool=self.name,
                    request_id=request_id,
                    attempt=attempt,
                )
                if attempt == settings.llm_max_retries:
                    raise ToolTimeoutError(
                        f"DeepSeek timeout after {settings.llm_timeout}s"
                    )
                last_error = httpx.TimeoutException(
                    f"Timeout on attempt {attempt}"
                )

            except httpx.HTTPStatusError as e:
                logger.warning(
                    "DeepSeek API HTTP error in consistency check",
                    tool=self.name,
                    request_id=request_id,
                    attempt=attempt,
                    status_code=e.response.status_code,
                )
                if attempt == settings.llm_max_retries:
                    raise ToolExecutionError(
                        f"DeepSeek HTTP error: {e}"
                    ) from e
                last_error = e

        raise ToolExecutionError(f"All retries exhausted: {last_error}")

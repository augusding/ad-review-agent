"""
文案违禁词与违禁表述检测 Tool。

检测策略：
1. 规则库匹配（高速）：基于 forbidden_words.json 进行关键词和正则匹配
2. LLM 语义理解（深度）：使用 DeepSeek 检测变体表述和隐性违规

两阶段结果合并后输出。
"""
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base_tool import BaseTool, ToolExecutionError, ToolTimeoutError
from src.schemas.tool_io import TextCheckerInput, TextCheckerOutput
from src.schemas.violation import ViolationItem, ViolationDimension, ViolationSeverity
from src.config import settings


class _LLMViolation(BaseModel):
    """LLM 返回的单个违规项（内部解析用）。"""
    dimension: str = "text_violation"
    description: str
    regulation_ref: str
    severity: str
    evidence: str


class _LLMResponse(BaseModel):
    """LLM 完整响应（内部解析用）。"""
    violations: list[_LLMViolation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class TextViolationChecker(BaseTool):
    """
    文案违禁词与违禁表述检测。

    执行流程：
    1. 加载违禁词规则库，对文案进行关键词 + 正则匹配
    2. 调用 DeepSeek LLM 进行语义级违规检测（覆盖变体和隐性违规）
    3. 合并两阶段结果，去重后输出

    规则库和 Prompt 模板在 __init__ 时加载并缓存，
    规则库支持热更新（通过文件 mtime 检测）。
    """

    name: str = "text_violation_checker"
    description: str = "检测广告文案违禁词和违禁表述"

    def __init__(self) -> None:
        """
        初始化 TextViolationChecker，加载并缓存规则库和 Prompt 模板。

        Raises:
            ToolExecutionError: 规则库或 Prompt 文件加载失败
        """
        self._rules_path = Path(settings.rules_dir) / "forbidden_words.json"
        self._prompt_path = Path("src/prompts/text_checker.txt")
        self._few_shot_path = Path("src/prompts/few_shot_examples.txt")

        from src.rules_loader import load_rules_sync
        self._rules: dict = load_rules_sync("forbidden_words")
        self._rules_loaded_at: float = time.monotonic()

        self._few_shot_examples: str = self._load_few_shot_file()
        self._prompt_template: str = self._load_prompt_file()

    def _load_prompt_file(self) -> str:
        """
        从 text_checker.txt 加载 Prompt 模板。

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

    def _load_few_shot_file(self) -> str:
        """
        从 few_shot_examples.txt 加载 Few-shot 示例。

        Returns:
            Few-shot 示例文本

        Raises:
            ToolExecutionError: 文件不存在或读取失败
        """
        try:
            return self._few_shot_path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise ToolExecutionError(
                f"Few-shot examples file not found: {self._few_shot_path}"
            ) from e

    async def _maybe_reload_rules(self) -> None:
        """
        检查规则是否需要重新加载。优先从数据库加载，降级读文件。

        热更新失败时保留旧规则并记录警告，不中断审核流程。
        """
        try:
            from src.rules_loader import load_rules
            new_rules = await load_rules("forbidden_words")
            if new_rules and new_rules.get("version") != self._rules.get("version"):
                logger.info(
                    "Rules reloaded",
                    tool=self.name,
                    old_version=self._rules.get("version"),
                    new_version=new_rules.get("version"),
                )
                self._rules = new_rules
                self._rules_loaded_at = time.monotonic()
        except Exception as e:
            logger.warning(
                "Failed to reload rules, keeping cached version",
                tool=self.name,
                error=str(e),
            )

    async def execute(self, input: TextCheckerInput) -> TextCheckerOutput:
        """
        执行文案违规检测。

        Args:
            input: TextCheckerInput，包含待检测文案、品类和请求 ID

        Returns:
            TextCheckerOutput，包含违规项列表和置信度

        Raises:
            ToolExecutionError: 规则库加载失败或 LLM 输出解析失败
            ToolTimeoutError: LLM 调用超时
        """
        start_ms = time.monotonic()
        await self._maybe_reload_rules()

        # Phase 1: 规则库匹配
        rule_violations = self._rule_based_check(input.text_content, input.ad_category)
        logger.info(
            "Rule-based check completed",
            tool=self.name,
            request_id=input.request_id,
            rule_violations_count=len(rule_violations),
        )

        # Phase 2: LLM 语义检测
        llm_violations, llm_confidence = await self._llm_semantic_check(
            input.text_content,
            input.ad_category,
            rule_violations,
            input.request_id,
        )
        logger.info(
            "LLM semantic check completed",
            tool=self.name,
            request_id=input.request_id,
            llm_violations_count=len(llm_violations),
            llm_confidence=llm_confidence,
        )

        # 合并结果
        all_violations = rule_violations + llm_violations

        # 计算最终置信度
        if rule_violations:
            # 规则库命中 → 高确信度（规则是确定性匹配，至少 0.92）
            confidence = max(0.92, llm_confidence)
            logger.debug(
                "Confidence path: rule_violations hit",
                tool=self.name,
                request_id=input.request_id,
                confidence=confidence,
            )
        elif llm_violations:
            # 仅 LLM 命中 → 采用 LLM 自身置信度
            confidence = llm_confidence
            logger.debug(
                "Confidence path: llm_violations only",
                tool=self.name,
                request_id=input.request_id,
                confidence=confidence,
            )
        else:
            # 两者均无违规 → 采用 LLM 置信度（通常 ≥ 0.90）
            confidence = llm_confidence
            logger.debug(
                "Confidence path: no violations",
                tool=self.name,
                request_id=input.request_id,
                confidence=confidence,
            )

        duration_ms = (time.monotonic() - start_ms) * 1000
        logger.info(
            "Text check completed",
            tool=self.name,
            request_id=input.request_id,
            total_violations=len(all_violations),
            confidence=confidence,
            duration_ms=round(duration_ms, 1),
        )

        return TextCheckerOutput(
            violations=all_violations,
            confidence=confidence,
            is_fallback=False,
        )

    async def _fallback(self, error: Exception, input: Any) -> TextCheckerOutput:
        """
        降级处理：返回低置信度结果，让流程走人工复核。

        规则库匹配结果仍然保留（如果能执行的话），
        但置信度设为低值以确保进入人工队列。

        Args:
            error: 导致降级的异常
            input: 原始 TextCheckerInput

        Returns:
            降级的 TextCheckerOutput（is_fallback=True, confidence < 0.7）
        """
        logger.warning(
            "Text checker falling back",
            tool=self.name,
            request_id=getattr(input, "request_id", "unknown"),
            error=str(error),
        )

        # 尝试保留规则库匹配结果
        rule_violations: list[ViolationItem] = []
        try:
            if isinstance(input, TextCheckerInput):
                rule_violations = self._rule_based_check(
                    input.text_content, input.ad_category
                )
        except Exception:
            pass  # 规则库也失败了，返回空列表

        return TextCheckerOutput(
            violations=rule_violations,
            confidence=0.5,
            is_fallback=True,
            fallback_reason=f"Tool fallback due to: {type(error).__name__}: {error}",
        )

    def _rule_based_check(
        self, text_content: str, ad_category: str
    ) -> list[ViolationItem]:
        """
        基于规则库的关键词和正则匹配检测。

        Args:
            text_content: 广告文案全文
            ad_category: 广告品类

        Returns:
            规则库命中的违规项列表
        """
        rules = self._rules.get("rules", {})
        violations: list[ViolationItem] = []

        for rule_key, rule_config in rules.items():
            # 品类专项规则只在对应品类时检测
            if rule_key == "game_specific" and ad_category != "game":
                continue
            if rule_key == "finance_specific" and ad_category != "finance":
                continue

            severity = ViolationSeverity(rule_config["severity"])
            regulation_ref = rule_config["regulation_ref"]
            description_prefix = rule_config["description"]

            # 关键词匹配
            for word in rule_config.get("words", []):
                if word in text_content:
                    violations.append(
                        ViolationItem(
                            dimension=ViolationDimension.TEXT_VIOLATION,
                            description=f"{description_prefix}：包含违禁词「{word}」",
                            regulation_ref=regulation_ref,
                            severity=severity,
                            evidence=word,
                        )
                    )

            # 正则模式匹配
            for pattern in rule_config.get("patterns", []):
                match = re.search(pattern, text_content)
                if match:
                    matched_text = match.group(0)
                    violations.append(
                        ViolationItem(
                            dimension=ViolationDimension.TEXT_VIOLATION,
                            description=f"{description_prefix}：匹配到违规模式「{pattern}」",
                            regulation_ref=regulation_ref,
                            severity=severity,
                            evidence=matched_text,
                        )
                    )

        return violations

    async def _llm_semantic_check(
        self,
        text_content: str,
        ad_category: str,
        rule_violations: list[ViolationItem],
        request_id: str,
    ) -> tuple[list[ViolationItem], float]:
        """
        使用 DeepSeek LLM 进行语义级违规检测。

        Args:
            text_content: 广告文案全文
            ad_category: 广告品类
            rule_violations: 规则库已命中的违规项（告知 LLM 不重复检测）
            request_id: 请求 ID

        Returns:
            (LLM 发现的新违规项列表, 置信度)

        Raises:
            ToolTimeoutError: LLM 调用超时
            ToolExecutionError: LLM 输出解析失败
        """
        # 构建规则库已命中描述
        rule_based_desc = "无" if not rule_violations else "\n".join(
            f"- {v.description}（证据：{v.evidence}）" for v in rule_violations
        )

        user_prompt = self._prompt_template.format(
            text_content=text_content,
            ad_category=ad_category,
            rule_based_violations=rule_based_desc,
            few_shot_examples=self._few_shot_examples,
        )

        # 调用 DeepSeek（OpenAI 兼容接口）
        try:
            raw_response = await self._call_deepseek(user_prompt, request_id)
        except httpx.TimeoutException as e:
            raise ToolTimeoutError(
                f"DeepSeek API timeout after {settings.llm_timeout}s"
            ) from e
        except httpx.HTTPError as e:
            raise ToolExecutionError(f"DeepSeek API HTTP error: {e}") from e

        # 解析 LLM 输出（DeepSeek 偶发返回 ```json ... ``` 包裹，需剥离）
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:]  # 去掉 ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            llm_result = _LLMResponse.model_validate_json(cleaned)
        except Exception as e:
            raise ToolExecutionError(
                f"Failed to parse LLM response: {e}\nRaw: {raw_response[:500]}"
            ) from e

        # 转换为标准 ViolationItem
        violations = []
        for v in llm_result.violations:
            try:
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.TEXT_VIOLATION,
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
                        dimension=ViolationDimension.TEXT_VIOLATION,
                        description=v.description,
                        regulation_ref=v.regulation_ref,
                        severity=ViolationSeverity.MEDIUM,
                        evidence=v.evidence,
                    )
                )

        return violations, llm_result.confidence

    async def _call_deepseek(self, user_prompt: str, request_id: str) -> str:
        """
        调用 DeepSeek Chat API（OpenAI 兼容接口），带超时和重试。

        Args:
            user_prompt: 用户 Prompt
            request_id: 请求 ID

        Returns:
            LLM 响应的文本内容（应为 JSON 字符串）

        Raises:
            httpx.TimeoutException: 超时
            httpx.HTTPError: HTTP 错误
            ToolExecutionError: 响应格式异常
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
                    "content": "你是一位专业的广告合规审核专家。请严格按照要求输出 JSON 格式。",
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        last_error: Exception | None = None
        for attempt in range(1, settings.llm_max_retries + 1):
            try:
                logger.debug(
                    "Calling DeepSeek API",
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
                    "DeepSeek API timeout",
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
                    "DeepSeek API HTTP error",
                    tool=self.name,
                    request_id=request_id,
                    attempt=attempt,
                    status_code=e.response.status_code,
                )
                if attempt == settings.llm_max_retries:
                    raise
                last_error = e

        # 不应到达这里，但保险起见
        raise ToolExecutionError(f"All retries exhausted: {last_error}")

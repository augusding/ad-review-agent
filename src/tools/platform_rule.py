"""
vivo 平台专项规范检测 Tool。

检测策略（纯规则匹配，不调用 LLM）：
1. 品牌保护：检查是否冒用 vivo 品牌
2. 竞品攻击：检查是否贬低竞品品牌
3. 品类专项规则：检查品类特定的禁用表述

规则文件支持热更新（通过文件 mtime 检测）。
"""
import json
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from src.tools.base_tool import BaseTool, ToolExecutionError
from src.schemas.tool_io import PlatformRuleCheckerInput, PlatformRuleCheckerOutput
from src.schemas.violation import ViolationItem, ViolationDimension, ViolationSeverity

# vivo 品牌相关词汇
_VIVO_BRAND_KEYWORDS = [
    "vivo", "OriginOS", "iQOO", "Funtouch",
    "X Fold", "X Flip", "X100", "X90", "X80",
    "S系列", "Y系列", "T系列",
    "XEA20", "TWS",
]

# 竞品品牌名
_COMPETITOR_BRANDS = [
    "华为", "Huawei", "小米", "Xiaomi", "OPPO", "荣耀", "Honor",
    "三星", "Samsung", "苹果", "Apple", "iPhone", "iPad",
    "realme", "一加", "OnePlus", "魅族", "Meizu", "中兴", "ZTE",
    "联想", "Lenovo", "努比亚", "nubia", "红米", "Redmi",
]

# 贬低性词汇
_NEGATIVE_WORDS = [
    "不如", "比不上", "差", "落后", "淘汰", "垃圾",
    "更好用", "更强", "更快", "碾压", "吊打", "秒杀",
    "不行", "太慢", "卡顿",
]


class PlatformRuleChecker(BaseTool):
    """
    vivo 平台专项规范检测。

    检测流程：
    1. 品牌保护检测：vivo 品牌冒用
    2. 竞品攻击检测：贬低其他手机品牌
    3. 品类专项规则检测：品类特定的禁用表述

    规则文件在 __init__ 时加载并缓存，支持热更新。
    """

    name: str = "platform_rule_checker"
    description: str = "检测 vivo 平台专项规范违规"

    def __init__(self) -> None:
        """
        初始化 PlatformRuleChecker，加载规则文件。

        Raises:
            ToolExecutionError: 规则文件加载失败
        """
        from src.rules_loader import load_rules_sync
        self._platform_rules: dict = load_rules_sync("vivo_platform")
        self._category_rules: dict = load_rules_sync("category_rules")

    async def _maybe_reload_rules(self) -> None:
        """检查规则是否需要重新加载。优先数据库，降级文件。"""
        try:
            from src.rules_loader import load_rules
            new_platform = await load_rules("vivo_platform")
            if new_platform and new_platform.get("version") != self._platform_rules.get("version"):
                self._platform_rules = new_platform
                logger.info("Platform rules reloaded", tool=self.name)
            new_category = await load_rules("category_rules")
            if new_category and new_category.get("version") != self._category_rules.get("version"):
                self._category_rules = new_category
                logger.info("Category rules reloaded", tool=self.name)
        except Exception as e:
            logger.warning("Failed to reload rules", tool=self.name, error=str(e))

    async def execute(
        self, input: PlatformRuleCheckerInput
    ) -> PlatformRuleCheckerOutput:
        """
        执行 vivo 平台专项规范检测。

        Args:
            input: PlatformRuleCheckerInput

        Returns:
            PlatformRuleCheckerOutput，包含违规项列表和置信度
        """
        start_ms = time.monotonic()
        await self._maybe_reload_rules()

        text_content = self._build_text(input)
        all_violations: list[ViolationItem] = []

        # 1. 品牌保护检测
        brand_violations = self._check_brand_protection(text_content, input.request_id)
        all_violations.extend(brand_violations)

        # 2. 竞品攻击检测
        competitor_violations = self._check_competitor_attack(
            text_content, input.request_id
        )
        all_violations.extend(competitor_violations)

        # 3. 品类专项规则检测
        category_violations = self._check_category_rules(
            text_content, input.ad_category, input.request_id
        )
        all_violations.extend(category_violations)

        # 置信度：规则匹配是确定性的，无违规时高置信度
        if all_violations:
            confidence = 0.95
        else:
            confidence = 0.98

        duration_ms = (time.monotonic() - start_ms) * 1000
        logger.info(
            "Platform rule check completed",
            tool=self.name,
            request_id=input.request_id,
            total_violations=len(all_violations),
            confidence=confidence,
            duration_ms=round(duration_ms, 1),
        )

        return PlatformRuleCheckerOutput(
            violations=all_violations,
            confidence=confidence,
            is_fallback=False,
        )

    async def _fallback(
        self, error: Exception, input: Any
    ) -> PlatformRuleCheckerOutput:
        """
        降级处理：返回低置信度结果，让流程走人工复核。

        Args:
            error: 导致降级的异常
            input: 原始输入

        Returns:
            降级的 PlatformRuleCheckerOutput
        """
        logger.warning(
            "Platform rule checker falling back",
            tool=self.name,
            request_id=getattr(input, "request_id", "unknown"),
            error=str(error),
        )

        return PlatformRuleCheckerOutput(
            violations=[],
            confidence=0.5,
            is_fallback=True,
            fallback_reason=f"Tool fallback due to: {type(error).__name__}: {error}",
        )

    # ==================== 检测方法 ====================

    def _build_text(self, input: PlatformRuleCheckerInput) -> str:
        """
        拼接文案全文用于规则匹配。

        Args:
            input: 检测输入

        Returns:
            拼接后的文案
        """
        parts = []
        if input.title:
            parts.append(input.title)
        if input.description:
            parts.append(input.description)
        return "\n".join(parts)

    def _check_brand_protection(
        self, text_content: str, request_id: str
    ) -> list[ViolationItem]:
        """
        检测文案中是否冒用 vivo 品牌。

        两阶段检测：
        1. 关键词匹配：检查 forbidden_keywords（vivo官方/vivo认证等）
           命中则直接判定违规（高置信度）
        2. 如果只是普通提及 vivo，属于正常产品名称或适配说明，
           不判定违规（allowed_uses）

        Args:
            text_content: 文案全文
            request_id: 请求 ID

        Returns:
            品牌冒用违规项列表
        """
        if not text_content:
            return []

        violations: list[ViolationItem] = []
        text_lower = text_content.lower()

        # 阶段1：检查明确的冒用表述（forbidden_keywords）
        brand_config = self._platform_rules.get("brand_protection", {})
        forbidden_keywords = brand_config.get("forbidden_keywords", [
            "vivo官方", "vivo认证", "vivo推荐", "vivo合作",
            "vivo授权", "vivo指定", "vivo独家",
        ])

        for keyword in forbidden_keywords:
            # 去掉空格做模糊匹配（防止「vivo 官方」「vivo  认证」绕过）
            normalized_text = re.sub(r"\s+", "", text_content)
            normalized_keyword = re.sub(r"\s+", "", keyword)
            if normalized_keyword.lower() in normalized_text.lower():
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.PLATFORM_RULE,
                        description=f"广告文案使用「{keyword}」表述，"
                                    f"冒用 vivo 品牌官方身份，误导用户",
                        regulation_ref="vivo 广告平台规范·品牌保护条款",
                        severity=ViolationSeverity.HIGH,
                        evidence=keyword,
                    )
                )

        # 阶段2：普通提及 vivo（产品名称、适配说明）→ 不判违规
        # 只有 forbidden_keywords 命中才算违规

        if violations:
            logger.debug(
                "Brand protection violations found",
                tool=self.name,
                request_id=request_id,
                count=len(violations),
            )

        return violations

    def _check_competitor_attack(
        self, text_content: str, request_id: str
    ) -> list[ViolationItem]:
        """
        检测文案中是否贬低竞品品牌。

        同时提及竞品品牌名和贬低性词汇时判定为竞品攻击。

        Args:
            text_content: 文案全文
            request_id: 请求 ID

        Returns:
            竞品攻击违规项列表
        """
        if not text_content:
            return []

        violations: list[ViolationItem] = []
        text_lower = text_content.lower()

        for brand in _COMPETITOR_BRANDS:
            if brand.lower() not in text_lower:
                continue

            # 品牌名出现了，检查是否有贬低性词汇
            for neg_word in _NEGATIVE_WORDS:
                if neg_word in text_content:
                    violations.append(
                        ViolationItem(
                            dimension=ViolationDimension.PLATFORM_RULE,
                            description=f"广告文案中提及竞品品牌「{brand}」"
                                        f"并使用贬低性表述「{neg_word}」，构成竞品攻击",
                            regulation_ref="vivo 广告平台规范·竞品攻击禁止条款",
                            severity=ViolationSeverity.HIGH,
                            evidence=f"{brand}...{neg_word}",
                        )
                    )
                    # 同一品牌只报一次
                    break

        if violations:
            logger.debug(
                "Competitor attack violations found",
                tool=self.name,
                request_id=request_id,
                count=len(violations),
            )

        return violations

    def _check_category_rules(
        self, text_content: str, ad_category: str, request_id: str
    ) -> list[ViolationItem]:
        """
        检测文案是否违反品类专项规则。

        从 category_rules.json 读取对应品类的 forbidden_claims，
        对文案进行关键词匹配。

        Args:
            text_content: 文案全文
            ad_category: 广告品类
            request_id: 请求 ID

        Returns:
            品类专项违规项列表
        """
        if not text_content:
            return []

        rules = self._category_rules.get("rules", {})
        category_config = rules.get(ad_category, {})
        forbidden = category_config.get("forbidden_claims", [])

        violations: list[ViolationItem] = []
        for claim in forbidden:
            if claim in text_content:
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.PLATFORM_RULE,
                        description=f"广告文案包含品类「{ad_category}」禁用表述「{claim}」",
                        regulation_ref=f"品类专项规则·{ad_category}",
                        severity=ViolationSeverity.MEDIUM,
                        evidence=claim,
                    )
                )

        if violations:
            logger.debug(
                "Category rule violations found",
                tool=self.name,
                request_id=request_id,
                category=ad_category,
                count=len(violations),
            )

        return violations

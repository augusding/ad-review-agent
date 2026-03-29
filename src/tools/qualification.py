"""
行业资质匹配检测 Tool。

检测策略：纯规则匹配，不调用 LLM。
1. 加载 qualification_map.json 获取品类所需资质清单
2. 对比广告主提交的 qualification_ids 是否满足要求
3. 对有 format_pattern 的资质做正则格式校验
4. 输出缺失资质列表和格式不合规的违规项
"""
import json
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from src.tools.base_tool import BaseTool, ToolExecutionError
from src.schemas.tool_io import QualificationCheckerInput, QualificationCheckerOutput
from src.schemas.violation import ViolationItem, ViolationDimension, ViolationSeverity
from src.config import settings


class QualificationChecker(BaseTool):
    """
    行业资质匹配检测。

    执行流程：
    1. 查询品类对应的资质要求（required / recommended / conditional）
    2. 检查广告主是否提交了必需资质
    3. 对有格式要求的资质做正则校验
    4. 输出 missing_qualifications 和 violations

    规则库在 __init__ 时加载并缓存，支持热更新。
    """

    name: str = "qualification_checker"
    description: str = "检测广告主行业资质是否满足要求"

    def __init__(self) -> None:
        """
        初始化 QualificationChecker，加载并缓存资质映射规则。

        Raises:
            ToolExecutionError: 规则文件加载失败
        """
        from src.rules_loader import load_rules_sync
        self._rules: dict = load_rules_sync("qualification_map")

    async def _maybe_reload_rules(self) -> None:
        """检查规则是否需要重新加载。优先数据库，降级文件。"""
        try:
            from src.rules_loader import load_rules
            new_rules = await load_rules("qualification_map")
            if new_rules and new_rules.get("version") != self._rules.get("version"):
                logger.info(
                    "Qualification rules reloaded",
                    tool=self.name,
                    new_version=new_rules.get("version"),
                )
                self._rules = new_rules
        except Exception as e:
            logger.warning(
                "Failed to reload rules, keeping cached version",
                tool=self.name,
                error=str(e),
            )

    async def execute(
        self, input: QualificationCheckerInput
    ) -> QualificationCheckerOutput:
        """
        执行资质匹配检测。

        Args:
            input: QualificationCheckerInput，包含品类、资质 ID 列表和请求 ID

        Returns:
            QualificationCheckerOutput，包含违规项、缺失资质和置信度

        Raises:
            ToolExecutionError: 规则库加载失败
        """
        start_ms = time.monotonic()
        await self._maybe_reload_rules()

        qualifications = self._rules.get("qualifications", {})
        category_config = qualifications.get(input.ad_category)

        # 该品类无资质要求，直接通过
        if not category_config:
            logger.info(
                "No qualification required for category",
                tool=self.name,
                request_id=input.request_id,
                ad_category=input.ad_category,
            )
            return QualificationCheckerOutput(
                violations=[],
                confidence=0.98,
                missing_qualifications=[],
                is_fallback=False,
            )

        violations: list[ViolationItem] = []
        missing_qualifications: list[str] = []

        # 检查 required 资质
        for req in category_config.get("required", []):
            self._check_qualification(
                req=req,
                qualification_ids=input.qualification_ids,
                violations=violations,
                missing_qualifications=missing_qualifications,
                request_id=input.request_id,
            )

        # 检查 recommended 资质（缺失为 low 严重度）
        for req in category_config.get("recommended", []):
            self._check_qualification(
                req=req,
                qualification_ids=input.qualification_ids,
                violations=violations,
                missing_qualifications=missing_qualifications,
                request_id=input.request_id,
            )

        # conditional 资质：当前仅记录，不做自动判断（需人工确认条件是否成立）
        for req in category_config.get("conditional", []):
            logger.debug(
                "Conditional qualification noted",
                tool=self.name,
                request_id=input.request_id,
                qualification=req["name"],
                condition=req.get("condition", ""),
            )

        # 计算置信度：纯规则匹配，确信度高
        if violations:
            confidence = 0.95
        else:
            confidence = 0.98

        duration_ms = (time.monotonic() - start_ms) * 1000
        logger.info(
            "Qualification check completed",
            tool=self.name,
            request_id=input.request_id,
            total_violations=len(violations),
            missing_qualifications=missing_qualifications,
            confidence=confidence,
            duration_ms=round(duration_ms, 1),
        )

        return QualificationCheckerOutput(
            violations=violations,
            confidence=confidence,
            missing_qualifications=missing_qualifications,
            is_fallback=False,
        )

    async def _fallback(
        self, error: Exception, input: Any
    ) -> QualificationCheckerOutput:
        """
        降级处理：返回低置信度结果，让流程走人工复核。

        Args:
            error: 导致降级的异常
            input: 原始 QualificationCheckerInput

        Returns:
            降级的 QualificationCheckerOutput（is_fallback=True, confidence < 0.7）
        """
        logger.warning(
            "Qualification checker falling back",
            tool=self.name,
            request_id=getattr(input, "request_id", "unknown"),
            error=str(error),
        )

        return QualificationCheckerOutput(
            violations=[],
            confidence=0.5,
            missing_qualifications=[],
            is_fallback=True,
            fallback_reason=f"Tool fallback due to: {type(error).__name__}: {error}",
        )

    def _check_qualification(
        self,
        req: dict,
        qualification_ids: list[str],
        violations: list[ViolationItem],
        missing_qualifications: list[str],
        request_id: str,
    ) -> None:
        """
        检查单项资质是否满足要求。

        先检查是否提交了资质（qualification_ids 非空匹配），
        再检查格式是否合规（若有 format_pattern）。

        Args:
            req: 资质要求配置（来自 qualification_map.json）
            qualification_ids: 广告主提交的资质 ID 列表
            violations: 违规列表（就地追加）
            missing_qualifications: 缺失资质列表（就地追加）
            request_id: 请求 ID
        """
        qual_name = req["name"]
        severity = ViolationSeverity(req.get("severity_if_missing", "high"))
        format_pattern = req.get("format_pattern")
        verification = req.get("verification", "none")

        if not qualification_ids:
            # 未提交任何资质
            missing_qualifications.append(qual_name)
            violations.append(
                ViolationItem(
                    dimension=ViolationDimension.QUALIFICATION,
                    description=f"缺失必需资质：{qual_name}（{req.get('description', '')}）",
                    regulation_ref="《广告法》第四十六条",
                    severity=severity,
                    evidence=f"广告主未提交{qual_name}",
                )
            )
            return

        # 有格式校验要求：至少有一个 ID 匹配格式
        if format_pattern and verification == "format_check":
            pattern = re.compile(format_pattern)
            matched = any(pattern.search(qid) for qid in qualification_ids)
            if not matched:
                violations.append(
                    ViolationItem(
                        dimension=ViolationDimension.QUALIFICATION,
                        description=(
                            f"{qual_name}格式不合规，"
                            f"期望格式：{format_pattern}"
                        ),
                        regulation_ref="《广告法》第四十六条",
                        severity=severity,
                        evidence=f"提交的资质 ID：{qualification_ids}",
                    )
                )
                missing_qualifications.append(qual_name)
                logger.debug(
                    "Qualification format mismatch",
                    tool=self.name,
                    request_id=request_id,
                    qualification=qual_name,
                    pattern=format_pattern,
                    submitted_ids=qualification_ids,
                )

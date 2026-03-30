"""
决策引擎：根据 ToolResultSet 计算综合置信度并输出 verdict。

双轨置信度策略：
- 有违规时：取发现违规的维度中最高置信度（任一维度确信即可退回）
- 无违规时：取核心维度中最低置信度（排除无违规的辅助维度，避免拉低整体）
- 全降级时：返回兜底值（由 constraints.yaml 定义）

所有决策阈值从 harness/constraints.yaml 读取，不硬编码在代码中。
"""
from loguru import logger

from src.schemas.result import ReviewVerdict
from src.schemas.violation import ViolationItem, ViolationDimension
from src.agents.types import ToolResultSet, Decision
from src.harness.constraint_loader import load_constraints


class DecisionEngine:
    """
    决策引擎：从 ToolResultSet 计算综合置信度、确定 verdict。

    阈值从 harness/constraints.yaml 加载。
    """

    def __init__(self) -> None:
        """
        初始化决策引擎，从 constraints.yaml 加载配置。
        """
        config = load_constraints()

        thresholds = config["decision_thresholds"]
        self._auto_pass: float = thresholds["auto_pass"]
        self._human_review_forced: float = thresholds["human_review_forced"]

        strategy = config["confidence_strategy"]
        self._all_fallback_default: float = strategy["all_fallback_default"]
        self._non_drag_dimensions: set[str] = set(strategy["non_drag_dimensions"])

    def decide(self, tool_results: ToolResultSet) -> Decision:
        """
        根据 ToolResultSet 计算置信度并决定 verdict。

        Args:
            tool_results: 所有 Tool 的聚合执行结果

        Returns:
            Decision，包含 verdict、confidence 和 reasons
        """
        confidence = self.compute_confidence(tool_results)
        verdict = self._determine_verdict(
            violations=tool_results.all_violations,
            confidence=confidence,
            has_fallback=tool_results.has_fallback,
        )

        reasons: list[str] = []
        if tool_results.all_violations:
            reasons.append(
                f"发现{len(tool_results.all_violations)}项违规"
            )
        if tool_results.has_fallback:
            reasons.append(
                f"部分维度降级：{'; '.join(tool_results.fallback_reasons)}"
            )
        if confidence < self._auto_pass:
            reasons.append(
                f"综合置信度 {confidence:.2f} 未达自动通过阈值 {self._auto_pass}"
            )

        return Decision(
            verdict=verdict,
            confidence=confidence,
            reasons=reasons,
        )

    def compute_confidence(self, tool_results: ToolResultSet) -> float:
        """
        双轨策略计算综合置信度。

        - 有违规时：取发现违规的维度中最高置信度（max_of_violation_dimensions）
        - 无违规时：取核心维度中最低置信度（min_of_core_dimensions），
          排除无违规的辅助维度（由 non_drag_dimensions 配置），避免拉低整体
        - 全降级时：返回 all_fallback_default

        Args:
            tool_results: 所有 Tool 的聚合执行结果

        Returns:
            综合置信度 0.0-1.0
        """
        dim_to_result = {
            ViolationDimension.TEXT_VIOLATION: tool_results.text,
            ViolationDimension.IMAGE_SAFETY: tool_results.image,
            ViolationDimension.LANDING_PAGE: tool_results.landing_page,
            ViolationDimension.QUALIFICATION: tool_results.qualification,
            ViolationDimension.PLATFORM_RULE: tool_results.platform_rule,
            ViolationDimension.CONSISTENCY: tool_results.consistency,
        }

        # 收集非降级维度的置信度
        normal_confidences: list[float] = []
        for r in dim_to_result.values():
            if r is not None and not getattr(r, "is_fallback", False):
                normal_confidences.append(r.confidence)

        if tool_results.all_violations and normal_confidences:
            # 双轨：取发现违规的维度中的最高置信度
            # 只要任一维度高置信度发现严重违规，就足够退回
            violation_dims = set(v.dimension for v in tool_results.all_violations)
            violation_confs = []
            for dim in violation_dims:
                r = dim_to_result.get(dim)
                if r and not getattr(r, "is_fallback", False):
                    violation_confs.append(r.confidence)
            return max(violation_confs) if violation_confs else min(normal_confidences)

        if normal_confidences:
            # 无违规：排除「无违规且 confidence 偏低」的辅助维度，
            # 这些维度无违规时不应拉低整体置信度阻止 pass
            filtered_confs = []
            for dim, r in dim_to_result.items():
                if r is None or getattr(r, "is_fallback", False):
                    continue
                if dim.value in self._non_drag_dimensions and not getattr(r, "violations", []):
                    continue
                filtered_confs.append(r.confidence)
            return min(filtered_confs) if filtered_confs else min(normal_confidences)

        return self._all_fallback_default

    def _determine_verdict(
        self,
        violations: list[ViolationItem],
        confidence: float,
        has_fallback: bool,
    ) -> ReviewVerdict:
        """
        确定最终 verdict。

        规则（confidence 已排除降级维度）：
        - confidence < human_review_forced → review
        - human_review_forced ≤ confidence < auto_pass → review
        - confidence ≥ auto_pass 且有违规 → returned（退回修改）
        - confidence ≥ auto_pass 且无违规且无降级 → pass
        - confidence ≥ auto_pass 且无违规但有降级 → pass（降级非阻塞）

        注意：reject 保留用于极严重违规（涉政/色情/暴力），
        由人工审核员手动标记，系统自动输出统一用 returned。

        Args:
            violations: 所有违规项
            confidence: 综合置信度（已排除降级维度）
            has_fallback: 是否有 Tool 降级

        Returns:
            ReviewVerdict 枚举值
        """
        if confidence < self._human_review_forced:
            logger.debug(
                "Verdict path: low confidence → review",
                confidence=confidence,
            )
            return ReviewVerdict.REVIEW

        if confidence < self._auto_pass:
            logger.debug(
                "Verdict path: medium confidence → review",
                confidence=confidence,
            )
            return ReviewVerdict.REVIEW

        # confidence ≥ auto_pass
        if violations:
            logger.debug("Verdict path: high confidence + violations → returned")
            return ReviewVerdict.RETURNED

        if has_fallback:
            # 无违规+高置信度：降级的维度不阻塞通过
            if not violations and confidence >= self._auto_pass:
                logger.debug(
                    "Verdict path: high confidence + no violations + non-critical fallback → pass",
                    confidence=confidence,
                )
                return ReviewVerdict.PASS
            logger.debug("Verdict path: high confidence + fallback → review")
            return ReviewVerdict.REVIEW

        logger.debug("Verdict path: high confidence + no violations → pass")
        return ReviewVerdict.PASS

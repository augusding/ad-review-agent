"""
Eval 指标计算模块。

封装所有指标计算逻辑，供 run_eval.py 调用。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class CaseResult:
    """单条案例的评估结果。"""
    case_id: str
    category: str
    expected_verdict: str
    actual_verdict: str
    confidence: float
    processing_ms: int
    is_fallback: bool
    violation_count: int
    match: bool  # expected == actual


@dataclass
class EvalMetrics:
    """评估指标汇总。"""

    total_cases: int = 0
    correct: int = 0
    accuracy: float = 0.0

    # 漏审：expected=reject 但 actual=pass（最危险）
    false_negative_count: int = 0
    false_negative_total: int = 0  # expected=reject 的总数
    false_negative_rate: float = 0.0

    # 误拒：expected=pass 但 actual=reject
    false_positive_count: int = 0
    false_positive_total: int = 0  # expected=pass 的总数
    false_positive_rate: float = 0.0

    # 自动化率：confidence ≥ 0.92
    auto_decision_count: int = 0
    automation_rate: float = 0.0

    # 平均处理时长
    avg_processing_ms: float = 0.0

    # 分类别准确率
    category_accuracy: dict[str, dict[str, int | float]] = field(
        default_factory=dict
    )

    # 漏审案例详情（用于排查）
    missed_cases: list[str] = field(default_factory=list)

    # 问题完整发现率：全维度检测占比（无提前终止）
    full_check_rate: float = 0.0
    # 修改建议可执行率（占位，需人工抽样）
    actionable_suggestion_rate: float = 0.0
    # 广告主重提通过率（占位，从数据库统计）
    resubmit_pass_rate: float = 0.0

    def to_dict(self) -> dict:
        """转为可序列化的字典。"""
        return asdict(self)


def compute_metrics(results: list[CaseResult]) -> EvalMetrics:
    """
    从案例结果列表计算所有评估指标。

    Args:
        results: 每条案例的评估结果

    Returns:
        EvalMetrics 汇总指标
    """
    m = EvalMetrics()
    m.total_cases = len(results)

    if not results:
        return m

    # 基础计数
    total_ms = 0
    category_stats: dict[str, dict[str, int]] = {}

    for r in results:
        # 准确率
        if r.match:
            m.correct += 1

        # 漏审（expected=reject, actual=pass）
        # 注意：returned 视为 reject 的等价结论（系统自动输出 returned 代替 reject）
        if r.expected_verdict in ("reject", "returned"):
            m.false_negative_total += 1
            if r.actual_verdict == "pass":
                m.false_negative_count += 1
                m.missed_cases.append(r.case_id)

        # 误拒（expected=pass, actual=reject/returned）
        if r.expected_verdict == "pass":
            m.false_positive_total += 1
            if r.actual_verdict in ("reject", "returned"):
                m.false_positive_count += 1

        # 自动化率
        if r.confidence >= 0.92:
            m.auto_decision_count += 1

        # 处理时长
        total_ms += r.processing_ms

        # 分类别统计
        cat = r.expected_verdict  # pass / reject / review
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "correct": 0}
        category_stats[cat]["total"] += 1
        if r.match:
            category_stats[cat]["correct"] += 1

    # 计算比率
    m.accuracy = m.correct / m.total_cases if m.total_cases > 0 else 0.0
    m.false_negative_rate = (
        m.false_negative_count / m.false_negative_total
        if m.false_negative_total > 0
        else 0.0
    )
    m.false_positive_rate = (
        m.false_positive_count / m.false_positive_total
        if m.false_positive_total > 0
        else 0.0
    )
    m.automation_rate = (
        m.auto_decision_count / m.total_cases if m.total_cases > 0 else 0.0
    )
    m.avg_processing_ms = total_ms / m.total_cases if m.total_cases > 0 else 0.0
    # 全维度检测率：无降级 = 全维度正常执行
    full_check_count = sum(1 for r in results if not r.is_fallback)
    m.full_check_rate = full_check_count / m.total_cases if m.total_cases > 0 else 0.0

    # 分类别准确率
    for cat, stats in category_stats.items():
        total = stats["total"]
        correct = stats["correct"]
        m.category_accuracy[cat] = {
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total > 0 else 0.0,
        }

    return m


def format_comparison(current: EvalMetrics, previous: EvalMetrics) -> str:
    """
    格式化当前指标与上次指标的对比。

    Args:
        current: 本次评估指标
        previous: 上次评估指标

    Returns:
        格式化的对比字符串
    """

    def _arrow(curr: float, prev: float, higher_is_better: bool = True) -> str:
        """生成带箭头的差值字符串。"""
        diff = curr - prev
        if abs(diff) < 0.0001:
            return "  (=)"
        symbol = "UP" if diff > 0 else "DOWN"
        # 对于 higher_is_better=True，↑ 为正面；反之 ↓ 为正面
        if higher_is_better:
            color = "green" if diff > 0 else "red"
        else:
            color = "red" if diff > 0 else "green"
        return f"  {symbol} {diff:+.2%}"

    lines = [
        "  ┌─────────────────── 对比上次结果 ───────────────────┐",
        f"  │ 准确率:     {current.accuracy:.2%} (was {previous.accuracy:.2%})"
        f"{_arrow(current.accuracy, previous.accuracy)}",
        f"  │ 漏审率:     {current.false_negative_rate:.2%} (was {previous.false_negative_rate:.2%})"
        f"{_arrow(current.false_negative_rate, previous.false_negative_rate, higher_is_better=False)}",
        f"  │ 误拒率:     {current.false_positive_rate:.2%} (was {previous.false_positive_rate:.2%})"
        f"{_arrow(current.false_positive_rate, previous.false_positive_rate, higher_is_better=False)}",
        f"  │ 自动化率:   {current.automation_rate:.2%} (was {previous.automation_rate:.2%})"
        f"{_arrow(current.automation_rate, previous.automation_rate)}",
        f"  │ 平均耗时:   {current.avg_processing_ms:.0f}ms (was {previous.avg_processing_ms:.0f}ms)",
        "  └───────────────────────────────────────────────────┘",
    ]
    return "\n".join(lines)

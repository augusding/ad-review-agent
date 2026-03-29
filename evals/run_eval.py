"""
一键执行评估脚本。

读取 golden_dataset/ 下所有标注案例，
调用 ReviewAgent 审核，计算指标，保存结果并对比历史。

用法：
    python evals/run_eval.py
"""
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from src.agents.review_agent import ReviewAgent
from src.schemas.request import (
    ReviewRequest,
    AdCategory,
    CreativeType,
    CreativeContent,
    AdPlatform,
)
from evals.metrics import CaseResult, EvalMetrics, compute_metrics, format_comparison


# ==================== 路径常量 ====================

GOLDEN_DIR = Path(__file__).parent / "golden_dataset"
RESULTS_DIR = Path(__file__).parent / "results"

DATASET_FILES = [
    "pass_cases.jsonl",
    "reject_cases.jsonl",
    "review_cases.jsonl",
    "adversarial_cases.jsonl",
]

# 品类映射
_CATEGORY_MAP = {
    "game": AdCategory.GAME,
    "tool_app": AdCategory.TOOL_APP,
    "ecommerce": AdCategory.ECOMMERCE,
    "finance": AdCategory.FINANCE,
    "health": AdCategory.HEALTH,
    "education": AdCategory.EDUCATION,
    "other": AdCategory.OTHER,
}

# 资质 ID（用于需要资质检测的品类）
_DEFAULT_QUALIFICATIONS = {
    "game": ["ISBN9787100001"],
    "finance": ["ABCD1234567890"],
    "health": ["国械广审2026010001"],
    "education": ["教社证字2026001"],
}


# ==================== 数据加载 ====================

def load_all_cases() -> list[dict]:
    """
    从 golden_dataset/ 加载所有标注案例。

    Returns:
        案例字典列表
    """
    all_cases = []
    for filename in DATASET_FILES:
        filepath = GOLDEN_DIR / filename
        if not filepath.exists():
            logger.warning(f"Dataset file not found: {filepath}")
            continue

        with filepath.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    case = json.loads(line)
                    case["_source_file"] = filename
                    all_cases.append(case)
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Invalid JSON in {filename}:{line_num}: {e}"
                    )

    return all_cases


def case_to_request(case: dict) -> ReviewRequest:
    """
    将标注案例转换为 ReviewRequest。

    Args:
        case: 标注案例字典

    Returns:
        ReviewRequest 实例
    """
    content_data = case.get("content", {})
    category_str = case.get("category", "other")
    ad_category = _CATEGORY_MAP.get(category_str, AdCategory.OTHER)

    qualifications = _DEFAULT_QUALIFICATIONS.get(category_str, [])

    return ReviewRequest(
        request_id=case.get("case_id", str(uuid4())),
        advertiser_id="eval-advertiser",
        ad_category=ad_category,
        creative_type=CreativeType.TEXT,
        content=CreativeContent(
            title=content_data.get("title"),
            description=content_data.get("description"),
            cta_text=content_data.get("cta_text"),
            image_urls=content_data.get("image_urls", []),
            video_url=content_data.get("video_url"),
            landing_page_url=content_data.get("landing_page_url"),
        ),
        advertiser_qualification_ids=qualifications,
        platform=AdPlatform.OTHER,
    )


# ==================== 结果存储 ====================

def find_latest_result() -> dict | None:
    """
    查找最近一次评估结果。

    Returns:
        上次评估结果字典，不存在则返回 None
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_files = sorted(RESULTS_DIR.glob("eval_*.json"))
    if not result_files:
        return None

    latest = result_files[-1]
    with latest.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_result(
    metrics: EvalMetrics,
    case_results: list[CaseResult],
) -> Path:
    """
    保存评估结果到 evals/results/eval_{timestamp}.json。

    Args:
        metrics: 指标汇总
        case_results: 每条案例的详细结果

    Returns:
        保存的文件路径
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = RESULTS_DIR / f"eval_{timestamp}.json"

    data = {
        "timestamp": datetime.now().isoformat(),
        "total_cases": metrics.total_cases,
        "metrics": metrics.to_dict(),
        "case_details": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "expected_verdict": r.expected_verdict,
                "actual_verdict": r.actual_verdict,
                "confidence": r.confidence,
                "processing_ms": r.processing_ms,
                "is_fallback": r.is_fallback,
                "violation_count": r.violation_count,
                "match": r.match,
            }
            for r in case_results
        ],
    }

    with filepath.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return filepath


# ==================== 主流程 ====================

async def run_eval() -> None:
    """
    执行完整评估流水线。

    1. 加载标注案例
    2. 逐条调用 ReviewAgent
    3. 计算指标
    4. 保存结果并对比历史
    """
    print("=" * 60)
    print("  Ad Review Agent — Eval Pipeline")
    print("=" * 60)

    # 加载案例
    cases = load_all_cases()
    print(f"\n  Loaded {len(cases)} cases from golden dataset")
    for filename in DATASET_FILES:
        count = sum(1 for c in cases if c.get("_source_file") == filename)
        print(f"    {filename}: {count} cases")

    if not cases:
        print("\n  ERROR: No cases found. Exiting.")
        return

    # 初始化 Agent
    print("\n  Initializing ReviewAgent...")
    agent = ReviewAgent()

    # 逐条评估
    print(f"\n  Running evaluation on {len(cases)} cases...\n")
    case_results: list[CaseResult] = []
    total_start = time.monotonic()

    for i, case in enumerate(cases, start=1):
        case_id = case.get("case_id", "unknown")
        expected = case.get("expected_verdict", "unknown")

        request = case_to_request(case)

        try:
            result = await agent.review(request)
            actual = result.verdict.value
            match = _verdict_match(expected, actual)

            cr = CaseResult(
                case_id=case_id,
                category=expected,  # 按 expected_verdict 分类
                expected_verdict=expected,
                actual_verdict=actual,
                confidence=result.confidence,
                processing_ms=result.processing_ms,
                is_fallback=result.is_fallback,
                violation_count=len(result.violations),
                match=match,
            )

        except Exception as e:
            logger.error(f"Case {case_id} failed: {e}")
            cr = CaseResult(
                case_id=case_id,
                category=expected,
                expected_verdict=expected,
                actual_verdict="error",
                confidence=0.0,
                processing_ms=0,
                is_fallback=True,
                violation_count=0,
                match=False,
            )

        case_results.append(cr)

        # 进度输出
        status = "OK" if cr.match else "FAIL"
        print(
            f"  [{i:2d}/{len(cases)}] {status:<4s}  {case_id:<20s}  "
            f"expected={expected:<8s}  actual={cr.actual_verdict:<8s}  "
            f"conf={cr.confidence:.2f}  {cr.processing_ms}ms"
        )

    total_elapsed = (time.monotonic() - total_start) * 1000

    # 计算指标
    metrics = compute_metrics(case_results)

    # 输出结果
    print("\n" + "=" * 60)
    print("  EVAL RESULTS")
    print("=" * 60)
    print(f"""
  Total cases:      {metrics.total_cases}
  Correct:          {metrics.correct}/{metrics.total_cases}
  Accuracy:         {metrics.accuracy:.2%}

  False Negative:   {metrics.false_negative_count}/{metrics.false_negative_total} ({metrics.false_negative_rate:.2%})
  False Positive:   {metrics.false_positive_count}/{metrics.false_positive_total} ({metrics.false_positive_rate:.2%})

  Automation rate:  {metrics.auto_decision_count}/{metrics.total_cases} ({metrics.automation_rate:.2%})
  Avg latency:      {metrics.avg_processing_ms:.0f}ms
  Total time:       {total_elapsed:.0f}ms
""")

    # 分类别准确率
    print("  Category breakdown:")
    for cat, stats in metrics.category_accuracy.items():
        print(
            f"    {cat:<10s}  {stats['correct']}/{stats['total']}  "
            f"({stats['accuracy']:.2%})"
        )

    # 漏审警告
    if metrics.false_negative_rate > 0.05:
        print(
            f"\n  \033[91m[WARNING] False Negative Rate "
            f"({metrics.false_negative_rate:.2%}) exceeds 5% threshold!\033[0m"
        )
        if metrics.missed_cases:
            print(f"  \033[91m  Missed cases: {', '.join(metrics.missed_cases)}\033[0m")

    # 对比历史
    previous = find_latest_result()
    if previous and "metrics" in previous:
        prev_m = previous["metrics"]
        prev_metrics = EvalMetrics(
            total_cases=prev_m.get("total_cases", 0),
            correct=prev_m.get("correct", 0),
            accuracy=prev_m.get("accuracy", 0.0),
            false_negative_count=prev_m.get("false_negative_count", 0),
            false_negative_total=prev_m.get("false_negative_total", 0),
            false_negative_rate=prev_m.get("false_negative_rate", 0.0),
            false_positive_count=prev_m.get("false_positive_count", 0),
            false_positive_total=prev_m.get("false_positive_total", 0),
            false_positive_rate=prev_m.get("false_positive_rate", 0.0),
            auto_decision_count=prev_m.get("auto_decision_count", 0),
            automation_rate=prev_m.get("automation_rate", 0.0),
            avg_processing_ms=prev_m.get("avg_processing_ms", 0.0),
        )
        print(f"\n{format_comparison(metrics, prev_metrics)}")

    # 保存结果
    result_path = save_result(metrics, case_results)
    print(f"\n  Results saved to: {result_path}")
    print("=" * 60)


def _verdict_match(expected: str, actual: str) -> bool:
    """
    判断实际 verdict 是否匹配预期。

    匹配规则：
    - 完全匹配为 True
    - expected=reject ↔ actual=returned 互相等价（系统用 returned 代替 reject）
    - expected=reject/returned, actual=review 视为安全侧偏差（不漏审）
    - expected=pass, actual=review 不算对（误拒进人工队列增加工作量）

    Args:
        expected: 预期 verdict
        actual: 实际 verdict

    Returns:
        是否匹配
    """
    if expected == actual:
        return True

    # reject ↔ returned 互相等价
    if {expected, actual} == {"reject", "returned"}:
        return True

    # reject/returned → review 是安全的（不会漏审，只是多了人工环节）
    if expected in ("reject", "returned") and actual == "review":
        return True

    return False


if __name__ == "__main__":
    asyncio.run(run_eval())

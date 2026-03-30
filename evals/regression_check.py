"""
Eval 回归检测工具。

对比当前评估结果与基线，检测指标回归。
任何关键指标下降超过阈值 → exit code 1（阻止合并）。

用法：
    # 对比最近两次结果
    python evals/regression_check.py

    # 指定基线文件
    python evals/regression_check.py --baseline evals/results/eval_20260330_112418.json

    # 先运行 eval 再对比
    python evals/regression_check.py --run-eval

退出码：
    0 — 无回归
    1 — 检测到回归
    2 — 无法执行（缺少结果文件等）
"""
import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"

# 关键指标及其回归阈值（绝对值下降超过此值即为回归）
# higher_is_better=True 的指标：下降为回归
# higher_is_better=False 的指标：上升为回归
_CHECKS = [
    {"key": "accuracy", "label": "Accuracy", "threshold": 0.01, "higher_is_better": True},
    {"key": "false_negative_rate", "label": "False Negative Rate", "threshold": 0.01, "higher_is_better": False},
    {"key": "false_positive_rate", "label": "False Positive Rate", "threshold": 0.01, "higher_is_better": False},
    {"key": "automation_rate", "label": "Automation Rate", "threshold": 0.02, "higher_is_better": True},
]


def load_result(filepath: Path) -> dict:
    """
    加载评估结果文件。

    Args:
        filepath: JSON 文件路径

    Returns:
        结果字典

    Raises:
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: 文件格式错误
    """
    with filepath.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_latest_results(count: int = 2) -> list[Path]:
    """
    查找最近 N 次评估结果文件。

    Args:
        count: 需要的文件数量

    Returns:
        按时间升序排列的文件路径列表（最旧在前）
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(RESULTS_DIR.glob("eval_*.json"))
    return files[-count:] if len(files) >= count else files


def check_regression(
    baseline_metrics: dict,
    current_metrics: dict,
) -> list[dict]:
    """
    对比基线和当前指标，返回回归项列表。

    Args:
        baseline_metrics: 基线指标字典
        current_metrics: 当前指标字典

    Returns:
        回归项列表，每项包含 key/label/baseline/current/diff/threshold
    """
    regressions = []

    for check in _CHECKS:
        key = check["key"]
        baseline_val = baseline_metrics.get(key, 0.0)
        current_val = current_metrics.get(key, 0.0)

        if check["higher_is_better"]:
            # 下降为回归
            diff = baseline_val - current_val
        else:
            # 上升为回归
            diff = current_val - baseline_val

        if diff > check["threshold"]:
            regressions.append({
                "key": key,
                "label": check["label"],
                "baseline": baseline_val,
                "current": current_val,
                "diff": diff,
                "threshold": check["threshold"],
                "higher_is_better": check["higher_is_better"],
            })

    return regressions


def format_report(
    baseline_path: Path,
    current_path: Path,
    baseline_metrics: dict,
    current_metrics: dict,
    regressions: list[dict],
) -> str:
    """
    格式化回归检测报告。

    Args:
        baseline_path: 基线文件路径
        current_path: 当前文件路径
        baseline_metrics: 基线指标
        current_metrics: 当前指标
        regressions: 回归项列表

    Returns:
        格式化的报告字符串
    """
    lines = [
        "=" * 60,
        "  Eval Regression Check",
        "=" * 60,
        f"  Baseline: {baseline_path.name}",
        f"  Current:  {current_path.name}",
        "",
        "  Metrics comparison:",
    ]

    for check in _CHECKS:
        key = check["key"]
        b = baseline_metrics.get(key, 0.0)
        c = current_metrics.get(key, 0.0)
        diff = c - b
        arrow = "+" if diff >= 0 else ""
        lines.append(
            f"    {check['label']:<25s}  {b:.2%} -> {c:.2%}  ({arrow}{diff:.2%})"
        )

    lines.append("")

    if regressions:
        lines.append("  REGRESSION DETECTED:")
        for r in regressions:
            direction = "dropped" if r["higher_is_better"] else "increased"
            lines.append(
                f"    {r['label']}: {direction} by {r['diff']:.2%} "
                f"(threshold: {r['threshold']:.2%})"
            )
        lines.append("")
        lines.append("  Result: FAIL (exit code 1)")
    else:
        lines.append("  ALL CLEAR - no regressions detected.")
        lines.append("")
        lines.append("  Result: PASS (exit code 0)")

    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    """
    主入口：解析参数，执行回归检测。

    Returns:
        退出码：0=通过，1=回归，2=无法执行
    """
    parser = argparse.ArgumentParser(
        description="Eval regression check — compare current results against baseline"
    )
    parser.add_argument(
        "--baseline",
        type=str,
        default=None,
        help="Path to baseline eval result JSON (default: second-latest in evals/results/)",
    )
    parser.add_argument(
        "--current",
        type=str,
        default=None,
        help="Path to current eval result JSON (default: latest in evals/results/)",
    )
    parser.add_argument(
        "--run-eval",
        action="store_true",
        help="Run eval first, then compare against baseline",
    )
    args = parser.parse_args()

    # 如果需要先运行 eval
    if args.run_eval:
        import asyncio
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evals.run_eval import run_eval
        asyncio.run(run_eval())

    # 确定 baseline 和 current 文件
    if args.baseline and args.current:
        baseline_path = Path(args.baseline)
        current_path = Path(args.current)
    elif args.baseline:
        baseline_path = Path(args.baseline)
        latest = find_latest_results(1)
        if not latest:
            print("ERROR: No eval results found for current. Run eval first.")
            return 2
        current_path = latest[-1]
    else:
        results = find_latest_results(2)
        if len(results) < 2:
            print("ERROR: Need at least 2 eval results to compare. Run eval first.")
            return 2
        baseline_path = results[-2]
        current_path = results[-1]

    # 加载结果
    try:
        baseline_data = load_result(baseline_path)
        current_data = load_result(current_path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: Failed to load result file: {e}")
        return 2

    baseline_metrics = baseline_data.get("metrics", {})
    current_metrics = current_data.get("metrics", {})

    # 执行回归检测
    regressions = check_regression(baseline_metrics, current_metrics)

    # 输出报告
    report = format_report(
        baseline_path, current_path,
        baseline_metrics, current_metrics,
        regressions,
    )
    print(report)

    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())

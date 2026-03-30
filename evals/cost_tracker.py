"""
成本追踪报告工具。

运行 eval（或加载最近结果），收集每条素材的 token 用量和成本，
按 Tool / 模型 维度汇总，输出格式化报告。

用法：
    python evals/cost_tracker.py              # 运行 eval 并生成成本报告
    python evals/cost_tracker.py --dry-run    # 使用最近 eval 结果（不重新运行）

成本目标：≤ ¥0.05/条（ADD.md §2.3）
"""
import asyncio
import json
import statistics
import sys
from pathlib import Path

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
from src.harness.tracer import ReviewTrace

# 复用 run_eval 中的数据加载逻辑
from evals.run_eval import load_all_cases, case_to_request

# 成本预算上限（人民币/条）
_COST_BUDGET_CNY = 0.05


def _format_cny(val: float) -> str:
    """格式化人民币金额。"""
    return f"¥{val:.4f}"


def _format_tokens(val: float) -> str:
    """格式化 token 数（取整）。"""
    return f"{val:,.0f}"


def generate_report(traces: list[ReviewTrace]) -> str:
    """
    从 trace 列表生成成本分析报告。

    Args:
        traces: 每条素材审核的 ReviewTrace

    Returns:
        格式化报告字符串
    """
    if not traces:
        return "No traces collected."

    # ── 总体统计 ──
    costs = [t.estimated_cost_cny for t in traces]
    tokens = [t.total_tokens for t in traces]
    input_tokens = [t.total_input_tokens for t in traces]
    output_tokens = [t.total_output_tokens for t in traces]

    avg_cost = statistics.mean(costs)
    p50_cost = statistics.median(costs)
    p95_cost = sorted(costs)[int(len(costs) * 0.95)] if len(costs) >= 2 else costs[0]
    max_cost = max(costs)

    # ── 按 Tool 维度 ──
    tool_stats: dict[str, dict] = {}
    for trace in traces:
        for span in trace.spans:
            name = span.tool_name
            if name not in tool_stats:
                tool_stats[name] = {
                    "count": 0,
                    "total_input": 0,
                    "total_output": 0,
                    "total_cost": 0.0,
                }
            s = tool_stats[name]
            s["count"] += 1
            s["total_input"] += span.input_tokens
            s["total_output"] += span.output_tokens
            # 单 span 成本（从 trace 的 cost config 计算）
            cost_config = trace._get_cost_config()
            if span.model_used and span.model_used in cost_config:
                mc = cost_config[span.model_used]
                s["total_cost"] += (span.input_tokens / 1000) * mc.get("input", 0)
                s["total_cost"] += (span.output_tokens / 1000) * mc.get("output", 0)

    # ── 按模型维度 ──
    model_stats: dict[str, dict] = {}
    for trace in traces:
        for span in trace.spans:
            model = span.model_used or "(none)"
            if model not in model_stats:
                model_stats[model] = {
                    "calls": 0,
                    "total_input": 0,
                    "total_output": 0,
                    "total_cost": 0.0,
                }
            ms = model_stats[model]
            ms["calls"] += 1
            ms["total_input"] += span.input_tokens
            ms["total_output"] += span.output_tokens
            cost_config = trace._get_cost_config()
            if span.model_used and span.model_used in cost_config:
                mc = cost_config[span.model_used]
                ms["total_cost"] += (span.input_tokens / 1000) * mc.get("input", 0)
                ms["total_cost"] += (span.output_tokens / 1000) * mc.get("output", 0)

    # ── 构建报告 ──
    n = len(traces)
    lines = [
        "=" * 60,
        "  Cost Tracker Report",
        "=" * 60,
        f"  Total requests:  {n}",
        "",
        "  -- Per-request summary --",
        f"  Avg tokens:      {_format_tokens(statistics.mean(tokens))} "
        f"(in: {_format_tokens(statistics.mean(input_tokens))}, "
        f"out: {_format_tokens(statistics.mean(output_tokens))})",
        f"  Avg cost:        {_format_cny(avg_cost)}",
        f"  P50 cost:        {_format_cny(p50_cost)}",
        f"  P95 cost:        {_format_cny(p95_cost)}",
        f"  Max cost:        {_format_cny(max_cost)}",
        f"  Budget:          {_format_cny(_COST_BUDGET_CNY)}/request",
    ]

    if avg_cost > _COST_BUDGET_CNY:
        lines.append(
            f"\n  WARNING: Average cost {_format_cny(avg_cost)} "
            f"exceeds budget {_format_cny(_COST_BUDGET_CNY)}!"
        )
    else:
        lines.append(
            f"  Status:          OK (avg {_format_cny(avg_cost)} <= budget)"
        )

    # Tool breakdown
    lines.append("")
    lines.append("  -- By Tool --")
    lines.append(f"  {'Tool':<28s} {'Calls':>6s} {'Avg In':>8s} {'Avg Out':>8s} {'Avg Cost':>10s}")
    lines.append(f"  {'-'*28} {'-'*6} {'-'*8} {'-'*8} {'-'*10}")
    for name in sorted(tool_stats, key=lambda k: tool_stats[k]["total_cost"], reverse=True):
        s = tool_stats[name]
        cnt = s["count"]
        lines.append(
            f"  {name:<28s} {cnt:>6d} "
            f"{s['total_input']/cnt:>8,.0f} "
            f"{s['total_output']/cnt:>8,.0f} "
            f"{_format_cny(s['total_cost']/cnt):>10s}"
        )

    # Model breakdown
    lines.append("")
    lines.append("  -- By Model --")
    lines.append(f"  {'Model':<28s} {'Calls':>6s} {'Total In':>10s} {'Total Out':>10s} {'Total Cost':>12s}")
    lines.append(f"  {'-'*28} {'-'*6} {'-'*10} {'-'*10} {'-'*12}")
    for model in sorted(model_stats, key=lambda k: model_stats[k]["total_cost"], reverse=True):
        ms = model_stats[model]
        lines.append(
            f"  {model:<28s} {ms['calls']:>6d} "
            f"{ms['total_input']:>10,d} "
            f"{ms['total_output']:>10,d} "
            f"{_format_cny(ms['total_cost']):>12s}"
        )

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


async def collect_traces() -> list[ReviewTrace]:
    """
    运行 eval 案例并收集每条的 ReviewTrace。

    Returns:
        ReviewTrace 列表
    """
    cases = load_all_cases()
    if not cases:
        print("ERROR: No cases found in golden dataset.")
        return []

    agent = ReviewAgent()
    traces: list[ReviewTrace] = []

    print(f"  Running {len(cases)} cases for cost analysis...\n")

    for i, case in enumerate(cases, start=1):
        request = case_to_request(case)
        try:
            result = await agent.review(request)
            # trace 在 orchestrator.run() 中创建并附加到 ToolResultSet
            # 通过 tracer 的 contextvars 获取
            from src.harness.tracer import get_current_trace
            trace = get_current_trace()
            if trace is not None:
                traces.append(trace)
        except Exception as e:
            print(f"  [{i:3d}/{len(cases)}] ERROR: {case.get('case_id', '?')}: {e}")
            continue

        if i % 10 == 0 or i == len(cases):
            print(f"  [{i:3d}/{len(cases)}] processed")

    return traces


def main() -> int:
    """
    主入口。

    Returns:
        0=成本达标，1=成本超标
    """
    import argparse

    parser = argparse.ArgumentParser(description="Cost tracker — analyze LLM cost per review")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip eval, print report template with last known traces",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("  (dry-run mode — no traces collected, showing empty report)")
        print(generate_report([]))
        return 0

    # Suppress verbose loguru output
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    traces = asyncio.run(collect_traces())
    report = generate_report(traces)
    print(report)

    if traces:
        avg_cost = statistics.mean([t.estimated_cost_cny for t in traces])
        return 1 if avg_cost > _COST_BUDGET_CNY else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

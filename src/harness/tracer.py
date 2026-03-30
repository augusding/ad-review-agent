"""
结构化追踪：为每次 review() 调用生成 trace，记录各 Tool 的执行详情。

使用 contextvars 跨异步调用传递当前 trace，Tool 在 run() 中自动追加 span。
成本估算基于 model_router.yaml 中的 cost_per_1k_tokens 配置。

用法::

    from src.harness.tracer import ReviewTrace, set_current_trace, get_current_trace

    trace = ReviewTrace(request_id="req-001")
    set_current_trace(trace)
    # ... Tool 执行期间自动追加 ToolSpan ...
    logger.info("Review trace", **trace.to_structured_log())
"""
from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ── 当前 trace 上下文变量 ──
_current_trace: contextvars.ContextVar[ReviewTrace | None] = contextvars.ContextVar(
    "_current_trace", default=None,
)


def get_current_trace() -> ReviewTrace | None:
    """
    获取当前异步上下文中的 ReviewTrace。

    Returns:
        当前 trace，未设置时返回 None
    """
    return _current_trace.get()


def set_current_trace(trace: ReviewTrace) -> None:
    """
    设置当前异步上下文的 ReviewTrace。

    Args:
        trace: 要绑定到当前上下文的 trace
    """
    _current_trace.set(trace)


@dataclass
class ToolSpan:
    """单个 Tool 执行的追踪记录。"""

    tool_name: str
    start_time: float = 0.0
    end_time: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_used: str = ""
    task_type: str = ""
    success: bool = True
    error: str = ""

    @property
    def duration_ms(self) -> float:
        """Tool 执行耗时（毫秒）。"""
        return round((self.end_time - self.start_time) * 1000, 1)

    @property
    def total_tokens(self) -> int:
        """输入 + 输出 token 总数。"""
        return self.input_tokens + self.output_tokens


@dataclass
class ReviewTrace:
    """
    一次 review() 调用的完整追踪，包含多个 ToolSpan。

    成本估算使用 model_router.yaml 中的 cost_per_1k_tokens 配置。
    """

    request_id: str
    spans: list[ToolSpan] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """所有 span 的 token 总和。"""
        return sum(s.total_tokens for s in self.spans)

    @property
    def total_input_tokens(self) -> int:
        """所有 span 的输入 token 总和。"""
        return sum(s.input_tokens for s in self.spans)

    @property
    def total_output_tokens(self) -> int:
        """所有 span 的输出 token 总和。"""
        return sum(s.output_tokens for s in self.spans)

    @property
    def total_duration_ms(self) -> float:
        """所有 span 的总耗时（毫秒）。"""
        return round(sum(s.duration_ms for s in self.spans), 1)

    @property
    def estimated_cost_cny(self) -> float:
        """
        基于 model_router.yaml 中 cost_per_1k_tokens 估算总成本（人民币）。

        每个 span 按其 model_used 查找单价，
        未知模型按 0 计价（不影响审核流程）。

        Returns:
            估算成本（人民币元）
        """
        cost_config = self._get_cost_config()
        total = 0.0
        for span in self.spans:
            if not span.model_used or span.model_used not in cost_config:
                continue
            model_cost = cost_config[span.model_used]
            input_price = model_cost.get("input", 0)
            output_price = model_cost.get("output", 0)
            total += (span.input_tokens / 1000) * input_price
            total += (span.output_tokens / 1000) * output_price
        return round(total, 6)

    def to_structured_log(self) -> dict[str, Any]:
        """
        输出结构化追踪日志。

        Returns:
            包含 trace 概要和各 span 详情的字典
        """
        return {
            "trace_id": self.request_id,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_duration_ms": self.total_duration_ms,
            "estimated_cost_cny": self.estimated_cost_cny,
            "span_count": len(self.spans),
            "spans": [
                {
                    "tool_name": s.tool_name,
                    "duration_ms": s.duration_ms,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "model_used": s.model_used,
                    "task_type": s.task_type,
                    "success": s.success,
                    "error": s.error,
                }
                for s in self.spans
            ],
        }

    @staticmethod
    def _get_cost_config() -> dict[str, dict[str, float]]:
        """
        从 ModelRouter 单例获取 cost_per_1k_tokens 配置。

        Returns:
            模型名 → {"input": float, "output": float} 映射
        """
        try:
            from src.harness.model_router import get_router
            return get_router()._cost_config
        except Exception:
            return {}

"""
所有 Tool 的抽象基类。
每个 Tool 必须继承此类并实现 execute() 和 _fallback() 方法。
"""
import time
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from src.harness.tracer import ToolSpan, get_current_trace


class ToolExecutionError(Exception):
    """Tool 执行失败异常。"""
    pass


class ToolTimeoutError(ToolExecutionError):
    """Tool 执行超时异常。"""
    pass


class BaseTool(ABC):
    """
    所有审核 Tool 的抽象基类。

    子类必须实现：
    - name: Tool 名称（用于日志和追踪）
    - description: Tool 功能描述
    - execute(): 主执行逻辑
    - _fallback(): 降级处理（不允许抛出异常）
    """
    name: str = "base_tool"
    description: str = ""

    @abstractmethod
    async def execute(self, input: Any) -> Any:
        """
        执行 Tool 的主逻辑。

        Args:
            input: 对应的 Input Pydantic Model 实例

        Returns:
            对应的 Output Pydantic Model 实例

        Raises:
            ToolExecutionError: 执行失败时抛出
            ToolTimeoutError: 超时时抛出
        """
        raise NotImplementedError

    @abstractmethod
    async def _fallback(self, error: Exception, input: Any) -> Any:
        """
        降级处理，在 execute() 失败时调用。
        此方法不允许抛出异常，必须返回一个有效的 Output。

        Args:
            error: 导致降级的异常
            input: 原始输入

        Returns:
            降级的 Output Pydantic Model 实例（is_fallback=True）
        """
        raise NotImplementedError

    async def run(self, input: Any) -> Any:
        """
        Tool 的安全执行入口，自动处理异常、降级和追踪。

        记录 ToolSpan（耗时、token 统计）并追加到当前 ReviewTrace。
        Agent 层调用此方法，而不是直接调用 execute()。
        """
        span = ToolSpan(tool_name=self.name, start_time=time.monotonic())

        try:
            logger.debug(f"Tool {self.name} starting", tool=self.name)
            result = await self.execute(input)
            logger.debug(f"Tool {self.name} completed", tool=self.name)
            span.success = True
            return result
        except (ToolExecutionError, ToolTimeoutError) as e:
            logger.warning(
                f"Tool {self.name} failed, using fallback",
                tool=self.name,
                error=str(e),
            )
            span.success = False
            span.error = f"{type(e).__name__}: {e}"
            return await self._fallback(e, input)
        except Exception as e:
            logger.error(
                f"Tool {self.name} unexpected error",
                tool=self.name,
                error=str(e),
                exc_info=True,
            )
            span.success = False
            span.error = f"{type(e).__name__}: {e}"
            return await self._fallback(e, input)
        finally:
            span.end_time = time.monotonic()
            # 追加到当前 trace（如果存在）
            trace = get_current_trace()
            if trace is not None:
                trace.spans.append(span)

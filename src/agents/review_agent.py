"""
Agent 主循环：薄门面层。

编排、决策、结果构建分别委托给：
- ReviewOrchestrator — Tool 编排
- DecisionEngine    — 置信度计算 + verdict 判定
- ResultBuilder     — reason / reviewer_hint / ReviewResult 组装
"""
import time

from src.schemas.request import ReviewRequest
from src.schemas.result import ReviewResult
from src.agents.orchestrator import ReviewOrchestrator
from src.agents.decision_engine import DecisionEngine
from src.agents.result_builder import ResultBuilder
from src.agents.types import ToolResultSet, Decision


class ReviewAgent:
    """
    广告素材合规审核 Agent。

    编排流程（按优先级）：
    1. TextViolationChecker — 每条素材必跑
    2. ImageContentChecker — 有图片时执行
    3. LandingPageChecker — 有落地页 URL 时执行
    4. QualificationChecker — 特殊行业时执行

    提前终止：任意 Tool 发现 high 违规且 confidence > 0.9 时跳过后续。
    综合置信度：双轨策略（详见 DecisionEngine）。
    """

    def __init__(self) -> None:
        """
        初始化 ReviewAgent。

        各职责委托给专用组件：
        - ReviewOrchestrator: Tool 实例化与编排
        - DecisionEngine: 置信度计算与 verdict 判定
        - ResultBuilder: 结果组装与 reason 生成
        """
        self._orchestrator = ReviewOrchestrator()
        self._decision_engine = DecisionEngine()
        self._result_builder = ResultBuilder()

    async def review(self, request: ReviewRequest) -> ReviewResult:
        """
        执行完整的广告素材合规审核。

        Args:
            request: ReviewRequest，包含素材内容和广告主信息

        Returns:
            ReviewResult，包含审核结论、违规项和面向广告主的说明
        """
        start_ms = time.monotonic()

        rs: ToolResultSet = await self._orchestrator.run(request)
        decision: Decision = self._decision_engine.decide(rs)
        return await self._result_builder.build(request, rs, decision, start_ms)

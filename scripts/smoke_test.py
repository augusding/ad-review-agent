"""
TextViolationChecker 冒烟测试脚本。

使用真实 DeepSeek API 对三条测试文案执行端到端审核，
验证规则库匹配 + LLM 语义检测 + 降级路径均正常工作。
"""
import asyncio

from loguru import logger

from src.tools.text_checker import TextViolationChecker
from src.schemas.tool_io import TextCheckerInput


SMOKE_CASES: list[dict[str, str]] = [
    {
        "request_id": "smoke-001",
        "text_content": "这是全网最好的游戏，第一名体验",
        "ad_category": "game",
    },
    {
        "request_id": "smoke-002",
        "text_content": "精美画面丰富剧情，和朋友一起冒险",
        "ad_category": "game",
    },
    {
        "request_id": "smoke-003",
        "text_content": "检测到您的手机存在风险，立即安装清理",
        "ad_category": "tool_app",
    },
]


async def main() -> None:
    """逐条执行冒烟测试并打印结果摘要。"""
    checker = TextViolationChecker()

    for case in SMOKE_CASES:
        input_data = TextCheckerInput(**case)
        result = await checker.run(input_data)

        logger.info(
            "Smoke test result",
            request_id=case["request_id"],
            violations_count=len(result.violations),
            confidence=result.confidence,
            is_fallback=result.is_fallback,
        )

        for v in result.violations:
            logger.info(
                "  violation",
                request_id=case["request_id"],
                severity=v.severity.value,
                evidence=v.evidence,
                regulation_ref=v.regulation_ref,
            )

    logger.info("smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())

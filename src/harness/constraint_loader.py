"""
约束配置加载器：从 harness/constraints.yaml 读取决策阈值和策略配置。

使用 lru_cache 缓存，进程生命周期内只读一次。
启动时校验关键字段存在，缺失则 fast-fail。
"""
import functools
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# 项目根目录下的 constraints.yaml
_CONSTRAINTS_PATH = Path(__file__).resolve().parent.parent.parent / "harness" / "constraints.yaml"

# 启动时必须存在的顶层字段
_REQUIRED_SECTIONS = (
    "decision_thresholds",
    "early_termination",
    "tool_execution",
    "qualification_required_categories",
    "confidence_strategy",
)


@functools.lru_cache(maxsize=1)
def load_constraints() -> dict[str, Any]:
    """
    加载 harness/constraints.yaml 并校验关键字段。

    Returns:
        解析后的配置字典

    Raises:
        FileNotFoundError: constraints.yaml 不存在
        ValueError: 缺少必需的配置字段
    """
    if not _CONSTRAINTS_PATH.exists():
        raise FileNotFoundError(
            f"constraints.yaml not found: {_CONSTRAINTS_PATH}"
        )

    with open(_CONSTRAINTS_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    missing = [s for s in _REQUIRED_SECTIONS if s not in config]
    if missing:
        raise ValueError(
            f"constraints.yaml missing required sections: {missing}"
        )

    logger.info(
        "Constraints loaded",
        version=config.get("version"),
        path=str(_CONSTRAINTS_PATH),
    )
    return config

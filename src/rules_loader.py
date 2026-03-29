"""
规则加载器：优先从数据库加载，数据库不可用时降级读取 JSON 文件。

所有 Tool 通过此模块加载规则，实现统一的数据库优先 + 文件保底策略。
"""
import json
import time
from pathlib import Path

from loguru import logger

from src.config import settings

# 缓存：rule_type → (content_dict, version_str, loaded_at_timestamp)
_cache: dict[str, tuple[dict, str, float]] = {}
_CHECK_INTERVAL = 300  # 每 5 分钟检查数据库版本


async def load_rules(rule_type: str) -> dict:
    """
    加载指定类型的规则。优先数据库，降级文件。

    Args:
        rule_type: 规则类型（forbidden_words / qualification_map /
                   category_rules / vivo_platform）

    Returns:
        规则内容字典
    """
    # 检查缓存是否需要刷新
    if rule_type in _cache:
        content, version, loaded_at = _cache[rule_type]
        if time.time() - loaded_at < _CHECK_INTERVAL:
            return content

    # 尝试从数据库加载
    db_content = await _load_from_db(rule_type)
    if db_content is not None:
        return db_content

    # 降级读取 JSON 文件
    return _load_from_file(rule_type)


def load_rules_sync(rule_type: str) -> dict:
    """
    同步加载规则（用于 Tool __init__）。仅从文件加载。

    Args:
        rule_type: 规则类型

    Returns:
        规则内容字典
    """
    if rule_type in _cache:
        content, _, loaded_at = _cache[rule_type]
        if time.time() - loaded_at < _CHECK_INTERVAL:
            return content
    return _load_from_file(rule_type)


async def _load_from_db(rule_type: str) -> dict | None:
    """
    从数据库加载当前生效的规则版本。

    Returns:
        规则字典，数据库不可用时返回 None
    """
    try:
        from sqlalchemy import select
        from src.database import RuleVersion, get_db

        async with get_db() as session:
            stmt = (
                select(RuleVersion)
                .where(RuleVersion.rule_type == rule_type, RuleVersion.is_active == True)
                .order_by(RuleVersion.created_at.desc())
                .limit(1)
            )
            rule = (await session.execute(stmt)).scalar_one_or_none()
            if rule:
                content = json.loads(rule.content_json)
                _cache[rule_type] = (content, rule.version, time.time())
                logger.debug(
                    "Rules loaded from database",
                    rule_type=rule_type,
                    version=rule.version,
                )
                return content
    except Exception as e:
        logger.debug(
            "Database rules not available, using file fallback",
            rule_type=rule_type,
            error=str(e),
        )
    return None


def _load_from_file(rule_type: str) -> dict:
    """
    从 JSON 文件加载规则（保底策略）。

    Returns:
        规则字典
    """
    file_map = {
        "forbidden_words": "forbidden_words.json",
        "qualification_map": "qualification_map.json",
        "category_rules": "category_rules.json",
        "vivo_platform": "vivo_platform.json",
    }
    filename = file_map.get(rule_type)
    if not filename:
        logger.warning("Unknown rule type", rule_type=rule_type)
        return {}

    path = Path(settings.rules_dir) / filename
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        _cache[rule_type] = (content, content.get("version", "file"), time.time())
        return content
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Failed to load rules from file", path=str(path), error=str(e))
        return {}


def invalidate_cache(rule_type: str) -> None:
    """
    手动清除指定规则类型的缓存，强制下次重新加载。

    Args:
        rule_type: 规则类型
    """
    _cache.pop(rule_type, None)

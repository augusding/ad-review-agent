#!/usr/bin/env python3
"""
执行这个脚本，生成完整的项目骨架（所有空文件和目录）。
在项目根目录执行：python scripts/init_project.py
"""
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent

DIRS = [
    "src/agents",
    "src/tools",
    "src/schemas",
    "src/prompts",
    "src/rules",
    "evals/golden_dataset",
    "evals/results",
    "tests/schemas",
    "tests/tools",
    "tests/agents",
    "scripts",
]

FILES = {
    # Python 包初始化
    "src/__init__.py": "",
    "src/agents/__init__.py": "",
    "src/tools/__init__.py": "# Tool 导出，每添加一个 Tool 在这里注册\n",
    "src/schemas/__init__.py": "",
    # 配置
    "src/config.py": '''"""
项目配置管理，所有配置从环境变量读取，不硬编码。
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # LLM API Keys
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    anthropic_api_key: str

    # 模型配置
    deepseek_model: str = "deepseek-chat"
    claude_model: str = "claude-sonnet-4-20250514"

    # 超时配置（秒）
    llm_timeout: float = 30.0
    http_timeout: float = 10.0

    # 重试配置
    llm_max_retries: int = 3
    llm_retry_delay: float = 1.0

    # 决策阈值
    auto_pass_threshold: float = 0.92
    auto_reject_threshold: float = 0.92
    human_review_lower: float = 0.70

    # Redis（人工复核队列）
    redis_url: str = "redis://localhost:6379/0"

    # PostgreSQL（审核日志）
    database_url: str = "postgresql://localhost:5432/ad_review"

    # 规则库路径
    rules_dir: str = "src/rules"

    # 日志级别
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
''',
    # Schemas
    "src/schemas/violation.py": '''"""
违规项数据模型。
"""
from enum import Enum
from pydantic import BaseModel, Field


class ViolationDimension(str, Enum):
    TEXT_VIOLATION = "text_violation"
    IMAGE_SAFETY = "image_safety"
    LANDING_PAGE = "landing_page"
    QUALIFICATION = "qualification"
    PLATFORM_RULE = "platform_rule"


class ViolationSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ViolationItem(BaseModel):
    """单个违规项。"""
    dimension: ViolationDimension = Field(description="违规所属的审核维度")
    description: str = Field(description="违规内容的具体描述")
    regulation_ref: str = Field(description="所违反的规定条款引用，如《广告法》第九条")
    severity: ViolationSeverity = Field(description="违规严重程度")
    evidence: str = Field(default="", description="违规的具体证据（违规文字或图片区域描述）")
''',
    "src/schemas/request.py": '''"""
审核请求数据模型。
"""
from enum import Enum
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class AdCategory(str, Enum):
    GAME = "game"
    TOOL_APP = "tool_app"
    ECOMMERCE = "ecommerce"
    FINANCE = "finance"
    HEALTH = "health"
    EDUCATION = "education"
    OTHER = "other"


class CreativeType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    TEXT = "text"
    LANDING_PAGE = "landing_page"
    MIXED = "mixed"


class AdPlatform(str, Enum):
    APP_STORE = "vivo_app_store"
    GAME_CENTER = "vivo_game_center"
    BROWSER = "vivo_browser"
    SCREEN_ON = "vivo_screen_on"
    OTHER = "other"


class CreativeContent(BaseModel):
    title: Optional[str] = Field(default=None, description="广告标题")
    description: Optional[str] = Field(default=None, description="广告描述文案")
    cta_text: Optional[str] = Field(default=None, description="行动召唤按钮文字")
    image_urls: list[str] = Field(default_factory=list, description="图片素材 URL 列表")
    video_url: Optional[str] = Field(default=None, description="视频素材 URL")
    landing_page_url: Optional[str] = Field(default=None, description="落地页 URL")


class ReviewRequest(BaseModel):
    """广告素材审核请求。"""
    request_id: str = Field(description="唯一请求 ID（UUID）")
    advertiser_id: str = Field(description="广告主 ID")
    ad_category: AdCategory = Field(description="广告品类")
    creative_type: CreativeType = Field(description="素材类型")
    content: CreativeContent = Field(description="素材内容")
    advertiser_qualification_ids: list[str] = Field(
        default_factory=list,
        description="广告主已提交的资质证明 ID 列表"
    )
    platform: AdPlatform = Field(
        default=AdPlatform.OTHER,
        description="投放平台"
    )
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
''',
    "src/schemas/result.py": '''"""
审核结果数据模型。
"""
from enum import Enum
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from .violation import ViolationItem, ViolationDimension


class ReviewVerdict(str, Enum):
    PASS = "pass"
    REVIEW = "review"   # 需要人工复核
    REJECT = "reject"


class ReviewResult(BaseModel):
    """广告素材审核结果。"""
    request_id: str = Field(description="对应请求的 ID")
    verdict: ReviewVerdict = Field(description="审核结论")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 0-1")
    violations: list[ViolationItem] = Field(
        default_factory=list,
        description="违规项列表，pass 时为空"
    )
    reason: str = Field(description="面向广告主的说明（中文）")
    checked_dimensions: list[ViolationDimension] = Field(
        default_factory=list,
        description="实际执行了检测的维度"
    )
    skipped_dimensions: list[ViolationDimension] = Field(
        default_factory=list,
        description="被跳过的维度"
    )
    skip_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="维度被跳过的原因"
    )
    processing_ms: int = Field(default=0, description="处理耗时（毫秒）")
    model_used: str = Field(default="", description="使用的模型")
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)
    is_fallback: bool = Field(default=False, description="是否为降级结果")
    fallback_reason: Optional[str] = Field(default=None, description="降级原因")

    @classmethod
    def human_review_required(
        cls,
        request_id: str,
        reason: str,
        fallback_reason: str,
    ) -> "ReviewResult":
        """创建一个要求人工复核的降级结果。"""
        return cls(
            request_id=request_id,
            verdict=ReviewVerdict.REVIEW,
            confidence=0.0,
            reason=reason,
            is_fallback=True,
            fallback_reason=fallback_reason,
        )
''',
    "src/schemas/tool_io.py": '''"""
各 Tool 的输入输出 Schema。
每添加一个新 Tool，在这里追加对应的 Input/Output 模型。
"""
from typing import Optional
from pydantic import BaseModel, Field

from .violation import ViolationItem


# ==================== TextViolationChecker ====================

class TextCheckerInput(BaseModel):
    text_content: str = Field(description="待检测的广告文案全文（标题+描述+CTA 拼接）")
    ad_category: str = Field(description="广告品类，影响专项规则的应用")
    request_id: str = Field(description="请求 ID，用于日志追踪")


class TextCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)


# ==================== ImageContentChecker ====================

class ImageCheckerInput(BaseModel):
    image_urls: list[str] = Field(description="图片 URL 列表")
    ad_category: str = Field(description="广告品类")
    request_id: str = Field(description="请求 ID")


class ImageCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)


# ==================== LandingPageChecker ====================

class LandingPageCheckerInput(BaseModel):
    landing_page_url: str = Field(description="落地页 URL")
    creative_summary: str = Field(description="素材内容摘要，用于一致性比对")
    request_id: str = Field(description="请求 ID")


class LandingPageCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    page_accessible: bool = Field(default=True, description="落地页是否可访问")
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)


# ==================== QualificationChecker ====================

class QualificationCheckerInput(BaseModel):
    ad_category: str = Field(description="广告品类")
    qualification_ids: list[str] = Field(description="广告主提交的资质 ID 列表")
    request_id: str = Field(description="请求 ID")


class QualificationCheckerOutput(BaseModel):
    violations: list[ViolationItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    missing_qualifications: list[str] = Field(
        default_factory=list,
        description="缺少的资质名称"
    )
    is_fallback: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)
''',
    # BaseTool
    "src/tools/base_tool.py": '''"""
所有 Tool 的抽象基类。
每个 Tool 必须继承此类并实现 execute() 和 _fallback() 方法。
"""
from abc import ABC, abstractmethod
from typing import Any
from loguru import logger


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
        Tool 的安全执行入口，自动处理异常和降级。
        Agent 层调用此方法，而不是直接调用 execute()。
        """
        try:
            logger.debug(f"Tool {self.name} starting", tool=self.name)
            result = await self.execute(input)
            logger.debug(f"Tool {self.name} completed", tool=self.name)
            return result
        except (ToolExecutionError, ToolTimeoutError) as e:
            logger.warning(
                f"Tool {self.name} failed, using fallback",
                tool=self.name,
                error=str(e),
            )
            return await self._fallback(e, input)
        except Exception as e:
            logger.error(
                f"Tool {self.name} unexpected error",
                tool=self.name,
                error=str(e),
                exc_info=True,
            )
            return await self._fallback(e, input)
''',
    # 规则库初始文件
    "src/rules/forbidden_words.json": '''{
  "version": "1.0.0",
  "updated_at": "2026-03-01",
  "updated_by": "初始化",
  "description": "广告违禁词库，基于《广告法》和平台规范",
  "rules": {
    "absolute_terms": {
      "description": "绝对化用语（广告法第九条）",
      "severity": "high",
      "regulation_ref": "《广告法》第九条第三款",
      "words": [
        "最好", "最佳", "最优", "最大", "最强", "最高", "最低", "最安全",
        "第一", "唯一", "顶级", "极致", "终极", "无与伦比",
        "国家级", "国际领先", "全球第一", "行业第一",
        "保证", "承诺效果"
      ]
    },
    "false_claims": {
      "description": "虚假宣传表述",
      "severity": "high",
      "regulation_ref": "《广告法》第二十八条",
      "patterns": [
        "下载量超.*亿",
        "好评率.*99",
        "用户数.*亿",
        "市值.*亿"
      ]
    },
    "inducement": {
      "description": "诱导性表述",
      "severity": "medium",
      "regulation_ref": "《互联网广告管理办法》第九条",
      "words": [
        "系统检测到", "您的手机存在风险", "立即清理",
        "恭喜您获得", "您被选中", "专属奖励"
      ]
    },
    "game_specific": {
      "description": "游戏买量专项禁用词",
      "severity": "high",
      "regulation_ref": "《网络游戏管理暂行办法》",
      "words": [
        "充值返利", "高额回报", "月入万元", "躺赚",
        "内购必赚", "不花钱也能赢"
      ]
    },
    "finance_specific": {
      "description": "金融类专项禁用词",
      "severity": "high",
      "regulation_ref": "《广告法》第二十五条",
      "words": [
        "保本", "保息", "无风险", "稳赚", "零风险",
        "年化收益.*%", "保证收益"
      ]
    }
  }
}
''',
    "src/rules/qualification_map.json": '''{
  "version": "1.0.0",
  "updated_at": "2026-03-01",
  "description": "行业品类与所需广告资质的映射关系",
  "qualifications": {
    "game": {
      "required": [
        {
          "name": "游戏版号",
          "description": "国家新闻出版署批准的游戏版号",
          "verification": "official_api",
          "severity_if_missing": "high"
        }
      ],
      "recommended": [
        {
          "name": "游戏分级标注",
          "description": "适龄提示标注",
          "severity_if_missing": "low"
        }
      ]
    },
    "finance": {
      "required": [
        {
          "name": "金融牌照",
          "description": "银保监会/证监会颁发的相关牌照",
          "verification": "format_check",
          "format_pattern": "^[A-Z0-9]{10,20}$",
          "severity_if_missing": "high"
        }
      ]
    },
    "health": {
      "required": [
        {
          "name": "医疗器械/药品广告批准文号",
          "description": "国家药监局批准的广告文号",
          "verification": "format_check",
          "format_pattern": "^(国械广审|国药广审)",
          "severity_if_missing": "high"
        }
      ]
    },
    "education": {
      "conditional": [
        {
          "name": "学科类培训资质",
          "condition": "涉及义务教育阶段学科培训",
          "severity_if_missing": "high"
        }
      ]
    }
  }
}
''',
    # Prompt 占位文件
    "src/prompts/system_prompt.txt": "# Version: 0.1\n# 待填写：主 System Prompt\n# 参考 ADD.md 第 4 节审核维度定义\n",
    "src/prompts/text_checker.txt": "# Version: 0.1\n# 待填写：TextViolationChecker 专用 Prompt\n",
    "src/prompts/image_checker.txt": "# Version: 0.1\n# 待填写：ImageContentChecker 专用 Prompt\n",
    "src/prompts/few_shot_examples.txt": "# Version: 0.1\n# 待填写：Few-shot 正例/负例/边界例\n",
    # Golden Dataset 占位
    "evals/golden_dataset/pass_cases.jsonl": "",
    "evals/golden_dataset/reject_cases.jsonl": "",
    "evals/golden_dataset/review_cases.jsonl": "",
    "evals/golden_dataset/adversarial_cases.jsonl": "",
    # 测试配置
    "tests/__init__.py": "",
    "tests/schemas/__init__.py": "",
    "tests/tools/__init__.py": "",
    "tests/agents/__init__.py": "",
    "tests/conftest.py": '''"""
pytest 全局配置和公共 fixtures。
"""
import pytest
from src.schemas.request import ReviewRequest, AdCategory, CreativeType, CreativeContent


@pytest.fixture
def sample_game_request() -> ReviewRequest:
    """游戏类广告请求样本（用于测试，不使用真实数据）。"""
    return ReviewRequest(
        request_id="test-001",
        advertiser_id="advertiser-test-001",
        ad_category=AdCategory.GAME,
        creative_type=CreativeType.TEXT,
        content=CreativeContent(
            title="测试游戏广告标题",
            description="这是一个用于测试的游戏描述文案",
            cta_text="立即下载",
        ),
        advertiser_qualification_ids=["qual-001"],
    )


@pytest.fixture
def sample_finance_request() -> ReviewRequest:
    """金融类广告请求样本。"""
    return ReviewRequest(
        request_id="test-002",
        advertiser_id="advertiser-test-002",
        ad_category=AdCategory.FINANCE,
        creative_type=CreativeType.TEXT,
        content=CreativeContent(
            title="理财产品广告",
            description="年化收益8%，安全稳健",
            cta_text="立即投资",
        ),
    )
''',
    # pyproject.toml
    "pyproject.toml": '''[project]
name = "ad-review-agent"
version = "0.1.0"
description = "广告素材合规审核 Agent"
requires-python = ">=3.11"

dependencies = [
    "anthropic>=0.40.0",
    "openai>=1.50.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.0.0",
    "httpx>=0.27.0",
    "loguru>=0.7.0",
    "python-dotenv>=1.0.0",
    "redis>=5.0.0",
    "asyncpg>=0.29.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.0.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
''',
    # .env.example
    ".env.example": '''# 复制此文件为 .env 并填写真实值
# 不要把 .env 提交到 git

# DeepSeek API
DEEPSEEK_API_KEY=sk-your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Anthropic API（用于图片/视频多模态审核）
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key

# 数据库
DATABASE_URL=postgresql://user:password@localhost:5432/ad_review
REDIS_URL=redis://localhost:6379/0

# 决策阈值（可选，有默认值）
# AUTO_PASS_THRESHOLD=0.92
# AUTO_REJECT_THRESHOLD=0.92
# HUMAN_REVIEW_LOWER=0.70

# 日志级别
LOG_LEVEL=INFO
''',
}

def init_project():
    for dir_path in DIRS:
        full_path = ROOT / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        print(f"✓ 创建目录: {dir_path}")

    for file_path, content in FILES.items():
        full_path = ROOT / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        if not full_path.exists():
            full_path.write_text(content, encoding="utf-8")
            print(f"✓ 创建文件: {file_path}")
        else:
            print(f"  跳过（已存在）: {file_path}")

    print("\n✅ 项目骨架初始化完成！")
    print("\n下一步：")
    print("  1. cp .env.example .env  （填写 API Keys）")
    print("  2. uv sync --dev          （安装依赖）")
    print("  3. pytest tests/          （验证骨架正常）")


if __name__ == "__main__":
    init_project()

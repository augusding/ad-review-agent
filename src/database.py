"""
数据库模型与连接管理。

定义审核记录表（ReviewRecord）和待审核队列表（ReviewQueue），
提供异步引擎初始化、建表和 session 管理。
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

from loguru import logger
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.config import settings


# ==================== Base ====================

class Base(DeclarativeBase):
    """SQLAlchemy 声明基类。"""
    pass


# ==================== ORM Models ====================

class ReviewRecord(Base):
    """
    审核记录表。

    存储每次审核的完整结果，包含 Agent 结论和人工复核信息。
    不存储原始素材内容（隐私合规要求，见 ADD.md 第8节）。
    """

    __tablename__ = "review_records"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    advertiser_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )
    ad_category: Mapped[str] = mapped_column(String(32), nullable=False)
    creative_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )
    audio_transcript: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )

    # Agent 审核结果
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    violations_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer_hint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processing_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now()
    )

    # 人工审核字段
    human_verdict: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default=None
    )
    human_reviewer: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    human_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    human_note: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    # 关联
    queue_item: Mapped["ReviewQueue | None"] = relationship(
        back_populates="review_record", uselist=False
    )

    def __repr__(self) -> str:
        """调试用字符串表示。"""
        return (
            f"<ReviewRecord request_id={self.request_id} "
            f"verdict={self.verdict} confidence={self.confidence}>"
        )


class ReviewQueue(Base):
    """
    待审核队列表。

    仅存储 verdict=review 的案例，供人工审核员处理。
    """

    __tablename__ = "review_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("review_records.request_id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    assigned_to: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )

    # 关联
    review_record: Mapped[ReviewRecord] = relationship(
        back_populates="queue_item"
    )

    def __repr__(self) -> str:
        """调试用字符串表示。"""
        return (
            f"<ReviewQueue request_id={self.request_id} "
            f"status={self.status} priority={self.priority}>"
        )


class User(Base):
    """
    用户表。

    存储管理后台用户信息，支持角色和审批状态管理。
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    real_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    employee_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="viewer"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, default=None
    )

    def __repr__(self) -> str:
        """调试用字符串表示。"""
        return (
            f"<User username={self.username} role={self.role} "
            f"status={self.status}>"
        )


class AsyncTask(Base):
    """
    异步审核任务表。

    视频等耗时审核任务异步执行，调用方通过 task_id 轮询状态。
    """

    __tablename__ = "async_tasks"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sync_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<AsyncTask id={self.id} status={self.status}>"


class RuleVersion(Base):
    """
    规则版本表。

    每次规则变更生成一条记录，is_active 标记当前生效版本。
    """

    __tablename__ = "rule_versions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    rule_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return (
            f"<RuleVersion type={self.rule_type} version={self.version} "
            f"active={self.is_active}>"
        )


# ==================== 引擎与 Session ====================

def _build_async_url(url: str) -> str:
    """
    将数据库连接字符串转换为异步驱动格式。

    支持 PostgreSQL（asyncpg）和 SQLite（aiosqlite）。

    Args:
        url: 原始数据库连接字符串

    Returns:
        异步驱动兼容的连接字符串
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("sqlite+aiosqlite://"):
        return url
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def _engine_kwargs(url: str) -> dict:
    """
    根据数据库类型返回引擎参数。

    SQLite 需要 check_same_thread=False；
    PostgreSQL 使用连接池配置。

    Args:
        url: 异步驱动格式的连接字符串

    Returns:
        create_async_engine 的关键字参数
    """
    kwargs: dict = {"echo": False}
    if "sqlite" in url:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
    return kwargs


_async_url = _build_async_url(settings.database_url)
_engine = create_async_engine(_async_url, **_engine_kwargs(_async_url))

_async_session = async_sessionmaker(
    _engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """
    创建所有表（如不存在）。

    在应用启动时调用一次。生产环境建议使用 Alembic 迁移替代。
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized")


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库异步 session 的上下文管理器。

    用法：
        async with get_db() as session:
            session.add(record)
            await session.commit()

    Yields:
        AsyncSession 实例，退出时自动关闭
    """
    session = _async_session()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

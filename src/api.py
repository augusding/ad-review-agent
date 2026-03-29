"""
广告素材合规审核 HTTP API。

提供审核接口、人工复核队列管理、统计查询和管理后台 UI。
"""
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Annotated, Optional

import bcrypt

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select, func as sa_func, and_, text

from src.agents.review_agent import ReviewAgent
from src.config import settings
from src.middleware import APIKeyMiddleware, RateLimitMiddleware
from src.uploader import cleanup_upload, save_upload_file, validate_file_type
from src.database import (
    AsyncTask,
    ReviewRecord,
    ReviewQueue,
    RuleVersion,
    User,
    get_db,
    init_db,
)
from src.schemas.request import CreativeContent, ReviewRequest
from src.schemas.result import ReviewResult, ReviewVerdict

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


_agent: ReviewAgent | None = None
_ingest_router = None
_task_worker = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理：启动时初始化数据库、ReviewAgent 和接入适配器。

    Agent 在启动时创建，复用规则库和 Prompt 缓存，
    避免每次请求重新加载。
    """
    global _agent, _ingest_router, _task_worker

    logger.info("Initializing database...")
    try:
        await init_db()
    except Exception as e:
        logger.warning(
            "Database init failed, API will run without persistence",
            error=str(e),
        )

    # 确保默认管理员存在
    await _ensure_default_admin()

    logger.info("Initializing ReviewAgent...")
    _agent = ReviewAgent()
    logger.info("ReviewAgent ready, server accepting requests")

    # 初始化接入适配器
    from src.ingest.router import IngestRouter
    _ingest_router = IngestRouter(_agent)
    await _ingest_router.start_all()

    # 启动异步任务处理器
    from src.task_worker import TaskWorker
    _task_worker = TaskWorker(_agent)
    await _task_worker.start()

    yield

    # 关闭
    if _task_worker:
        await _task_worker.stop()
    if _ingest_router:
        await _ingest_router.stop_all()
    logger.info("Server shutting down")


app = FastAPI(
    title="Ad Creative Compliance Review API",
    version="0.1.0",
    description="广告素材合规审核 Agent HTTP 接口",
    lifespan=lifespan,
)

# 注册中间件（顺序：先限流再认证，执行时反序）
app.add_middleware(RateLimitMiddleware)
app.add_middleware(APIKeyMiddleware)

# Session 签名器
_signer = URLSafeTimedSerializer(settings.session_secret)
_SESSION_COOKIE = "ad_review_session"
_SESSION_MAX_AGE = 86400  # 24 hours


def _hash_password(password: str) -> str:
    """对密码进行 bcrypt 哈希。"""
    return bcrypt.hashpw(
        password.encode(), bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    ).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    """验证密码是否匹配 bcrypt 哈希。"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def _ensure_default_admin() -> None:
    """首次启动时自动创建默认管理员账号。"""
    try:
        async with get_db() as session:
            stmt = select(User).where(User.username == settings.default_admin_username)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                return
            admin = User(
                username=settings.default_admin_username,
                email=f"{settings.default_admin_username}@vivo.com",
                password_hash=_hash_password(settings.default_admin_password),
                real_name="系统管理员",
                employee_id="ADMIN",
                role="admin",
                status="active",
            )
            session.add(admin)
            await session.commit()
            logger.info("Default admin user created", username=settings.default_admin_username)
    except Exception as e:
        logger.warning("Failed to create default admin", error=str(e))


def _get_session_data(request: Request) -> dict:
    """
    从 session cookie 解析用户数据。

    Returns:
        包含 username/role 的字典，解析失败时返回空字典
    """
    token = request.cookies.get(_SESSION_COOKIE)
    if not token:
        return {}
    try:
        return _signer.loads(token, max_age=_SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return {}


def _get_current_user(request: Request) -> dict:
    """
    从 session cookie 获取当前登录用户。

    未登录时重定向到 /login。

    Args:
        request: HTTP 请求

    Returns:
        包含 username/role 的用户数据字典

    Raises:
        HTTPException: 302 重定向到登录页
    """
    data = _get_session_data(request)
    if not data or "username" not in data:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return data


def _require_role(*roles: str):
    """
    生成角色检查依赖项。

    Args:
        roles: 允许的角色列表

    Returns:
        FastAPI 依赖函数
    """
    def _checker(user: dict = Depends(_get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user
    return _checker


# ==================== 审核接口 ====================


@app.post("/review")
async def review_ad(request: ReviewRequest) -> JSONResponse:
    """
    审核广告素材。

    同步模式：文案/图片/落地页/资质/平台规范检测，立即返回结果。
    异步模式：当包含视频时，快速检测文案/资质，视频审核异步执行，
    返回 task_id 供轮询。

    Args:
        request: 广告素材审核请求

    Returns:
        同步结果（ReviewResult）或异步任务信息
    """
    has_video = bool(request.content.video_url)

    logger.info(
        "API request received",
        request_id=request.request_id,
        ad_category=request.ad_category.value,
        creative_type=request.creative_type.value,
        async_mode=has_video,
    )

    # 视频素材 → 异步模式
    if has_video:
        return await _handle_async_review(request)

    # 非视频 → 同步模式
    result = await _agent.review(request)

    logger.info(
        "API request completed",
        request_id=result.request_id,
        verdict=result.verdict.value,
        confidence=result.confidence,
        duration_ms=result.processing_ms,
    )

    await _save_to_db(request, result)
    return JSONResponse(content=result.model_dump(mode="json"))


async def _handle_async_review(request: ReviewRequest) -> JSONResponse:
    """
    异步审核处理：快速检测文案/资质，视频部分异步执行。

    如果文案/资质已发现高置信度违规，直接返回 reject。
    否则创建异步任务，后台处理视频审核。

    Args:
        request: 审核请求

    Returns:
        直接 reject 结果，或异步任务信息
    """
    from src.schemas.tool_io import TextCheckerInput, QualificationCheckerInput
    from src.schemas.violation import ViolationSeverity

    # 快速文案检测
    text_content = "\n".join(filter(None, [
        request.content.title, request.content.description, request.content.cta_text
    ]))
    text_violations = []
    text_confidence = 0.95
    if text_content:
        text_input = TextCheckerInput(
            text_content=text_content,
            ad_category=request.ad_category.value,
            request_id=request.request_id,
        )
        text_result = await _agent._text_checker.run(text_input)
        text_violations = text_result.violations
        text_confidence = text_result.confidence

    # 如果文案已发现高置信度违规，直接返回
    high_violations = [
        v for v in text_violations if v.severity == ViolationSeverity.HIGH
    ]
    if high_violations and text_confidence >= 0.92:
        result = await _agent.review(request)  # 完整审核（文案已缓存，很快）
        await _save_to_db(request, result)
        return JSONResponse(content=result.model_dump(mode="json"))

    # 创建异步任务
    try:
        async with get_db() as session:
            task = AsyncTask(
                request_id=request.request_id,
                status="pending",
                request_json=request.model_dump_json(),
                sync_result_json=json.dumps({
                    "text_verdict": "reject" if text_violations else "pass",
                    "text_violations": len(text_violations),
                    "text_confidence": text_confidence,
                }, ensure_ascii=False),
            )
            session.add(task)
            await session.commit()
            task_id = task.id

        logger.info(
            "Async task created for video review",
            task_id=task_id,
            request_id=request.request_id,
        )

        return JSONResponse(content={
            "mode": "async",
            "task_id": task_id,
            "request_id": request.request_id,
            "status": "pending",
            "sync_result": {
                "text_verdict": "reject" if text_violations else "pass",
                "text_violations": len(text_violations),
            },
            "message": "视频内容审核已提交，预计30秒内完成",
        })
    except Exception as e:
        logger.error("Failed to create async task", error=str(e))
        # 降级为同步
        result = await _agent.review(request)
        await _save_to_db(request, result)
        return JSONResponse(content=result.model_dump(mode="json"))


@app.get("/review/task/{task_id}")
async def get_task_status(task_id: str) -> dict:
    """
    查询异步审核任务状态。

    Args:
        task_id: 任务 ID

    Returns:
        任务状态和结果
    """
    try:
        async with get_db() as session:
            task = (await session.execute(
                select(AsyncTask).where(AsyncTask.id == task_id)
            )).scalar_one_or_none()

            if not task:
                return {"error": "task_not_found"}

            response = {
                "task_id": task.id,
                "request_id": task.request_id,
                "status": task.status,
                "created_at": task.created_at.isoformat() if task.created_at else None,
            }

            if task.status == "processing":
                response["progress"] = "视频审核处理中..."
                response["started_at"] = task.started_at.isoformat() if task.started_at else None

            elif task.status == "completed" and task.result_json:
                result = json.loads(task.result_json)
                response["result"] = result
                response["completed_at"] = task.completed_at.isoformat() if task.completed_at else None

            elif task.status == "failed":
                response["error"] = task.error_message

            return response
    except Exception as e:
        return {"error": str(e)}


@app.post("/review/upload")
async def review_upload(
    files: list[UploadFile] = File(..., description="图片或视频文件，最多5个"),
    advertiser_id: str = Form(..., description="广告主 ID"),
    ad_category: str = Form(..., description="广告品类"),
    title: Optional[str] = Form(default=None, description="广告标题"),
    description: Optional[str] = Form(default=None, description="广告描述"),
    cta_text: Optional[str] = Form(default=None, description="CTA 文案"),
    landing_page_url: Optional[str] = Form(default=None, description="落地页 URL"),
):
    """
    通过文件上传审核广告素材。

    接收 multipart/form-data，支持图片和视频文件直接上传。
    文件保存为临时文件，审核完成后自动清理。

    Args:
        files: 上传的图片/视频文件列表（最多5个）
        advertiser_id: 广告主 ID
        ad_category: 广告品类
        title: 广告标题（可选）
        description: 广告描述（可选）
        cta_text: CTA 文案（可选）
        landing_page_url: 落地页 URL（可选）

    Returns:
        审核结果 ReviewResult
    """
    request_id = f"upload-{uuid.uuid4().hex[:12]}"

    if len(files) > 5:
        return ReviewResult.human_review_required(
            request_id=request_id,
            reason="上传文件数量超过限制（最多5个）",
            fallback_reason="file_count_exceeded",
        )

    saved_paths: list[str] = []
    image_paths: list[str] = []
    video_path: str | None = None

    try:
        # 1. 验证文件类型和大小，保存到临时目录
        for f in files:
            try:
                file_type = validate_file_type(f.filename or "", f.content_type or "")
            except ValueError as e:
                logger.warning(
                    "Unsupported file type in upload",
                    request_id=request_id,
                    filename=f.filename,
                    error=str(e),
                )
                return ReviewResult.human_review_required(
                    request_id=request_id,
                    reason=f"不支持的文件类型: {f.filename}",
                    fallback_reason=f"unsupported_file_type: {f.filename}",
                )

            # 检查文件大小
            content = await f.read()
            size_mb = len(content) / (1024 * 1024)
            await f.seek(0)  # 重置读取位置

            if file_type == "image" and size_mb > settings.max_image_size_mb:
                return ReviewResult.human_review_required(
                    request_id=request_id,
                    reason=f"图片文件过大: {f.filename} ({size_mb:.1f}MB，上限{settings.max_image_size_mb}MB)",
                    fallback_reason="image_too_large",
                )
            if file_type == "video" and size_mb > settings.max_video_size_mb:
                return ReviewResult.human_review_required(
                    request_id=request_id,
                    reason=f"视频文件过大: {f.filename} ({size_mb:.1f}MB，上限{settings.max_video_size_mb}MB)",
                    fallback_reason="video_too_large",
                )

            saved_path = await save_upload_file(f)
            saved_paths.append(saved_path)

            if file_type == "image":
                image_paths.append(saved_path)
            elif file_type == "video":
                if video_path is not None:
                    return ReviewResult.human_review_required(
                        request_id=request_id,
                        reason="一次审核最多支持上传1个视频文件",
                        fallback_reason="multiple_videos",
                    )
                video_path = saved_path

        # 2. 确定 creative_type
        if video_path and image_paths:
            creative_type = "mixed"
        elif video_path:
            creative_type = "video"
        elif image_paths:
            creative_type = "image"
        else:
            creative_type = "text"

        # 3. 构建 ReviewRequest
        review_request = ReviewRequest(
            request_id=request_id,
            advertiser_id=advertiser_id,
            ad_category=ad_category,
            creative_type=creative_type,
            content=CreativeContent(
                title=title,
                description=description,
                cta_text=cta_text,
                image_urls=image_paths,
                video_url=video_path,
                landing_page_url=landing_page_url,
            ),
        )

        logger.info(
            "Upload review request built",
            request_id=request_id,
            ad_category=ad_category,
            creative_type=creative_type,
            image_count=len(image_paths),
            has_video=video_path is not None,
        )

        # 4. 视频上传 → 异步模式（不清理视频文件，由 TaskWorker 完成后清理）
        if video_path:
            resp = await _handle_async_review(review_request)
            # 异步模式下只清理图片，视频留给 worker
            for path in image_paths:
                cleanup_upload(path)
            saved_paths.clear()  # 防止 finally 重复清理
            return resp

        # 非视频 → 同步审核
        result = await _agent.review(review_request)

        logger.info(
            "Upload review completed",
            request_id=result.request_id,
            verdict=result.verdict.value,
            confidence=result.confidence,
            duration_ms=result.processing_ms,
        )

        await _save_to_db(review_request, result)
        return JSONResponse(content=result.model_dump(mode="json"))

    finally:
        # 清理临时文件（异步模式下 saved_paths 已清空）
        for path in saved_paths:
            cleanup_upload(path)


async def _save_to_db(request: ReviewRequest, result: ReviewResult) -> None:
    """
    将审核结果写入数据库。

    写入 ReviewRecord；如果 verdict=review 则同时写入 ReviewQueue。
    数据库异常只记录日志，不影响接口响应。

    Args:
        request: 原始审核请求
        result: 审核结果
    """
    try:
        async with get_db() as session:
            violations_data = [
                v.model_dump(mode="json") for v in result.violations
            ]

            record = ReviewRecord(
                request_id=result.request_id,
                advertiser_id=request.advertiser_id,
                ad_category=request.ad_category.value,
                creative_type=request.creative_type.value,
                content_json=request.content.model_dump_json(),
                verdict=result.verdict.value,
                confidence=result.confidence,
                violations_json=json.dumps(violations_data, ensure_ascii=False),
                reason=result.reason,
                reviewer_hint=result.reviewer_hint,
                is_fallback=result.is_fallback,
                processing_ms=result.processing_ms,
                reviewed_at=result.reviewed_at,
            )
            session.add(record)

            # verdict=review → 加入人工复核队列
            if result.verdict == ReviewVerdict.REVIEW:
                # 低置信度优先级更高
                priority = 1 if result.confidence < 0.70 else 0
                queue_item = ReviewQueue(
                    request_id=result.request_id,
                    status="pending",
                    priority=priority,
                )
                session.add(queue_item)

            await session.commit()

            logger.info(
                "Review result saved to database",
                request_id=result.request_id,
                verdict=result.verdict.value,
                queued=result.verdict == ReviewVerdict.REVIEW,
            )
    except Exception as e:
        logger.error(
            "Failed to save review result to database",
            request_id=result.request_id,
            error=str(e),
        )


# ==================== 队列管理接口 ====================


@app.get("/queue")
async def get_queue(
    status: str = Query(default="pending", description="队列状态筛选"),
    limit: int = Query(default=50, ge=1, le=200, description="返回条数"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
) -> dict:
    """
    获取待审核队列。

    Args:
        status: 状态筛选（pending/processing/done）
        limit: 返回条数上限
        offset: 分页偏移

    Returns:
        队列列表和总数
    """
    try:
        async with get_db() as session:
            # 查总数
            count_stmt = select(sa_func.count()).select_from(ReviewQueue).where(
                ReviewQueue.status == status
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            # 查列表（按 priority 降序 + created_at 降序）
            stmt = (
                select(ReviewQueue, ReviewRecord)
                .join(
                    ReviewRecord,
                    ReviewQueue.request_id == ReviewRecord.request_id,
                )
                .where(ReviewQueue.status == status)
                .order_by(
                    ReviewQueue.priority.desc(),
                    ReviewQueue.created_at.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()

            items = []
            for queue_item, record in rows:
                items.append({
                    "request_id": queue_item.request_id,
                    "status": queue_item.status,
                    "priority": queue_item.priority,
                    "assigned_to": queue_item.assigned_to,
                    "created_at": queue_item.created_at.isoformat() if queue_item.created_at else None,
                    "ad_category": record.ad_category,
                    "verdict": record.verdict,
                    "confidence": record.confidence,
                    "is_fallback": record.is_fallback,
                    "reason": record.reason,
                })

            return {"total": total, "items": items}
    except Exception as e:
        logger.error("Failed to query review queue", error=str(e))
        return {"total": 0, "items": [], "error": str(e)}


@app.get("/queue/{request_id}")
async def get_queue_detail(request_id: str) -> dict:
    """
    获取单条待审核详情。

    Args:
        request_id: 请求 ID

    Returns:
        审核记录详情和队列状态
    """
    try:
        async with get_db() as session:
            stmt = select(ReviewRecord).where(
                ReviewRecord.request_id == request_id
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if not record:
                return {"error": "not_found", "message": f"Record {request_id} not found"}

            # 查队列状态
            queue_stmt = select(ReviewQueue).where(
                ReviewQueue.request_id == request_id
            )
            queue_item = (await session.execute(queue_stmt)).scalar_one_or_none()

            violations = json.loads(record.violations_json) if record.violations_json else []
            content = json.loads(record.content_json) if record.content_json else {}

            return {
                "request_id": record.request_id,
                "advertiser_id": record.advertiser_id,
                "ad_category": record.ad_category,
                "creative_type": record.creative_type,
                "content": content,
                "verdict": record.verdict,
                "confidence": record.confidence,
                "violations": violations,
                "reason": record.reason,
                "reviewer_hint": record.reviewer_hint,
                "is_fallback": record.is_fallback,
                "processing_ms": record.processing_ms,
                "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
                "human_verdict": record.human_verdict,
                "human_reviewer": record.human_reviewer,
                "human_reviewed_at": record.human_reviewed_at.isoformat() if record.human_reviewed_at else None,
                "human_note": record.human_note,
                "audio_transcript": record.audio_transcript,
                "queue_status": queue_item.status if queue_item else None,
                "queue_priority": queue_item.priority if queue_item else None,
                "queue_assigned_to": queue_item.assigned_to if queue_item else None,
            }
    except Exception as e:
        logger.error(
            "Failed to query queue detail",
            request_id=request_id,
            error=str(e),
        )
        return {"error": "query_failed", "message": str(e)}


class HumanDecision(BaseModel):
    """人工审核决定请求体。"""
    verdict: str = Field(description="人工审核结论：pass 或 reject")
    reviewer: str = Field(description="审核员标识")
    note: str = Field(default="", description="审核备注")


@app.post("/queue/{request_id}/decide")
async def decide_review(request_id: str, decision: HumanDecision) -> dict:
    """
    提交人工审核决定。

    更新 ReviewRecord 的人工审核字段，将 ReviewQueue 状态设为 done。

    Args:
        request_id: 请求 ID
        decision: 人工审核决定

    Returns:
        操作结果
    """
    if decision.verdict not in ("pass", "reject"):
        return {"error": "invalid_verdict", "message": "verdict must be 'pass' or 'reject'"}

    try:
        async with get_db() as session:
            # 更新 ReviewRecord
            stmt = select(ReviewRecord).where(
                ReviewRecord.request_id == request_id
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if not record:
                return {"error": "not_found", "message": f"Record {request_id} not found"}

            record.human_verdict = decision.verdict
            record.human_reviewer = decision.reviewer
            record.human_reviewed_at = datetime.utcnow()
            record.human_note = decision.note

            # 更新 ReviewQueue 状态
            queue_stmt = select(ReviewQueue).where(
                ReviewQueue.request_id == request_id
            )
            queue_item = (await session.execute(queue_stmt)).scalar_one_or_none()
            if queue_item:
                queue_item.status = "done"
                queue_item.assigned_to = decision.reviewer

            await session.commit()

            logger.info(
                "Human decision recorded",
                request_id=request_id,
                human_verdict=decision.verdict,
                reviewer=decision.reviewer,
            )

            return {
                "status": "ok",
                "request_id": request_id,
                "human_verdict": decision.verdict,
                "reviewer": decision.reviewer,
            }
    except Exception as e:
        logger.error(
            "Failed to record human decision",
            request_id=request_id,
            error=str(e),
        )
        return {"error": "save_failed", "message": str(e)}


# ==================== 查询接口 ====================


@app.get("/records")
async def get_records(
    verdict: Optional[str] = Query(default=None, description="按 verdict 筛选"),
    ad_category: Optional[str] = Query(default=None, description="按品类筛选"),
    date_from: Optional[str] = Query(default=None, description="起始日期 YYYY-MM-DD"),
    date_to: Optional[str] = Query(default=None, description="截止日期 YYYY-MM-DD"),
    limit: int = Query(default=50, ge=1, le=200, description="返回条数"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
) -> dict:
    """
    获取审核历史记录。

    支持按 verdict、ad_category、日期范围筛选。

    Returns:
        记录列表和总数
    """
    try:
        async with get_db() as session:
            conditions = []
            if verdict:
                conditions.append(ReviewRecord.verdict == verdict)
            if ad_category:
                conditions.append(ReviewRecord.ad_category == ad_category)
            if date_from:
                conditions.append(
                    ReviewRecord.created_at >= datetime.fromisoformat(date_from)
                )
            if date_to:
                # 截止日期包含当天（加一天）
                to_date = date.fromisoformat(date_to)
                conditions.append(
                    ReviewRecord.created_at < datetime(
                        to_date.year, to_date.month, to_date.day
                    ).replace(hour=23, minute=59, second=59)
                )

            where_clause = and_(*conditions) if conditions else True

            # 查总数
            count_stmt = select(sa_func.count()).select_from(
                ReviewRecord
            ).where(where_clause)
            total = (await session.execute(count_stmt)).scalar() or 0

            # 查列表
            stmt = (
                select(ReviewRecord)
                .where(where_clause)
                .order_by(ReviewRecord.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            records = (await session.execute(stmt)).scalars().all()

            items = []
            for r in records:
                items.append({
                    "request_id": r.request_id,
                    "advertiser_id": r.advertiser_id,
                    "ad_category": r.ad_category,
                    "creative_type": r.creative_type,
                    "verdict": r.verdict,
                    "confidence": r.confidence,
                    "violation_count": len(
                        json.loads(r.violations_json) if r.violations_json else []
                    ),
                    "reason": r.reason,
                    "is_fallback": r.is_fallback,
                    "processing_ms": r.processing_ms,
                    "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                    "human_verdict": r.human_verdict,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })

            return {"total": total, "items": items}
    except Exception as e:
        logger.error("Failed to query records", error=str(e))
        return {"total": 0, "items": [], "error": str(e)}


@app.get("/stats")
async def get_stats() -> dict:
    """
    获取审核统计数据。

    Returns:
        各维度统计：总量、verdict 分布、平均置信度、
        平均处理时长、队列待处理数、人工一致率
    """
    try:
        async with get_db() as session:
            # 总量
            total = (await session.execute(
                select(sa_func.count()).select_from(ReviewRecord)
            )).scalar() or 0

            if total == 0:
                return {
                    "total_reviews": 0,
                    "verdict_distribution": {},
                    "avg_confidence": 0.0,
                    "avg_processing_ms": 0,
                    "fallback_count": 0,
                    "queue_pending": 0,
                    "human_agreement_rate": None,
                }

            # Verdict 分布
            verdict_stmt = select(
                ReviewRecord.verdict,
                sa_func.count(),
            ).group_by(ReviewRecord.verdict)
            verdict_rows = (await session.execute(verdict_stmt)).all()
            verdict_dist = {row[0]: row[1] for row in verdict_rows}

            # 平均置信度
            avg_conf = (await session.execute(
                select(sa_func.avg(ReviewRecord.confidence))
            )).scalar() or 0.0

            # 平均处理时长
            avg_ms = (await session.execute(
                select(sa_func.avg(ReviewRecord.processing_ms))
            )).scalar() or 0

            # 降级数
            fallback_count = (await session.execute(
                select(sa_func.count()).select_from(ReviewRecord).where(
                    ReviewRecord.is_fallback == True  # noqa: E712
                )
            )).scalar() or 0

            # 队列待处理数
            queue_pending = (await session.execute(
                select(sa_func.count()).select_from(ReviewQueue).where(
                    ReviewQueue.status == "pending"
                )
            )).scalar() or 0

            # 人工一致率：agent verdict == human verdict 的比例
            human_reviewed_stmt = select(sa_func.count()).select_from(
                ReviewRecord
            ).where(ReviewRecord.human_verdict.isnot(None))
            human_total = (await session.execute(human_reviewed_stmt)).scalar() or 0

            human_agree = None
            if human_total > 0:
                agree_count = (await session.execute(
                    select(sa_func.count()).select_from(ReviewRecord).where(
                        and_(
                            ReviewRecord.human_verdict.isnot(None),
                            ReviewRecord.verdict == ReviewRecord.human_verdict,
                        )
                    )
                )).scalar() or 0
                human_agree = round(agree_count / human_total, 4)

            # 计算各 verdict 数量（returned 和 reject 合并计为自动退回）
            auto_pass = verdict_dist.get("pass", 0)
            auto_returned = (
                verdict_dist.get("returned", 0)
                + verdict_dist.get("reject", 0)
            )
            human_review = verdict_dist.get("review", 0)
            automation_rate = round(
                (auto_pass + auto_returned) / total, 4
            ) if total > 0 else 0.0

            return {
                "total_reviews": total,
                "verdict_distribution": verdict_dist,
                "auto_pass": auto_pass,
                "auto_returned": auto_returned,
                "human_review": human_review,
                "automation_rate": automation_rate,
                "avg_confidence": round(float(avg_conf), 4),
                "avg_processing_ms": int(avg_ms),
                "fallback_count": fallback_count,
                "queue_pending": queue_pending,
                "human_reviewed": human_total,
                "human_agreement_rate": human_agree,
            }
    except Exception as e:
        logger.error("Failed to compute stats", error=str(e))
        return {"error": str(e)}


# ==================== 营销页面 & 认证 ====================

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    """营销首页。"""
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """登录注册页面。"""
    return templates.TemplateResponse("login.html", {"request": request})


class _LoginRequest(BaseModel):
    """登录请求体。"""
    username: str
    password: str


class _RegisterRequest(BaseModel):
    """注册请求体。"""
    name: str
    employee_id: str
    email: str
    password: str


@app.post("/auth/login")
async def auth_login(body: _LoginRequest) -> JSONResponse:
    """
    用户登录：查询数据库验证用户名密码，成功后设置 session cookie。

    Args:
        body: 包含 username 和 password 的 JSON

    Returns:
        成功返回 200 + set-cookie，失败返回 401
    """
    try:
        async with get_db() as session:
            stmt = select(User).where(User.username == body.username)
            user = (await session.execute(stmt)).scalar_one_or_none()

            if not user or not _verify_password(body.password, user.password_hash):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "用户名或密码错误"},
                )

            if user.status == "pending":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "账号注册审批中，请等待管理员审批"},
                )
            if user.status == "disabled":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "账号已被禁用，请联系管理员"},
                )

            # 更新最后登录时间
            user.last_login_at = datetime.utcnow()
            await session.commit()

            # 生成 session token（存储 username + role）
            token = _signer.dumps({"username": user.username, "role": user.role})
            response = JSONResponse(content={
                "status": "ok",
                "username": user.username,
                "role": user.role,
            })
            response.set_cookie(
                key=_SESSION_COOKIE,
                value=token,
                max_age=_SESSION_MAX_AGE,
                httponly=True,
                samesite="lax",
            )
            logger.info("User login", username=user.username, role=user.role)
            return response
    except Exception as e:
        logger.error("Login error", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"detail": "登录服务异常，请稍后重试"},
        )


@app.post("/auth/register")
async def auth_register(body: _RegisterRequest) -> JSONResponse:
    """
    用户注册：创建 pending 状态的用户，等待管理员审批。

    Args:
        body: 注册信息

    Returns:
        成功返回 200，用户名/邮箱冲突返回 409
    """
    try:
        async with get_db() as session:
            # 检查用户名冲突
            existing = (await session.execute(
                select(User).where(User.username == body.employee_id)
            )).scalar_one_or_none()
            if existing:
                return JSONResponse(
                    status_code=409,
                    content={"detail": "该工号已注册"},
                )

            # 检查邮箱冲突
            existing_email = (await session.execute(
                select(User).where(User.email == body.email)
            )).scalar_one_or_none()
            if existing_email:
                return JSONResponse(
                    status_code=409,
                    content={"detail": "该邮箱已注册"},
                )

            user = User(
                username=body.employee_id,
                email=body.email,
                password_hash=_hash_password(body.password),
                real_name=body.name,
                employee_id=body.employee_id,
                role="reviewer",
                status="pending",
            )
            session.add(user)
            await session.commit()

            logger.info("New user registered", username=body.employee_id, email=body.email)
            return JSONResponse(content={"status": "ok", "message": "注册申请已提交，请等待管理员审批"})
    except Exception as e:
        logger.error("Register error", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"detail": "注册服务异常，请稍后重试"},
        )


@app.post("/auth/logout")
async def auth_logout() -> RedirectResponse:
    """清除 session，跳转到登录页。"""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(_SESSION_COOKIE)
    return response


@app.get("/auth/check")
async def auth_check(request: Request) -> dict:
    """检查当前 session 是否有效。"""
    data = _get_session_data(request)
    if not data:
        return {"authenticated": False}
    return {"authenticated": True, **data}


# ==================== Webhook 接入 ====================


@app.post("/ingest/webhook")
async def ingest_webhook(request: Request) -> JSONResponse:
    """
    接收外部系统推送的素材审核请求。

    支持 HMAC-SHA256 签名验证（X-Webhook-Signature Header）。
    通过 HttpWebhookAdapter 转换格式并调用 ReviewAgent 审核。

    Returns:
        审核结果 JSON
    """
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")

    adapter = _ingest_router.get_adapter("http_webhook")
    if not adapter:
        return JSONResponse(
            status_code=503,
            content={"detail": "Webhook adapter not available"},
        )

    # 签名验证
    if not adapter.verify_signature(body, signature):
        logger.warning("Webhook signature verification failed")
        return JSONResponse(
            status_code=403,
            content={"detail": "Invalid webhook signature"},
        )

    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid JSON body"},
        )

    result = await adapter.process(raw)

    # 写入数据库
    try:
        review_request = adapter.transform(raw)
        await _save_to_db(review_request, result)
    except Exception as e:
        logger.warning("Failed to save webhook result to DB", error=str(e))

    return JSONResponse(content=result.model_dump(mode="json"))


# ==================== 管理后台页面 ====================


@app.get("/admin/submit", response_class=HTMLResponse)
async def admin_submit_page(
    request: Request, user: dict = Depends(_require_role("admin", "reviewer"))
) -> HTMLResponse:
    """素材提交页面（reviewer 及以上）。"""
    raw_keys = settings.api_keys.strip()
    api_key = raw_keys.split(",")[0].strip() if raw_keys else ""
    return templates.TemplateResponse(
        "submit.html",
        {"request": request, "api_key": api_key, "user": user},
    )


@app.get("/admin/", response_class=HTMLResponse)
async def admin_queue_page(
    request: Request, user: dict = Depends(_require_role("admin", "reviewer"))
) -> HTMLResponse:
    """待审核队列页面（reviewer 及以上）。"""
    data = await get_queue(status="pending", limit=50, offset=0)
    return templates.TemplateResponse(
        "queue.html",
        {"request": request, "total": data["total"], "items": data["items"], "user": user},
    )


@app.get("/admin/detail/{request_id}", response_class=HTMLResponse)
async def admin_detail_page(
    request: Request,
    request_id: str,
    user: dict = Depends(_require_role("admin", "reviewer")),
) -> HTMLResponse:
    """
    审核详情页面，含上下导航。

    query param `from` 决定导航来源：queue 或 history。
    """
    list_source = request.query_params.get("from", "history")
    detail = await get_queue_detail(request_id)

    # 查上一个/下一个
    prev_id, next_id, current_idx, total_count = None, None, 0, 0
    try:
        async with get_db() as session:
            if list_source == "queue":
                stmt = (
                    select(ReviewQueue.request_id)
                    .where(ReviewQueue.status == "pending")
                    .order_by(ReviewQueue.created_at.desc())
                )
            else:
                stmt = select(ReviewRecord.request_id).order_by(
                    ReviewRecord.reviewed_at.desc()
                )
            rows = (await session.execute(stmt)).scalars().all()
            total_count = len(rows)
            if request_id in rows:
                idx = rows.index(request_id)
                current_idx = idx + 1
                if idx > 0:
                    prev_id = rows[idx - 1]
                if idx < len(rows) - 1:
                    next_id = rows[idx + 1]
    except Exception as e:
        logger.warning("Failed to compute prev/next nav", error=str(e))

    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "detail": detail,
            "user": user,
            "prev_request_id": prev_id,
            "next_request_id": next_id,
            "list_source": list_source,
            "current_index": current_idx,
            "total_count": total_count,
        },
    )


@app.post("/admin/detail/{request_id}/decide")
async def admin_decide(
    request_id: str,
    verdict: str = Form(...),
    reviewer: str = Form(...),
    note: str = Form(default=""),
    user: dict = Depends(_require_role("admin", "reviewer")),
) -> RedirectResponse:
    """
    处理人工审核表单提交。

    接收表单数据，调用 decide_review API，完成后跳回队列页。

    Args:
        request_id: 请求 ID
        verdict: 人工审核结论
        reviewer: 审核员标识
        note: 审核备注
    """
    decision = HumanDecision(verdict=verdict, reviewer=reviewer, note=note)
    await decide_review(request_id, decision)
    return RedirectResponse(url="/admin/", status_code=303)


@app.get("/admin/history", response_class=HTMLResponse)
async def admin_history_page(
    request: Request,
    verdict: Optional[str] = Query(default=None),
    ad_category: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    user: dict = Depends(_require_role("admin", "reviewer", "viewer")),
) -> HTMLResponse:
    """
    审核历史页面。

    接收筛选参数，调用 /records API 获取数据后渲染模板。
    """
    data = await get_records(
        verdict=verdict,
        ad_category=ad_category,
        date_from=date_from,
        date_to=date_to,
        limit=100,
        offset=0,
    )
    filters = {
        "verdict": verdict or "",
        "ad_category": ad_category or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
    }
    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "total": data.get("total", 0),
            "items": data.get("items", []),
            "filters": filters,
            "user": user,
        },
    )


@app.get("/admin/stats", response_class=HTMLResponse)
async def admin_stats_page(
    request: Request, user: dict = Depends(_require_role("admin", "reviewer", "viewer"))
) -> HTMLResponse:
    """
    统计看板页面。

    调用 /stats API 获取数据后渲染模板。
    数据库不可用时使用空数据兜底。
    """
    stats = await get_stats()
    if "error" in stats:
        stats = {
            "total_reviews": 0,
            "verdict_distribution": {},
            "avg_confidence": 0.0,
            "avg_processing_ms": 0,
            "fallback_count": 0,
            "queue_pending": 0,
            "human_reviewed": 0,
            "human_agreement_rate": None,
        }
    return templates.TemplateResponse(
        "stats.html",
        {"request": request, "stats": stats, "user": user},
    )


# ==================== 用户管理 ====================


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request, user: dict = Depends(_require_role("admin"))
) -> HTMLResponse:
    """用户管理页面（仅 admin）。"""
    return templates.TemplateResponse(
        "users.html", {"request": request, "user": user}
    )


@app.get("/admin/api/users")
async def api_list_users(
    user: dict = Depends(_require_role("admin")),
    status: Optional[str] = Query(default=None),
) -> dict:
    """用户列表 JSON。"""
    try:
        async with get_db() as session:
            conditions = []
            if status:
                conditions.append(User.status == status)
            where = and_(*conditions) if conditions else True
            stmt = select(User).where(where).order_by(User.created_at.desc())
            users = (await session.execute(stmt)).scalars().all()
            return {"users": [
                {
                    "id": str(u.id),
                    "username": u.username,
                    "email": u.email,
                    "real_name": u.real_name,
                    "employee_id": u.employee_id,
                    "role": u.role,
                    "status": u.status,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                    "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                    "approved_by": u.approved_by,
                }
                for u in users
            ]}
    except Exception as e:
        logger.error("Failed to list users", error=str(e))
        return {"users": [], "error": str(e)}


@app.get("/admin/api/users/pending")
async def api_pending_users(user: dict = Depends(_require_role("admin"))) -> dict:
    """待审批用户列表。"""
    return await api_list_users(user=user, status="pending")


@app.post("/admin/api/users/{user_id}/approve")
async def api_approve_user(
    user_id: str, request: Request, admin: dict = Depends(_require_role("admin"))
) -> dict:
    """审批通过用户。"""
    try:
        body = await request.json()
        role = body.get("role", "reviewer")
    except Exception:
        role = "reviewer"

    try:
        async with get_db() as session:
            stmt = select(User).where(User.id == user_id)
            u = (await session.execute(stmt)).scalar_one_or_none()
            if not u:
                return {"error": "user_not_found"}
            u.status = "active"
            u.role = role
            u.approved_by = admin["username"]
            u.approved_at = datetime.utcnow()
            await session.commit()
            logger.info("User approved", user=u.username, role=role, by=admin["username"])
            return {"status": "ok", "username": u.username, "role": role}
    except Exception as e:
        logger.error("Failed to approve user", error=str(e))
        return {"error": str(e)}


@app.post("/admin/api/users/{user_id}/reject")
async def api_reject_user(
    user_id: str, admin: dict = Depends(_require_role("admin"))
) -> dict:
    """拒绝注册。"""
    try:
        async with get_db() as session:
            stmt = select(User).where(User.id == user_id)
            u = (await session.execute(stmt)).scalar_one_or_none()
            if not u:
                return {"error": "user_not_found"}
            await session.delete(u)
            await session.commit()
            logger.info("User registration rejected", user=u.username, by=admin["username"])
            return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/admin/api/users/{user_id}/role")
async def api_change_role(
    user_id: str, request: Request, admin: dict = Depends(_require_role("admin"))
) -> dict:
    """修改用户角色。"""
    try:
        body = await request.json()
        new_role = body.get("role")
        if new_role not in ("admin", "reviewer", "viewer"):
            return {"error": "invalid_role"}
        async with get_db() as session:
            u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if not u:
                return {"error": "user_not_found"}
            u.role = new_role
            await session.commit()
            return {"status": "ok", "username": u.username, "role": new_role}
    except Exception as e:
        return {"error": str(e)}


@app.post("/admin/api/users/{user_id}/disable")
async def api_disable_user(
    user_id: str, admin: dict = Depends(_require_role("admin"))
) -> dict:
    """禁用用户。"""
    try:
        async with get_db() as session:
            u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if not u:
                return {"error": "user_not_found"}
            u.status = "disabled"
            await session.commit()
            return {"status": "ok", "username": u.username}
    except Exception as e:
        return {"error": str(e)}


@app.post("/admin/api/users/{user_id}/enable")
async def api_enable_user(
    user_id: str, admin: dict = Depends(_require_role("admin"))
) -> dict:
    """启用用户。"""
    try:
        async with get_db() as session:
            u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if not u:
                return {"error": "user_not_found"}
            u.status = "active"
            await session.commit()
            return {"status": "ok", "username": u.username}
    except Exception as e:
        return {"error": str(e)}


# ==================== 报告导出 ====================


@app.get("/admin/export/excel")
async def export_excel(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    verdict: Optional[str] = Query(default=None),
    user: dict = Depends(_require_role("admin", "reviewer")),
):
    """
    导出 Excel 审核报告。

    支持按日期和 verdict 筛选。返回 .xlsx 文件下载。
    """
    from fastapi.responses import Response
    from src.reporter import generate_excel_report

    data = await get_records(
        verdict=verdict, ad_category=None, date_from=start_date, date_to=end_date,
        limit=200, offset=0,
    )
    stats_data = await get_stats()

    excel_bytes = generate_excel_report(data.get("items", []), stats_data)
    filename = f"ad_review_report_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx"

    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/export/pdf")
async def export_pdf(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    verdict: Optional[str] = Query(default=None),
    user: dict = Depends(_require_role("admin", "reviewer")),
):
    """
    导出 PDF 审核报告。

    支持按日期和 verdict 筛选。返回 .pdf 文件下载。
    """
    from fastapi.responses import Response
    from src.reporter import generate_pdf_report

    data = await get_records(
        verdict=verdict, ad_category=None, date_from=start_date, date_to=end_date,
        limit=200, offset=0,
    )
    stats_data = await get_stats()

    pdf_bytes = generate_pdf_report(data.get("items", []), stats_data)
    filename = f"ad_review_report_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ==================== 规则管理 ====================

_VALID_RULE_TYPES = {"forbidden_words", "category_rules", "vivo_platform", "qualification_map", "consistency_rules"}


@app.get("/admin/overview", response_class=HTMLResponse)
async def admin_overview_page(
    request: Request, user: dict = Depends(_require_role("admin"))
) -> HTMLResponse:
    """系统全貌页面（仅 admin）。"""
    return templates.TemplateResponse(
        "overview.html", {"request": request, "user": user}
    )


@app.get("/admin/rules", response_class=HTMLResponse)
async def admin_rules_page(
    request: Request, user: dict = Depends(_require_role("admin"))
) -> HTMLResponse:
    """规则管理页面（仅 admin）。"""
    return templates.TemplateResponse(
        "rules.html", {"request": request, "user": user}
    )


@app.get("/admin/api/rules/{rule_type}")
async def api_get_rules(
    rule_type: str, user: dict = Depends(_require_role("admin"))
) -> dict:
    """获取当前生效规则。"""
    if rule_type not in _VALID_RULE_TYPES:
        return {"error": "invalid_rule_type"}
    try:
        async with get_db() as session:
            stmt = (
                select(RuleVersion)
                .where(RuleVersion.rule_type == rule_type, RuleVersion.is_active == True)
                .order_by(RuleVersion.created_at.desc())
                .limit(1)
            )
            rule = (await session.execute(stmt)).scalar_one_or_none()
            if rule:
                return {
                    "version": rule.version,
                    "content": json.loads(rule.content_json),
                    "updated_by": rule.created_by,
                    "updated_at": rule.created_at.isoformat() if rule.created_at else None,
                    "source": "database",
                }
        # 降级读文件
        from src.rules_loader import load_rules_sync
        content = load_rules_sync(rule_type)
        return {
            "version": content.get("version", "file"),
            "content": content,
            "updated_by": content.get("updated_by", "file"),
            "updated_at": content.get("updated_at"),
            "source": "file",
        }
    except Exception as e:
        return {"error": str(e)}


@app.post("/admin/api/rules/{rule_type}")
async def api_save_rules(
    rule_type: str, request: Request,
    user: dict = Depends(_require_role("admin"))
) -> dict:
    """
    保存新版本规则。自动递增版本号，旧版本 is_active 设为 False。
    """
    if rule_type not in _VALID_RULE_TYPES:
        return {"error": "invalid_rule_type"}
    try:
        body = await request.json()
        content = body.get("content")
        change_summary = body.get("change_summary", "")

        if not content:
            return {"error": "content is required"}

        # 验证 JSON 可序列化
        content_str = json.dumps(content, ensure_ascii=False)

        async with get_db() as session:
            # 获取当前版本号
            stmt = (
                select(RuleVersion)
                .where(RuleVersion.rule_type == rule_type, RuleVersion.is_active == True)
                .order_by(RuleVersion.created_at.desc())
                .limit(1)
            )
            current = (await session.execute(stmt)).scalar_one_or_none()
            if current:
                # 递增版本号
                parts = current.version.split(".")
                parts[-1] = str(int(parts[-1]) + 1)
                new_version = ".".join(parts)
                # 旧版本失效
                current.is_active = False
            else:
                old_content = content
                new_version = old_content.get("version", "1.0.0")
                # 首次保存时从内容中取版本号，递增一次
                parts = new_version.split(".")
                parts[-1] = str(int(parts[-1]) + 1)
                new_version = ".".join(parts)

            # 更新内容中的版本号
            content["version"] = new_version
            content["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d")
            content["updated_by"] = user["username"]
            content_str = json.dumps(content, ensure_ascii=False)

            new_rule = RuleVersion(
                rule_type=rule_type,
                version=new_version,
                content_json=content_str,
                change_summary=change_summary,
                created_by=user["username"],
                is_active=True,
            )
            session.add(new_rule)
            await session.commit()

            # 清除缓存让 Tool 下次加载最新版本
            from src.rules_loader import invalidate_cache
            invalidate_cache(rule_type)

            logger.info(
                "Rules updated",
                rule_type=rule_type,
                version=new_version,
                by=user["username"],
                summary=change_summary,
            )

            return {"status": "ok", "version": new_version, "rule_type": rule_type}
    except Exception as e:
        logger.error("Failed to save rules", error=str(e))
        return {"error": str(e)}


@app.get("/admin/api/rules/{rule_type}/history")
async def api_rules_history(
    rule_type: str, user: dict = Depends(_require_role("admin"))
) -> dict:
    """获取规则历史版本列表。"""
    if rule_type not in _VALID_RULE_TYPES:
        return {"error": "invalid_rule_type"}
    try:
        async with get_db() as session:
            stmt = (
                select(RuleVersion)
                .where(RuleVersion.rule_type == rule_type)
                .order_by(RuleVersion.created_at.desc())
                .limit(20)
            )
            versions = (await session.execute(stmt)).scalars().all()
            return {"versions": [
                {
                    "id": v.id,
                    "version": v.version,
                    "change_summary": v.change_summary,
                    "created_by": v.created_by,
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                    "is_active": v.is_active,
                }
                for v in versions
            ]}
    except Exception as e:
        return {"error": str(e)}


@app.post("/admin/api/rules/{rule_type}/rollback/{version_id}")
async def api_rollback_rules(
    rule_type: str, version_id: str,
    user: dict = Depends(_require_role("admin"))
) -> dict:
    """回滚到指定版本。"""
    try:
        async with get_db() as session:
            # 将当前版本失效
            current_stmt = (
                select(RuleVersion)
                .where(RuleVersion.rule_type == rule_type, RuleVersion.is_active == True)
            )
            for r in (await session.execute(current_stmt)).scalars():
                r.is_active = False

            # 激活目标版本
            target = (await session.execute(
                select(RuleVersion).where(RuleVersion.id == version_id)
            )).scalar_one_or_none()
            if not target:
                return {"error": "version_not_found"}
            target.is_active = True
            await session.commit()

            from src.rules_loader import invalidate_cache
            invalidate_cache(rule_type)

            logger.info(
                "Rules rolled back",
                rule_type=rule_type,
                version=target.version,
                by=user["username"],
            )
            return {"status": "ok", "version": target.version}
    except Exception as e:
        return {"error": str(e)}


# ==================== 健康检查 ====================


@app.get("/health")
async def health_check() -> dict:
    """
    健康检查接口。

    检测数据库连接状态，返回服务整体健康状况。

    Returns:
        status=ok 或 degraded，database 连接状态
    """
    db_status = "disconnected"
    try:
        async with get_db() as session:
            await session.execute(text("SELECT 1"))
            db_status = "connected"
    except Exception:
        pass

    status = "ok" if db_status == "connected" else "degraded"
    return {
        "status": status,
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ==================== 全局异常处理 ====================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    全局异常处理：未捕获异常返回 500，推人工队列。

    确保任何内部错误不会暴露给调用方，
    同时返回结构化的降级结果。
    """
    logger.error(
        "Unhandled exception in API",
        error=str(exc),
        path=request.url.path,
        exc_info=True,
    )

    # 尝试从请求体中提取 request_id
    request_id = "unknown"
    try:
        body = await request.json()
        request_id = body.get("request_id", "unknown")
    except Exception:
        pass

    fallback_result = ReviewResult.human_review_required(
        request_id=request_id,
        reason="系统内部错误，素材已转人工审核队列",
        fallback_reason=f"Unhandled exception: {type(exc).__name__}",
    )

    return JSONResponse(
        status_code=500,
        content=fallback_result.model_dump(mode="json"),
    )

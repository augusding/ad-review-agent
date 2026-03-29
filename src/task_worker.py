"""
异步任务处理器。

轮询数据库中 status=pending 的 AsyncTask，
执行视频审核等耗时操作，完成后更新结果。
"""
import asyncio
import json
from datetime import datetime

from loguru import logger
from sqlalchemy import select

from src.agents.review_agent import ReviewAgent
from src.database import AsyncTask, get_db
from src.schemas.request import ReviewRequest
from src.schemas.result import ReviewResult
from src.tools.aliyun_green import AliyunGreenChecker
from src.tools.audio_checker import AudioChecker


class TaskWorker:
    """
    异步任务处理器。

    在后台持续轮询 pending 任务，逐个执行审核，
    完成后更新数据库记录。
    """

    def __init__(self, agent: ReviewAgent, poll_interval: float = 2.0) -> None:
        """
        初始化任务处理器。

        Args:
            agent: ReviewAgent 实例
            poll_interval: 轮询间隔（秒）
        """
        self._agent = agent
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._aliyun = AliyunGreenChecker()
        self._audio_checker = AudioChecker()

    async def start(self) -> None:
        """启动后台轮询。"""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("TaskWorker started", poll_interval=self._poll_interval)

    async def stop(self) -> None:
        """停止后台轮询。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TaskWorker stopped")

    async def _loop(self) -> None:
        """
        主轮询循环。

        每次轮询取一个 pending 任务执行，完成后继续轮询。
        """
        while self._running:
            try:
                processed = await self._process_one()
                if not processed:
                    await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("TaskWorker loop error", error=str(e))
                await asyncio.sleep(self._poll_interval)

    async def _process_one(self) -> bool:
        """
        处理一个 pending 任务。

        Returns:
            True 如果处理了一个任务，False 如果没有待处理任务
        """
        try:
            async with get_db() as session:
                stmt = (
                    select(AsyncTask)
                    .where(AsyncTask.status == "pending")
                    .order_by(AsyncTask.created_at.asc())
                    .limit(1)
                )
                task = (await session.execute(stmt)).scalar_one_or_none()
                if not task:
                    return False

                # 标记为 processing
                task.status = "processing"
                task.started_at = datetime.utcnow()
                await session.commit()

                task_id = task.id
                request_id = task.request_id

            logger.info(
                "Processing async task",
                task_id=task_id,
                request_id=request_id,
            )

            # 从 request_json 重建 ReviewRequest 并执行完整审核
            async with get_db() as session:
                task = (await session.execute(
                    select(AsyncTask).where(AsyncTask.id == task_id)
                )).scalar_one()

                try:
                    request_data = json.loads(task.request_json)
                    review_request = ReviewRequest(**request_data)

                    # 执行完整审核（含视频关键帧+阿里云双重验证）
                    result = await self._agent.review(review_request)

                    # 并行执行音频检测（如果有视频）
                    audio_transcript = None
                    if review_request.content.video_url:
                        audio_result = await self._run_audio_check(
                            review_request, result
                        )
                        if audio_result and not audio_result.skipped:
                            audio_transcript = audio_result.transcript
                            # 合并音频违规项到结果
                            # 只有音频发现违规时才影响置信度
                            # 音频无违规不拉低（与 review_agent 逻辑一致）
                            if audio_result.violations:
                                result.violations.extend(audio_result.violations)
                                if audio_result.confidence < result.confidence:
                                    result.confidence = min(
                                        result.confidence, audio_result.confidence
                                    )

                    task.status = "completed"
                    task.completed_at = datetime.utcnow()
                    task.result_json = result.model_dump_json()

                    # 保存音频转写到 ReviewRecord
                    if audio_transcript:
                        await self._save_audio_transcript(
                            request_id, audio_transcript
                        )

                    logger.info(
                        "Async task completed",
                        task_id=task_id,
                        request_id=request_id,
                        verdict=result.verdict.value,
                        confidence=result.confidence,
                        has_audio=bool(audio_transcript),
                    )

                except Exception as e:
                    task.status = "failed"
                    task.completed_at = datetime.utcnow()
                    task.error_message = f"{type(e).__name__}: {str(e)[:400]}"
                    task.retry_count += 1

                    logger.error(
                        "Async task failed",
                        task_id=task_id,
                        request_id=request_id,
                        error=str(e),
                    )

                await session.commit()
            return True

        except Exception as e:
            logger.error("TaskWorker process error", error=str(e))
            return False

    async def _run_audio_check(
        self, request: ReviewRequest, result: ReviewResult
    ):
        """
        对视频执行音频检测。

        需要先下载视频到临时文件，然后提取音频。

        Args:
            request: 审核请求
            result: 当前审核结果（用于获取视频路径）

        Returns:
            AudioCheckOutput 或 None
        """
        video_url = request.content.video_url
        if not video_url:
            return None

        try:
            import tempfile
            import httpx
            from pathlib import Path

            # 下载视频到临时文件
            tmp_path = str(
                Path(tempfile.gettempdir()) / f"{request.request_id}_audio_video.mp4"
            )

            if video_url.startswith("http"):
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(video_url)
                    resp.raise_for_status()
                    Path(tmp_path).write_bytes(resp.content)
            else:
                tmp_path = video_url  # 本地文件

            audio_result = await self._audio_checker.check_video_audio(
                video_path=tmp_path,
                ad_category=request.ad_category.value,
                request_id=request.request_id,
            )

            # 清理临时视频文件
            if video_url.startswith("http"):
                try:
                    import os
                    os.remove(tmp_path)
                except Exception:
                    pass

            return audio_result

        except Exception as e:
            logger.warning(
                "Audio check failed in async task",
                request_id=request.request_id,
                error=str(e),
            )
            return None

    async def _save_audio_transcript(
        self, request_id: str, transcript: str
    ) -> None:
        """
        将音频转写内容保存到 ReviewRecord。

        Args:
            request_id: 请求 ID
            transcript: 转写文字
        """
        try:
            from src.database import ReviewRecord
            async with get_db() as session:
                stmt = select(ReviewRecord).where(
                    ReviewRecord.request_id == request_id
                )
                record = (await session.execute(stmt)).scalar_one_or_none()
                if record:
                    record.audio_transcript = transcript
                    await session.commit()
                    logger.info(
                        "Audio transcript saved",
                        request_id=request_id,
                        length=len(transcript),
                    )
        except Exception as e:
            logger.warning(
                "Failed to save audio transcript",
                request_id=request_id,
                error=str(e),
            )

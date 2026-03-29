"""
视频音频提取 + 语音转写 + 文案违规检测。

流程：视频文件 → moviepy 提取音频 → DashScope 语音识别转写 →
复用 TextViolationChecker 检测违规。
"""
import asyncio
import os
import tempfile
from pathlib import Path

from loguru import logger

from src.config import settings
from src.schemas.tool_io import AudioCheckOutput, TextCheckerInput
from src.schemas.violation import ViolationItem


class AudioChecker:
    """
    视频音频违规检测器。

    从视频中提取音频，调用阿里云 DashScope 语音识别转写为文字，
    然后复用 TextViolationChecker 进行违规检测。

    配置 audio_check_enabled=false 时所有方法返回跳过结果。
    """

    def __init__(self) -> None:
        """初始化。"""
        self._enabled = settings.audio_check_enabled
        self._max_duration = settings.audio_max_duration_seconds
        self._text_checker = None  # 懒加载，避免循环导入

    async def check_video_audio(
        self,
        video_path: str,
        ad_category: str,
        request_id: str,
    ) -> AudioCheckOutput:
        """
        对视频文件做音频违规检测。

        Args:
            video_path: 本地视频文件路径
            ad_category: 广告品类
            request_id: 请求 ID

        Returns:
            AudioCheckOutput，包含转写文字和违规项
        """
        if not self._enabled:
            return AudioCheckOutput(skipped=True, skip_reason="音频检测已禁用")

        logger.info(
            "Starting audio check",
            request_id=request_id,
            video_path=video_path[:60],
        )

        # Step 1: 提取音频
        audio_path = await self._extract_audio(video_path, request_id)
        if not audio_path:
            return AudioCheckOutput(
                skipped=True,
                skip_reason="音频提取失败或视频无音轨",
            )

        try:
            # Step 2: 语音转写
            transcript = await self._transcribe(audio_path, request_id)
            if not transcript:
                return AudioCheckOutput(
                    skipped=True,
                    skip_reason="语音转写失败或无语音内容",
                )

            logger.info(
                "Audio transcribed",
                request_id=request_id,
                transcript_length=len(transcript),
                preview=transcript[:80],
            )

            # Step 3: 复用 TextViolationChecker 检测
            if self._text_checker is None:
                from src.tools.text_checker import TextViolationChecker
                self._text_checker = TextViolationChecker()

            text_input = TextCheckerInput(
                text_content=transcript,
                ad_category=ad_category,
                request_id=f"{request_id}-audio",
            )
            text_result = await self._text_checker.run(text_input)

            # 标注违规项来源为视频语音
            for v in text_result.violations:
                v.evidence = f"[视频语音] {v.evidence}"
                v.description = f"[语音内容] {v.description}"

            return AudioCheckOutput(
                transcript=transcript,
                violations=text_result.violations,
                confidence=text_result.confidence,
                skipped=False,
            )

        finally:
            # 清理临时音频文件
            try:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception:
                pass

    async def _extract_audio(
        self, video_path: str, request_id: str
    ) -> str | None:
        """
        用 moviepy 从视频中提取音频，保存为临时 wav 文件。

        Args:
            video_path: 视频文件路径
            request_id: 请求 ID

        Returns:
            音频文件路径，失败返回 None
        """
        audio_path = str(
            Path(tempfile.gettempdir()) / f"{request_id}_audio.wav"
        )

        def _extract():
            try:
                from moviepy import VideoFileClip

                clip = VideoFileClip(video_path)

                # 检查时长
                if clip.duration > self._max_duration:
                    logger.warning(
                        "Video too long for audio check, skipping",
                        request_id=request_id,
                        duration=clip.duration,
                        max_duration=self._max_duration,
                    )
                    clip.close()
                    return None

                # 检查是否有音轨
                if clip.audio is None:
                    logger.info(
                        "Video has no audio track",
                        request_id=request_id,
                    )
                    clip.close()
                    return None

                clip.audio.write_audiofile(
                    audio_path,
                    fps=16000,
                    nbytes=2,
                    codec="pcm_s16le",
                    logger=None,
                )
                clip.close()
                return audio_path

            except Exception as e:
                logger.warning(
                    "Audio extraction failed",
                    request_id=request_id,
                    error=str(e),
                )
                return None

        return await asyncio.get_event_loop().run_in_executor(None, _extract)

    async def _transcribe(
        self, audio_path: str, request_id: str
    ) -> str | None:
        """
        调用阿里云 DashScope 语音识别转写音频。

        使用 paraformer-v2 模型，支持中英文混合识别。

        Args:
            audio_path: 音频文件路径
            request_id: 请求 ID

        Returns:
            转写文字，失败返回 None
        """

        def _do_transcribe():
            try:
                import dashscope
                from dashscope.audio.asr import Recognition

                dashscope.api_key = settings.dashscope_api_key

                recognition = Recognition(
                    model="paraformer-realtime-v2",
                    format="wav",
                    sample_rate=16000,
                    callback=None,
                )

                result = recognition.call(audio_path)

                if hasattr(result, "output") and result.output:
                    sentences = result.output.get("sentence", [])
                    if sentences:
                        transcript = "".join(
                            s.get("text", "") for s in sentences
                        )
                        return transcript.strip() or None

                # 备用解析
                if hasattr(result, "get_sentence"):
                    sentences = result.get_sentence()
                    if sentences:
                        return " ".join(
                            s.get("text", "") for s in sentences
                        ).strip() or None

                logger.info(
                    "No speech content detected",
                    request_id=request_id,
                )
                return None

            except Exception as e:
                logger.warning(
                    "DashScope ASR transcribe failed",
                    request_id=request_id,
                    error=str(e),
                )
                return None

        return await asyncio.get_event_loop().run_in_executor(
            None, _do_transcribe
        )

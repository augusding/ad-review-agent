"""
文件上传处理模块。

提供上传文件的保存、类型验证和清理功能。
文件保存到 uploads/ 目录，使用 UUID 命名避免冲突。
"""
import uuid
from pathlib import Path

from fastapi import UploadFile
from loguru import logger

from src.config import settings

# 支持的文件类型映射：扩展名 → 类别
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}

# MIME 类型前缀映射
_IMAGE_CONTENT_TYPES = {"image/"}
_VIDEO_CONTENT_TYPES = {"video/"}


def validate_file_type(filename: str, content_type: str) -> str:
    """
    验证文件类型，返回 "image" 或 "video"。

    优先通过扩展名判断，扩展名不明确时回退到 content_type。

    Args:
        filename: 原始文件名
        content_type: HTTP content-type 头

    Returns:
        "image" 或 "video"

    Raises:
        ValueError: 不支持的文件类型
    """
    ext = Path(filename).suffix.lower() if filename else ""

    if ext in _IMAGE_EXTENSIONS:
        return "image"
    if ext in _VIDEO_EXTENSIONS:
        return "video"

    # 回退到 content_type
    ct = (content_type or "").lower()
    if any(ct.startswith(prefix) for prefix in _IMAGE_CONTENT_TYPES):
        return "image"
    if any(ct.startswith(prefix) for prefix in _VIDEO_CONTENT_TYPES):
        return "video"

    raise ValueError(
        f"Unsupported file type: filename={filename}, "
        f"content_type={content_type}. "
        f"Supported: images ({', '.join(sorted(_IMAGE_EXTENSIONS))}), "
        f"videos ({', '.join(sorted(_VIDEO_EXTENSIONS))})"
    )


async def save_upload_file(file: UploadFile) -> str:
    """
    保存上传文件到 uploads/ 目录。

    文件名使用 UUID 避免冲突，保留原始扩展名。

    Args:
        file: FastAPI UploadFile 对象

    Returns:
        保存后的本地文件绝对路径

    Raises:
        ValueError: 文件类型不支持
        IOError: 文件写入失败
    """
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower() if file.filename else ""
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest = upload_dir / unique_name

    content = await file.read()

    dest.write_bytes(content)

    abs_path = str(dest.resolve())
    logger.debug(
        "Upload file saved",
        original_name=file.filename,
        saved_path=abs_path,
        size_bytes=len(content),
    )

    return abs_path


def cleanup_upload(file_path: str) -> None:
    """
    删除临时上传文件。

    审核完成后调用，静默处理删除失败。

    Args:
        file_path: 文件绝对路径
    """
    try:
        p = Path(file_path)
        if p.exists():
            p.unlink()
            logger.debug("Upload file cleaned up", path=file_path)
    except OSError as e:
        logger.warning(
            "Failed to cleanup upload file",
            path=file_path,
            error=str(e),
        )

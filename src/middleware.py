"""
API 认证和限流中间件。

- APIKeyMiddleware: 对审核接口进行 API Key 认证
- RateLimitMiddleware: 基于 API Key 的请求频率限制
"""
import time

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings

# 需要认证的路径前缀
_PROTECTED_PREFIXES = ("/review",)
# 从认证中排除的路径前缀
_EXEMPT_PREFIXES: tuple[str, ...] = ()


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    API Key 认证中间件。

    拦截 /review 和 /review/upload 请求，
    从 X-API-Key Header 读取 Key 并与配置比对。
    其他路由（/health、/admin/、/queue 等）不需要认证。
    API_KEYS 为空时不启用认证。
    """

    def __init__(self, app) -> None:
        """
        初始化中间件，解析合法 API Key 集合。

        Args:
            app: ASGI 应用
        """
        super().__init__(app)
        raw = settings.api_keys.strip()
        self._valid_keys: set[str] = (
            {k.strip() for k in raw.split(",") if k.strip()}
            if raw
            else set()
        )
        if self._valid_keys:
            logger.info(
                "API key auth enabled",
                key_count=len(self._valid_keys),
            )
        else:
            logger.warning("API key auth disabled: API_KEYS is empty")

    async def dispatch(self, request: Request, call_next):
        """
        拦截请求，检查 API Key。

        Args:
            request: HTTP 请求
            call_next: 下一个中间件或路由处理器

        Returns:
            HTTP 响应，未认证返回 401
        """
        # 不启用认证时放行所有请求
        if not self._valid_keys:
            return await call_next(request)

        # 仅拦截受保护路径
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)
        if not any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "")
        if api_key not in self._valid_keys:
            logger.warning(
                "API key auth failed",
                path=path,
                provided_key=api_key[:8] + "..." if api_key else "(empty)",
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    基于 API Key 的请求频率限制中间件。

    使用滑动窗口算法，在内存中记录每个 Key 的请求时间戳，
    每分钟超过 rate_limit_per_minute 次返回 429。
    仅对受保护路径生效。
    """

    def __init__(self, app) -> None:
        """
        初始化限流中间件。

        Args:
            app: ASGI 应用
        """
        super().__init__(app)
        self._limit = settings.rate_limit_per_minute
        # key -> list of request timestamps
        self._requests: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        """
        检查请求频率，超限返回 429。

        Args:
            request: HTTP 请求
            call_next: 下一个中间件或路由处理器

        Returns:
            HTTP 响应，超限返回 429
        """
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)
        if not any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "unknown")
        now = time.monotonic()
        window_start = now - 60.0

        # 获取并清理过期记录
        timestamps = self._requests.get(api_key, [])
        timestamps = [t for t in timestamps if t > window_start]

        if len(timestamps) >= self._limit:
            logger.warning(
                "Rate limit exceeded",
                api_key=api_key[:8] + "...",
                request_count=len(timestamps),
                limit=self._limit,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded, retry after 60s"},
            )

        timestamps.append(now)
        self._requests[api_key] = timestamps

        return await call_next(request)

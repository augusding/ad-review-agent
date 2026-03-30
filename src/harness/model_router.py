"""
ModelRouter：YAML 驱动的 LLM 调用统一路由。

从 harness/model_router.yaml 读取 provider 和 route 配置，
为所有 Tool 提供统一的 LLM 调用接口，替代各 Tool 中直接构造 httpx 客户端的方式。

用法::

    from src.harness.model_router import get_router

    router = get_router()
    result = await router.call("text_analysis", messages=[
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."},
    ])
    print(result["content"])       # LLM 响应文本
    print(result["usage"])         # {"input_tokens": ..., "output_tokens": ...}
    print(result["model"])         # 实际使用的模型名
"""
import os
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from loguru import logger


_ROUTER_YAML_PATH = Path(__file__).resolve().parent.parent.parent / "harness" / "model_router.yaml"


class ModelRouter:
    """
    YAML 驱动的 LLM 调用路由器。

    根据 task_type 查找 route 配置，构造 httpx 请求调用对应 provider 的
    OpenAI 兼容 API，支持超时、重试和 fallback。
    """

    def __init__(self) -> None:
        """
        从 harness/model_router.yaml 加载路由配置。

        Raises:
            FileNotFoundError: YAML 文件不存在
            ValueError: 缺少 providers 或 routes 配置
        """
        if not _ROUTER_YAML_PATH.exists():
            raise FileNotFoundError(
                f"model_router.yaml not found: {_ROUTER_YAML_PATH}"
            )

        with open(_ROUTER_YAML_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if "providers" not in config:
            raise ValueError("model_router.yaml missing 'providers' section")
        if "routes" not in config:
            raise ValueError("model_router.yaml missing 'routes' section")

        self._providers: dict[str, dict[str, Any]] = config["providers"]
        self._routes: dict[str, dict[str, Any]] = config["routes"]
        self._cost_config: dict[str, dict[str, Any]] = config.get("cost_per_1k_tokens", {})

        logger.info(
            "ModelRouter initialized",
            version=config.get("version"),
            providers=list(self._providers.keys()),
            routes=list(self._routes.keys()),
        )

    async def call(
        self,
        task_type: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        根据 task_type 路由 LLM 调用。

        Args:
            task_type: 任务类型，对应 model_router.yaml 中的 route key
                       （如 "text_analysis", "image_analysis"）
            messages: OpenAI 格式的消息列表
            **kwargs: 覆盖 route 默认参数（temperature, response_format 等）

        Returns:
            标准化响应 dict::

                {
                    "content": str,          # LLM 响应文本
                    "usage": {
                        "input_tokens": int,
                        "output_tokens": int,
                    },
                    "model": str,            # 实际使用的模型名
                    "task_type": str,        # 本次调用的任务类型
                    "latency_ms": float,     # 调用耗时（毫秒）
                }

        Raises:
            ValueError: task_type 未在 routes 中定义
            httpx.TimeoutException: 所有重试均超时
            httpx.HTTPStatusError: HTTP 错误且重试耗尽
        """
        if task_type not in self._routes:
            raise ValueError(
                f"Unknown task_type '{task_type}', "
                f"available: {list(self._routes.keys())}"
            )

        route = self._routes[task_type]
        provider_name = route["provider"]

        try:
            return await self._call_provider(
                task_type=task_type,
                provider_name=provider_name,
                route=route,
                messages=messages,
                **kwargs,
            )
        except Exception as primary_err:
            # 尝试 fallback route（如果配置了）
            fallback_route_name = route.get("fallback_route")
            if fallback_route_name and fallback_route_name in self._routes:
                logger.warning(
                    "Primary route failed, trying fallback",
                    task_type=task_type,
                    provider=provider_name,
                    fallback_route=fallback_route_name,
                    error=str(primary_err),
                )
                fallback_route = self._routes[fallback_route_name]
                return await self._call_provider(
                    task_type=task_type,
                    provider_name=fallback_route["provider"],
                    route=fallback_route,
                    messages=messages,
                    **kwargs,
                )
            raise

    async def _call_provider(
        self,
        task_type: str,
        provider_name: str,
        route: dict[str, Any],
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        向指定 provider 发送 OpenAI 兼容 API 请求，带超时和重试。

        Args:
            task_type: 任务类型（用于日志）
            provider_name: provider 名称（对应 providers 配置）
            route: route 配置 dict
            messages: 消息列表
            **kwargs: 覆盖参数

        Returns:
            标准化响应 dict

        Raises:
            ValueError: provider 未定义或 API 格式不支持
            httpx.TimeoutException: 超时且重试耗尽
            httpx.HTTPStatusError: HTTP 错误且重试耗尽
        """
        if provider_name not in self._providers:
            raise ValueError(f"Unknown provider '{provider_name}'")

        provider = self._providers[provider_name]
        api_format = provider.get("api_format", "openai_compatible")
        if api_format != "openai_compatible":
            raise ValueError(
                f"Provider '{provider_name}' uses unsupported api_format '{api_format}'. "
                f"Only 'openai_compatible' is supported by ModelRouter.call()."
            )

        # 解析 provider 配置
        base_url = os.environ.get(
            provider["api_key_env"].replace("API_KEY", "BASE_URL"),
            provider.get("base_url_default", ""),
        )
        if "base_url_env" in provider:
            base_url = os.environ.get(
                provider["base_url_env"],
                provider.get("base_url_default", ""),
            )
        api_key = os.environ.get(provider["api_key_env"], "")

        endpoint = provider.get("endpoint_path", "/chat/completions")
        url = f"{base_url.rstrip('/')}{endpoint}"

        # 解析 route 配置
        model = kwargs.pop("model", None) or route.get("model") or provider.get("default_model")
        timeout = float(kwargs.pop("timeout", route.get("timeout", 30.0)))
        max_retries = int(kwargs.pop("max_retries", route.get("max_retries", 3)))
        retry_delay = float(kwargs.pop("retry_delay", route.get("retry_delay", 1.0)))
        temperature = kwargs.pop("temperature", route.get("temperature", 0.1))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        # 合并调用方传入的额外参数（如 response_format）
        for k, v in kwargs.items():
            payload[k] = v

        start_ms = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.debug(
                    "ModelRouter calling LLM",
                    task_type=task_type,
                    provider=provider_name,
                    model=model,
                    attempt=attempt,
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                usage_raw = data.get("usage", {})

                input_tokens = usage_raw.get("prompt_tokens", 0)
                output_tokens = usage_raw.get("completion_tokens", 0)
                latency_ms = (time.monotonic() - start_ms) * 1000

                logger.info(
                    "ModelRouter call completed",
                    task_type=task_type,
                    provider=provider_name,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=round(latency_ms, 1),
                    attempt=attempt,
                )

                return {
                    "content": content,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                    "model": model,
                    "task_type": task_type,
                    "latency_ms": round(latency_ms, 1),
                }

            except httpx.TimeoutException as e:
                logger.warning(
                    "ModelRouter timeout",
                    task_type=task_type,
                    provider=provider_name,
                    attempt=attempt,
                    max_retries=max_retries,
                )
                last_error = e
                if attempt == max_retries:
                    raise

            except httpx.HTTPStatusError as e:
                logger.warning(
                    "ModelRouter HTTP error",
                    task_type=task_type,
                    provider=provider_name,
                    attempt=attempt,
                    status_code=e.response.status_code,
                )
                last_error = e
                if attempt == max_retries:
                    raise

            # 重试前等待（使用 asyncio.sleep 而非 time.sleep）
            if attempt < max_retries and retry_delay > 0:
                import asyncio
                await asyncio.sleep(retry_delay)

        # 不应到达此处，但保险起见
        raise httpx.HTTPError(f"All {max_retries} retries exhausted: {last_error}")


# ── 模块级单例 ──

_instance: ModelRouter | None = None


def get_router() -> ModelRouter:
    """
    获取 ModelRouter 单例。

    首次调用时初始化，后续调用返回同一实例。

    Returns:
        ModelRouter 实例
    """
    global _instance
    if _instance is None:
        _instance = ModelRouter()
    return _instance

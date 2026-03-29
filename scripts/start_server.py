"""
启动广告素材合规审核 API 服务。

用法：
    python scripts/start_server.py
"""
import uvicorn

from src.api import app


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )

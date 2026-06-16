import asyncio
import uvicorn
from src.api.server import app


def main():
    """启动撮合系统服务"""
    uvicorn.run(
        "src.api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()

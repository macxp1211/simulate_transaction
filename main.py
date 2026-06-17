import os
import uvicorn
from src.api.server import app


def main():
    """启动撮合系统服务

    生产环境默认关闭 uvicorn 的 auto-reload，避免任何 .py 文件变更
    （如 IDE 自动保存、日志轮转、临时脚本写入）触发整进程重启，
    从而导致内存中的订单簿、账户、行情状态全部丢失。

    开发环境可通过设置环境变量启用热重载：
        UVICORN_RELOAD=true python main.py
    """
    reload_flag = os.getenv("UVICORN_RELOAD", "false").lower() in ("true", "1", "yes")
    uvicorn.run(
        "src.api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=reload_flag,
        log_level="info",
    )


if __name__ == "__main__":
    main()

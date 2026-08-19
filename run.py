r"""多人格群聊平台 — 启动入口

本地（默认 127.0.0.1:8000）:
    .venv\Scripts\python run.py

服务器（通过环境变量）:
    HOST=0.0.0.0 PORT=8123 .venv/bin/python run.py
"""
import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, workers=1)

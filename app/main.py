"""FastAPI 应用入口。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import api, seed
from .auth import AuthMiddleware
from .config import settings
from .database import Base, engine, session_factory
from .engine import ChatEngine
from .llm import LLMClient

app = FastAPI(title="多人格群聊平台", version="0.1.0")

llm = LLMClient()
api.engine = ChatEngine(llm)
llm.set_usage_sink(api.engine.usage_sink)
app.add_middleware(AuthMiddleware)


def _migrate_sqlite():
    """老库补列（如 Room.paused/allow_search、Agent.avatar），新库无操作。"""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        for stmt in (
            "ALTER TABLE rooms ADD COLUMN paused BOOLEAN DEFAULT 0",
            "ALTER TABLE rooms ADD COLUMN allow_search BOOLEAN DEFAULT 1",
            "ALTER TABLE agents ADD COLUMN avatar VARCHAR(500) DEFAULT ''",
            "ALTER TABLE agents ADD COLUMN reply_len VARCHAR(20) DEFAULT 'normal'",
            "ALTER TABLE agents ADD COLUMN is_god BOOLEAN DEFAULT 0",
            "ALTER TABLE agents ADD COLUMN memory TEXT DEFAULT ''",
            "ALTER TABLE messages ADD COLUMN concentration_scores TEXT DEFAULT '{}'",
            "ALTER TABLE messages ADD COLUMN feedback VARCHAR(10) DEFAULT ''",
            "ALTER TABLE messages ADD COLUMN quoted_message_id INTEGER DEFAULT NULL",
            "ALTER TABLE messages ADD COLUMN quoted_sender VARCHAR(100) DEFAULT ''",
            "ALTER TABLE messages ADD COLUMN quoted_content TEXT DEFAULT ''",
        ):
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # 列已存在


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()
    with session_factory() as db:
        seed.seed_if_empty(db, embed_fn=llm.embed)
        seed.ensure_god(db)
    api.engine.load_paused()


app.include_router(api.router)


@app.middleware("http")
async def no_cache(request, call_next):
    """禁止缓存：改代码后浏览器刷新即生效，避免拿着旧 JS/HTML。"""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


_static = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=_static, html=True), name="static")

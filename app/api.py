"""API 路由：agents / rooms / messages / 知识库 / 铸造 / 状态 / 监控 / 认证 / 设置。"""
import asyncio
import base64
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import auth, rag, seed
from .config import settings
from .database import get_db
from .models import (
    Agent,
    DecisionLog,
    DraftAgent,
    KnowledgeEntry,
    LLMCall,
    Message,
    PromptVersion,
    Room,
)
from .schemas import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
    AvatarIn,
    ConcentrationRow,
    DecisionLogOut,
    DraftOut,
    DraftUpdate,
    FeedbackIn,
    KnowledgeOut,
    KnowledgeSearchResult,
    KnowledgeTextIn,
    LLMCallRow,
    LoginIn,
    MessageOut,
    PauseIn,
    PromptVersionOut,
    RoomCreate,
    RoomOut,
    RoomUpdate,
    SendMessage,
    SettingsUpdateIn,
    StatsOverview,
)

router = APIRouter(prefix="/api")

# 由 main.py 注入
engine = None


def _to_agent_out(a: Agent) -> AgentOut:
    return AgentOut.model_validate(a)


def _to_room_out(r: Room, db: Session) -> RoomOut:
    out = RoomOut.model_validate(r)
    out.agents = [
        _to_agent_out(x)
        for aid in (r.agent_ids or [])
        if (x := db.get(Agent, aid)) is not None
    ]
    return out


# ---------- 系统 ----------
@router.get("/system")
def system_info():
    return {
        "llm_mode": settings.llm_mode,
        "llm_model": settings.llm_model,
        "god_model": settings.god_model or settings.llm_model,
    }


# ---------- Agents ----------
@router.get("/agents", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db)):
    return [_to_agent_out(a) for a in db.query(Agent).order_by(Agent.id).all()]


@router.post("/agents", response_model=AgentOut, status_code=201)
def create_agent(body: AgentCreate, db: Session = Depends(get_db)):
    a = Agent(**body.model_dump())
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_agent_out(a)


@router.put("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, body: AgentUpdate, db: Session = Depends(get_db)):
    a = db.get(Agent, agent_id)
    if a is None:
        raise HTTPException(404, "agent 不存在")
    fields = body.model_dump(exclude_unset=True)
    # 手动修改人格/记忆时，先把当前状态存成快照（支持回滚）
    if "system_prompt" in fields or "memory" in fields:
        db.add(
            PromptVersion(
                agent_id=a.id,
                ts=time.time(),
                system_prompt=a.system_prompt,
                memory=a.memory or "",
            )
        )
    for k, v in fields.items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return _to_agent_out(a)


# ---------- 提示词/记忆历史版本（回滚） ----------
@router.get("/agents/{agent_id}/versions", response_model=list[PromptVersionOut])
def agent_versions(agent_id: int, db: Session = Depends(get_db)):
    return [
        PromptVersionOut.model_validate(v)
        for v in db.query(PromptVersion)
        .filter(PromptVersion.agent_id == agent_id)
        .order_by(PromptVersion.id.desc())
        .limit(30)
        .all()
    ]


@router.post("/agents/{agent_id}/versions/{version_id}/rollback", response_model=AgentOut)
def rollback_agent(agent_id: int, version_id: int, db: Session = Depends(get_db)):
    """把 agent 的人格/记忆回滚到某个历史版本（当前状态先存为新快照）。"""
    a = db.get(Agent, agent_id)
    v = db.get(PromptVersion, version_id)
    if a is None or v is None:
        raise HTTPException(404, "agent 或版本不存在")
    if v.agent_id != agent_id:
        raise HTTPException(400, "版本不属于该 agent")
    db.add(
        PromptVersion(
            agent_id=a.id,
            ts=time.time(),
            system_prompt=a.system_prompt,
            memory=a.memory or "",
        )
    )
    a.system_prompt = v.system_prompt
    a.memory = v.memory or ""
    db.commit()
    db.refresh(a)
    return _to_agent_out(a)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: int, db: Session = Depends(get_db)):
    a = db.get(Agent, agent_id)
    if a is None:
        raise HTTPException(404, "agent 不存在")
    # 从所有群组的成员列表里移除该 agent，避免群"空转"（悬空引用导致无人回复）
    for room in db.query(Room).all():
        ids = list(room.agent_ids or [])
        if agent_id in ids:
            room.agent_ids = [x for x in ids if x != agent_id]
    db.delete(a)
    db.commit()


# ---------- Rooms ----------
@router.get("/rooms", response_model=list[RoomOut])
def list_rooms(db: Session = Depends(get_db)):
    return [_to_room_out(r, db) for r in db.query(Room).order_by(Room.id).all()]


@router.post("/rooms", response_model=RoomOut, status_code=201)
def create_room(body: RoomCreate, db: Session = Depends(get_db)):
    r = Room(**body.model_dump())
    db.add(r)
    db.commit()
    db.refresh(r)
    return _to_room_out(r, db)


@router.put("/rooms/{room_id}", response_model=RoomOut)
def update_room(room_id: int, body: RoomUpdate, db: Session = Depends(get_db)):
    r = db.get(Room, room_id)
    if r is None:
        raise HTTPException(404, "房间不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    return _to_room_out(r, db)


@router.delete("/rooms/{room_id}", status_code=204)
def delete_room(room_id: int, db: Session = Depends(get_db)):
    r = db.get(Room, room_id)
    if r is None:
        raise HTTPException(404, "房间不存在")
    db.delete(r)
    db.commit()


# ---------- Messages ----------
@router.get("/rooms/{room_id}/messages", response_model=list[MessageOut])
def list_messages(
    room_id: int, after_id: int = 0, limit: int = 200, db: Session = Depends(get_db)
):
    q = db.query(Message).filter(Message.room_id == room_id)
    if after_id:
        q = q.filter(Message.id > after_id)
    return [
        MessageOut.model_validate(m)
        for m in q.order_by(Message.id).limit(limit).all()
    ]


@router.post("/rooms/{room_id}/messages", response_model=MessageOut, status_code=201)
async def send_message(room_id: int, body: SendMessage, db: Session = Depends(get_db)):
    r = db.get(Room, room_id)
    if r is None:
        raise HTTPException(404, "房间不存在")
    m = Message(
        room_id=room_id,
        sender=body.sender or "我",
        sender_type="human",
        content=body.content,
        ts=time.time(),
    )
    if body.quoted_message_id:
        q = db.get(Message, body.quoted_message_id)
        if q is not None:
            m.quoted_message_id = q.id
            m.quoted_sender = q.sender
            m.quoted_content = q.content[:200]
    db.add(m)
    db.commit()
    db.refresh(m)
    quoted_text = f"{m.quoted_sender}：{m.quoted_content}" if m.quoted_content else ""
    asyncio.create_task(
        engine.on_message(
            room_id, m.sender, "human", m.content, message_id=m.id, quoted_text=quoted_text
        )
    )
    return MessageOut.model_validate(m)


@router.get("/rooms/{room_id}/concentration", response_model=list[ConcentrationRow])
def concentration_log(room_id: int, limit: int = 20, db: Session = Depends(get_db)):
    """浓度复盘：最近消息各 agent 的话题浓度分数。"""
    rows = (
        db.query(Message)
        .filter(Message.room_id == room_id)
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    agents = {a.id: a.name for a in db.query(Agent).all()}
    out = []
    for m in reversed(rows):
        scores = {}
        for aid_str, score in (m.concentration_scores or {}).items():
            try:
                name = agents.get(int(aid_str), aid_str)
            except ValueError:
                name = aid_str
            scores[name] = score
        out.append(
            ConcentrationRow(
                id=m.id,
                sender=m.sender,
                sender_type=m.sender_type,
                content=m.content[:60],
                ts=m.ts,
                scores=scores,
            )
        )
    return out


@router.get("/rooms/{room_id}/state")
def room_state(room_id: int):
    return engine.state(room_id)


# ---------- 真人控制：暂停 / 结束对话 ----------
@router.post("/rooms/{room_id}/pause")
def pause_room(room_id: int, body: PauseIn, db: Session = Depends(get_db)):
    r = db.get(Room, room_id)
    if r is None:
        raise HTTPException(404, "房间不存在")
    r.paused = body.paused
    db.commit()
    engine.set_paused(room_id, body.paused)
    return {"paused": body.paused}


@router.post("/rooms/{room_id}/close")
async def close_room(room_id: int, db: Session = Depends(get_db)):
    """结束当前话题：取消待发言、清空上下文与静默计数。

    必须 async：engine 内存状态属于事件循环线程，同步 def 会在线程池里
    跨线程操作（cancel 任务/改 dict），可能把引擎搞挂。
    """
    r = db.get(Room, room_id)
    if r is None:
        raise HTTPException(404, "房间不存在")
    engine.close_conversation(room_id)
    return {"closed": True}


@router.post("/rooms/{room_id}/clear")
async def clear_room(room_id: int, db: Session = Depends(get_db)):
    """清空聊天记录（彻底删除记忆）：DB 消息/决策日志 + 内存上下文全部清掉。

    必须 async：与 close 同理，避免跨线程操作 engine 内存。
    """
    r = db.get(Room, room_id)
    if r is None:
        raise HTTPException(404, "房间不存在")
    engine.clear_room(room_id)
    return {"cleared": True}


# ---------- 运行时可调设置 ----------
SETTING_KEYS = (
    "silence_limit", "delay_min", "delay_max", "cooldown_min", "cooldown_max",
    "suppress_factor", "high_conc_threshold", "prob_base", "prob_factor",
    "conc_low", "conc_high", "conc_min", "context_n", "repeat_threshold",
)

_DEFAULTS = {
    "silence_limit": settings.silence_limit,
    "delay_min": settings.delay_min,
    "delay_max": settings.delay_max,
    "cooldown_min": settings.cooldown_min,
    "cooldown_max": settings.cooldown_max,
    "suppress_factor": settings.suppress_factor,
    "high_conc_threshold": settings.high_conc_threshold,
    "prob_base": settings.prob_base,
    "prob_factor": settings.prob_factor,
    "conc_low": settings.conc_low,
    "conc_high": settings.conc_high,
    "conc_min": settings.conc_min,
    "context_n": settings.context_n,
    "repeat_threshold": settings.repeat_threshold,
}


@router.get("/settings")
def get_settings():
    """返回全部可调参数的当前生效值（含 .env 默认）。"""
    return {k: engine._p(k, _DEFAULTS[k]) for k in SETTING_KEYS}


@router.put("/settings")
def update_settings(body: SettingsUpdateIn):
    """批量修改设置，立即生效无需重启。"""
    for key, value in body.settings.items():
        if key not in SETTING_KEYS:
            raise HTTPException(400, f"未知设置项: {key}")
        try:
            float(value)
        except ValueError:
            raise HTTPException(400, f"设置值必须是数字: {key}={value}")
        engine.rt.set(key, value)
    return {"ok": True}


@router.delete("/settings")
def reset_settings():
    """恢复全部设置为默认值。"""
    engine.rt.reset()
    return {"ok": True}


# ---------- 恢复示例人格 ----------
@router.post("/system/restore-demo")
def restore_demo(db: Session = Depends(get_db)):
    created = seed.restore_demo_agents(db, embed_fn=engine.llm.embed)
    return {"created": [a.name for a in created]}


# ---------- 知识库（每 agent 独立） ----------
@router.get("/agents/{agent_id}/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(agent_id: int, db: Session = Depends(get_db)):
    if db.get(Agent, agent_id) is None:
        raise HTTPException(404, "agent 不存在")
    return [
        KnowledgeOut.model_validate(e)
        for e in db.query(KnowledgeEntry)
        .filter(KnowledgeEntry.agent_id == agent_id)
        .order_by(KnowledgeEntry.id)
        .all()
    ]


@router.post("/agents/{agent_id}/knowledge/text", response_model=list[KnowledgeOut], status_code=201)
def add_knowledge_text(agent_id: int, body: KnowledgeTextIn, db: Session = Depends(get_db)):
    if db.get(Agent, agent_id) is None:
        raise HTTPException(404, "agent 不存在")
    created = rag.add_text(db, agent_id, body.title, body.content, body.source, engine.llm.embed)
    db.commit()
    for e in created:
        db.refresh(e)
    return [KnowledgeOut.model_validate(e) for e in created]


@router.delete("/knowledge/{entry_id}", status_code=204)
def delete_knowledge(entry_id: int, db: Session = Depends(get_db)):
    e = db.get(KnowledgeEntry, entry_id)
    if e is None:
        raise HTTPException(404, "知识条目不存在")
    db.delete(e)
    db.commit()


@router.get("/agents/{agent_id}/knowledge/search", response_model=list[KnowledgeSearchResult])
def search_knowledge(agent_id: int, q: str, db: Session = Depends(get_db)):
    if db.get(Agent, agent_id) is None:
        raise HTTPException(404, "agent 不存在")
    if not q.strip():
        return []
    results = rag.retrieve(db, agent_id, q, engine.llm.embed, k=5, min_score=0.0)
    return [
        KnowledgeSearchResult(title=r.title, content=r.content[:300], score=round(s, 4))
        for r, s in results
    ]


# ---------- 头像 ----------
_AVATAR_DIR = Path(__file__).parent / "static" / "avatars"
_ALLOWED_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


@router.post("/agents/{agent_id}/avatar", response_model=AgentOut)
def upload_avatar(agent_id: int, body: AvatarIn, db: Session = Depends(get_db)):
    """data URL 上传头像（前端 FileReader 转 base64），保存到 static/avatars/。"""
    a = db.get(Agent, agent_id)
    if a is None:
        raise HTTPException(404, "agent 不存在")
    if not body.data_url.startswith("data:image/"):
        raise HTTPException(400, "仅支持图片 data URL")
    header, _, b64 = body.data_url.partition(",")
    mime = header.removeprefix("data:").split(";")[0]
    ext = _ALLOWED_EXT.get(mime)
    if ext is None:
        raise HTTPException(400, f"不支持的图片类型: {mime}")
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(400, "图片数据解码失败")
    if len(raw) > 3 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 3MB")
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"agent_{agent_id}_{int(time.time())}{ext}"
    (_AVATAR_DIR / filename).write_bytes(raw)
    a.avatar = f"/avatars/{filename}"
    db.commit()
    db.refresh(a)
    return AgentOut.model_validate(a)


# ---------- 铸造：待确认档案 ----------
@router.get("/god/drafts", response_model=list[DraftOut])
def list_drafts(db: Session = Depends(get_db)):
    return [
        DraftOut.model_validate(d)
        for d in db.query(DraftAgent).order_by(DraftAgent.id.desc()).all()
    ]


@router.get("/god/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    d = db.get(DraftAgent, draft_id)
    if d is None:
        raise HTTPException(404, "草稿不存在")
    return DraftOut.model_validate(d)


@router.put("/god/drafts/{draft_id}", response_model=DraftOut)
def update_draft(draft_id: int, body: DraftUpdate, db: Session = Depends(get_db)):
    d = db.get(DraftAgent, draft_id)
    if d is None:
        raise HTTPException(404, "草稿不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    db.commit()
    db.refresh(d)
    return DraftOut.model_validate(d)


@router.post("/god/drafts/{draft_id}/approve", response_model=AgentOut)
def approve_draft(draft_id: int, db: Session = Depends(get_db)):
    """真人确认：创建正式 agent + 知识库，草稿标记 approved。"""
    d = db.get(DraftAgent, draft_id)
    if d is None:
        raise HTTPException(404, "草稿不存在")
    if d.status != "pending":
        raise HTTPException(400, f"草稿已处理（{d.status}）")
    params = d.suggested_params or {}
    a = Agent(
        name=d.name,
        aliases=d.aliases or [],
        system_prompt=d.system_prompt or "",
        few_shot=d.few_shot or [],
        trigger_keywords=d.trigger_keywords or [],
        base_freq=float(params.get("base_freq", 0.3)),
        base_cd=float(params.get("base_cd_s", 30)),
        reply_len=params.get("reply_len", "normal") if params.get("reply_len") in ("short", "normal", "long") else "normal",
        model=params.get("model", "") or "",
        created_by="god",
    )
    db.add(a)
    db.flush()
    for entry in d.knowledge_entries or []:
        rag.add_text(
            db,
            a.id,
            entry.get("title", ""),
            entry.get("content", ""),
            entry.get("source", "铸造师"),
            engine.llm.embed,
        )
    d.status = "approved"
    db.commit()
    db.refresh(a)
    return AgentOut.model_validate(a)


@router.delete("/god/drafts/{draft_id}", status_code=204)
def reject_draft(draft_id: int, db: Session = Depends(get_db)):
    d = db.get(DraftAgent, draft_id)
    if d is None:
        raise HTTPException(404, "草稿不存在")
    d.status = "rejected"
    db.commit()


# ---------- 认证 ----------
@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if auth.is_locked(ip):
        raise HTTPException(429, "尝试次数过多，请 1 分钟后再试")
    if not auth.verify_password(body.password):
        auth.record_fail(ip)
        raise HTTPException(401, "密码错误")
    auth.reset_fails(ip)
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.make_token(),
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


# ---------- 监控：反馈 / 统计 / 决策日志 ----------
@router.post("/messages/{message_id}/feedback")
def set_feedback(message_id: int, body: FeedbackIn, db: Session = Depends(get_db)):
    m = db.get(Message, message_id)
    if m is None:
        raise HTTPException(404, "消息不存在")
    if body.value not in ("", "up", "down"):
        raise HTTPException(400, "feedback 只允许 up/down/空")
    m.feedback = body.value
    db.commit()
    return {"feedback": body.value}


@router.get("/stats/overview", response_model=StatsOverview)
def stats_overview(db: Session = Depends(get_db)):
    def count(model, **filters):
        q = db.query(func.count(model.id))
        for k, v in filters.items():
            q = q.filter(getattr(model, k) == v)
        return q.scalar() or 0

    total_tokens = (
        db.query(
            func.coalesce(func.sum(LLMCall.prompt_tokens), 0)
            + func.coalesce(func.sum(LLMCall.completion_tokens), 0)
        ).scalar()
        or 0
    )
    return StatsOverview(
        total_messages=count(Message),
        total_replies=count(Message, sender_type="agent"),
        total_calls=count(LLMCall),
        total_tokens=int(total_tokens),
        feedback_up=count(Message, feedback="up"),
        feedback_down=count(Message, feedback="down"),
    )


@router.get("/stats/llm", response_model=list[LLMCallRow])
def stats_llm(db: Session = Depends(get_db)):
    rows = (
        db.query(
            LLMCall.purpose,
            LLMCall.model,
            func.count(LLMCall.id),
            func.sum(LLMCall.prompt_tokens),
            func.sum(LLMCall.completion_tokens),
        )
        .group_by(LLMCall.purpose, LLMCall.model)
        .order_by(func.count(LLMCall.id).desc())
        .all()
    )
    return [
        LLMCallRow(
            purpose=purpose,
            model=model,
            calls=calls,
            prompt_tokens=int(pt or 0),
            completion_tokens=int(ct or 0),
        )
        for purpose, model, calls, pt, ct in rows
    ]


@router.get("/logs/decisions", response_model=list[DecisionLogOut])
def decision_logs(room_id: int | None = None, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(DecisionLog).order_by(DecisionLog.id.desc())
    if room_id:
        q = q.filter(DecisionLog.room_id == room_id)
    return [DecisionLogOut.model_validate(d) for d in q.limit(limit).all()]

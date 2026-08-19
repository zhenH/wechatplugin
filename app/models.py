"""ORM 模型：Agent / Room / Message / KnowledgeEntry / DraftAgent / 监控。"""
import time

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list)  # 别名（@触发）
    system_prompt: Mapped[str] = mapped_column(Text, default="")  # 人格设定
    few_shot: Mapped[list] = mapped_column(JSON, default=list)  # 语气样本（进 prompt）
    trigger_keywords: Mapped[list] = mapped_column(JSON, default=list)  # 触发关键词
    base_freq: Mapped[float] = mapped_column(Float, default=0.3)  # 话痨度（频率）
    base_cd: Mapped[float] = mapped_column(Float, default=30.0)  # 基础冷却(秒)
    reply_len: Mapped[str] = mapped_column(String(20), default="normal")  # short|normal|long（性格）
    model: Mapped[str] = mapped_column(String(100), default="")  # 留空用全局模型
    avatar: Mapped[str] = mapped_column(String(500), default="")  # 头像 URL 或 /avatars/xxx.png
    created_by: Mapped[str] = mapped_column(String(20), default="manual")  # manual | god（来源标记）
    is_god: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否铸造师本体（走铸造流程）
    memory: Mapped[str] = mapped_column(Text, default="")  # 长期记忆（agent 聊天中自主积累）


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="web")  # web | wechat
    agent_ids: Mapped[list] = mapped_column(JSON, default=list)  # 启用的 agent
    paused: Mapped[bool] = mapped_column(Boolean, default=False)  # 真人暂停 agent 发言
    allow_search: Mapped[bool] = mapped_column(Boolean, default=True)  # 允许上帝 agent 联网搜索


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sender: Mapped[str] = mapped_column(String(100), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(20), default="human")  # human | agent
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[float] = mapped_column(Float, default=lambda: time.time())
    concentration_scores: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # {agent_id: 浓度} 复盘用
    feedback: Mapped[str] = mapped_column(String(10), default="")  # up | down | ""
    # 引用：真人引用之前某条消息（含 agent 的话）再回复
    quoted_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    quoted_sender: Mapped[str] = mapped_column(String(100), default="")
    quoted_content: Mapped[str] = mapped_column(Text, default="")


class KnowledgeEntry(Base):
    """RAG 知识库条目（入库时已分块，每条即一个 chunk）。"""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(500), default="")
    embedding: Mapped[list] = mapped_column(JSON, default=list)  # 向量（按需生成）
    ts: Mapped[float] = mapped_column(Float, default=lambda: time.time())


class DraftAgent(Base):
    """铸造师生成的待确认角色档案（真人确认后才创建正式 agent）。"""

    __tablename__ = "draft_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    few_shot: Mapped[list] = mapped_column(JSON, default=list)
    trigger_keywords: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_entries: Mapped[list] = mapped_column(JSON, default=list)  # [{title,content,source}]
    suggested_params: Mapped[dict] = mapped_column(JSON, default=dict)  # {base_freq, base_cd_s, model}
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    created_at: Mapped[float] = mapped_column(Float, default=lambda: time.time())


class DecisionLog(Base):
    """每次 agent 发言的决策链路：触发消息、浓度、概率、知识命中、回复。"""

    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    agent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    trigger_content: Mapped[str] = mapped_column(Text, default="")  # 触发它的那条消息
    concentration: Mapped[float] = mapped_column(Float, default=0.0)
    probability: Mapped[float] = mapped_column(Float, default=0.0)
    mentioned: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否被点名/触发词
    knowledge_hits: Mapped[list] = mapped_column(JSON, default=list)  # [{title, score}]
    reply: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[float] = mapped_column(Float, default=lambda: time.time())


class LLMCall(Base):
    """LLM 调用与 token 统计。"""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[float] = mapped_column(Float, index=True, default=lambda: time.time())
    purpose: Mapped[str] = mapped_column(String(20), default="chat")  # chat|forge|score|embed
    model: Mapped[str] = mapped_column(String(100), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)


class AppSetting(Base):
    """运行时可调设置（管理端设置页），DB 优先于 .env 默认值。"""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(String(200), default="")


class PromptVersion(Base):
    """agent 提示词/记忆的历史快照（用于回滚）。"""

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ts: Mapped[float] = mapped_column(Float, default=lambda: time.time())
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    memory: Mapped[str] = mapped_column(Text, default="")

"""Pydantic 请求/响应模型。"""
from typing import Optional

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str
    aliases: list[str] = []
    system_prompt: str = ""
    few_shot: list[str] = []
    trigger_keywords: list[str] = []
    base_freq: float = Field(0.3, ge=0.0, le=1.0)
    base_cd: float = Field(30.0, ge=1.0)
    reply_len: str = "normal"  # short | normal | long
    model: str = ""
    memory: str = ""
    created_by: str = "manual"


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    aliases: Optional[list[str]] = None
    system_prompt: Optional[str] = None
    few_shot: Optional[list[str]] = None
    trigger_keywords: Optional[list[str]] = None
    base_freq: Optional[float] = Field(None, ge=0.0, le=1.0)
    base_cd: Optional[float] = Field(None, ge=1.0)
    reply_len: Optional[str] = None
    model: Optional[str] = None
    avatar: Optional[str] = None
    memory: Optional[str] = None


class AgentOut(BaseModel):
    id: int
    name: str
    aliases: list[str]
    system_prompt: str
    few_shot: list[str]
    trigger_keywords: list[str]
    base_freq: float
    base_cd: float
    reply_len: str = "normal"
    model: str
    avatar: str = ""
    created_by: str
    is_god: bool = False
    memory: str = ""

    model_config = {"from_attributes": True}


class PromptVersionOut(BaseModel):
    id: int
    agent_id: int
    ts: float
    system_prompt: str
    memory: str

    model_config = {"from_attributes": True}


class AvatarIn(BaseModel):
    data_url: str  # data:image/png;base64,....


class RoomCreate(BaseModel):
    name: str
    channel: str = "web"
    agent_ids: list[int] = []
    paused: bool = False
    allow_search: bool = True


class RoomUpdate(BaseModel):
    name: Optional[str] = None
    channel: Optional[str] = None
    agent_ids: Optional[list[int]] = None
    paused: Optional[bool] = None
    allow_search: Optional[bool] = None


class RoomOut(BaseModel):
    id: int
    name: str
    channel: str
    agent_ids: list[int]
    paused: bool = False
    allow_search: bool = True
    agents: list[AgentOut] = []

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    room_id: int
    sender: str
    sender_type: str
    content: str
    ts: float
    feedback: str = ""
    quoted_message_id: Optional[int] = None
    quoted_sender: str = ""
    quoted_content: str = ""

    model_config = {"from_attributes": True}


class SendMessage(BaseModel):
    sender: str = "我"
    content: str = Field(..., min_length=1)
    quoted_message_id: Optional[int] = None  # 引用某条消息后再回复


class PauseIn(BaseModel):
    paused: bool


class KnowledgeTextIn(BaseModel):
    title: str = ""
    content: str = Field(..., min_length=1)
    source: str = ""


class KnowledgeOut(BaseModel):
    id: int
    agent_id: int
    title: str
    content: str
    source: str
    ts: float

    model_config = {"from_attributes": True}


class KnowledgeSearchResult(BaseModel):
    title: str
    content: str
    score: float


class DraftOut(BaseModel):
    id: int
    name: str
    aliases: list[str]
    system_prompt: str
    few_shot: list[str]
    trigger_keywords: list[str]
    knowledge_entries: list[dict] = []
    suggested_params: dict = {}
    status: str
    created_at: float

    model_config = {"from_attributes": True}


class DraftUpdate(BaseModel):
    name: Optional[str] = None
    aliases: Optional[list[str]] = None
    system_prompt: Optional[str] = None
    few_shot: Optional[list[str]] = None
    trigger_keywords: Optional[list[str]] = None
    knowledge_entries: Optional[list[dict]] = None
    suggested_params: Optional[dict] = None


class ConcentrationRow(BaseModel):
    id: int
    sender: str
    sender_type: str
    content: str
    ts: float
    scores: dict[str, float] = {}  # {agent_name: 浓度}


class FeedbackIn(BaseModel):
    value: str = ""  # up | down | ""


class LoginIn(BaseModel):
    password: str


class SettingsUpdateIn(BaseModel):
    settings: dict[str, str] = {}  # {key: value}


class DecisionLogOut(BaseModel):
    id: int
    room_id: int
    agent_id: int
    agent_name: str
    trigger_content: str
    concentration: float
    probability: float
    mentioned: bool
    knowledge_hits: list[dict] = []
    reply: str
    ts: float

    model_config = {"from_attributes": True}


class StatsOverview(BaseModel):
    total_messages: int
    total_replies: int
    total_calls: int
    total_tokens: int
    feedback_up: int
    feedback_down: int


class LLMCallRow(BaseModel):
    purpose: str
    model: str
    calls: int
    prompt_tokens: int
    completion_tokens: int

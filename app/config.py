"""全局配置：从环境变量 / .env 读取，均有默认值。"""
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv 未安装也不影响
    pass


def _get(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # 数据库
    database_url: str = _get("DATABASE_URL", "sqlite:///./chat.db")

    # LLM
    llm_mode: str = _get("LLM_MODE", "mock")  # mock | openai
    llm_base_url: str = _get("LLM_BASE_URL", "https://api.deepseek.com")
    llm_api_key: str = _get("LLM_API_KEY", "")
    llm_model: str = _get("LLM_MODEL", "deepseek-chat")
    god_model: str = _get("GOD_MODEL", "")
    # 向量化（RAG）：DeepSeek 无 embedding 接口，默认本地向量；
    # 也可单独配一个 embedding 服务（如 OpenAI / 硅基流动）
    embed_mode: str = _get("EMBED_MODE", "auto")  # auto | api | local
    embed_base_url: str = _get("EMBED_BASE_URL", "")  # 空则用 llm_base_url
    embed_api_key: str = _get("EMBED_API_KEY", "")
    embed_model: str = _get("EMBED_MODEL", "text-embedding-3-small")

    # 发言算法参数（见 DESIGN.md §3.6）
    context_n: int = _get_int("CONTEXT_N", 20)
    silence_limit: int = _get_int("SILENCE_LIMIT", 3)
    delay_min: float = _get_float("DELAY_MIN", 2.0)
    delay_max: float = _get_float("DELAY_MAX", 8.0)
    cooldown_min: float = _get_float("COOLDOWN_MIN", 5.0)
    cooldown_max: float = _get_float("COOLDOWN_MAX", 180.0)
    suppress_factor: float = _get_float("SUPPRESS_FACTOR", 0.45)
    high_conc_threshold: float = _get_float("HIGH_CONC_THRESHOLD", 0.7)

    # 发言概率 P = base_freq × (prob_base + prob_factor × c) × suppress
    prob_base: float = _get_float("PROB_BASE", 0.4)  # 概率常数项（越大越爱接话）
    prob_factor: float = _get_float("PROB_FACTOR", 2.0)  # 浓度对概率的放大系数

    # 浓度混合计算（DESIGN.md §3.3）：embedding 粗筛 + LLM 校准
    conc_low: float = _get_float("CONC_LOW", 0.25)  # sim 低于此 → 直接低浓度，不调 LLM
    conc_high: float = _get_float("CONC_HIGH", 0.75)  # sim 高于此 → 直接高浓度，不调 LLM
    conc_min: float = _get_float("CONC_MIN", 0.3)  # 非点名闲聊基线浓度（参与闲聊意愿）
    repeat_threshold: float = _get_float("REPEAT_THRESHOLD", 0.8)  # 防复读相似度阈值

    # 长期记忆（agent 自主积累经历，自动裁剪 + 版本回滚）
    memory_enabled: bool = _get("MEMORY_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    memory_max_items: int = _get_int("MEMORY_MAX_ITEMS", 12)  # 超过则自动压缩
    memory_max_chars: int = _get_int("MEMORY_MAX_CHARS", 800)

    # 访问认证（公网防护）：ACCESS_PASSWORD 非空即启用登录
    access_password: str = _get("ACCESS_PASSWORD", "")
    access_secret: str = _get("ACCESS_SECRET", "")  # token 签名密钥，留空用 ACCESS_PASSWORD
    session_days: int = _get_int("SESSION_DAYS", 7)  # 登录有效期（天）


settings = Settings()

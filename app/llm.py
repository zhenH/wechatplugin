"""LLM 客户端：OpenAI 兼容接口（urllib 实现，零额外依赖）+ mock 模式 + 向量化。"""
import asyncio
import json
import math
import random
import re
import urllib.request
import zlib

from .config import settings

_MOCK_PATTERNS = [
    "{tag}……说到这个我倒有点想法，不过先听你说。",
    "{tag} 你这话挺有意思，我记下了。",
    "{tag} 嗯？有人聊到我在行的事了。",
    "{tag} 哈哈，这话题我能聊一晚上。",
    "{tag} 我不太认同，但你说说看。",
]


def char_bigram_vec(text: str, dim: int = 256) -> list[float]:
    """离线向量化：字符 bigram 哈希 + 归一化。

    用 crc32 保证跨进程稳定（Python 内置 hash 有随机盐，不能用于持久化向量）。
    中文效果尚可，是 embedding API 不可用时的兜底。
    """
    v = [0.0] * dim
    s = re.sub(r"\s+", "", (text or "").lower())
    for i in range(len(s) - 1):
        h = zlib.crc32(s[i : i + 2].encode("utf-8")) % dim
        v[h] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class LLMClient:
    def __init__(self):
        self.mode = settings.llm_mode
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.default_model = settings.llm_model
        self._usage_sink = None

    def set_usage_sink(self, fn):
        """注入用量回调 fn(purpose, model, prompt_tokens, completion_tokens)。"""
        self._usage_sink = fn

    def _report(self, purpose: str, model: str, prompt_tokens: int, completion_tokens: int):
        if self._usage_sink:
            try:
                self._usage_sink(purpose, model, int(prompt_tokens or 0), int(completion_tokens or 0))
            except Exception:
                pass

    # ---------- 对外 ----------
    async def chat(
        self,
        *,
        system_prompt: str = "",
        few_shot: list[str] | None = None,
        context_lines: list[str] | None = None,
        knowledge: str = "",
        model: str | None = None,
        reply_len: str = "normal",
        last_own: str = "",
        memory: str = "",
    ) -> str:
        """生成一条回复；不相关/空回复时返回空字符串。knowledge 为检索到的知识库块。

        reply_len：角色性格的发言长短（short|normal|long），独立于话痨度（频率）。
        last_own：该 agent 刚说过的话（动态注入，防止语义复读）。
        memory：该 agent 的长期记忆（经历），作为人格的一部分注入。
        """
        reply_len = self._bounce_len(reply_len)  # 长度弹性：每次发言有波动
        if self.mode == "mock":
            await asyncio.sleep(0)
            reply = self._mock_reply(system_prompt or "AI", context_lines or [])
            if knowledge:
                titles = re.findall(r"^\[\d+\] ([^\n]+)", knowledge, re.M)
                if titles:
                    reply += f"〔知识库命中：{'、'.join(titles[:2])}〕"
            # mock 估算：中文约每字 0.7 token
            pt = int((len(system_prompt) + sum(len(x) for x in (context_lines or []))) * 0.7)
            ct = int(len(reply) * 0.7)
            self._report("chat", self.default_model, pt, ct)
            return reply
        messages = self._build_messages(
            system_prompt, few_shot or [], context_lines or [], knowledge, reply_len, last_own, memory
        )
        # max_tokens 只是"保险丝"防失控，不靠它卡长度（否则话会被硬截断）；
        # 长短性格靠 prompt 软性引导
        max_tokens = {"short": 100, "normal": 700, "long": 1500}.get(reply_len, 700)
        return await asyncio.to_thread(self._openai_chat, messages, model, "chat", max_tokens)

    def embed(self, text: str) -> list[float]:
        """文本向量化。

        - EMBED_MODE=api 或 (auto 且配了 EMBED_API_KEY) → 调独立 embedding 服务
        - 其余情况（含 DeepSeek 无 embedding 接口、API 失败）→ 本地字符 bigram 向量
        """
        use_api = settings.embed_mode == "api" or (
            settings.embed_mode == "auto" and bool(settings.embed_api_key)
        )
        if use_api:
            try:
                return self._openai_embed(text)
            except Exception:
                pass  # 失败回退本地，不让检索/浓度崩溃
        vec = char_bigram_vec(text)
        self._report("embed", settings.embed_model, int(len(text) * 0.4), 0)
        return vec

    async def extract_memory(self, persona: str, memory: str, context_lines: list[str]) -> list[str]:
        """从最近对话中提取值得角色长期记住的新事实（如收到馈赠/承诺/重要信息）。

        返回新记忆条目列表（最多 3 条）；mock 模式或失败返回 []。
        """
        if self.mode == "mock":
            return []
        history = "\n".join(context_lines[-30:]) if context_lines else "（无）"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是角色记忆提取器。从对话中提取【这个角色】值得长期记住的事实："
                    "别人送的礼物/帮助、承诺、重要事件、关系变化。"
                    "注意：群聊里的『我』指真人用户（不是本角色）。"
                    "只输出 JSON 字符串数组，每条一句话（不含角色名）；没有新事实就输出 []。不要输出其他内容。"
                ),
            },
            {
                "role": "user",
                "content": f"角色简介：{persona}\n已记住：{memory or '（无）'}\n\n对话记录（最新在最后）：\n{history}\n\n输出新增记忆 JSON：",
            },
        ]
        try:
            text = await asyncio.to_thread(self._openai_chat, messages, None, "memory", 200)
        except Exception:
            return []
        items = self._parse_json_list(text)
        return [str(x).strip() for x in items if str(x).strip()][:3]

    async def compact_memory(self, memory: str) -> str:
        """压缩整理长期记忆：保留最重要/最新的，删掉过时细节（防提示词无限膨胀）。"""
        if self.mode == "mock":
            return memory
        messages = [
            {
                "role": "system",
                "content": (
                    "你是角色记忆整理员。把记忆压缩成最重要的 6 条以内，每条一句话："
                    "优先保留最近、影响大的（礼物/承诺/关系），删掉过时琐碎细节。直接输出整理后的列表，每行一条。"
                ),
            },
            {"role": "user", "content": f"记忆：\n{memory}"},
        ]
        try:
            text = await asyncio.to_thread(self._openai_chat, messages, None, "memory", 300)
        except Exception:
            return memory
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        return "\n".join(lines[:6]) if lines else memory

    @staticmethod
    def _parse_json_list(text: str) -> list:
        m = re.search(r"\[.*\]", text or "", re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    async def relevance_score(self, persona: str, content: str) -> float | None:
        """LLM 打分：消息与某 agent 人设/知识领域的相关度 0~10 → /10。

        返回 None 表示无法打分（mock 模式 / 调用失败 / 解析失败），调用方应回退启发式。
        """
        if self.mode == "mock":
            return None
        messages = [
            {
                "role": "system",
                "content": f"你是{persona}。你正在一个群里，需要判断一条消息是否与你有关。",
            },
            {
                "role": "user",
                "content": (
                    f"群消息：{content[:200]}\n"
                    "这条消息与你的角色设定/知识领域的相关度是多少？只输出一个 0-10 的整数："
                ),
            },
        ]
        try:
            text = await asyncio.to_thread(self._openai_chat, messages, None, "score")
        except Exception:
            return None
        m = re.search(r"\d+", text or "")
        if not m:
            return None
        return max(0.0, min(1.0, int(m.group(0)) / 10.0))

    # ---------- 消息组装 ----------
    _LEN_RULES = {
        "short": "- 回复要短，10~20 字，一句说完，能短则短，干脆利落。",
        "normal": "- 回复保持简短自然（50 字左右），观点说完就收尾，不要刻意拉长。",
        "long": "- 可以适当展开（100~200 字），把观点说透，自然收尾不重复啰嗦。",
    }

    # 长度弹性：同一角色的发言长短会有起伏（像真人），以性格档位为中心波动
    _LEN_BOUNCE = {
        "short": [("short", 0.75), ("normal", 0.25)],
        "normal": [("normal", 0.6), ("short", 0.2), ("long", 0.2)],
        "long": [("long", 0.75), ("normal", 0.25)],
    }

    def _bounce_len(self, base: str) -> str:
        if self.mode == "mock":
            return base
        opts = self._LEN_BOUNCE.get(base, [("normal", 1.0)])
        r = random.random()
        acc = 0.0
        for option, prob in opts:
            acc += prob
            if r <= acc:
                return option
        return base

    def _build_messages(
        self,
        system_prompt: str,
        few_shot: list[str],
        context_lines: list[str],
        knowledge: str = "",
        reply_len: str = "normal",
        last_own: str = "",
        memory: str = "",
    ) -> list[dict]:
        sys = (system_prompt or "你是一个群聊参与者。").strip()
        if memory:
            sys += f"\n\n## 你的经历记忆（长期记住的事，聊天时会自然提及）\n{memory}"
        if few_shot:
            samples = "\n".join(f"- {s}" for s in few_shot)
            sys += f"\n\n## 你的语气风格示例（模仿句子的语气，不要照抄内容）\n{samples}"
        sys += (
            "\n\n## 群聊规则\n"
            "- 你在一个群里，群里有真人和其他 AI，像真人一样自然地聊天。\n"
            f"{self._LEN_RULES.get(reply_len, self._LEN_RULES['normal'])}\n"
            "- 不要解释自己的身份，不要用列表。\n"
            "- 先看群聊记录里**你自己刚说过的话**：如果你已经回应过这个话题，"
            "这次就补充全新的角度、细节或玩笑，绝不复述自己说过的话；实在没新意就简短表态。\n"
            "- 如果这条消息与你无关、没有可说的，直接输出空字符串。"
        )
        if last_own:
            sys += f"\n- 你刚才说过：「{last_own[:120]}」——新回复不要重复这句话的意思，换个全新角度或简短收尾。"
        history = "\n".join(context_lines[-40:]) if context_lines else "（群聊刚开始，还没有历史消息）"
        kb = ""
        if knowledge:
            kb = f"\n\n## 知识库参考（只有当回答需要依据时才引用，且不要逐字照搬）\n{knowledge}"
        messages = [
            {"role": "system", "content": sys},
            {
                "role": "user",
                "content": (
                    f"群聊记录（最新在最后）：\n{history}\n{kb}\n\n"
                    "现在轮到你发言了，直接输出你的回复："
                ),
            },
        ]
        return messages

    # ---------- OpenAI 兼容调用 ----------
    def _openai_chat(
        self,
        messages: list[dict],
        model: str | None,
        purpose: str = "chat",
        max_tokens: int = 300,
        timeout: int = 180,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": 0.9,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # 网络/鉴权失败不炸群
            return f"（LLM 调用失败：{exc}）"
        usage = data.get("usage") or {}
        self._report(
            purpose,
            model or self.default_model,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        if not content and msg.get("reasoning_content"):
            # deepseek-reasoner 推理占满 max_tokens 时 content 可能为空
            return "（模型回复为空：推理过长被截断，请重试或换 deepseek-chat）"
        return content

    def _openai_embed(self, text: str) -> list[float]:
        url = f"{(settings.embed_base_url or self.base_url).rstrip('/')}/embeddings"
        payload = {"model": settings.embed_model, "input": text}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.embed_api_key or self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        usage = data.get("usage") or {}
        self._report(
            "embed",
            settings.embed_model,
            usage.get("prompt_tokens", 0),
            usage.get("total_tokens", 0) - usage.get("prompt_tokens", 0),
        )
        return data["data"][0]["embedding"]

    # ---------- mock 模式 ----------
    def _mock_reply(self, system_prompt: str, context_lines: list[str]) -> str:
        first = ""
        if system_prompt.strip():
            first = system_prompt.strip().splitlines()[0]
        tag = first[2:] if first.startswith("你是") else first
        tag = (tag or "AI")[:12]
        name_hint = ""
        for line in reversed(context_lines):
            if ":" in line:
                name_hint = line.split(":", 1)[0].strip()
                break
        reply = random.choice(_MOCK_PATTERNS).format(tag=tag)
        if name_hint:
            reply += f"（回应 {name_hint} 的话）"
        return reply

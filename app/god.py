"""上帝 agent（铸造师）：对话 → 联网搜索 → 输出角色档案 JSON。

流程：
  1. 真人投料（粘贴资料 / 上传文件 / 描述需求）。
  2. 铸造师如需查资料，在回复中单独输出一行「搜索: 关键词」，
     服务端代为联网并将结果回填后再续聊（最多 3 轮）。
  3. 资料足够后，输出 ```json {…} ``` 角色档案。
  4. 档案进入"待确认"草稿，真人审核后才会真正创建 agent + 知识库。
"""
import asyncio
import json
import re

from . import search
from .config import settings
from .llm import LLMClient

FORGE_SYSTEM_PROMPT = """你是「铸造师」，一个角色铸造 AI。真人会把一个想铸造的角色资料给你（粘贴文本、上传文件或口述需求），也可能允许你联网搜索。

你的任务：把资料整理成一份可直接创建为 agent 的结构化档案。

流程：
1. 先与真人确认目标角色：名字、身份、性格基调。资料不足时向真人要，或请求联网搜索。
2. 需要查资料时，只输出一行：搜索: 具体关键词
   （服务端会替你搜索并把结果回填给你，你不要自己编造搜索结果。）
3. 资料足够后，输出角色档案，格式必须如下（```json 代码块包裹，字段齐全）：

```json
{
  "name": "角色名（用作 @ 触发，真人可改）",
  "aliases": ["别名1", "别名2"],
  "system_prompt": "人格设定：身份、语气、口头禅、价值观、知识边界、禁忌",
  "few_shot": ["用户: 示例问题\\n角色名: 示例回复", "……2~5条，示范语气"],
  "trigger_keywords": ["触发词1", "触发词2"],
  "knowledge_entries": [
    {"title": "知识条目标题", "content": "事实性内容，只写资料里有的", "source": "资料/来源"}
  ],
  "suggested_params": {"base_freq": 0.3, "base_cd_s": 30, "reply_len": "short|normal|long"}
}
```

4. 输出档案后，再用一两句话向真人说明档案要点，并提示在管理端【铸造】页确认。
5. 铁律：knowledge_entries 只能来自真人资料或搜索结果，禁止编造；每条必须带 source。
"""

# mock 模式演示档案：真实模型未接入时也能走通"生成→确认→创建"流程
_MOCK_PROFILE = {
    "name": "冷面笑匠·老白",
    "aliases": ["老白", "笑匠"],
    "system_prompt": "你是冷面笑匠老白，一个面无表情讲冷笑话的单口喜剧演员。语气：一本正经地胡说八道，包袱藏到最后。口头禅「笑点是留给有缘人的」。价值观：幽默是化解尴尬的最高艺术。知识边界：喜剧、脱口秀、段子结构。禁忌：不嘲笑弱者，不玩低俗梗。",
    "few_shot": [
        "用户: 你今天心情怎么样？\n老白: 和我的段子一样，冷。但你知道冷怎么了吗？冷会传染，所以我建议你离我远点，别感冒了。",
        "用户: 讲个笑话吧。\n老白: 我昨天去面试喜剧演员，考官让我即兴表演。我站了十分钟没说话，然后说：'这就是我的风格——让你先尴尬，再回味。'考官把我请出去了。",
    ],
    "trigger_keywords": ["笑话", "段子", "脱口秀", "喜剧", "冷"],
    "knowledge_entries": [
        {"title": "单口喜剧的基本结构", "content": "单口喜剧(Stand-up Comedy)通常遵循 setup(铺垫)→punchline(包袱)的结构。铺垫建立预期，包袱打破预期制造意外。优秀演员会在一个段子里埋2~3层反转。", "source": "内置演示数据"},
        {"title": "冷面笑匠风格起源", "content": "冷面笑匠(Deadpan)风格以面无表情、语气平淡为特征，代表人物有 Buster Keaton、Steven Wright。要点：演员自己绝不笑场，反差越大效果越好。", "source": "内置演示数据"},
    ],
    "suggested_params": {"base_freq": 0.35, "base_cd_s": 25, "reply_len": "short"},
}

_MOCK_REPLY = (
    "好，资料我看完了。这个角色我建议按「冷面笑匠」的路子来：一个面无表情讲冷笑话的单口喜剧演员，"
    "人设反差感强，群里接话效果会很好。\n\n"
    "```json\n" + json.dumps(_MOCK_PROFILE, ensure_ascii=False, indent=2) + "\n```\n\n"
    "档案要点：语气一本正经地胡说八道；知识库两条（单口喜剧结构 + 冷面笑匠风格起源）。"
    "请在管理端【铸造】页确认后创建。"
)


def _extract_profile(text: str) -> dict | None:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("name"):
        return None
    return data


async def forge_chat(
    llm: LLMClient,
    history_lines: list[str],
    allow_search: bool,
    model: str | None = None,
) -> tuple[str, dict | None]:
    """执行一次铸造对话（含搜索回填循环）。返回 (最终回复文本, 档案dict 或 None)。"""
    if llm.mode == "mock":
        await asyncio.sleep(0)
        return _MOCK_REPLY, dict(_MOCK_PROFILE)

    history = "\n".join(history_lines[-60:]) if history_lines else "（还没有资料，先向真人了解需求）"
    messages = [
        {"role": "system", "content": FORGE_SYSTEM_PROMPT},
        {"role": "user", "content": f"铸造室对话记录（最新在最后）：\n{history}\n\n现在轮到你："},
    ]
    text = await asyncio.to_thread(
        llm._openai_chat,
        messages,
        model or settings.god_model or settings.llm_model,
        "forge",
        2000,  # 铸造要输出长 JSON 档案，max_tokens 给足（reasoner 也不易被截断）
    )
    for _ in range(3):  # 搜索回填，最多 3 轮
        m = re.search(r"^\s*搜索[:：]\s*(.+?)\s*$", text, re.M)
        if not m:
            break
        query = m.group(1).strip()
        text = text.replace(m.group(0), "")
        if not allow_search:
            text += "\n\n[系统提示：真人已禁用联网搜索，请基于已有资料直接完成档案]"
            continue
        try:
            results = await asyncio.to_thread(search.web_search, query)
        except Exception:
            results = "（搜索异常，请基于已有资料继续）"
        text += (
            f"\n\n[系统：已替你联网搜索「{query}」，结果如下：\n{results}\n"
            "请基于这些结果继续；资料足够就直接输出最终档案。]"
        )
    text = text.strip()
    if not text:
        text = "（铸造师暂时没有回应，请再说一次或补充资料）"
    return text, _extract_profile(text)

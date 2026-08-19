"""首次启动时播种 3 个示例人格 agent、演示知识库、默认群与铸造师。"""
from sqlalchemy.orm import Session

from . import rag
from .models import Agent, KnowledgeEntry, Room

SEED_AGENTS = [
    {
        "name": "毒舌老王",
        "aliases": ["老王", "毒舌"],
        "system_prompt": (
            "你是毒舌老王，一个说话尖酸刻薄但一针见血的影评人，年过四十，阅片无数。\n"
            "语气：阴阳怪气、爱用比喻讽刺，但观点经常犀利到让人无法反驳。口头禅「这片子我二刷都嫌多」。\n"
            "价值观：讨厌烂片、讨厌流量明星、讨厌剧透；对好电影会罕见地真诚夸奖。\n"
            "知识边界：电影、电视剧、导演、演员八卦。其他话题少聊，聊了也是毒舌风格。\n"
            "禁忌：不骂人带脏字，讽刺但不人身攻击。"
        ),
        "few_shot": [
            "用户: 最近有什么好电影推荐吗？\n老王: 好电影？你先告诉我你受得了多慢的节奏，免得我推荐《穆赫兰道》你骂我装逼。",
            "用户: 我觉得那部流量剧挺好看的。\n老王: 那玩意儿叫剧？那叫PPT配乐朗诵。",
        ],
        "trigger_keywords": ["电影", "剧", "烂片", "吐槽", "影评", "推荐"],
        "base_freq": 0.45,
        "base_cd": 20.0,
        "reply_len": "short",  # 毒舌，一句话怼完
    },
    {
        "name": "温柔小晴",
        "aliases": ["小晴"],
        "system_prompt": (
            "你是温柔小晴，一个共情力极强的知心朋友，说话轻声细语，永远先接住对方的情绪。\n"
            "语气：柔软、治愈，常用「嗯嗯」「我懂」「没关系的」。口头禅「你已经做得很好了」。\n"
            "价值观：先共情再建议，不评判不指责；鼓励人把情绪说出来。\n"
            "知识边界：情绪疏导、人际关系、生活小烦恼。被问到专业问题会坦诚说自己不太懂。\n"
            "禁忌：不说教、不灌鸡汤式敷衍、不打听隐私。"
        ),
        "few_shot": [
            "用户: 今天工作好累，感觉撑不下去了。\n小晴: 嗯嗯，我听出来了，你今天真的辛苦了。先别急着扛，跟我说说是什么事这么累呀？",
            "用户: 我和朋友吵架了。\n小晴: 我懂，和朋友吵架心里最难受了。你们之间发生了什么呀？",
        ],
        "trigger_keywords": ["累", "难受", "难过", "压力", "焦虑", "哭", "烦", "撑不下去"],
        "base_freq": 0.45,
        "base_cd": 20.0,
        "reply_len": "normal",  # 温柔体贴，适度展开
    },
    {
        "name": "技术极客阿睿",
        "aliases": ["阿睿", "极客", "程序员"],
        "system_prompt": (
            "你是技术极客阿睿，一个程序员，对新技术充满热情，聊到代码就停不下来。\n"
            "语气：语速快、爱用术语但会主动解释，动不动就想推荐工具/框架，偶尔讲冷幽默。口头禅「这个我熟」。\n"
            "价值观：推崇开源、简单方案优先、反对过度设计。\n"
            "知识边界：编程、AI、硬件、开源项目。生活话题会聊但很快绕回技术。\n"
            "禁忌：不写大段代码给别人（群里只说思路），不炫耀装逼。"
        ),
        "few_shot": [
            "用户: 想学编程从哪开始？\n阿睿: 这个我熟！先别急着选语言，你先想清楚想做什么：做网站就学Python/JS，做App就学Flutter……",
            "用户: AI最近好火啊。\n阿睿: 火得对！我这两天刚把本地跑了个小模型，说实话消费级显卡也能玩得转了。",
        ],
        "trigger_keywords": ["代码", "编程", "AI", "模型", "服务器", "电脑", "软件", "开源"],
        "base_freq": 0.5,
        "base_cd": 15.0,
        "reply_len": "long",  # 技术宅，爱展开讲
    },
]


SEED_KNOWLEDGE = {
    # 老王：电影知识
    "毒舌老王": [
        (
            "《肖申克的救赎》",
            "《肖申克的救赎》1994年上映，导演弗兰克·德拉邦特，主演蒂姆·罗宾斯、摩根·弗里曼。"
            "改编自斯蒂芬·金小说。豆瓣常年第一。IMDb第一。讲的是银行家安迪被冤枉入狱，用二十年挖通隧道重获自由。"
            "影评人视角：节奏慢但每一分钟都值，摩根·弗里曼的旁白是教科书级。",
        ),
        (
            "《穆赫兰道》",
            "《穆赫兰道》2001年，导演大卫·林奇。公认最难懂的电影之一，梦境与现实的拼图，"
            "看一遍看不懂是正常的，看懂了反而要怀疑自己。林奇的招牌是诡异氛围和开放式结局。",
        ),
        (
            "王家卫与《花样年华》",
            "《花样年华》2000年，导演王家卫，主演梁朝伟、张曼玉。东方含蓄美学的巅峰，"
            "旗袍、窄巷、雨夜，一段没有说出口的感情。梁朝伟靠它拿戛纳影帝。",
        ),
    ],
    # 小晴：情绪与关系
    "温柔小晴": [
        (
            "情绪急救：累的时候怎么办",
            "当你感到疲惫和撑不下去时：1）先承认情绪，不评判自己；2）把压力源拆小，只处理眼前一件小事；"
            "3）允许自己休息，休息不是偷懒；4）找一个信得过的人说出来，倾诉本身就有疗愈作用。",
        ),
        (
            "和朋友吵架之后",
            "吵架后的修复：先冷静，不要在气头上说狠话；等情绪平复后用'我感受'句式表达（'我当时觉得难过，是因为…'）；"
            "分清事实和感受；道歉要具体，不要'对不起行了吧'这种敷衍；关系比输赢重要。",
        ),
    ],
    # 阿睿：技术
    "技术极客阿睿": [
        (
            "RAG 是什么",
            "RAG（检索增强生成）：把文档切块、向量化存入知识库，回答问题时先检索最相关的片段，"
            "再喂给大模型生成答案。解决大模型不知道私有知识、容易幻觉的问题。流程：入库（切块+embedding）→ 检索（向量相似度）→ 生成（检索结果+问题拼进prompt）。",
        ),
        (
            "Python 还是 Node.js",
            "选型看场景：AI/数据分析/后端重逻辑 → Python；实时应用/全栈同语言/高并发IO → Node.js。"
            "现在很多项目前后端都用 TypeScript，一个人维护最省心。没有绝对答案，团队熟什么用什么。",
        ),
    ],
}


def seed_if_empty(db: Session, embed_fn=None):
    if db.query(Agent).count() > 0:
        return
    agents = [Agent(**a) for a in SEED_AGENTS]
    db.add_all(agents)
    db.flush()
    for a in agents:
        for title, content in SEED_KNOWLEDGE.get(a.name, []):
            if embed_fn is not None:
                rag.add_text(db, a.id, title, content, "内置演示数据", embed_fn)
            else:
                db.add(KnowledgeEntry(agent_id=a.id, title=title, content=content))
    db.add(Room(name="闲聊一号群", channel="web", agent_ids=[a.id for a in agents]))
    db.commit()


GOD_AGENT = {
    "name": "铸造师",
    "aliases": ["上帝", "铸造师", "造人", "铸师"],
    "system_prompt": (
        "你是铸造师，平台的灵魂角色：真人把想铸造的角色资料给你（粘贴/上传/口述），"
        "经真人允许后你也可以联网搜索资料，最终生成结构化角色档案供真人确认。"
        "完整铸造指令由系统提供。"
    ),
    "trigger_keywords": ["铸造", "角色", "人格", "造人", "档案", "人物"],
    "base_freq": 0.9,
    "base_cd": 10.0,
    "created_by": "god",
    "is_god": True,
}


def ensure_god(db: Session):
    """确保存在铸造师与铸造室（幂等，每次启动执行）。"""
    god_agent = db.query(Agent).filter(Agent.created_by == "god", Agent.is_god.is_(True)).first()
    if god_agent is None:
        # 老库兼容：找名字叫铸造师的，或直接新建
        god_agent = (
            db.query(Agent).filter(Agent.name == "铸造师").first()
            or Agent(**GOD_AGENT)
        )
        god_agent.is_god = True
        if god_agent.id is None:
            db.add(god_agent)
        db.flush()
    else:
        god_agent.is_god = True
    forge_room = db.query(Room).filter(Room.name == "铸造室").first()
    if forge_room is None:
        db.add(
            Room(
                name="铸造室",
                channel="web",
                agent_ids=[god_agent.id],
                allow_search=True,
            )
        )
    db.commit()


def restore_demo_agents(db: Session, embed_fn=None):
    """恢复 3 个示例人格 agent（用户误删后重建，维持原样）+ 确保闲聊群存在。"""
    created = []
    for data in SEED_AGENTS:
        if db.query(Agent).filter(Agent.name == data["name"]).first() is None:
            agent = Agent(**data)
            db.add(agent)
            db.flush()
            created.append(agent)
            for title, content in SEED_KNOWLEDGE.get(data["name"], []):
                if embed_fn is not None:
                    rag.add_text(db, agent.id, title, content, "内置演示数据", embed_fn)
                else:
                    db.add(KnowledgeEntry(agent_id=agent.id, title=title, content=content))
    if not created:
        db.commit()
        return []
    # 确保闲聊一号群存在，并把 demo agents 加入（顺带清理悬空的旧 id）
    room = db.query(Room).filter(Room.name == "闲聊一号群").first()
    if room is None:
        room = Room(name="闲聊一号群", channel="web", agent_ids=[])
        db.add(room)
        db.flush()
    ids = [i for i in (room.agent_ids or []) if db.get(Agent, i) is not None]
    for a in created:
        if a.id not in ids:
            ids.append(a.id)
    room.agent_ids = ids
    db.commit()
    return created

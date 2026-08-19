# 多人格群聊平台（阶段 0）

真人和多个"不同人格的 agent"在同一个群里聊天。每个 agent 有独立的人格提示词、
触发规则和发言参数；agent 之间可以互相交流。设计文档见 [DESIGN.md](DESIGN.md)。

## 快速开始

```powershell
# 1. 创建虚拟环境并安装依赖（已装过可跳过）
uv venv --python "C:\Users\31244\miniconda3\python.exe" .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt

# 2. 启动（默认 mock 模式，无需 API Key）
.venv\Scripts\python run.py

# 3. 打开
#    聊天室: http://127.0.0.1:8000/
#    管理端: http://127.0.0.1:8000/admin.html
```

## 公网部署防护（访问密码）

上传公网前，在 `.env` 设置访问密码：

```
ACCESS_PASSWORD=你的强密码
ACCESS_SECRET=随意一串随机字符   # 可选，token 签名用
SESSION_DAYS=7                   # 登录有效期（天）
```

- 设置后，访问任何页面都需登录（页面跳登录页，API 返回 401）
- 登录状态为 HMAC 签名 Cookie（HttpOnly），7 天有效
- 同 IP 密码错误 5 次锁定 1 分钟
- **留空则不启用**，本地开发不受影响
- 公网建议：用 Nginx/Caddy 反代并配 HTTPS，效果更佳

> 注意：登录只防"访问者"，不代表完整安全加固。公网暴露请同时考虑
> 反代限速、HTTPS、日志监控等。

## 服务器部署（Ubuntu/Debian，2G 2核 足够）

**资源占用极轻**：内存约 200MB，CPU 几乎无压力（LLM 推理在 DeepSeek 远程，
本地只有请求处理）。**必须单 worker**（SQLite 不支持多进程写）。

```bash
# 1. 上传项目到服务器（本地执行）
scp -r . 用户名@服务器IP:/opt/wechatplugin

# 2. 服务器上执行部署脚本（端口与已有项目冲突就换一个，如 8123）
sudo bash /opt/wechatplugin/deploy/setup.sh 8123 /opt/wechatplugin

# 3. 首次运行会提示编辑 .env，填 DeepSeek key + 访问密码后重跑
sudo nano /opt/wechatplugin/.env
sudo bash /opt/wechatplugin/deploy/setup.sh 8123 /opt/wechatplugin
```

- 服务名 `wechatplugin`，systemd 托管：开机自启、崩溃自动重启、内存上限 1.5G（保护整机）
- 访问：`http://服务器IP:8123`（仅 IP 无域名，建议用密码防护；上 HTTPS 需域名）
- 常用命令：`journalctl -u wechatplugin -f`（看日志）/ `systemctl restart wechatplugin`
- **备份 = 拷两个东西**：`chat.db` + `app/static/avatars/` 目录

> 与已有项目共存：脚本会自动检查端口占用，冲突就换端口；两个服务互不干扰。

## 接入 DeepSeek（推荐）

```
LLM_MODE=openai
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-你的key
LLM_MODEL=deepseek-chat
GOD_MODEL=deepseek-reasoner      # 铸造师可选更强推理模型
```

**关于向量化**：DeepSeek 没有 embedding 接口。默认 `EMBED_MODE=auto` 会
自动使用**本地字符向量**（离线可用，无需额外 key）；若想用更高质量的向量，
可单独配一个 embedding 服务（OpenAI / 硅基流动等）：

```
EMBED_MODE=api
EMBED_BASE_URL=https://api.openai.com/v1
EMBED_API_KEY=sk-xxx
EMBED_MODEL=text-embedding-3-small
```

> 也可以保持 `LLM_MODE=mock` 不填任何 key，纯本地演示全流程。

## 已实现

- ✅ 管理端：Agent CRUD（人格 prompt / 别名 / 触发词 / 话痨度 / 冷却 / 专属模型）
- ✅ 群组管理：创建群、勾选启用哪些 agent、暂停状态
- ✅ 聊天室：真人 + 多 agent 同群聊天、正在输入提示、静默计数显示
- ✅ **真人控制**：⏸ 暂停/恢复 agent 发言；✕ 结束对话（取消待发言、清空 agent 记忆与冷却，新话题即刻开始）
- ✅ **RAG 知识库**（每 agent 独立）：txt/md 上传或粘贴文本，自动分块+向量化；回答时检索注入；管理端可测试检索命中
  - openai 模式走 `/embeddings`；mock 模式用本地字符 bigram 向量（离线可用）
- ✅ **铸造师（上帝 agent）**：聊天室【铸造室】对话铸造。投料方式：粘贴资料 / 📎 上传 txt、md 文件 / 口述需求；
  - 经真人允许（群设置勾选）可**自行联网搜索**角色资料（Bing/DDG，无需 API Key，失败自动降级）
  - 生成**结构化角色档案**（人格 prompt + 语气示例 + 触发词 + 知识库条目 + 建议参数）
  - 档案进入管理端【铸造】页**待确认**，真人可预览/修改后再"确认创建"或拒绝——创建前必确认
- ✅ **头像**：每个 agent 可上传图片或填 URL 设头像，聊天室消息与管理端都显示；名字、别名随时可改
- ✅ **监控面板**（管理端【监控】tab）：
  - 概览：消息数 / agent 发言数 / LLM 调用次数 / token 总量 / 👍👎 反馈数
  - LLM 调用统计（按用途/模型聚合：chat / forge / score / embed）
  - **发言决策日志**：每次 agent 发言的完整链路（触发消息、浓度、概率、是否点名、知识命中、最终回复），点开即复盘
  - openai 模式取真实 usage，mock 模式估算
- ✅ **反馈闭环**：聊天室每条 agent 消息可 👍👎，计入监控统计
- ✅ 会话持久化：agent 上下文从消息记录重建，重启不丢
- ✅ 发言算法 v1（见 DESIGN.md §3）：
  - 强触发（@名字/别名/触发词）必答；否则按浓度×话痨度×抑制概率接话
  - **话题浓度混合计算**：embedding 粗筛（消息 vs 知识库/人格向量）+ 边界情况 LLM 校准
    （0.25~0.75 中间地带才打分，省 ~60% 调用；mock 模式启发式兜底）
  - 随机延迟 2~8s 错开、冷却时间、高浓度抢答后其他 agent 降频
  - 连续 3 条非真人消息后 agent 暂停，真人 @ 名字可突破并重置
  - **浓度记录在消息上**，管理端【群组】页有浓度复盘表（颜色标注高低）
- ✅ 每个 agent 独立滚动上下文（最近 20 条），互相隔离

## 阶段计划（见 DESIGN.md §8）

阶段 4：微信插件（wechaty 独立进程）——已暂缓，网页端优先

## 铸造师用法（3 步）

1. 聊天室顶部选【铸造室】→ 粘贴角色资料、点 📎 上传文件，或直接描述需求（"我想铸造一个毒舌的武侠小说家"）
2. 铸造师会追问/联网搜索（可在群设置里关掉联网权限）→ 生成档案并提示确认
3. 管理端【铸造】页 → 预览/修改 → **确认创建**（自动生成 agent + 知识库）→ Agents 页改名、设头像

## 项目结构

```
app/
├── main.py       # FastAPI 入口
├── config.py     # 环境配置
├── database.py   # SQLAlchemy（SQLite，可换 Postgres）
├── models.py     # Agent / Room / Message
├── schemas.py    # Pydantic 模型
├── llm.py        # LLM 客户端（OpenAI 兼容 + mock）
├── engine.py     # 发言引擎（触发/浓度/概率/冷却/延迟/静默/抑制）
├── api.py        # REST 路由
├── seed.py       # 3 个示例人格
└── static/       # 前端（聊天室 + 管理端，vanilla JS）
```

"""群聊发言引擎 — 实现 DESIGN.md §3 的发言算法 v1。

每条消息广播给房间内所有启用 agent，各自独立决策是否接话：
  强触发(名字/别名/关键词) → 高概率必答；否则按 浓度×话痨度×抑制 的概率接话。
  浓度高 → 更主动、冷却更短、先开口；被高浓度抢答 → 其他 agent 降频。
  连续非真人消息 ≥ SILENCE_LIMIT → 概率接话暂停，真人 @ 名字可突破并重置计数。

真人控制：
  暂停(pause) → 该群 agent 全部闭嘴，直到真人恢复。
  结束对话(close) → 取消所有待发言任务、清空上下文与静默计数，彻底切断当前话题。
"""
import asyncio
import random
import time
from collections import deque

from . import god, rag
from .config import settings
from .database import session_factory
from .llm import LLMClient, char_bigram_vec
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
from .settings_store import RuntimeSettings


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class ChatEngine:
    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.rt = RuntimeSettings()  # 运行时可调参数（管理端设置页）
        self.cooldowns: dict[int, float] = {}  # agent_id -> 下次可发言的时间戳
        self.silence: dict[int, int] = {}  # room_id -> 连续非真人消息数
        self.contexts: dict[tuple[int, int], deque] = {}  # (room_id, agent_id) -> 滚动窗口
        self.pending: dict[int, set[int]] = {}  # room_id -> 正在思考(延迟中)的 agent
        self.tasks: dict[int, set[asyncio.Task]] = {}  # room_id -> 待发言任务（可取消）
        self.paused: set[int] = set()  # 被真人暂停的 room_id
        self.last_claim_conc: dict[int, float] = {}  # room_id -> 最后一条 agent 回复的浓度
        self._vec_cache: dict[tuple[int, str], list] = {}  # (agent_id, prompt) -> 人格向量
        self._locks: dict[int, asyncio.Lock] = {}  # room_id -> 提交锁（防并发超静默上限）

    def _p(self, name: str, default):
        """运行时可调参数：DB 设置优先，.env 默认值兜底。"""
        return self.rt.get(name, default)

    # ---------- 真人控制 ----------
    def usage_sink(self, purpose: str, model: str, prompt_tokens: int, completion_tokens: int):
        """LLM 用量落库（由 llm.set_usage_sink 注入）。"""
        try:
            with session_factory() as db:
                db.add(
                    LLMCall(
                        ts=time.time(),
                        purpose=purpose,
                        model=model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                )
                db.commit()
        except Exception:
            pass  # 统计失败不影响聊天

    def set_paused(self, room_id: int, paused: bool):
        if paused:
            self.paused.add(room_id)
        else:
            self.paused.discard(room_id)

    def close_conversation(self, room_id: int):
        """结束当前对话：取消待发言任务、清空上下文/静默/抢占/冷却。"""
        for t in list(self.tasks.get(room_id, set())):
            t.cancel()
        self.tasks.pop(room_id, None)
        self.pending.pop(room_id, None)
        for key in [k for k in self.contexts if k[0] == room_id]:
            del self.contexts[key]
        self.silence.pop(room_id, None)
        self.last_claim_conc.pop(room_id, None)
        with session_factory() as db:
            room = db.get(Room, room_id)
            if room is not None:
                for aid in room.agent_ids or []:
                    self.cooldowns.pop(aid, None)

    def clear_room(self, room_id: int):
        """清空聊天记录（彻底删除记忆）：取消任务 + 清内存 + 删 DB 消息/决策日志。"""
        for t in list(self.tasks.get(room_id, set())):
            t.cancel()
        self.tasks.pop(room_id, None)
        self.pending.pop(room_id, None)
        for key in [k for k in self.contexts if k[0] == room_id]:
            del self.contexts[key]
        self.silence.pop(room_id, None)
        self.last_claim_conc.pop(room_id, None)
        with session_factory() as db:
            db.query(Message).filter(Message.room_id == room_id).delete()
            db.query(DecisionLog).filter(DecisionLog.room_id == room_id).delete()
            room = db.get(Room, room_id)
            if room is not None:
                for aid in room.agent_ids or []:
                    self.cooldowns.pop(aid, None)
            db.commit()

    def load_paused(self):
        """启动时从 DB 恢复暂停状态。"""
        with session_factory() as db:
            for rid, in db.query(Room.id).filter(Room.paused.is_(True)).all():
                self.paused.add(rid)

    # ---------- 对外 ----------
    async def on_message(
        self,
        room_id: int,
        sender: str,
        sender_type: str,
        content: str,
        message_id: int | None = None,
        quoted_text: str = "",
    ):
        """广播一条消息给房间内所有 agent；并行计算各 agent 的话题浓度并记录。

        quoted_text：引用的历史消息（"老王：xxx"），会并入上下文与浓度判断，
        被引用的 agent 会优先回应。
        """
        combined = f"{content}（引用了{quoted_text}的话）" if quoted_text else content
        with session_factory() as db:
            room = db.get(Room, room_id)
            if room is None:
                return
            agents = [db.get(Agent, aid) for aid in (room.agent_ids or [])]
            agents = [a for a in agents if a is not None]

        self._record(room_id, sender, sender_type, agents, combined)
        # 并行算浓度（embedding 粗筛，边界情况才调 LLM 打分）
        concs = await asyncio.gather(*(self._concentration(combined, a) for a in agents))
        score_map = {str(a.id): round(c, 3) for a, c in zip(agents, concs)}
        if message_id:
            with session_factory() as db:
                m = db.get(Message, message_id)
                if m is not None:
                    m.concentration_scores = score_map
                    db.commit()
        if room_id in self.paused:
            return  # 暂停中：记录消息但不调度发言
        for a, c in zip(agents, concs):
            self._schedule_reply(room_id, a, sender, sender_type, combined, c)

    def _schedule_reply(self, room_id: int, agent: Agent, sender: str, sender_type: str, content: str, conc: float):
        """调度一个 agent 的回复决策，并跟踪任务（可取消/可看异常）。"""
        task = asyncio.create_task(
            self._maybe_reply(room_id, agent.id, sender, sender_type, content, conc=conc)
        )
        self.tasks.setdefault(room_id, set()).add(task)

        def _done(t: asyncio.Task):
            self.tasks.get(room_id, set()).discard(t)
            if not t.cancelled() and t.exception():
                import logging

                logging.getLogger("engine").exception("agent 回复任务异常", exc_info=t.exception())

        task.add_done_callback(_done)

    def state(self, room_id: int) -> dict:
        return {
            "silence_count": self.silence.get(room_id, 0),
            "silence_limit": self._p("silence_limit", settings.silence_limit),
            "paused": room_id in self.paused,
            "pending": sorted(self.pending.get(room_id, set())),
            "cooldowns": {str(k): round(v, 1) for k, v in self.cooldowns.items()},
            "llm_mode": self.llm.mode,
            "llm_model": self.llm.default_model,
        }

    # ---------- 内部 ----------
    def _record(self, room_id: int, sender: str, sender_type: str, agents: list[Agent], content: str = ""):
        """更新静默计数与所有 agent 的滚动上下文。"""
        if sender_type == "human":
            self.silence[room_id] = 0
        else:
            self.silence[room_id] = self.silence.get(room_id, 0) + 1
        line = f"{sender}: {content}"
        for a in agents:
            self._context(room_id, a.id, maxlen=self._ctx_len(a)).append(line)

    def _ctx_len(self, agent: Agent) -> int:
        """铸造师本体需要更长的铸造对话上下文。"""
        return max(self._p("context_n", settings.context_n), 60) if agent.is_god else self._p("context_n", settings.context_n)

    def _context(self, room_id: int, agent_id: int, maxlen: int | None = None) -> deque:
        """取滚动窗口；为空时从 DB 重建（会话持久化，重启不丢）。"""
        key = (room_id, agent_id)
        if key not in self.contexts:
            self.contexts[key] = deque(maxlen=maxlen or self._p("context_n", settings.context_n))
            with session_factory() as db:
                rows = (
                    db.query(Message)
                    .filter(Message.room_id == room_id)
                    .order_by(Message.id.desc())
                    .limit(maxlen or self._p("context_n", settings.context_n))
                    .all()
                )
            for m in reversed(rows):
                self.contexts[key].append(f"{m.sender}: {m.content}")
        return self.contexts[key]

    def _mentioned(self, content: str, agent: Agent) -> bool:
        names = [agent.name] + list(agent.aliases or []) + list(agent.trigger_keywords or [])
        return any(n and n.strip() and n.strip() in content for n in names)

    # ---------- 话题浓度：embedding 粗筛 + LLM 校准（DESIGN.md §3.3） ----------
    async def _concentration(self, content: str, agent: Agent) -> float:
        """混合计算 c(A, m) ∈ [0,1]。

        1) 名字/别名点名 → 0.9（强触发的自然延伸）
        2) embedding 粗筛 sim（vs 知识库条目向量，无库则 vs 人格向量）
        3) sim < CONC_LOW → c=sim；sim > CONC_HIGH → c=sim；中间 → LLM 打分
           （mock/打分失败 → 启发式 0.35+0.55*sim）
        4) 触发关键词命中 → 小幅兜底提升
        """
        names = [agent.name] + list(agent.aliases or [])
        if any(n and n.strip() and n.strip() in content for n in names):
            return 0.9

        sim = await self._embed_sim(content, agent)
        if sim < self._p("conc_low", settings.conc_low):
            c = sim
        elif sim > self._p("conc_high", settings.conc_high):
            c = sim
        else:
            score = await self.llm.relevance_score(self._persona_line(agent), content)
            c = (0.35 + 0.55 * sim) if score is None else score

        kws = [k for k in (agent.trigger_keywords or []) if k and k.strip()]
        hits = sum(1 for k in kws if k in content)
        if hits:
            c = max(c, min(0.85, 0.5 + 0.12 * hits))
        # 闲聊基线：非点名时也保留参与闲聊的意愿（CONC_MIN），避免群里死寂
        return clamp(max(c, self._p("conc_min", settings.conc_min)), 0.02, 0.98)

    async def _embed_sim(self, content: str, agent: Agent) -> float:
        """消息向量 vs 知识库条目向量的最大相似度；无库/低相关时并入人格向量。"""
        qv = await asyncio.to_thread(self.llm.embed, content)
        best = 0.0
        with session_factory() as db:
            rows = (
                db.query(KnowledgeEntry)
                .filter(KnowledgeEntry.agent_id == agent.id)
                .all()
            )
        for r in rows:
            s = rag.cosine(qv, r.embedding or [])
            if s > best:
                best = s
        pv = await asyncio.to_thread(self._persona_vec, agent)
        if pv:
            best = max(best, rag.cosine(qv, pv))
        return best

    def _persona_line(self, agent: Agent) -> str:
        """给打分 LLM 的一句话人设摘要。"""
        first = (agent.system_prompt or "").strip().splitlines()[0] if agent.system_prompt.strip() else ""
        if first.startswith("你是"):
            first = first[2:]
        return f"{agent.name}（{first[:40]}）"

    def _persona_vec(self, agent: Agent) -> list | None:
        """人格向量（懒计算 + 按 prompt 缓存，prompt 改了自动失效）。"""
        prompt = (agent.system_prompt or agent.name or "").strip()
        if not prompt:
            return None
        key = (agent.id, prompt)
        if key not in self._vec_cache:
            self._vec_cache[key] = self.llm.embed(prompt)
        return self._vec_cache[key]

    def _suppress(self, room_id: int) -> float:
        """上一条 agent 回复是高浓度(≥阈值) → 其他 agent 降频。"""
        last = self.last_claim_conc.get(room_id, 0.0)
        return (
            self._p("suppress_factor", settings.suppress_factor)
            if last >= self._p("high_conc_threshold", settings.high_conc_threshold)
            else 1.0
        )

    async def _maybe_reply(
        self,
        room_id: int,
        agent_id: int,
        sender: str,
        sender_type: str,
        content: str,
        conc: float = 0.25,
    ):
        with session_factory() as db:
            agent = db.get(Agent, agent_id)
            room = db.get(Room, room_id)
            if agent is None:
                return
            allow_search = bool(room is not None and room.allow_search)

        now = time.time()
        if now < self.cooldowns.get(agent_id, 0.0):
            return
        if room_id in self.paused:
            return

        silent = self.silence.get(room_id, 0) >= self._p("silence_limit", settings.silence_limit)
        mentioned = self._mentioned(content, agent)
        was_human_mention = mentioned and sender_type == "human"
        # 静默期：仅真人 @ 名字可接话
        if silent and not was_human_mention:
            return

        c = conc
        p = agent.base_freq * (
            self._p("prob_base", settings.prob_base) + self._p("prob_factor", settings.prob_factor) * c
        ) * self._suppress(room_id)
        p = clamp(p, 0.02, 0.98)
        if mentioned:
            p = max(p, 0.95)  # 强触发 → 必答候选
        if random.random() > p:
            return

        delay = random.uniform(
            self._p("delay_min", settings.delay_min), self._p("delay_max", settings.delay_max)
        ) * (1 - 0.5 * c)
        self.pending.setdefault(room_id, set()).add(agent_id)
        try:
            await asyncio.sleep(delay)

            # ===== 预检（锁内）：冷却 / 暂停 / 静默——第一道闸 =====
            async with self._lock(room_id):
                if time.time() < self.cooldowns.get(agent_id, 0.0):
                    return
                if room_id in self.paused:
                    return
                if self.silence.get(room_id, 0) >= self._p("silence_limit", settings.silence_limit) and not was_human_mention:
                    return

            # ===== 锁外：生成回复（耗时调用不阻塞其他 agent 的提交）=====
            ctx = list(self.contexts.get((room_id, agent_id), deque()))
            if agent.is_god:
                # 铸造师本体：专用铸造对话（含搜索回填 + 档案提取）
                reply, draft = await god.forge_chat(
                    self.llm, ctx, allow_search, model=agent.model or None
                )
                reply = (reply or "").strip()
                if draft:
                    db_draft = DraftAgent(
                        name=draft.get("name", "未命名"),
                        aliases=draft.get("aliases", []),
                        system_prompt=draft.get("system_prompt", ""),
                        few_shot=draft.get("few_shot", []),
                        trigger_keywords=draft.get("trigger_keywords", []),
                        knowledge_entries=draft.get("knowledge_entries", []),
                        suggested_params=draft.get("suggested_params", {}),
                        status="pending",
                    )
                    with session_factory() as db:
                        db.add(db_draft)
                        db.commit()
                    reply += "\n\n（📋 已生成角色档案草稿，请在管理端【铸造】页确认后创建）"
                if not reply:
                    return
            else:
                # 用正要回复的那条消息检索本 agent 的知识库（DESIGN.md §3.5）
                with session_factory() as db:
                    results = rag.retrieve(db, agent.id, content, self.llm.embed)
                    kb = rag.format_block(results)
                    hits = [{"title": r.title, "score": round(s, 3)} for r, s in results]
                reply = (
                    await self.llm.chat(
                        system_prompt=agent.system_prompt,
                        few_shot=agent.few_shot or [],
                        context_lines=ctx,
                        knowledge=kb,
                        model=agent.model or None,
                        reply_len=agent.reply_len or "normal",
                        last_own=self._last_own_message(room_id, agent),
                        memory=agent.memory or "",
                    )
                    or ""
                ).strip()
                if not reply:
                    return
                # 防复读：与自己的上一条太像就不发
                if self._is_repeat(room_id, agent, reply):
                    return
                # 长期记忆：从对话中提取值得记住的新事实（自动积累经历）
                await self._update_memory(agent, ctx)

            # ===== 提交（锁内终检 + 原子计数/落库）：杜绝并发超限 =====
            async with self._lock(room_id):
                if time.time() < self.cooldowns.get(agent_id, 0.0):
                    return
                if self.silence.get(room_id, 0) >= self._p("silence_limit", settings.silence_limit) and not was_human_mention:
                    return
                with session_factory() as db:
                    db.add(
                        Message(
                            room_id=room_id,
                            sender=agent.name,
                            sender_type="agent",
                            content=reply,
                            ts=time.time(),
                        )
                    )
                    db.add(
                        DecisionLog(
                            room_id=room_id,
                            agent_id=agent.id,
                            agent_name=agent.name,
                            trigger_content=content,
                            concentration=round(c, 3),
                            probability=round(p, 3),
                            mentioned=mentioned,
                            knowledge_hits=hits if not agent.is_god else [],
                            reply=reply,
                            ts=time.time(),
                        )
                    )
                    db.commit()
                    # 自己的回复进入自己的记忆（避免下一轮重复自己刚说的话）
                    self._context(room_id, agent_id, maxlen=self._ctx_len(agent)).append(
                        f"{agent.name}: {reply}"
                    )
                    # 其他 agent 看到这条回复，并触发他们对这条回复的新一轮决策（连续互动）
                    room = db.get(Room, room_id)
                    others = [
                        db.get(Agent, aid)
                        for aid in (room.agent_ids or [])
                        if aid != agent_id and db.get(Agent, aid) is not None
                    ]
                # 静默计数在锁内原子 +1（_broadcast 不再负责）
                self.silence[room_id] = self.silence.get(room_id, 0) + 1
                self.last_claim_conc[room_id] = c
            # 锁外：广播给其他 agent（含浓度计算，可能调 LLM 打分）
            await self._broadcast_agent_reply(room_id, agent.name, reply, others)
        except asyncio.CancelledError:
            return  # 真人"结束对话"取消
        finally:
            self.pending.setdefault(room_id, set()).discard(agent_id)
            self.cooldowns[agent_id] = time.time() + clamp(
                agent.base_cd / (0.4 + c),
                self._p("cooldown_min", settings.cooldown_min),
                self._p("cooldown_max", settings.cooldown_max),
            )

    def _last_own_message(self, room_id: int, agent: Agent) -> str:
        """该 agent 最近说过的一句话（用于生成前注入防语义复读）。"""
        ctx = self.contexts.get((room_id, agent.id))
        if not ctx:
            return ""
        prefix = f"{agent.name}:"
        for line in reversed(ctx):
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""

    def _is_repeat(self, room_id: int, agent: Agent, reply: str) -> bool:
        """防复读：新回复与"自己刚说的上一条"相似度过高 → 不发（复读被拦，补充放行）。

        用本地字符向量比对（零成本），只对较长的回复检查。
        """
        if len(reply.strip()) <= 6:
            return False
        ctx = self.contexts.get((room_id, agent.id))
        if not ctx:
            return False
        last = None
        prefix = f"{agent.name}:"
        for line in reversed(ctx):
            if line.startswith(prefix):
                last = line[len(prefix):].strip()
                break
        if not last or len(last) <= 6:
            return False
        a = char_bigram_vec(reply)
        b = char_bigram_vec(last)
        return rag.cosine(a, b) > self._p("repeat_threshold", settings.repeat_threshold)

    async def _update_memory(self, agent: Agent, ctx: list[str]):
        """长期记忆：从对话提取新事实 → 更新 agent.memory；超长自动压缩；变更存快照。"""
        if not self._p("memory_enabled", settings.memory_enabled):
            return
        try:
            with session_factory() as db:
                a = db.get(Agent, agent.id)
                if a is None:
                    return
                persona = f"{a.name}：{a.system_prompt[:80]}"
                mem = (a.memory or "").strip()
                new_items = await self.llm.extract_memory(persona, mem, ctx)
                lines = [l.strip() for l in mem.splitlines() if l.strip()] if mem else []
                date = time.strftime("%Y-%m-%d")
                added = []
                for item in new_items:
                    entry = item if item.startswith("-") else f"- {date}：{item}"
                    if entry not in lines:
                        lines.append(entry)
                        added.append(entry)
                if not added:
                    return
                # 超长自动压缩（淡化/删除太久远或不重要的记忆）
                if len(lines) > self._p("memory_max_items", settings.memory_max_items) or sum(
                    len(l) for l in lines
                ) > self._p("memory_max_chars", settings.memory_max_chars):
                    compacted = await self.llm.compact_memory("\n".join(lines))
                    final = compacted if compacted else "\n".join(lines)
                else:
                    final = "\n".join(lines)
                a.memory = final
                db.add(
                    PromptVersion(
                        agent_id=a.id,
                        ts=time.time(),
                        system_prompt=a.system_prompt,
                        memory=final,
                    )
                )
                db.commit()
        except Exception:
            pass  # 记忆失败不影响聊天

    def _lock(self, room_id: int) -> asyncio.Lock:
        if room_id not in self._locks:
            self._locks[room_id] = asyncio.Lock()
        return self._locks[room_id]

    async def _broadcast_agent_reply(self, room_id: int, sender: str, content: str, agents: list[Agent]):
        """agent 回复进入其他 agent 上下文，并触发他们对这条回复的新一轮决策（连续互动）。

        防回声护栏：静默计数已在提交锁内原子 +1；这里只更新记忆 + 调度。
        """
        line = f"{sender}: {content}"
        for a in agents:
            self._context(room_id, a.id, maxlen=self._ctx_len(a)).append(line)
        if room_id in self.paused or not agents:
            return
        concs = await asyncio.gather(*(self._concentration(content, a) for a in agents))
        for a, c in zip(agents, concs):
            self._schedule_reply(room_id, a, sender, "agent", content, c)

"""RAG 知识库：分块、向量化、检索（每 agent 独立）。"""
import math
import re

from sqlalchemy.orm import Session

from .models import KnowledgeEntry

CHUNK_SIZE = 500  # 每块字符数
CHUNK_OVERLAP = 80  # 块间重叠


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按段落分块，超长段落单独切分，块间带重叠。"""
    paras = [p.strip() for p in re.split(r"\n+", (text or "")) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(p) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            for i in range(0, len(p), size - overlap):
                chunks.append(p[i : i + size])
            continue
        if cur and len(cur) + len(p) + 1 > size:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}".strip() if cur else p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def add_text(
    db: Session,
    agent_id: int,
    title: str,
    content: str,
    source: str,
    embed_fn,
) -> list[KnowledgeEntry]:
    """分块并入库（每块一行，带向量）。返回创建的条目。"""
    chunks = chunk_text(content)
    created = []
    base = title or "未命名"
    for i, chunk in enumerate(chunks):
        entry = KnowledgeEntry(
            agent_id=agent_id,
            title=base if len(chunks) == 1 else f"{base} · 第{i + 1}段",
            content=chunk,
            source=source,
            embedding=embed_fn(chunk),
        )
        db.add(entry)
        created.append(entry)
    return created


def retrieve(
    db: Session,
    agent_id: int,
    query: str,
    embed_fn,
    k: int = 3,
    min_score: float = 0.05,
) -> list[tuple[KnowledgeEntry, float]]:
    """检索该 agent 知识库中与 query 最相关的 k 块。"""
    rows = db.query(KnowledgeEntry).filter(KnowledgeEntry.agent_id == agent_id).all()
    if not rows:
        return []
    qv = embed_fn(query)
    scored = [(r, cosine(qv, r.embedding or [])) for r in rows]
    scored.sort(key=lambda t: t[1], reverse=True)
    return [(r, s) for r, s in scored[:k] if s >= min_score]


def format_block(results: list[tuple[KnowledgeEntry, float]]) -> str:
    """检索结果 → 注入 prompt 的知识块。"""
    if not results:
        return ""
    parts = []
    for i, (r, s) in enumerate(results, 1):
        parts.append(f"[{i}] {r.title}\n{r.content[:300]}")
    return "\n\n".join(parts)

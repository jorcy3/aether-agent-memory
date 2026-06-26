from datetime import timedelta

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState
from aether_agent_memory.core.memory import Memory, RecalledMemory
from aether_agent_memory.interfaces.embedding import EmbeddingClient
from aether_agent_memory.mocks._base import BaseMockMemoryManager, cosine_similarity


class MockSemanticMemoryManager(BaseMockMemoryManager):
    def __init__(
        self,
        embedder: EmbeddingClient,
        default_ttl: timedelta | None = None,
    ) -> None:
        super().__init__(default_ttl=default_ttl)
        self._embedder = embedder

    async def write(self, memory: Memory) -> Memory:
        if memory.embedding is None:
            memory.embedding = await self._embedder.embed_one(memory.content)
        return await super().write(memory)

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        query_vec = await self._embedder.embed_one(request.query)
        candidates = [
            m
            for m in self._store.values()
            if m.agent_id == request.agent_id and m.state == MemoryState.ACTIVE
        ]
        scored: list[tuple[float, RecalledMemory]] = []
        for m in candidates:
            score = cosine_similarity(query_vec, m.embedding or [])
            scored.append((score, RecalledMemory(memory=m, score=score)))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        limit = request.max_candidates
        return [pair[1] for pair in scored[:limit]]

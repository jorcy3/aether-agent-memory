from datetime import UTC, datetime

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState
from aether_agent_memory.core.memory import RecalledMemory
from aether_agent_memory.mocks._base import BaseMockMemoryManager


class MockWorkingMemoryManager(BaseMockMemoryManager):
    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        now = datetime.now(UTC)
        candidates = [
            m
            for m in self._store.values()
            if m.state == MemoryState.ACTIVE and m.session_id == request.session_id
        ]
        scored: list[tuple[float, RecalledMemory]] = []
        for m in candidates:
            age = now - m.updated_at
            age_seconds = max(age.total_seconds(), 0.0)
            recency = 1.0 / (1.0 + age_seconds / 60.0)
            score = m.importance * recency
            scored.append((score, RecalledMemory(memory=m, score=score)))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        limit = request.max_candidates
        return [pair[1] for pair in scored[:limit]]

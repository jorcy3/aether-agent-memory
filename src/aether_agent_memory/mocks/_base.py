import math
from datetime import UTC, datetime, timedelta

from aether_agent_memory.context.models import ContextRequest
from aether_agent_memory.core.enums import MemoryState
from aether_agent_memory.core.exceptions import MemoryNotFoundError
from aether_agent_memory.core.memory import Memory, RecalledMemory
from aether_agent_memory.lifecycle.decay import is_ttl_expired
from aether_agent_memory.lifecycle.state import transition


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class BaseMockMemoryManager:
    def __init__(self, default_ttl: timedelta | None = None) -> None:
        self._store: dict[str, Memory] = {}
        self._default_ttl = default_ttl

    async def write(self, memory: Memory) -> Memory:
        if memory.expires_at is None and self._default_ttl is not None:
            memory.expires_at = datetime.now(UTC) + self._default_ttl
        self._store[memory.id] = memory
        return memory

    async def get(self, memory_id: str) -> Memory | None:
        m = self._store.get(memory_id)
        if m is not None:
            m.touch()
        return m

    async def query(
        self,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        state: MemoryState | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[Memory]:
        results: list[Memory] = []
        for m in self._store.values():
            if session_id is not None and m.session_id != session_id:
                continue
            if agent_id is not None and m.agent_id != agent_id:
                continue
            if user_id is not None and m.user_id != user_id:
                continue
            if state is not None and m.state != state:
                continue
            if tags is not None and not set(tags) & set(m.tags):
                continue
            results.append(m)
        return results[:limit]

    async def update_state(self, memory_id: str, new_state: MemoryState) -> Memory:
        m = self._store.get(memory_id)
        if m is None:
            raise MemoryNotFoundError(memory_id)
        transition(m.state, new_state)
        m.state = new_state
        m.updated_at = datetime.now(UTC)
        return m

    async def delete(self, memory_id: str) -> bool:
        return self._store.pop(memory_id, None) is not None

    async def expire_stale(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        count = 0
        for m in self._store.values():
            if m.state == MemoryState.ACTIVE and is_ttl_expired(m, now=current):
                m.state = MemoryState.EXPIRED
                m.updated_at = current
                count += 1
        return count

    async def recall(self, request: ContextRequest) -> list[RecalledMemory]:
        raise NotImplementedError

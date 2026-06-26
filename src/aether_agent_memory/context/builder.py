from datetime import UTC, datetime

from aether_agent_memory.context.models import ContextPack, ContextRequest
from aether_agent_memory.core.enums import MemoryType
from aether_agent_memory.core.memory import RecalledMemory
from aether_agent_memory.episodic.manager import MockEpisodicMemoryManager
from aether_agent_memory.interfaces.managers import MemoryManager
from aether_agent_memory.semantic.manager import MockSemanticMemoryManager
from aether_agent_memory.working.manager import MockWorkingMemoryManager

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(len(text) // _CHARS_PER_TOKEN, 1)


class MockContextPackBuilder:
    def __init__(
        self,
        working: MockWorkingMemoryManager,
        episodic: MockEpisodicMemoryManager,
        semantic: MockSemanticMemoryManager,
    ) -> None:
        self._managers: dict[MemoryType, MemoryManager] = {
            MemoryType.WORKING: working,
            MemoryType.EPISODIC: episodic,
            MemoryType.SEMANTIC: semantic,
        }

    async def build(self, request: ContextRequest) -> ContextPack:
        merged: list[RecalledMemory] = []
        for memory_type in request.memory_types:
            mgr = self._managers.get(memory_type)
            if mgr is not None:
                merged.extend(await mgr.recall(request))
        merged.sort(key=lambda r: r.score, reverse=True)

        candidates = merged[: request.max_candidates]

        selected: list[RecalledMemory] = []
        total_tokens = 0
        for rm in candidates:
            tokens = _estimate_tokens(rm.memory.content)
            if total_tokens + tokens > request.max_tokens:
                continue
            selected.append(rm)
            total_tokens += tokens

        recall_scores = {rm.memory.id: rm.score for rm in selected}
        lines = [
            f"[{i + 1}] ({rm.memory.type.value}) {rm.memory.content}"
            for i, rm in enumerate(selected)
        ]
        assembled_text = "\n".join(lines)

        return ContextPack(
            request=request,
            memories=[rm.memory for rm in selected],
            total_tokens=total_tokens,
            budget_tokens=request.max_tokens,
            recall_scores=recall_scores,
            assembled_text=assembled_text,
            built_at=datetime.now(UTC),
        )

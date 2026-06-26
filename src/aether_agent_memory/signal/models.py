from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from aether_agent_memory.core.enums import MemoryType, SignalType


class MemorySignal(BaseModel):
    memory_id: str
    memory_type: MemoryType
    session_id: str
    agent_id: str
    signal_type: SignalType
    heat: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

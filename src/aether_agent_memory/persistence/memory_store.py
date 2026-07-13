from __future__ import annotations

import asyncio
import builtins
import sqlite3
from pathlib import Path

from aether_agent_memory.core.memory import Memory


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, Memory] = {}

    async def upsert(self, memory: Memory) -> None:
        self._items[memory.id] = memory

    async def get(self, memory_id: str) -> Memory | None:
        return self._items.get(memory_id)

    async def list(self) -> list[Memory]:
        return list(self._items.values())

    async def delete(self, memory_id: str) -> bool:
        return self._items.pop(memory_id, None) is not None


class SQLiteMemoryStore:
    """Small-footprint durable B2 store for local development and MVP integration."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._initialize()

    async def upsert(self, memory: Memory) -> None:
        async with self._lock:
            await asyncio.to_thread(self._upsert_sync, memory)

    async def get(self, memory_id: str) -> Memory | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, memory_id)

    async def list(self) -> list[Memory]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync)

    async def delete(self, memory_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, memory_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    tenant_id TEXT,
                    user_id TEXT,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_scope "
                "ON memories (tenant_id, user_id, agent_id, session_id, memory_type, state)"
            )

    def _upsert_sync(self, memory: Memory) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (
                    id, memory_type, state, tenant_id, user_id, agent_id,
                    session_id, created_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    memory_type=excluded.memory_type,
                    state=excluded.state,
                    tenant_id=excluded.tenant_id,
                    user_id=excluded.user_id,
                    agent_id=excluded.agent_id,
                    session_id=excluded.session_id,
                    created_at=excluded.created_at,
                    payload=excluded.payload
                """,
                (
                    memory.id,
                    memory.type.value,
                    memory.state.value,
                    memory.tenant_id,
                    memory.user_id,
                    memory.agent_id,
                    memory.session_id,
                    memory.created_at.isoformat(),
                    memory.model_dump_json(),
                ),
            )

    def _get_sync(self, memory_id: str) -> Memory | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        return Memory.model_validate_json(row[0]) if row is not None else None

    def _list_sync(self) -> builtins.list[Memory]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM memories ORDER BY created_at, id"
            ).fetchall()
        return [Memory.model_validate_json(row[0]) for row in rows]

    def _delete_sync(self, memory_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cursor.rowcount > 0

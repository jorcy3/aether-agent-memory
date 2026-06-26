from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta

from aether_agent_memory import (
    MockContextPackBuilder,
    MockEmbeddingClient,
    MockEpisodicMemoryManager,
    MockSemanticMemoryManager,
    MockSignalEmitter,
    MockStorageClient,
    MockWorkingMemoryManager,
    Settings,
)

BOX_TL = "┌"
BOX_TR = "┐"
BOX_BL = "└"
BOX_BR = "┘"
BOX_H = "─"
BOX_V = "│"
CROSS_T = "┬"
CROSS_B = "┴"
CROSS_M = "┼"


def disp_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def pad(text: str, width: int) -> str:
    return text + " " * (width - disp_width(text))


def section_banner(title: str, subtitle: str = "") -> str:
    inner = f" {title} "
    width = disp_width(inner)
    line = BOX_TL + BOX_H * width + BOX_TR
    mid = BOX_V + inner + BOX_V
    bot = BOX_BL + BOX_H * width + BOX_BR
    parts = ["", line, mid, bot]
    if subtitle:
        parts.append(f"  {subtitle}")
    return "\n".join(parts)


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "  (空 — 暂无数据)"
    widths = [disp_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], disp_width(cell))

    def border(left: str, mid: str, right: str) -> str:
        return left + mid.join(BOX_H * (w + 2) for w in widths) + right

    top = border(BOX_TL, CROSS_T, BOX_TR)
    sep = border(CROSS_M, CROSS_M, CROSS_M)
    bot = border(BOX_BL, CROSS_B, BOX_BR)
    head = BOX_V + BOX_V.join(f" {pad(h, widths[i])} " for i, h in enumerate(headers)) + BOX_V
    body_lines = [
        BOX_V + BOX_V.join(f" {pad(c, widths[i])} " for i, c in enumerate(row)) + BOX_V
        for row in rows
    ]
    return "\n".join([top, head, sep, *body_lines, bot])


def render_kv(pairs: list[tuple[str, object]], indent: int = 2) -> str:
    pad_left = " " * indent
    key_w = max((disp_width(k) for k, _ in pairs), default=0)
    lines = []
    for key, value in pairs:
        lines.append(f"{pad_left}{pad(key, key_w)}  {value}")
    return "\n".join(lines)


def fmt_dt(dt: object) -> str:
    if dt is None:
        return "—"
    text = str(dt)
    return text.split("+")[0].split(".")[0]


def fmt_embedding(emb: object, shown: int = 3) -> str:
    if emb is None:
        return "—"
    vals = list(emb)
    if not vals:
        return "[]"
    head = ", ".join(f"{v:+.3f}" for v in vals[:shown])
    more = f", …(+{len(vals) - shown})" if len(vals) > shown else ""
    return f"[{head}{more}]  dim={len(vals)}"


@dataclass
class DemoEnv:
    settings: Settings = field(default_factory=Settings)
    embedder: MockEmbeddingClient = field(default_factory=lambda: MockEmbeddingClient(dim=32))
    storage: MockStorageClient = field(default_factory=MockStorageClient)
    working: MockWorkingMemoryManager = field(init=False)
    episodic: MockEpisodicMemoryManager = field(init=False)
    semantic: MockSemanticMemoryManager = field(init=False)
    builder: MockContextPackBuilder = field(init=False)
    emitter: MockSignalEmitter = field(default_factory=MockSignalEmitter)

    def __post_init__(self) -> None:
        self.working = MockWorkingMemoryManager(
            default_ttl=timedelta(seconds=self.settings.default_ttl_seconds)
        )
        self.episodic = MockEpisodicMemoryManager(
            embedder=self.embedder, half_life_hours=self.settings.half_life_hours
        )
        self.semantic = MockSemanticMemoryManager(embedder=self.embedder)
        self.builder = MockContextPackBuilder(
            working=self.working,
            episodic=self.episodic,
            semantic=self.semantic,
        )

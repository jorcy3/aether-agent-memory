from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aether_agent_memory import (
    MockContextPackBuilder,
    MockEmbeddingClient,
    MockEpisodicMemoryManager,
    MockSemanticMemoryManager,
    MockSignalEmitter,
    MockStorageClient,
    MockWorkingMemoryManager,
    Settings,
    StorageTier,
)

console = Console()

SCORE_FLOOR = 0.5
SCORE_CEIL = 0.95


def normalize_score(raw: float) -> float:
    clamped = max(0.0, min(1.0, (raw + 1.0) / 2.0))
    return round(SCORE_FLOOR + clamped * (SCORE_CEIL - SCORE_FLOOR), 4)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def fmt_dt(dt: object) -> str:
    if dt is None:
        return "—"
    return str(dt).split("+")[0].split(".")[0]


def fmt_embedding(emb: object, shown: int = 3) -> str:
    if emb is None:
        return "—"
    vals = list(emb)
    if not vals:
        return "[]"
    head = ", ".join(f"{v:+.3f}" for v in vals[:shown])
    more = f", …(+{len(vals) - shown})" if len(vals) > shown else ""
    return f"[{head}{more}]  dim={len(vals)}"


def make_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    title: str | None = None,
) -> Table:
    table = Table(title=title, show_lines=False, expand=True, pad_edge=False)
    for h in headers:
        table.add_column(h, overflow="fold", no_wrap=False)
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    return table


def make_panel(title: str, subtitle: str = "") -> Panel:
    body = f"[bold cyan]{title}[/bold cyan]"
    if subtitle:
        body += f"\n[dim]{subtitle}[/dim]"
    return Panel(body, border_style="cyan", expand=True, padding=(0, 2))


def print_kv(pairs: list[tuple[str, object]], *, indent: int = 2) -> None:
    pad_left = " " * indent
    key_w = max((len(str(k)) for k, _ in pairs), default=0)
    for key, value in pairs:
        console.print(f"{pad_left}[bold]{str(key).ljust(key_w)}[/bold]  {value}")


@dataclass
class DemoEnv:
    settings: Settings = field(default_factory=Settings)
    embedder: MockEmbeddingClient = field(default_factory=lambda: MockEmbeddingClient(dim=32))
    storage: MockStorageClient = field(
        default_factory=lambda: MockStorageClient(default_tier=StorageTier.L1_NVME)
    )
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

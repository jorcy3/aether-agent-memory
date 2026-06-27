from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from mock_services import (
    DemoEnv,
    episodic_detail_rows,
    fmt_dt,
    memory_table_rows,
    normalize_score,
    truncate,
)
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.reactive import reactive
from textual.widgets import Footer, Header, RichLog, Static

from aether_agent_memory import (
    ContextRequest,
    Memory,
    MemorySignal,
    MemoryState,
    MemoryType,
    SignalType,
    SourceType,
)
from aether_agent_memory.lifecycle.decay import ebb_decay_weight

AGENT_ID = "agent-hydro-01"
USER_ID = "user-meteorologist"
SESSION_1 = "sess-2026-001"
SESSION_2 = "sess-2026-002"


def _make_table(headers: list[str], rows: list[list[str]], *, title: str | None = None) -> Table:
    table = Table(title=title, show_lines=False, expand=True, pad_edge=False, box=None)
    for h in headers:
        table.add_column(h, overflow="fold", no_wrap=False)
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    return table


class B2DemoApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }
    #main-area {
        height: 1fr;
        layout: horizontal;
    }
    #user-pane {
        width: 2fr;
        border: round $primary;
        padding: 0 1;
        background: $surface;
    }
    #system-pane {
        width: 3fr;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    #user-pane RichLog, #system-pane RichLog {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    #status-bar {
        height: 3;
        dock: bottom;
        background: $panel;
        border-top: solid $primary;
        padding: 0 2;
        content-align: center middle;
    }
    """

    stage: reactive[str] = reactive("准备中")

    def __init__(self) -> None:
        super().__init__()
        self.env = DemoEnv()
        self._advance: asyncio.Event = asyncio.Event()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="main-area"):
            with Vertical(id="user-pane") as user_pane:
                user_pane.border_title = "用户视角 · Agent 对话"
                yield RichLog(id="user-log", wrap=True, markup=True)
            with Vertical(id="system-pane") as system_pane:
                system_pane.border_title = "记忆系统内部 · B2"
                yield RichLog(id="system-log", wrap=True, markup=True)
        yield Static("准备中", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.set_stage("场景 1 · 多轮对话写入 Working Memory")
        self.run_worker(self.run_scenario, exclusive=True)

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        bar = self.query_one("#status-bar", Static)
        bar.update(
            f"[bold cyan]{stage}[/bold cyan]    [dim]按 Enter 进入下一步 · Ctrl+C 退出[/dim]"
        )

    def user_log(self) -> RichLog:
        return self.query_one("#user-log", RichLog)

    def system_log(self) -> RichLog:
        return self.query_one("#system-log", RichLog)

    def user_write(self, text: str, *, who: str = "user") -> None:
        label = "用户" if who == "user" else "Agent"
        color = "bold cyan" if who == "user" else "bold green"
        panel = Panel(
            Text(text, no_wrap=False),
            title=f"[{color}]{label}[/{color}]",
            title_align="left",
            border_style="cyan" if who == "user" else "green",
            padding=(0, 1),
        )
        self.user_log().write(panel)

    def system_write(self, content: Any) -> None:
        self.system_log().write(content)

    def system_section(self, title: str) -> None:
        self.system_write(Rule(title, style="bold cyan"))

    async def wait_for_enter(self, hint: str = "按 Enter 进入下一步") -> None:
        bar = self.query_one("#status-bar", Static)
        bar.update(f"[bold cyan]{self.stage}[/bold cyan]    [yellow]{hint}[/yellow]")
        self._advance.clear()
        await self._advance.wait()
        bar.update(
            f"[bold cyan]{self.stage}[/bold cyan]    [dim]按 Enter 进入下一步 · Ctrl+C 退出[/dim]"
        )
        await asyncio.sleep(0.1)

    def on_key(self, event: Key) -> None:
        if event.key in ("enter", "space"):
            self._advance.set()

    async def run_scenario(self) -> None:
        try:
            await self.scenario_1()
            await self.wait_for_enter("按 Enter 进入「会话结束 → 异步归档」")
            self.system_log().clear()
            await self.scenario_2()
            await self.wait_for_enter("按 Enter 进入「新会话 → Context Pack 构建」")
            self.system_log().clear()
            await self.scenario_3()
            await self.wait_for_enter("按 Enter 进入「记忆生命周期事件」")
            self.system_log().clear()
            await self.scenario_4()
            await self.wait_for_enter("按 Enter 进入「Memory Signal 输出」")
            self.system_log().clear()
            await self.scenario_5()
            await self.wait_for_enter("按 Enter 查看演示总结")
            self.system_log().clear()
            self.user_log().clear()
            await self.summary()
            await self.wait_for_enter("演示结束，按 Enter 退出")
            self.exit()
        except Exception as e:
            self.system_write(f"[red]演示异常：{e}[/red]")
            raise

    async def scenario_1(self) -> None:
        self.set_stage("场景 1 · 多轮对话写入 Working Memory")
        self.system_section("场景 1 · Working Memory 写入")
        self.system_write("[dim]每条用户消息即时写入 DRAM(L0)，P99 < 1ms[/dim]\n")

        turns = [
            ("这次暴雨过程的降雨量有多大？", ["rainstorm", "rainfall"], "正在查询降雨量数据……"),
            (
                "气象台预报未来24小时累计降水量多少毫米？",
                ["forecast", "24h-rain"],
                "已检索预报数据，24h累计约 85mm。",
            ),
            (
                "哪些区域需要发布暴雨预警？",
                ["warning", "region"],
                "建议对 3 个区县发布暴雨橙色预警。",
            ),
        ]

        for msg, tags, reply in turns:
            self.user_write(msg, who="user")
            mem = Memory(
                type=MemoryType.WORKING,
                session_id=SESSION_1,
                agent_id=AGENT_ID,
                user_id=USER_ID,
                content=f"[用户] {msg}",
                source=SourceType.USER,
                tags=tags,
            )
            await self.env.working.write(mem)
            self.system_write(
                f"[green]▸[/green] 写入 Working Memory  id={mem.id[:8]}  "
                f"tags={tags}  ttl={fmt_dt(mem.expires_at)}"
            )
            self.user_write(reply, who="agent")
            await asyncio.sleep(0.2)

        self.system_write("\n[bold]当前 Working Memory 状态[/bold]（按 session 过滤）：")
        items = await self.env.working.query(session_id=SESSION_1)
        self.system_write(
            _make_table(
                ["ID", "层级", "状态", "内容", "来源", "访问数", "过期时间"],
                memory_table_rows(items),
            )
        )

    async def scenario_2(self) -> None:
        self.set_stage("场景 2 · 会话结束 → 异步归档到 Episodic Memory")
        self.user_write("（会话结束）", who="user")
        self.user_write("本次会话已归档，谢谢使用。", who="agent")

        self.system_section("场景 2 · 归档到 Episodic Memory")
        working_items = await self.env.working.query(session_id=SESSION_1)
        self.system_write(f"归档前 Working={len(working_items)}  Episodic=0\n")

        archived: list[Memory] = []
        for m in working_items:
            await self.env.working.update_state(m.id, MemoryState.ARCHIVED)
            ref = await self.env.storage.put(f"episodic/{m.id}", m.content.encode("utf-8"))
            ep = Memory(
                type=MemoryType.EPISODIC,
                session_id=m.session_id,
                agent_id=m.agent_id,
                user_id=m.user_id,
                content=m.content,
                source=m.source,
                tags=list(m.tags),
                importance=m.importance,
                metadata={"archived_from": m.id},
                p2_ref=ref,
            )
            await self.env.episodic.write(ep)
            archived.append(ep)
            await self.env.working.delete(m.id)
            self.system_write(
                f"[green]▸[/green] {m.id[:8]}  [yellow]active→archived[/yellow] → "
                f"episodic {ep.id[:8]}  "
                f"embedding dim={len(ep.embedding or [])}  tier={ref.tier.value}"
            )
            await asyncio.sleep(0.15)

        self.system_write(f"\n归档后 Working=0（清空）  Episodic={len(archived)}\n")
        self.system_write("[bold]Episodic Memory 详情[/bold]（embedding + P2 关联）：")
        self.system_write(
            _make_table(
                ["ID", "状态", "内容", "Embedding", "P2 object_key", "存储层"],
                episodic_detail_rows(archived),
            )
        )

    async def scenario_3(self) -> None:
        self.set_stage("场景 3 · 新会话 → Context Pack 构建")
        self.user_write(
            "开始新会话。上次提到的暴雨过程，降水量预报是多少？要不要发预警？", who="user"
        )

        self.system_section("场景 3 · Context Pack 构建")
        semantic_fact = Memory(
            type=MemoryType.SEMANTIC,
            session_id=SESSION_1,
            agent_id=AGENT_ID,
            content="暴雨预警标准：24小时降水量 ≥ 50mm 为暴雨，≥ 100mm 为大暴雨",
            source=SourceType.DISTILLED,
            tags=["warning", "standard"],
        )
        await self.env.semantic.write(semantic_fact)
        self.system_write(f"预置 Semantic 知识：{semantic_fact.content}\n")

        request = ContextRequest(
            session_id=SESSION_2,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            query="上次提到的暴雨过程，降水量预报是多少？要不要发预警？",
            memory_types=[MemoryType.EPISODIC, MemoryType.SEMANTIC],
            max_tokens=256,
            max_candidates=10,
        )
        self.system_write("[bold]ContextRequest[/bold]")
        self.system_write(
            f"  query={truncate(request.query, 40)}\n"
            f"  types={[t.value for t in request.memory_types]}  "
            f"max_tokens={request.max_tokens}\n"
        )

        pack = await self.env.builder.build(request)
        epi_hits = await self.env.episodic.recall(request)
        sem_hits = await self.env.semantic.recall(request)
        self.system_write(f"召回候选：Episodic={len(epi_hits)}  Semantic={len(sem_hits)}\n")
        self.system_write("Episodic 召回（cosine × 衰减，归一化 0.5~0.95）：")
        self.system_write(
            _make_table(
                ["ID", "内容", "score"],
                [
                    [
                        r.memory.id[:8],
                        truncate(r.memory.content, 34),
                        f"{normalize_score(r.score):.4f}",
                    ]
                    for r in epi_hits
                ],
            )
        )
        self.system_write("Semantic 召回（cosine，无衰减）：")
        self.system_write(
            _make_table(
                ["ID", "内容", "score"],
                [
                    [
                        r.memory.id[:8],
                        truncate(r.memory.content, 34),
                        f"{normalize_score(r.score):.4f}",
                    ]
                    for r in sem_hits
                ],
            )
        )

        self.system_write(
            f"\n[bold]Context Pack[/bold]  入选={len(pack.memories)}  "
            f"tokens={pack.total_tokens}/{pack.budget_tokens}"
        )
        self.system_write("[bold]入选记忆明细[/bold]：")
        self.system_write(
            _make_table(
                ["#", "层级", "内容", "recall_score"],
                [
                    [
                        str(i + 1),
                        m.type.value,
                        truncate(m.content, 36),
                        f"{normalize_score(pack.recall_scores[m.id]):.4f}",
                    ]
                    for i, m in enumerate(pack.memories)
                ],
            )
        )
        self.system_write("[bold]assembled_text（返回给 Agent）[/bold]：")
        for line in pack.assembled_text.split("\n"):
            self.system_write(f"  {line}")

        self.user_write(
            "根据历史会话，上次暴雨过程 24h 累计约 85mm，已达暴雨标准；"
            "建议对相关区县发布暴雨预警。",
            who="agent",
        )

    async def scenario_4(self) -> None:
        self.set_stage("场景 4 · 记忆生命周期事件")
        self.user_write("（系统侧生命周期演示）", who="user")
        self.user_write("将演示 TTL 过期、信息纠正替代、遗忘衰减。", who="agent")

        self.system_section("4-A · TTL 到期过期")
        short = Memory(
            type=MemoryType.WORKING,
            session_id="sess-ttl-demo",
            agent_id=AGENT_ID,
            content="[临时] 这次查询的缓存结果",
            tags=["cache"],
        )
        await self.env.working.write(short)
        self.system_write(
            f"写入 {short.id[:8]}  state={short.state.value}  expires_at={fmt_dt(short.expires_at)}"
        )
        future = datetime.now(UTC) + timedelta(hours=2)
        expired_n = await self.env.working.expire_stale(now=future)
        after = await self.env.working.get(short.id)
        self.system_write(
            f"时间前进 2h → expire_stale() 过期 {expired_n} 条  "
            f"state=[red]{after.state.value if after else '—'}[/red]\n"
        )

        self.system_section("4-B · 用户纠正 → supersede")
        self.user_write("等等，API 限速应该是 1000 次每分钟，不是 100。", who="user")
        old_fact = Memory(
            type=MemoryType.SEMANTIC,
            session_id="sess-supersede",
            agent_id=AGENT_ID,
            content="API 限速为每分钟 100 次请求",
            source=SourceType.DISTILLED,
            tags=["api", "ratelimit"],
        )
        await self.env.semantic.write(old_fact)
        new_fact = Memory(
            type=MemoryType.SEMANTIC,
            session_id="sess-supersede",
            agent_id=AGENT_ID,
            content="API 限速已更新为每分钟 1000 次请求",
            source=SourceType.USER,
            tags=["api", "ratelimit"],
            importance=1.2,
        )
        await self.env.semantic.write(new_fact)
        await self.env.semantic.update_state(old_fact.id, MemoryState.SUPERSEDED)
        old_fact.superseded_by = new_fact.id
        self.system_write(
            _make_table(
                ["记忆", "状态", "内容", "superseded_by"],
                [
                    [
                        old_fact.id[:8],
                        old_fact.state.value,
                        truncate(old_fact.content, 30),
                        new_fact.id[:8],
                    ],
                    [new_fact.id[:8], new_fact.state.value, truncate(new_fact.content, 30), "—"],
                ],
            )
        )
        self.user_write("已更新，谢谢纠正。", who="agent")

        self.system_section("4-C · Ebbinghaus 遗忘曲线衰减")
        sample = Memory(
            type=MemoryType.EPISODIC,
            session_id="sess-decay-demo",
            agent_id=AGENT_ID,
            content="[用户] 上周讨论过卫星云图识别暴雨",
            created_at=datetime.now(UTC),
        )
        ages = [
            ("刚刚", timedelta(0)),
            ("1 小时后", timedelta(hours=1)),
            ("1 天后", timedelta(days=1)),
            ("7 天后（半衰期）", timedelta(days=7)),
            ("30 天后", timedelta(days=30)),
        ]
        rows = []
        for label, delta in ages:
            w = ebb_decay_weight(sample, now=sample.created_at + delta, half_life_hours=168.0)
            bar = "█" * int(w * 20) + "░" * (20 - int(w * 20))
            rows.append([label, f"{w:.4f}", bar])
        self.system_write(_make_table(["经过时间", "衰减权重", "权重可视化"], rows))

    async def scenario_5(self) -> None:
        self.set_stage("场景 5 · Memory Signal 输出")
        self.user_write("（Agent 召回了一条历史记忆）", who="user")

        self.system_section("场景 5 · Memory Signal → B3")
        target = await self.env.episodic.query(session_id=SESSION_1, limit=1)
        if not target:
            self.system_write("[red]无可演示的记忆[/red]")
            return
        mem = target[0]
        mem.touch()
        remaining = (mem.expires_at - datetime.now(UTC)).total_seconds() if mem.expires_at else None
        heat = min(mem.importance * (0.5 + 0.1 * mem.access_count), 1.0)
        signal = MemorySignal(
            memory_id=mem.id,
            memory_type=mem.type,
            session_id=mem.session_id,
            agent_id=mem.agent_id,
            signal_type=SignalType.ACCESS,
            heat=round(heat, 4),
            metadata={
                "importance": mem.importance,
                "use_count": mem.access_count,
                "status": mem.state.value,
                "ttl_remaining_seconds": (round(remaining, 1) if remaining is not None else None),
                "storage_tier": mem.p2_ref.tier.value if mem.p2_ref else "L0",
                "tags": mem.tags,
            },
        )
        await self.env.emitter.emit(signal)

        self.system_write("[bold]MemorySignal 结构[/bold]（B3 收到）：")
        sig_lines = [
            f"  signal_type   {signal.signal_type.value}",
            f"  memory_id     {signal.memory_id[:12]}…",
            f"  memory_type   {signal.memory_type.value}",
            f"  session_id    {signal.session_id}",
            f"  agent_id      {signal.agent_id}",
            f"  heat          {signal.heat}  ← B3 调度决策热度",
            f"  timestamp     {fmt_dt(signal.timestamp)}",
        ]
        self.system_write("\n".join(sig_lines))
        self.system_write("\n[bold]metadata[/bold]（B3 调度上下文）：")
        meta_lines = [
            f"    importance             {signal.metadata['importance']}",
            f"    use_count              {signal.metadata['use_count']}",
            f"    status                 {signal.metadata['status']}",
            f"    ttl_remaining_seconds  {signal.metadata['ttl_remaining_seconds']}",
            f"    storage_tier           {signal.metadata['storage_tier']}",
            f"    tags                   {signal.metadata['tags']}",
        ]
        self.system_write("\n".join(meta_lines))
        self.system_write(f"\n已发射，累计信号数：[bold]{len(self.env.emitter.signals)}[/bold]")

    async def summary(self) -> None:
        self.set_stage("演示总结")
        self.user_log().clear()
        self.user_write("演示结束。以上为 B2 Agent 记忆管理器的核心能力。", who="agent")
        self.system_section("演示总结 · B2 已展示的能力")
        caps = [
            ("三层记忆模型", "Working(DRAM)/Episodic(NVMe)/Semantic 三层统一管理"),
            ("即时上下文读写", "多轮对话写入 Working Memory，会话粒度 TTL"),
            ("异步归档与向量化", "会话结束提纯→调 B1 生成 embedding→落地 Episodic + P2 关联"),
            ("Context Pack 构建", "联合召回 + 排序 + token 预算裁剪 + 结构化文本组装"),
            ("TTL 自动过期", "过期扫描将到期记忆 active→expired"),
            ("版本替代", "用户纠正触发 supersede，旧记忆指向新版本"),
            ("Ebbinghaus 衰减", "基于遗忘曲线的召回权重衰减，越久越低"),
            ("B3 价值信号", "向调度器发射 importance/use_count/status/ttl 等决策字段"),
        ]
        self.system_write(_make_table(["能力", "说明"], [[c, d] for c, d in caps]))
        self.system_write(
            "\n[dim]全部基于内存 Mock 实现，零外部依赖；"
            "生产环境将对接 B1 Embedding Sidecar 与 P2 引擎。[/dim]"
        )

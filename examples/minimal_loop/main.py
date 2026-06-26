from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from mock_services import (
    DemoEnv,
    fmt_dt,
    fmt_embedding,
    render_kv,
    render_table,
    section_banner,
    truncate,
)

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


def _memory_table(memories: list[Memory]) -> str:
    rows = [
        [
            m.id[:8],
            m.type.value,
            m.state.value,
            truncate(m.content, 30),
            m.source.value,
            str(m.access_count),
            fmt_dt(m.expires_at),
        ]
        for m in memories
    ]
    return render_table(["ID", "层级", "状态", "内容", "来源", "访问数", "过期时间"], rows)


def _episodic_detail_table(memories: list[Memory]) -> str:
    rows = [
        [
            m.id[:8],
            m.state.value,
            truncate(m.content, 28),
            fmt_embedding(m.embedding),
            m.p2_ref.object_key if m.p2_ref else "—",
            m.p2_ref.tier.value if m.p2_ref else "—",
        ]
        for m in memories
    ]
    return render_table(["ID", "状态", "内容", "Embedding", "P2 object_key", "存储层"], rows)


async def scenario_1(env: DemoEnv) -> None:
    print(
        section_banner(
            "场景 1 · 多轮对话写入 Working Memory",
            "即时上下文存入 DRAM，会话粒度 TTL",
        )
    )
    print("模拟 Agent 在一次会话中接收 3 条用户消息，每条写入 Working Memory 后展示当前状态。\n")

    turns = [
        ("这次暴雨过程的降雨量有多大？", ["rainstorm", "rainfall"]),
        ("气象台预报未来24小时累计降水量多少毫米？", ["forecast", "24h-rain"]),
        ("哪些区域需要发布暴雨预警？", ["warning", "region"]),
    ]

    for i, (msg, tags) in enumerate(turns, start=1):
        mem = Memory(
            type=MemoryType.WORKING,
            session_id=SESSION_1,
            agent_id=AGENT_ID,
            user_id=USER_ID,
            content=f"[用户] {msg}",
            source=SourceType.USER,
            tags=tags,
        )
        await env.working.write(mem)
        print(f"  ▸ 第 {i} 轮：用户消息已写入 Working Memory（id={mem.id[:8]}）")
        print(f"    内容：{msg}")
        print(f"    标签：{tags}")
        print()

    print("  当前 Working Memory 状态（按 session 过滤）：")
    items = await env.working.query(session_id=SESSION_1)
    print(_memory_table(items))
    print()
    print(
        "  说明：Working Memory 位于 DRAM(L0)，P99 读写 < 1ms；每条记忆带会话 TTL，"
        "会话结束后异步归档。\n"
    )


async def scenario_2(env: DemoEnv) -> None:
    print(
        section_banner(
            "场景 2 · 会话结束 → 异步归档到 Episodic Memory",
            "提纯 → 调 B1 向量化 → 双库分流",
        )
    )
    print(
        "会话结束触发归档：Working 项状态 active→archived，内容流入 Episodic，"
        "调用 B1 生成 embedding 并写入 P2 存储。\n"
    )

    working_items = await env.working.query(session_id=SESSION_1)
    print(f"  归档前 Working Memory 条数：{len(working_items)}")
    print(f"  归档前 Episodic Memory 条数：{len(await env.episodic.query(session_id=SESSION_1))}")
    print()

    archived: list[Memory] = []
    for m in working_items:
        await env.working.update_state(m.id, MemoryState.ARCHIVED)
        ref = await env.storage.put(f"episodic/{m.id}", m.content.encode("utf-8"))
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
        await env.episodic.write(ep)
        archived.append(ep)
        await env.working.delete(m.id)
        print(
            f"  ▸ 归档 {m.id[:8]}：working state active→archived → 删除；"
            f"episodic {ep.id[:8]} 已写入（embedding dim={len(ep.embedding or [])}）"
        )

    print()
    working_left = len(await env.working.query(session_id=SESSION_1))
    print(f"  归档后 Working Memory 条数：{working_left}（已清空）")
    episodic_n = len(await env.episodic.query(session_id=SESSION_1))
    print(f"  归档后 Episodic Memory 条数：{episodic_n}")
    print()
    print("  Episodic Memory 详情（含 embedding 引用与 P2 存储关联）：")
    print(_episodic_detail_table(archived))
    print()
    print(
        "  说明：Episodic 落地 L1 NVMe，按 Ebbinghaus 遗忘曲线衰减；"
        "embedding 由 B1 Sidecar 生成，原始内容存于 P2 对象引擎。\n"
    )


async def scenario_3(env: DemoEnv) -> None:
    print(
        section_banner(
            "场景 3 · 新会话 → Context Pack 构建",
            "带时间衰减的联合检索 + 排序 + token 预算裁剪",
        )
    )
    print("新会话开始，用户提出与之前对话相关的问题，系统从 Episodic/Semantic 召回并组装上下文。\n")

    semantic_fact = Memory(
        type=MemoryType.SEMANTIC,
        session_id=SESSION_1,
        agent_id=AGENT_ID,
        content="暴雨预警标准：24小时降水量 ≥ 50mm 为暴雨，≥ 100mm 为大暴雨",
        source=SourceType.DISTILLED,
        tags=["warning", "standard"],
    )
    await env.semantic.write(semantic_fact)
    print(f"  预置 Semantic 知识：{semantic_fact.content}")
    print()

    request = ContextRequest(
        session_id=SESSION_2,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        query="上次提到的暴雨过程，降水量预报是多少？要不要发预警？",
        memory_types=[MemoryType.EPISODIC, MemoryType.SEMANTIC],
        max_tokens=256,
        max_candidates=10,
    )
    print("  构建请求 ContextRequest：")
    print(
        render_kv(
            [
                ("session_id", request.session_id),
                ("query", truncate(request.query, 40)),
                ("memory_types", [t.value for t in request.memory_types]),
                ("max_tokens", request.max_tokens),
                ("max_candidates", request.max_candidates),
            ]
        )
    )
    print()

    pack = await env.builder.build(request)

    epi_hits = await env.episodic.recall(request)
    sem_hits = await env.semantic.recall(request)
    print(f"  召回候选：Episodic {len(epi_hits)} 条，Semantic {len(sem_hits)} 条")
    print("  Episodic 召回（按 cosine × 衰减 排序）：")
    print(
        render_table(
            ["ID", "内容", "score"],
            [[r.memory.id[:8], truncate(r.memory.content, 34), f"{r.score:.4f}"] for r in epi_hits],
        )
    )
    print("  Semantic 召回（按 cosine 排序，无衰减）：")
    print(
        render_table(
            ["ID", "内容", "score"],
            [[r.memory.id[:8], truncate(r.memory.content, 34), f"{r.score:.4f}"] for r in sem_hits],
        )
    )
    print()

    print("  最终 Context Pack：")
    print(
        render_kv(
            [
                ("入选记忆数", len(pack.memories)),
                ("已用 tokens", f"{pack.total_tokens} / {pack.budget_tokens}"),
                ("构建时间", fmt_dt(pack.built_at)),
            ]
        )
    )
    print()
    print("  入选记忆明细：")
    print(
        render_table(
            ["#", "层级", "内容", "recall_score"],
            [
                [
                    str(i + 1),
                    m.type.value,
                    truncate(m.content, 36),
                    f"{pack.recall_scores[m.id]:.4f}",
                ]
                for i, m in enumerate(pack.memories)
            ],
        )
    )
    print()
    print("  组装后返回给 Agent 的文本（assembled_text）：")
    for line in pack.assembled_text.split("\n"):
        print(f"    {line}")
    print()
    print(
        "  说明：联合检索按 recall_score 排序，超出 token 预算的低分项被裁剪；"
        "Agent 拿到的是结构化、可直接拼入 prompt 的上下文。\n"
    )


async def scenario_4(env: DemoEnv) -> None:
    print(
        section_banner(
            "场景 4 · 记忆生命周期事件",
            "TTL 过期 · 旧记忆被替代 · Ebbinghaus 衰减",
        )
    )

    print("\n  4-A · TTL 到期过期")
    print("  模拟一条 Working Memory 记忆的 TTL 到期，由过期扫描标记为 expired。\n")
    short = Memory(
        type=MemoryType.WORKING,
        session_id="sess-ttl-demo",
        agent_id=AGENT_ID,
        content="[临时] 这次查询的缓存结果",
        tags=["cache"],
    )
    await env.working.write(short)
    print(
        f"  写入记忆 {short.id[:8]}"
        f"（state={short.state.value}, expires_at={fmt_dt(short.expires_at)}）"
    )
    future = datetime.now(UTC) + timedelta(hours=2)
    expired_n = await env.working.expire_stale(now=future)
    after = await env.working.get(short.id)
    print(f"  模拟时间前进 2 小时后执行 expire_stale()：过期 {expired_n} 条")
    print(f"  记忆 {short.id[:8]} 状态：active → {after.state.value if after else '—'}")
    print()

    print("  4-B · 用户纠正 → 旧记忆被新版本替代（supersede）")
    print("  旧 Semantic 知识被用户纠正，标记为 superseded 并指向新版本。\n")
    old_fact = Memory(
        type=MemoryType.SEMANTIC,
        session_id="sess-supersede",
        agent_id=AGENT_ID,
        content="API 限速为每分钟 100 次请求",
        source=SourceType.DISTILLED,
        tags=["api", "ratelimit"],
    )
    await env.semantic.write(old_fact)
    new_fact = Memory(
        type=MemoryType.SEMANTIC,
        session_id="sess-supersede",
        agent_id=AGENT_ID,
        content="API 限速已更新为每分钟 1000 次请求",
        source=SourceType.USER,
        tags=["api", "ratelimit"],
        importance=1.2,
    )
    await env.semantic.write(new_fact)
    await env.semantic.update_state(old_fact.id, MemoryState.SUPERSEDED)
    old_fact.superseded_by = new_fact.id
    print(
        render_table(
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
    print("  说明：superseded 为终态，不再被召回；新版本成为活跃知识。\n")

    print("  4-C · Ebbinghaus 遗忘曲线衰减")
    print("  同一条 Episodic 记忆随时间推移，召回权重逐步下降（半衰期 7 天）。\n")
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
    print(render_table(["经过时间", "衰减权重", "权重可视化（20 格）"], rows))
    print("  说明：权重 = 0.5^(age/half_life)，越久越低；衰减后排序靠后，更易被裁剪。\n")


async def scenario_5(env: DemoEnv) -> None:
    print(section_banner("场景 5 · Memory Signal 输出", "发给 B3 智能分层调度器的记忆价值信号"))
    print("每次记忆访问/归档/过期都会向 B3 发射信号，B3 据此决定介质迁移。\n")

    target = await env.episodic.query(session_id=SESSION_1, limit=1)
    if not target:
        print("  (无可演示的记忆)")
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
    await env.emitter.emit(signal)

    print("  触发场景：Agent 召回并访问了一条 Episodic 记忆 → 发射 ACCESS 信号\n")
    print("  MemorySignal 结构（B3 收到的内容）：")
    print(
        render_kv(
            [
                ("signal_type", signal.signal_type.value),
                ("memory_id", signal.memory_id[:12] + "…"),
                ("memory_type", signal.memory_type.value),
                ("session_id", signal.session_id),
                ("agent_id", signal.agent_id),
                ("heat", f"{signal.heat}  ← B3 用于调度决策的综合热度"),
                ("timestamp", fmt_dt(signal.timestamp)),
            ]
        )
    )
    print()
    print("  metadata（B3 调度所需上下文字段）：")
    print(
        render_kv(
            [
                ("importance", signal.metadata["importance"]),
                ("use_count", signal.metadata["use_count"]),
                ("status", signal.metadata["status"]),
                ("ttl_remaining_seconds", signal.metadata["ttl_remaining_seconds"]),
                ("storage_tier", signal.metadata["storage_tier"]),
                ("tags", signal.metadata["tags"]),
            ],
            indent=4,
        )
    )
    print()
    print(f"  信号已通过 MockSignalEmitter 发射，累计发射数：{len(env.emitter.signals)}")
    print("  说明：B3 结合 heat / use_count / ttl 决定该记忆保留 L1 还是下沉 L3。\n")


async def summary() -> None:
    print(section_banner("演示总结 · B2 Agent 记忆管理器已展示的能力"))
    capabilities = [
        ("三层记忆模型", "Working(DRAM)/Episodic(NVMe)/Semantic 三层统一管理"),
        ("即时上下文读写", "多轮对话写入 Working Memory，会话粒度 TTL"),
        ("异步归档与向量化", "会话结束提纯→调 B1 生成 embedding→落地 Episodic + P2 关联"),
        ("Context Pack 构建", "联合召回 + 排序 + token 预算裁剪 + 结构化文本组装"),
        ("TTL 自动过期", "过期扫描将到期记忆 active→expired"),
        ("版本替代", "用户纠正触发 supersede，旧记忆指向新版本"),
        ("Ebbinghaus 衰减", "基于遗忘曲线的召回权重衰减，越久越低"),
        ("B3 价值信号", "向调度器发射 importance/use_count/status/ttl 等决策字段"),
    ]
    print()
    print(
        render_table(
            ["能力", "说明"],
            [[cap, desc] for cap, desc in capabilities],
        )
    )
    print()
    print("  全部基于内存 Mock 实现，零外部依赖；生产环境将对接 B1 Embedding Sidecar 与 P2 引擎。")
    print()


async def main() -> None:
    env = DemoEnv()
    print()
    print("=" * 78)
    print("  B2 Agent 记忆管理器 · 概念演示（面向甲方）".center(64))
    print("  AetherBrain / P3 · Working / Episodic / Semantic 三层记忆".center(60))
    print("=" * 78)
    print()
    await scenario_1(env)
    await scenario_2(env)
    await scenario_3(env)
    await scenario_4(env)
    await scenario_5(env)
    await summary()


if __name__ == "__main__":
    asyncio.run(main())

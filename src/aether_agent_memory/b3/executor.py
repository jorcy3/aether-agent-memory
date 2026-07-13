from aether_agent_memory.b3.models import (
    ActionType,
    ExecuteStatus,
    ExecutionFeedback,
    ScheduleAction,
)
from aether_agent_memory.core.enums import StorageTier


class MockExecutor:
    """Deterministic P1/P2 substitute with idempotent feedback for MVP validation."""

    def __init__(
        self,
        *,
        initial_tiers: dict[str, StorageTier] | None = None,
        fail_object_ids: set[str] | None = None,
    ) -> None:
        self.current_tiers = dict(initial_tiers or {})
        self.fail_object_ids = set(fail_object_ids or set())
        self._feedback_by_action: dict[str, ExecutionFeedback] = {}

    async def execute(self, action: ScheduleAction) -> ExecutionFeedback:
        existing = self._feedback_by_action.get(action.action_id)
        if existing is not None:
            return existing

        should_fail = action.object_id in self.fail_object_ids and action.action_type not in (
            ActionType.KEEP,
            ActionType.PIN,
        )
        if should_fail:
            feedback = ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.FAILED,
                execute_latency_ms=1.0,
                new_tier=self.current_tiers.get(action.object_id, action.source_tier),
                error_code="MOCK_EXECUTOR_FAILURE",
                failure_reason="configured deterministic failure",
                trace_id=action.trace_id,
            )
        else:
            resolved_tier = action.target_tier or action.source_tier
            new_tier: StorageTier | None = resolved_tier
            if action.action_type == ActionType.EVICT:
                self.current_tiers.pop(action.object_id, None)
                new_tier = None
            else:
                self.current_tiers[action.object_id] = resolved_tier
            feedback = ExecutionFeedback(
                action_id=action.action_id,
                object_id=action.object_id,
                action_type=action.action_type,
                execute_status=ExecuteStatus.SUCCESS,
                execute_latency_ms=1.0,
                new_tier=new_tier,
                trace_id=action.trace_id,
            )
        self._feedback_by_action[action.action_id] = feedback
        return feedback

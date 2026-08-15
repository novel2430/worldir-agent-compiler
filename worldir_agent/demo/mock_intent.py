from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from ..runtime.models import StrictContractModel
from .models import (
    FuturePolicyV0,
    PersistentClimateHistoryV0,
    WorldEvolutionStateV0,
)


INITIAL_WORLD_PROMPT = "西边是一片靠海的茂密森林，一条小路从西向东穿过森林，树木成簇分布。"
FUTURE_POLICY_PROMPT = "从前方开始，让接下来生成的区域变成一片废弃研究基地。"
HISTORY_PROMPT = "十年前这里开始持续下雪，而且一直没有停。"


class DemoInterpretRequest(StrictContractModel):
    prompt: Annotated[str, Field(min_length=1)]
    current_ir: dict[str, Any] | None
    evolution_state: WorldEvolutionStateV0
    frontier_coord: Annotated[list[int], Field(min_length=2, max_length=2)]
    current_chunk_coord: Annotated[list[int], Field(min_length=2, max_length=2)] | None = None


class DemoInterpretResult(StrictContractModel):
    status: Literal["ok"]
    action: Literal["compile_world", "set_future_policy", "add_history"]
    future_policy: FuturePolicyV0 | None = None
    history_event: PersistentClimateHistoryV0 | None = None


def interpret_demo_prompt(request: DemoInterpretRequest) -> DemoInterpretResult:
    """Intentionally narrow keyword matcher used only by the mock server."""
    prompt = request.prompt.strip()
    next_sequence = request.evolution_state.revision + 1
    if ("十年前" in prompt or "历史" in prompt) and "雪" in prompt:
        return DemoInterpretResult(
            status="ok",
            action="add_history",
            history_event=PersistentClimateHistoryV0(
                id=f"history_{next_sequence:03d}",
                kind="persistent_climate",
                effect="snow",
                scope="global",
                since_years_ago=10,
                sequence=next_sequence,
            ),
        )
    if ("前方" in prompt or "接下来" in prompt or "未来" in prompt) and (
        "研究" in prompt or "基地" in prompt
    ):
        return DemoInterpretResult(
            status="ok",
            action="set_future_policy",
            future_policy=FuturePolicyV0(
                id=f"future_policy_{next_sequence:03d}",
                kind="environment_override",
                target_profile="research_base",
                scope="future_unresolved_chunks",
                created_at_frontier=list(request.frontier_coord),
                sequence=next_sequence,
                revision=next_sequence,
            ),
        )
    return DemoInterpretResult(status="ok", action="compile_world")

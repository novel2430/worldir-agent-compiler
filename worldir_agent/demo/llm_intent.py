from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from ..json_utils import parse_json_object
from ..llm import LLM
from ..runtime.models import StrictContractModel
from .mock_intent import DemoInterpretRequest, DemoInterpretResult
from .models import FuturePolicyV0, PersistentClimateHistoryV0


class DemoIntentValidationError(RuntimeError):
    pass


class IntentDecision(StrictContractModel):
    action: Literal["compile_world", "set_future_policy", "add_history"]
    environment: Literal["research_base"] | None = None
    kind: Literal["persistent_climate"] | None = None
    effect: Literal["snow"] | None = None
    since_years_ago: int | None = None


class DemoIntentInterpreterProtocol(Protocol):
    def interpret(self, request: DemoInterpretRequest) -> DemoInterpretResult: ...


class LLMDemoIntentInterpreter:
    """Narrow real-LLM router for the three interactive demo capabilities."""

    def __init__(self, llm: LLM, prompt_path: str | Path) -> None:
        self.llm = llm
        self.prompt_template = Path(prompt_path).read_text(encoding="utf-8")

    def interpret(self, request: DemoInterpretRequest) -> DemoInterpretResult:
        context = {
            "user_prompt": request.prompt,
            "current_world_ir": request.current_ir,
            "world_evolution_state": request.evolution_state.model_dump(
                mode="json", exclude_none=True
            ),
            "current_chunk_coord": request.current_chunk_coord,
            "frontier_coord": request.frontier_coord,
        }
        prompt = self.prompt_template.replace(
            "{{DEMO_CONTEXT_JSON}}",
            json.dumps(context, ensure_ascii=False, indent=2),
        )
        decision = self._validated_decision(prompt)
        next_sequence = request.evolution_state.revision + 1
        if decision.action == "set_future_policy":
            if decision.environment != "research_base":
                raise DemoIntentValidationError(
                    "Future intent must target the supported research_base environment"
                )
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
        if decision.action == "add_history":
            if decision.kind != "persistent_climate" or decision.effect != "snow":
                raise DemoIntentValidationError(
                    "History intent must be the supported persistent snow climate"
                )
            return DemoInterpretResult(
                status="ok",
                action="add_history",
                history_event=PersistentClimateHistoryV0(
                    id=f"history_{next_sequence:03d}",
                    kind="persistent_climate",
                    effect="snow",
                    scope="global",
                    since_years_ago=max(0, decision.since_years_ago or 0),
                    sequence=next_sequence,
                ),
            )
        return DemoInterpretResult(status="ok", action="compile_world")

    def _validated_decision(self, prompt: str) -> IntentDecision:
        raw = self.llm.complete("demo_intent", prompt)
        try:
            return IntentDecision.model_validate(parse_json_object(raw))
        except (ValueError, ValidationError) as first_error:
            repair_prompt = (
                prompt
                + "\n\nYour previous response was invalid:\n"
                + raw
                + "\nReturn exactly one valid JSON object matching the output schema."
            )
            repaired = self.llm.complete("demo_intent_repair", repair_prompt)
            try:
                return IntentDecision.model_validate(parse_json_object(repaired))
            except (ValueError, ValidationError) as second_error:
                raise DemoIntentValidationError(
                    "The LLM returned malformed or unsupported intent JSON"
                ) from second_error

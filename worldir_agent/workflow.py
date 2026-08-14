from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .config import WorkflowConfig
from .json_utils import parse_json_object, pretty_json
from .llm import LLM
from .prompts import PromptStore
from .runtime.models import CompileDraft, RuntimeContext
from .runtime.validator import RuntimeContractValidator
from .schema import IRSpec, IRValidator
from .trace import RunTrace, TraceEvent


class WorkflowError(RuntimeError):
    pass


@dataclass(slots=True)
class WorkflowResult:
    status: str
    ir: dict[str, Any] | None
    detail: dict[str, Any]
    trace: RunTrace
    runtime_bindings: list[dict[str, Any]] = field(default_factory=list)
    runtime_fact_ops: list[dict[str, Any]] = field(default_factory=list)


class WorldIRWorkflow:
    def __init__(
        self,
        llm: LLM,
        prompts: PromptStore,
        spec: IRSpec,
        config: WorkflowConfig,
        route_override: str = "auto",
        runtime_semantics: str | None = None,
    ):
        self.llm = llm
        self.prompts = prompts
        self.spec = spec
        self.validator = IRValidator(spec)
        self.runtime_validator = RuntimeContractValidator()
        self.config = config
        self.route_override = route_override
        self.runtime_semantics = runtime_semantics
        self.last_trace: RunTrace | None = None

    def _render_ir_prompt(self, name: str, **values: str) -> str:
        """Render an IR-aware prompt with one shared structural + semantic contract."""
        return self.prompts.render(
            name,
            IR_SCHEMA_JSON=self.spec.pretty(),
            IR_SEMANTIC_GUIDANCE=self.spec.semantic_guidance,
            **values,
        )

    def _render_edit_prompt(
        self,
        name: str,
        runtime_context: RuntimeContext,
        **values: str,
    ) -> str:
        return self._render_ir_prompt(
            name,
            RUNTIME_SEMANTICS=(
                self.runtime_semantics
                if self.runtime_semantics is not None
                else self.prompts.read("common/runtime_semantics")
            ),
            RUNTIME_CONTEXT_JSON=pretty_json(
                runtime_context.model_dump(mode="json", exclude_none=True)
            ),
            **values,
        )

    def run(
        self,
        user_prompt: str,
        current_ir: dict[str, Any] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        if current_ir is None:
            return self._run_initial(user_prompt)
        require_compile_draft = runtime_context is not None
        context = RuntimeContext.model_validate(
            runtime_context or {"version": "1", "facts": []}
        )
        return self._run_edit(
            user_prompt,
            current_ir,
            context,
            require_compile_draft=require_compile_draft,
        )

    def _call_json(self, trace: RunTrace, node: str, prompt: str, attempt: int = 1) -> dict[str, Any]:
        current_prompt = prompt
        current_node = node
        raw = ""

        for repair_attempt in range(0, self.config.json_repair_max_attempts + 1):
            try:
                raw = self.llm.complete(current_node, current_prompt)
            except Exception as exc:
                trace.add(TraceEvent(
                    node=current_node,
                    attempt=attempt,
                    prompt=current_prompt,
                    raw_response=raw,
                    parsed_response={"llm_error": str(exc)},
                ))
                raise

            try:
                parsed = parse_json_object(raw)
            except Exception as exc:
                trace.add(TraceEvent(
                    node=current_node,
                    attempt=attempt,
                    prompt=current_prompt,
                    raw_response=raw,
                    parsed_response={"parse_error": str(exc)},
                ))
                if repair_attempt >= self.config.json_repair_max_attempts:
                    raise WorkflowError(
                        f"{node} returned invalid JSON after {repair_attempt + 1} response attempt(s): {exc}"
                    ) from exc

                current_node = f"{node}_json_repair"
                current_prompt = self.prompts.render(
                    "json_repair",
                    ORIGINAL_PROMPT=prompt,
                    RAW_RESPONSE=raw if raw else "<EMPTY RESPONSE>",
                    PARSE_ERROR=str(exc),
                )
                continue

            trace.add(TraceEvent(
                node=current_node,
                attempt=attempt,
                prompt=current_prompt,
                raw_response=raw,
                parsed_response=parsed,
            ))
            return parsed

        raise AssertionError("unreachable")

    def _run_initial(self, user_prompt: str) -> WorkflowResult:
        trace = RunTrace(mode="initial")
        self.last_trace = trace
        feedback = "None"
        for attempt in range(1, self.config.initial_max_attempts + 1):
            prompt = self._render_ir_prompt(
                "initial_translator",
                USER_PROMPT=user_prompt,
                VALIDATION_FEEDBACK=feedback,
            )
            candidate = self._call_json(trace, "initial_translator", prompt, attempt)
            local = self.validator.validate(candidate)
            trace.add_validation(
                layer="world_ir",
                attempt=attempt,
                valid=local.valid,
                issues=local.issues,
            )
            if local.valid:
                return WorkflowResult("ok", candidate, {"mode": "initial"}, trace)
            feedback = "\n".join(f"- {x}" for x in local.issues)
        return WorkflowResult(
            "validation_failed",
            None,
            {"issues": local.issues},
            trace,
        )

    def _run_edit(
        self,
        user_prompt: str,
        current_ir: dict[str, Any],
        runtime_context: RuntimeContext,
        *,
        require_compile_draft: bool,
    ) -> WorkflowResult:
        trace = RunTrace(mode="edit")
        self.last_trace = trace
        current_validation = self.validator.validate(current_ir)
        trace.add_validation(
            layer="current_world_ir",
            attempt=1,
            valid=current_validation.valid,
            issues=current_validation.issues,
        )
        if not current_validation.valid:
            return WorkflowResult(
                "invalid_current_state",
                None,
                {"issues": current_validation.issues},
                trace,
            )

        route = self.route_override
        router_output: dict[str, Any] = {"route": route}
        if route == "auto":
            prompt = self._render_edit_prompt(
                "router",
                runtime_context,
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
            )
            router_output = self._call_json(trace, "router", prompt)
            route = str(router_output.get("route", "")).lower()

        if route not in {"bypass", "deliberate"}:
            raise WorkflowError(f"Router returned unsupported route: {route!r}")

        if route == "bypass":
            semantic_intent = {
                "source": "direct",
                "instruction": user_prompt,
                "preserve_unspecified_state": True,
            }
        else:
            semantic_intent = self._planner_loop(
                trace,
                user_prompt,
                current_ir,
                runtime_context,
            )
            if semantic_intent is None:
                return WorkflowResult(
                    "planning_failed",
                    None,
                    {"route": route},
                    trace,
                )

        express_prompt = self._render_edit_prompt(
            "expressibility",
            runtime_context,
            CURRENT_IR=pretty_json(current_ir),
            USER_PROMPT=user_prompt,
            SEMANTIC_INTENT=pretty_json(semantic_intent),
        )
        express = self._call_json(trace, "expressibility", express_prompt)
        if not bool(express.get("expressible")):
            return WorkflowResult(
                "ir_gap",
                None,
                {
                    "route": route,
                    "router": router_output,
                    "semantic_intent": semantic_intent,
                    "expressibility": express,
                },
                trace,
            )

        editor_feedback = "None"
        for attempt in range(1, self.config.editor_max_attempts + 1):
            editor_prompt = self._render_edit_prompt(
                "editor",
                runtime_context,
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                SEMANTIC_INTENT=pretty_json(semantic_intent),
                VALIDATION_FEEDBACK=editor_feedback,
            )
            editor_output = self._call_json(trace, "editor", editor_prompt, attempt)
            draft, draft_issues = self._parse_compile_draft(
                editor_output,
                require_compile_draft=require_compile_draft,
            )
            if draft is None:
                trace.add_validation(
                    layer="compile_draft",
                    attempt=attempt,
                    valid=False,
                    issues=draft_issues,
                )
                editor_feedback = "Compile Draft contract errors:\n" + "\n".join(
                    f"- {issue}" for issue in draft_issues
                )
                continue
            trace.add_validation(
                layer="compile_draft",
                attempt=attempt,
                valid=True,
                issues=[],
            )

            candidate = draft.world_ir
            local = self.validator.validate(candidate)
            trace.add_validation(
                layer="world_ir",
                attempt=attempt,
                valid=local.valid,
                issues=local.issues,
            )
            if not local.valid:
                editor_feedback = "Local schema/reference errors:\n" + "\n".join(f"- {x}" for x in local.issues)
                continue

            runtime_validation = self.runtime_validator.validate(draft, runtime_context)
            trace.add_validation(
                layer="runtime_contract",
                attempt=attempt,
                valid=runtime_validation.valid,
                issues=runtime_validation.issues,
            )
            if not runtime_validation.valid:
                editor_feedback = "Runtime contract errors:\n" + "\n".join(
                    f"- {issue}" for issue in runtime_validation.issues
                )
                continue

            validator_prompt = self._render_edit_prompt(
                "ir_validator",
                runtime_context,
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                SEMANTIC_INTENT=pretty_json(semantic_intent),
                CANDIDATE_DRAFT=pretty_json(draft.model_dump(mode="json")),
            )
            verdict = self._call_json(trace, "ir_validator", validator_prompt, attempt)
            semantic_valid = bool(verdict.get("valid"))
            trace.add_validation(
                layer="semantic",
                attempt=attempt,
                valid=semantic_valid,
                issues=verdict.get("issues") or verdict.get("critique") or [],
            )
            if semantic_valid:
                return WorkflowResult(
                    "ok",
                    candidate,
                    {
                        "mode": "edit",
                        "route": route,
                        "router": router_output,
                        "semantic_intent": semantic_intent,
                        "expressibility": express,
                    },
                    trace,
                    runtime_bindings=[
                        binding.model_dump(mode="json")
                        for binding in draft.runtime_bindings
                    ],
                    runtime_fact_ops=[
                        operation.model_dump(mode="json")
                        for operation in draft.runtime_fact_ops
                    ],
                )
            editor_feedback = str(verdict.get("critique") or verdict.get("issues") or "Validator rejected the candidate IR")

        return WorkflowResult(
            "validation_failed",
            None,
            {
                "route": route,
                "semantic_intent": semantic_intent,
                "last_feedback": editor_feedback,
            },
            trace,
        )

    def _parse_compile_draft(
        self,
        editor_output: dict[str, Any],
        *,
        require_compile_draft: bool,
    ) -> tuple[CompileDraft | None, list[str]]:
        try:
            return CompileDraft.model_validate(editor_output), []
        except ValidationError as exc:
            # Preserve the pre-server CLI/test contract for calls that do not
            # supply Runtime Context. The Server path always requires a draft.
            if not require_compile_draft and set(editor_output) == set(self.spec.data["root_collections"]):
                return CompileDraft(
                    world_ir=editor_output,
                    runtime_bindings=[],
                    runtime_fact_ops=[],
                ), []
            return None, [error["msg"] for error in exc.errors()]

    def _planner_loop(
        self,
        trace: RunTrace,
        user_prompt: str,
        current_ir: dict[str, Any],
        runtime_context: RuntimeContext,
    ) -> dict[str, Any] | None:
        # Default experimental path: one Planner pass, then continue directly to
        # expressibility checking. The subjective Planner Checker is optional.
        if not self.config.use_planner_checker:
            planner_prompt = self._render_edit_prompt(
                "planner",
                runtime_context,
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                CHECKER_FEEDBACK="None",
            )
            return self._call_json(trace, "planner", planner_prompt, 1)

        critique = "None"
        for attempt in range(1, self.config.planner_max_attempts + 1):
            planner_prompt = self._render_edit_prompt(
                "planner",
                runtime_context,
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                CHECKER_FEEDBACK=critique,
            )
            plan = self._call_json(trace, "planner", planner_prompt, attempt)

            checker_prompt = self._render_edit_prompt(
                "planner_checker",
                runtime_context,
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                PLAN=pretty_json(plan),
            )
            check = self._call_json(trace, "planner_checker", checker_prompt, attempt)
            if str(check.get("status", "")).lower() == "pass":
                return plan
            critique = str(check.get("critique") or "Planner checker requested a retry")
        return None

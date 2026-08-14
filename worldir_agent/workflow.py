from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import WorkflowConfig
from .json_utils import parse_json_object, pretty_json
from .llm import LLM
from .prompts import PromptStore
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


class WorldIRWorkflow:
    def __init__(
        self,
        llm: LLM,
        prompts: PromptStore,
        spec: IRSpec,
        config: WorkflowConfig,
        route_override: str = "auto",
    ):
        self.llm = llm
        self.prompts = prompts
        self.spec = spec
        self.validator = IRValidator(spec)
        self.config = config
        self.route_override = route_override
        self.last_trace: RunTrace | None = None

    def _render_ir_prompt(self, name: str, **values: str) -> str:
        """Render an IR-aware prompt with one shared structural + semantic contract."""
        return self.prompts.render(
            name,
            IR_SCHEMA_JSON=self.spec.pretty(),
            IR_SEMANTIC_GUIDANCE=self.spec.semantic_guidance,
            **values,
        )

    def run(self, user_prompt: str, current_ir: dict[str, Any] | None = None) -> WorkflowResult:
        if current_ir is None:
            return self._run_initial(user_prompt)
        return self._run_edit(user_prompt, current_ir)

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
            if local.valid:
                return WorkflowResult("ok", candidate, {"mode": "initial"}, trace)
            feedback = "\n".join(f"- {x}" for x in local.issues)
        return WorkflowResult(
            "validation_failed",
            None,
            {"issues": local.issues},
            trace,
        )

    def _run_edit(self, user_prompt: str, current_ir: dict[str, Any]) -> WorkflowResult:
        trace = RunTrace(mode="edit")
        self.last_trace = trace
        current_validation = self.validator.validate(current_ir)
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
            prompt = self._render_ir_prompt(
                "router",
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
            semantic_intent = self._planner_loop(trace, user_prompt, current_ir)
            if semantic_intent is None:
                return WorkflowResult(
                    "planning_failed",
                    None,
                    {"route": route},
                    trace,
                )

        express_prompt = self._render_ir_prompt(
            "expressibility",
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
            editor_prompt = self._render_ir_prompt(
                "editor",
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                SEMANTIC_INTENT=pretty_json(semantic_intent),
                VALIDATION_FEEDBACK=editor_feedback,
            )
            candidate = self._call_json(trace, "editor", editor_prompt, attempt)
            local = self.validator.validate(candidate)
            if not local.valid:
                editor_feedback = "Local schema/reference errors:\n" + "\n".join(f"- {x}" for x in local.issues)
                continue

            validator_prompt = self._render_ir_prompt(
                "ir_validator",
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                SEMANTIC_INTENT=pretty_json(semantic_intent),
                CANDIDATE_IR=pretty_json(candidate),
            )
            verdict = self._call_json(trace, "ir_validator", validator_prompt, attempt)
            if bool(verdict.get("valid")):
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

    def _planner_loop(self, trace: RunTrace, user_prompt: str, current_ir: dict[str, Any]) -> dict[str, Any] | None:
        # Default experimental path: one Planner pass, then continue directly to
        # expressibility checking. The subjective Planner Checker is optional.
        if not self.config.use_planner_checker:
            planner_prompt = self._render_ir_prompt(
                "planner",
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                CHECKER_FEEDBACK="None",
            )
            return self._call_json(trace, "planner", planner_prompt, 1)

        critique = "None"
        for attempt in range(1, self.config.planner_max_attempts + 1):
            planner_prompt = self._render_ir_prompt(
                "planner",
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                CHECKER_FEEDBACK=critique,
            )
            plan = self._call_json(trace, "planner", planner_prompt, attempt)

            checker_prompt = self._render_ir_prompt(
                "planner_checker",
                CURRENT_IR=pretty_json(current_ir),
                USER_PROMPT=user_prompt,
                PLAN=pretty_json(plan),
            )
            check = self._call_json(trace, "planner_checker", checker_prompt, attempt)
            if str(check.get("status", "")).lower() == "pass":
                return plan
            critique = str(check.get("critique") or "Planner checker requested a retry")
        return None

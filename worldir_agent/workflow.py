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
        judge_llm: LLM | None = None,
    ):
        self.llm = llm
        # A separate client instance makes the context boundary explicit. The
        # configured provider/model may still be shared; no generator messages,
        # plans, or semantic intents are passed to this client.
        self.judge_llm = judge_llm or llm
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
            WORLD_CATALOG_JSON=self.spec.catalog_pretty(),
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

    def _call_json(
        self,
        trace: RunTrace,
        node: str,
        prompt: str,
        attempt: int = 1,
        *,
        llm: LLM | None = None,
    ) -> dict[str, Any]:
        current_prompt = prompt
        current_node = node
        raw = ""
        client = llm or self.llm

        for repair_attempt in range(0, self.config.json_repair_max_attempts + 1):
            try:
                raw = client.complete(current_node, current_prompt)
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
        empty_context = RuntimeContext.model_validate({"version": "1", "facts": []})
        last_issues: list[str] = []
        for attempt in range(1, self.config.initial_max_attempts + 1):
            prompt = self._render_ir_prompt(
                "initial_translator",
                USER_PROMPT=user_prompt,
                VALIDATION_FEEDBACK=feedback,
            )
            candidate = self._call_json(trace, "initial_translator", prompt, attempt)
            self._canonicalize_new_distributions(candidate, current_ir=None)
            local = self.validator.validate(candidate)
            trace.add_validation(
                layer="world_ir",
                attempt=attempt,
                valid=local.valid,
                issues=local.issues,
            )
            if not local.valid:
                last_issues = local.issues
                feedback = "Local schema/reference/catalog errors:\n" + "\n".join(
                    f"- {issue}" for issue in local.issues
                )
                continue

            draft = CompileDraft(
                world_ir=candidate,
                runtime_bindings=[],
                runtime_fact_ops=[],
            )
            judgment = self._judge_candidate(
                trace,
                mode="initial",
                user_prompt=user_prompt,
                current_ir=None,
                runtime_context=empty_context,
                draft=draft,
                attempt=attempt,
            )
            if judgment["verdict"] == "pass":
                return WorkflowResult("ok", candidate, {"mode": "initial"}, trace)
            if judgment["verdict"] == "ir_gap":
                return self._judge_ir_gap_result(
                    trace,
                    judgment,
                    detail={"mode": "initial"},
                )

            last_issues = self._judgment_issues(judgment)
            feedback = "Independent Semantic Judge feedback:\n" + "\n".join(
                f"- {issue}" for issue in last_issues
            )
        return WorkflowResult(
            "validation_failed",
            None,
            {"issues": last_issues, "last_feedback": feedback},
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
            self._canonicalize_new_distributions(candidate, current_ir=current_ir)
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

            judgment = self._judge_candidate(
                trace,
                mode="edit",
                user_prompt=user_prompt,
                current_ir=current_ir,
                runtime_context=runtime_context,
                draft=draft,
                attempt=attempt,
            )
            if judgment["verdict"] == "pass":
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
            if judgment["verdict"] == "ir_gap":
                return self._judge_ir_gap_result(
                    trace,
                    judgment,
                    detail={
                        "mode": "edit",
                        "route": route,
                        "router": router_output,
                        "semantic_intent": semantic_intent,
                    },
                )
            editor_feedback = "Independent Semantic Judge feedback:\n" + "\n".join(
                f"- {issue}" for issue in self._judgment_issues(judgment)
            )

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

    def _canonicalize_new_distributions(
        self,
        candidate: dict[str, Any],
        *,
        current_ir: dict[str, Any] | None,
    ) -> None:
        """Make the amount of newly created V2 Distributions explicit.

        The V2 schema deliberately keeps population.amount optional. Compiler
        output is stricter: a new Distribution without an amount receives the
        canonical medium density so a Backend never supplies a hidden default.
        Existing Distributions are left untouched during edits, and a density
        profile remains authoritative rather than receiving a conflicting
        uniform-density amount.
        """
        if self.spec.data.get("version") != "World IR V2":
            return

        distributions = candidate.get("distributions")
        if not isinstance(distributions, list):
            return

        existing_ids: set[str] = set()
        if isinstance(current_ir, dict):
            current_distributions = current_ir.get("distributions")
            if isinstance(current_distributions, list):
                existing_ids = {
                    item["id"]
                    for item in current_distributions
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and item["id"].strip()
                }

        for distribution in distributions:
            if not isinstance(distribution, dict):
                continue
            distribution_id = distribution.get("id")
            if isinstance(distribution_id, str) and distribution_id in existing_ids:
                continue

            if "population" not in distribution:
                distribution["population"] = {
                    "amount": {"mode": "density", "value": "medium"}
                }
                continue
            population = distribution["population"]
            if not isinstance(population, dict):
                continue
            if "amount" not in population and "density_profile" not in population:
                population["amount"] = {"mode": "density", "value": "medium"}

    def _judge_candidate(
        self,
        trace: RunTrace,
        *,
        mode: str,
        user_prompt: str,
        current_ir: dict[str, Any] | None,
        runtime_context: RuntimeContext,
        draft: CompileDraft,
        attempt: int,
    ) -> dict[str, Any]:
        judge_prompt = self._render_edit_prompt(
            "semantic_judge",
            runtime_context,
            MODE=mode,
            CURRENT_IR="null" if current_ir is None else pretty_json(current_ir),
            USER_PROMPT=user_prompt,
            CANDIDATE_DRAFT=pretty_json(draft.model_dump(mode="json")),
        )
        judgment = self._call_json(
            trace,
            "semantic_judge",
            judge_prompt,
            attempt,
            llm=self.judge_llm,
        )
        self._validate_judgment(judgment)
        issues = self._judgment_issues(judgment)
        trace.add_validation(
            layer="semantic_judge",
            attempt=attempt,
            valid=judgment["verdict"] == "pass",
            issues=issues,
        )
        return judgment

    @staticmethod
    def _validate_judgment(judgment: dict[str, Any]) -> None:
        expected_fields = {
            "verdict",
            "faithful",
            "complete",
            "restrained",
            "preserved",
            "unsupported_user_meaning",
            "missing_observable_evidence",
            "invented_content",
            "critique",
        }
        if set(judgment) != expected_fields:
            missing = sorted(expected_fields - set(judgment))
            extra = sorted(set(judgment) - expected_fields)
            raise WorkflowError(
                "Semantic Judge returned an invalid contract shape: "
                f"missing={missing}, extra={extra}"
            )

        verdict = judgment.get("verdict")
        if verdict not in {"pass", "retry", "ir_gap"}:
            raise WorkflowError(
                f"Semantic Judge returned unsupported verdict: {verdict!r}"
            )

        dimensions = ("faithful", "complete", "restrained", "preserved")
        for field_name in dimensions:
            if not isinstance(judgment.get(field_name), bool):
                raise WorkflowError(
                    f"Semantic Judge field {field_name!r} must be boolean"
                )

        list_fields = (
            "unsupported_user_meaning",
            "missing_observable_evidence",
            "invented_content",
        )
        for field_name in list_fields:
            value = judgment.get(field_name)
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise WorkflowError(
                    f"Semantic Judge field {field_name!r} must be a string array"
                )

        critique = judgment.get("critique")
        if not isinstance(critique, str):
            raise WorkflowError("Semantic Judge field 'critique' must be a string")
        if verdict == "pass" and not all(judgment[field] for field in dimensions):
            raise WorkflowError(
                "Semantic Judge pass verdict conflicts with a failed dimension"
            )
        if verdict == "pass" and (
            judgment["unsupported_user_meaning"]
            or judgment["missing_observable_evidence"]
            or judgment["invented_content"]
            or critique.strip()
        ):
            raise WorkflowError(
                "Semantic Judge pass verdict must not contain issues or critique"
            )
        if verdict == "ir_gap" and not judgment["unsupported_user_meaning"]:
            raise WorkflowError(
                "Semantic Judge ir_gap verdict requires unsupported_user_meaning"
            )

    @staticmethod
    def _judgment_issues(judgment: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        for field_name in (
            "unsupported_user_meaning",
            "missing_observable_evidence",
            "invented_content",
        ):
            issues.extend(judgment.get(field_name, []))
        critique = judgment.get("critique", "").strip()
        if critique:
            issues.append(critique)
        return issues

    def _judge_ir_gap_result(
        self,
        trace: RunTrace,
        judgment: dict[str, Any],
        *,
        detail: dict[str, Any],
    ) -> WorkflowResult:
        unsupported = judgment["unsupported_user_meaning"]
        reason = judgment["critique"].strip() or (
            "The independent Semantic Judge found essential meaning outside the active contracts."
        )
        return WorkflowResult(
            "ir_gap",
            None,
            {
                **detail,
                "semantic_judgment": judgment,
                "expressibility": {
                    "expressible": False,
                    "reason": reason,
                    "unsupported": unsupported,
                },
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

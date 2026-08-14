from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .runtime.models import RuntimeContext
from .trace import RunTrace
from .workflow import WorkflowResult, WorldIRWorkflow


@dataclass(slots=True)
class CompilerFailure(RuntimeError):
    message: str
    detail: dict[str, Any]
    trace: RunTrace | None = None

    def __str__(self) -> str:
        return self.message


class CompilerInputError(CompilerFailure):
    """The request envelope is valid, but its compiler input is not."""


class CompilerExecutionError(CompilerFailure):
    """The compiler exhausted its workflow without a contract result."""


class WorldCompiler:
    """Stateless application-facing entry point around the existing workflow."""

    def __init__(
        self,
        workflow: WorldIRWorkflow | Callable[[], WorldIRWorkflow],
    ):
        self._workflow_factory = workflow if callable(workflow) else lambda: workflow

    def compile_world(
        self,
        prompt: str,
        current_ir: dict[str, Any] | None,
        runtime_context: RuntimeContext,
    ) -> WorkflowResult:
        if current_ir is None and runtime_context.facts:
            raise CompilerInputError(
                "Initial generation requires an empty Runtime Context",
                {"runtime_context": "facts must be empty when current_ir is null"},
            )

        workflow = self._workflow_factory()
        try:
            result = workflow.run(
                prompt,
                current_ir,
                runtime_context=runtime_context.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
            )
        except Exception as exc:
            # Keep the original exception type for HTTP error mapping while
            # making the request-local partial trace available to the adapter.
            setattr(exc, "compiler_trace", workflow.last_trace)
            raise
        if result.status in {"ok", "ir_gap"}:
            return result
        if result.status == "invalid_current_state":
            raise CompilerInputError(
                "Current World IR is invalid",
                result.detail,
                result.trace,
            )
        raise CompilerExecutionError(
            f"Compiler workflow ended with status {result.status!r}",
            result.detail,
            result.trace,
        )


def compile_world(
    workflow: WorldIRWorkflow,
    prompt: str,
    current_ir: dict[str, Any] | None,
    runtime_context: RuntimeContext,
) -> WorkflowResult:
    """Functional entry point for CLI/tests that do not need a long-lived wrapper."""
    return WorldCompiler(workflow).compile_world(prompt, current_ir, runtime_context)

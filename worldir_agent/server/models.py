from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from ..runtime.models import (
    RuntimeBinding,
    RuntimeContext,
    RuntimeFactOp,
    StrictContractModel,
)


class CompileRequest(StrictContractModel):
    prompt: Annotated[str, Field(min_length=1)]
    current_ir: dict[str, Any] | None
    runtime_context: RuntimeContext


class CompileMeta(StrictContractModel):
    request_id: Annotated[str, Field(min_length=1)]
    mode: Literal["initial", "edit"]
    route: Literal["bypass", "deliberate"] = None  # type: ignore[assignment]


class CompileResultOk(StrictContractModel):
    status: Literal["ok"]
    world_ir: dict[str, Any]
    runtime_bindings: list[RuntimeBinding]
    runtime_fact_ops: list[RuntimeFactOp]
    meta: CompileMeta


class IRGap(StrictContractModel):
    reason: Annotated[str, Field(min_length=1)]
    unsupported: list[str]


class CompileResultIRGap(StrictContractModel):
    status: Literal["ir_gap"]
    gap: IRGap
    meta: CompileMeta


CompileResult = Annotated[
    CompileResultOk | CompileResultIRGap,
    Field(discriminator="status"),
]


class HealthResult(StrictContractModel):
    status: Literal["ok"]


class InfoResult(StrictContractModel):
    compiler_version: Literal["0.3.0"]
    world_ir_version: Literal["2"]
    world_catalog_version: Literal["1"]
    runtime_context_version: Literal["1"]
    compile_result_version: Literal["1"]

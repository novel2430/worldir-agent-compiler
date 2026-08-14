from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import CompileDraft, RuntimeContext


@dataclass(slots=True)
class RuntimeValidationResult:
    valid: bool
    issues: list[str]


class RuntimeContractValidator:
    """Validate references that cross Compile Draft and Runtime Context."""

    def validate(
        self,
        draft: CompileDraft,
        runtime_context: RuntimeContext,
    ) -> RuntimeValidationResult:
        issues: list[str] = []
        ir_ids = self._world_ir_ids(draft.world_ir)
        runtime_ids = runtime_context.fact_ids

        for index, binding in enumerate(draft.runtime_bindings):
            if binding.ir_object_id not in ir_ids:
                issues.append(
                    f"runtime_bindings[{index}].ir_object_id references unknown candidate IR id: "
                    f"{binding.ir_object_id}"
                )
            if binding.runtime_fact_id not in runtime_ids:
                issues.append(
                    f"runtime_bindings[{index}].runtime_fact_id references unknown Runtime Fact id: "
                    f"{binding.runtime_fact_id}"
                )

        for index, operation in enumerate(draft.runtime_fact_ops):
            if operation.runtime_fact_id not in runtime_ids:
                issues.append(
                    f"runtime_fact_ops[{index}].runtime_fact_id references unknown Runtime Fact id: "
                    f"{operation.runtime_fact_id}"
                )

        return RuntimeValidationResult(valid=not issues, issues=issues)

    @staticmethod
    def _world_ir_ids(world_ir: dict[str, Any]) -> set[str]:
        ids: set[str] = set()
        for collection in ("regions", "networks", "entities", "distributions"):
            values = world_ir.get(collection, [])
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, dict) and isinstance(value.get("id"), str):
                    ids.add(value["id"])
        return ids

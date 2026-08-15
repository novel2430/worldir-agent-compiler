from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ..runtime.models import RuntimeContext
from ..schema import IRSpec, IRValidator
from ..trace import RunTrace
from ..workflow import WorkflowResult


MOCK_WORLD_IR: dict[str, Any] = {
    "regions": [
        {"id": "coast_west", "type": "coast", "placement": {"anchor": "west"}},
        {
            "id": "forest_west",
            "type": "forest",
            "placement": {
                "anchor": "west",
                "relations": [{"type": "near", "target": "coast_west"}],
            },
        },
    ],
    "networks": [{
        "id": "west_east_path",
        "type": "path",
        "topology": {"from": "west", "to": "east", "via": ["forest_west"]},
        "placement": {"relations": [{"type": "inside", "target": "forest_west"}]},
    }],
    "entities": [],
    "distributions": [{
        "id": "forest_trees",
        "type": "tree",
        "placement": {"relations": [{"type": "inside", "target": "forest_west"}]},
        "population": {
            "amount": {"mode": "density", "value": "high"},
            "arrangement": {"type": "clustered"},
        },
    }],
}


class MockWorldCompiler:
    """Deterministic dev compiler that still returns Compile Result V1."""

    fingerprint = "mock-world-ir-v2-coastal-forest-v1"

    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[2]
        validation = IRValidator(IRSpec(root / "config/world_ir_v2.json")).validate(
            MOCK_WORLD_IR
        )
        if not validation.valid:
            raise ValueError(f"Built-in mock World IR is invalid: {validation.issues}")

    def compile_world(
        self,
        prompt: str,
        current_ir: dict[str, Any] | None,
        runtime_context: RuntimeContext,
    ) -> WorkflowResult:
        del prompt, runtime_context
        mode = "initial" if current_ir is None else "edit"
        result = copy.deepcopy(MOCK_WORLD_IR if current_ir is None else current_ir)
        return WorkflowResult(
            status="ok",
            ir=result,
            detail={"mode": mode, "mock": True},
            trace=RunTrace(mode=mode),
        )

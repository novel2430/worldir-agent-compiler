"""API-key-free ASGI app used by the documented local smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path

from worldir_agent.compiler import WorldCompiler
from worldir_agent.config import TraceConfig, WorkflowConfig
from worldir_agent.llm import LLMProviderError
from worldir_agent.prompts import PromptStore
from worldir_agent.schema import IRSpec
from worldir_agent.server.app import create_app
from worldir_agent.trace import ServerTraceWriter
from worldir_agent.workflow import WorldIRWorkflow


ROOT = Path(__file__).resolve().parents[1]


class SmokeLLM:
    def complete(self, node: str, prompt: str) -> str:
        if "SMOKE_PROVIDER_FAILURE" in prompt:
            raise LLMProviderError("smoke provider failure")
        state = json.loads(
            (ROOT / "examples/state0_v2.json").read_text(encoding="utf-8")
        )
        if node == "initial_translator":
            return json.dumps(state)
        if node == "router":
            return json.dumps({"route": "bypass", "reason": "explicit smoke edit"})
        if node == "expressibility":
            return json.dumps({
                "expressible": True,
                "reason": "World IR plus Runtime Binding V1 can execute the edit",
                "unsupported": [],
            })
        if node == "editor":
            state["entities"].append({
                "id": "tent_in_clearing",
                "type": "tent",
                "placement": {
                    "relations": [
                        {"type": "inside", "target": "coastal_forest"}
                    ]
                },
            })
            return json.dumps({
                "world_ir": state,
                "runtime_bindings": [{
                    "ir_object_id": "tent_in_clearing",
                    "runtime_fact_id": "clearing_01",
                    "placement": "inside",
                }],
                "runtime_fact_ops": [],
            })
        if node == "semantic_judge":
            return json.dumps({
                "verdict": "pass",
                "faithful": True,
                "complete": True,
                "restrained": True,
                "preserved": True,
                "unsupported_user_meaning": [],
                "missing_observable_evidence": [],
                "invented_content": [],
                "critique": "",
            })
        raise AssertionError(f"Unexpected smoke compiler node: {node}")


def workflow_factory() -> WorldIRWorkflow:
    return WorldIRWorkflow(
        llm=SmokeLLM(),
        prompts=PromptStore(ROOT / "prompts"),
        spec=IRSpec(ROOT / "config/world_ir_v2.json"),
        config=WorkflowConfig(),
    )


trace_dir = os.environ.get("WORLDIR_SMOKE_TRACE_DIR")
trace_writer = (
    ServerTraceWriter(TraceConfig(enabled=True, dir=trace_dir))
    if trace_dir
    else None
)
app = create_app(WorldCompiler(workflow_factory), trace_writer=trace_writer)

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(slots=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str
    api_key_env: str
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_seconds: int = 90
    anthropic_version: str = "2023-06-01"


@dataclass(slots=True)
class WorkflowConfig:
    use_planner_checker: bool = False
    planner_max_attempts: int = 3
    editor_max_attempts: int = 3
    initial_max_attempts: int = 3
    json_repair_max_attempts: int = 1


@dataclass(slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    log_level: str = "info"


@dataclass(slots=True)
class IRConfig:
    version: str = "2"
    spec: str = "config/world_ir_v2.json"
    semantics: str = "config/world_ir_v2_semantics.md"


@dataclass(slots=True)
class RuntimeConfig:
    context_version: str = "1"


@dataclass(slots=True)
class PromptsConfig:
    dir: str = "prompts"
    runtime_semantics: str = "prompts/common/runtime_semantics.md"


@dataclass(slots=True)
class TraceConfig:
    enabled: bool = True
    dir: str = "runs"
    store_prompts: bool = True
    store_raw_responses: bool = True


@dataclass(slots=True)
class AppConfig:
    llm: LLMConfig
    workflow: WorkflowConfig
    server: ServerConfig = field(default_factory=ServerConfig)
    ir: IRConfig = field(default_factory=IRConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    llm = LLMConfig(**data["llm"])
    workflow = WorkflowConfig(**data.get("workflow", {}))
    server = ServerConfig(**data.get("server", {}))
    ir = IRConfig(**data.get("ir", {}))
    runtime = RuntimeConfig(**data.get("runtime", {}))
    prompts = PromptsConfig(**data.get("prompts", {}))
    trace = TraceConfig(**data.get("trace", {}))
    return AppConfig(
        llm=llm,
        workflow=workflow,
        server=server,
        ir=ir,
        runtime=runtime,
        prompts=prompts,
        trace=trace,
    )

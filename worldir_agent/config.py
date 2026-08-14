from __future__ import annotations

from dataclasses import dataclass
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
class AppConfig:
    llm: LLMConfig
    workflow: WorkflowConfig


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    llm = LLMConfig(**data["llm"])
    workflow = WorkflowConfig(**data.get("workflow", {}))
    return AppConfig(llm=llm, workflow=workflow)

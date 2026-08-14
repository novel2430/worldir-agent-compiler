from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .config import TraceConfig


@dataclass(slots=True)
class TraceEvent:
    node: str
    attempt: int
    prompt: str
    raw_response: str
    parsed_response: Any = None


@dataclass(slots=True)
class RunTrace:
    mode: str
    events: list[TraceEvent] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)

    def add(self, event: TraceEvent) -> None:
        self.events.append(event)

    def add_validation(
        self,
        *,
        layer: str,
        attempt: int,
        valid: bool,
        issues: Any,
    ) -> None:
        self.validations.append({
            "layer": layer,
            "attempt": attempt,
            "valid": valid,
            "issues": issues,
        })

    def to_dict(
        self,
        *,
        store_prompts: bool = True,
        store_raw_responses: bool = True,
    ) -> dict:
        events = []
        for event in self.events:
            payload = asdict(event)
            if not store_prompts:
                payload["prompt"] = None
            if not store_raw_responses:
                payload["raw_response"] = None
            events.append(payload)
        return {
            "mode": self.mode,
            "events": events,
            "validations": self.validations,
        }


class ServerTraceWriter:
    """Persist one trace artifact per compile request when enabled."""

    def __init__(self, config: TraceConfig):
        self.config = config

    def write(
        self,
        *,
        request_id: str,
        request: dict[str, Any],
        trace: RunTrace | None,
        compiler_fingerprint: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> Path | None:
        if not self.config.enabled:
            return None

        day = datetime.now().strftime("%Y%m%d")
        path = Path(self.config.dir) / day / f"{request_id}.trace.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request_id": request_id,
            "compiler_fingerprint": compiler_fingerprint,
            "request": request,
            "workflow": (
                trace.to_dict(
                    store_prompts=self.config.store_prompts,
                    store_raw_responses=self.config.store_raw_responses,
                )
                if trace is not None
                else None
            ),
            "result": result,
            "error": error,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

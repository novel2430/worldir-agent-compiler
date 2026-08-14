from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any


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

    def add(self, event: TraceEvent) -> None:
        self.events.append(event)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "events": [asdict(e) for e in self.events],
        }

from __future__ import annotations

from pathlib import Path


class PromptStore:
    def __init__(self, prompts_dir: str | Path):
        self.prompts_dir = Path(prompts_dir)

    def render(self, name: str, **values: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        text = path.read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        return text

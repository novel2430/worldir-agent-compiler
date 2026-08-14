#!/usr/bin/env python3
"""Make one minimal real Chat Completions call per configured model."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from worldir_agent.config import load_config
from worldir_agent.llm import HTTPJSONLLM


CONFIGS = [
    Path("config/config.oneapi.deepseek-v4-flash.toml"),
    Path("config/config.oneapi.gpt-5.6-sol.toml"),
]


def main() -> int:
    results = []
    for path in CONFIGS:
        config = load_config(path)
        started = time.monotonic()
        try:
            content = HTTPJSONLLM(config.llm).complete(
                "oneapi_smoke",
                "Reply with exactly: ONEAPI_OK",
            )
            result = {
                "model": config.llm.model,
                "ok": True,
                "latency_seconds": round(time.monotonic() - started, 3),
                "response": content.strip()[:200],
            }
        except Exception as exc:
            result = {
                "model": config.llm.model,
                "ok": False,
                "latency_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    result_path = Path("runs/oneapi_smoke_latest.json")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"results: {result_path}", flush=True)
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())

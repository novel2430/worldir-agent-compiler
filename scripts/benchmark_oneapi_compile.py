#!/usr/bin/env python3
"""Benchmark the real HTTP compile endpoint against each OneAPI model."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from worldir_agent.config import load_config


DEFAULT_MODELS = [
    "deepseek-v4-flash",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "MiniMax-M2.7",
]
DEFAULT_PROMPT = (
    "生成一个废弃海边小镇，西边是森林，东边是海岸，一条主路从南到北。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record full /v1/compile outputs and latency for OneAPI models."
    )
    parser.add_argument("--config", default="config/config.toml")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument(
        "--thinking",
        choices=("config", "false", "true", "both"),
        default="config",
        help="Thinking mode(s) to benchmark; default uses the config value.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json_response(request: Request, *, timeout: float) -> tuple[int, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw_body": body}
        return exc.code, payload


def wait_for_server(proc: subprocess.Popen[bytes], health_url: str) -> float:
    started = time.perf_counter()
    deadline = started + 30
    while time.perf_counter() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited during startup with code {proc.returncode}")
        try:
            request = Request(health_url, method="GET")
            status, payload = read_json_response(request, timeout=1)
            if status == 200 and payload == {"status": "ok"}:
                return time.perf_counter() - started
        except (URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise TimeoutError("server did not become healthy within 30 seconds")


def stop_server(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def config_for_model(
    base_config: str,
    model: str,
    thinking: bool | None,
) -> str:
    updated, replacements = re.subn(
        r'(?m)^model\s*=\s*"[^"]*"\s*$',
        f'model = "{model}"',
        base_config,
        count=1,
    )
    if replacements != 1:
        raise ValueError("expected exactly one quoted model entry in the config")
    if thinking is not None:
        thinking_line = f"thinking = {'true' if thinking else 'false'}"
        updated, replacements = re.subn(
            r"(?m)^thinking\s*=\s*(?:true|false)\s*$",
            thinking_line,
            updated,
            count=1,
        )
        if replacements == 0:
            updated, replacements = re.subn(
                r"(?m)^(max_tokens\s*=\s*\d+\s*)$",
                rf"\1\n{thinking_line}",
                updated,
                count=1,
            )
            if replacements != 1:
                raise ValueError("could not insert thinking into the LLM config")
    return updated


def benchmark_model(
    *,
    model: str,
    thinking: bool | None,
    config_text: str,
    payload: dict[str, Any],
    port: int,
    temp_dir: Path,
) -> dict[str, Any]:
    thinking_label = "default" if thinking is None else str(thinking).lower()
    config_path = temp_dir / f"{model}.{thinking_label}.toml"
    config_path.write_text(
        config_for_model(config_text, model, thinking), encoding="utf-8"
    )
    server_log_path = temp_dir / f"{model}.{thinking_label}.server.log"
    result: dict[str, Any] = {
        "model": model,
        "thinking": thinking,
        "started_at": utc_now(),
    }

    with server_log_path.open("wb") as server_log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "worldir_agent.server",
                "--config",
                str(config_path),
            ],
            stdout=server_log,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        try:
            startup_seconds = wait_for_server(
                proc, f"http://127.0.0.1:{port}/health"
            )
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = Request(
                f"http://127.0.0.1:{port}/v1/compile",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            started = time.perf_counter()
            status, output = read_json_response(request, timeout=600)
            result.update(
                {
                    "ok": status == 200,
                    "server_startup_seconds": round(startup_seconds, 3),
                    "compile_seconds": round(time.perf_counter() - started, 3),
                    "http_status": status,
                    "output": output,
                }
            )
        except Exception as exc:
            result.update(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            stop_server(proc)

    result["completed_at"] = utc_now()
    if not result["ok"]:
        result["server_log"] = server_log_path.read_text(
            encoding="utf-8", errors="replace"
        )
    return result


def main() -> int:
    args = parse_args()
    if not os.environ.get("ONEAPI_API_KEY"):
        print("Missing ONEAPI_API_KEY", file=sys.stderr)
        return 2

    config_path = Path(args.config)
    config_text = config_path.read_text(encoding="utf-8")
    config = load_config(config_path)
    port = config.server.port
    payload = {
        "prompt": args.prompt,
        "current_ir": None,
        "runtime_context": {"version": "1", "facts": []},
    }
    benchmark_started = utc_now()
    results: list[dict[str, Any]] = []
    thinking_modes = {
        "config": [config.llm.thinking],
        "false": [False],
        "true": [True],
        "both": [False, True],
    }[args.thinking]

    with tempfile.TemporaryDirectory(prefix="worldir-oneapi-") as raw_temp_dir:
        temp_dir = Path(raw_temp_dir)
        for model in args.models:
            for thinking in thinking_modes:
                print(f"[{model} thinking={thinking}] compiling...", flush=True)
                result = benchmark_model(
                    model=model,
                    thinking=thinking,
                    config_text=config_text,
                    payload=payload,
                    port=port,
                    temp_dir=temp_dir,
                )
                results.append(result)
                summary = {
                    "model": model,
                    "thinking": thinking,
                    "ok": result["ok"],
                    "compile_seconds": result.get("compile_seconds"),
                    "http_status": result.get("http_status"),
                    "error": result.get("error"),
                }
                print(json.dumps(summary, ensure_ascii=False), flush=True)

    report = {
        "benchmark_started_at": benchmark_started,
        "benchmark_completed_at": utc_now(),
        "endpoint": f"http://127.0.0.1:{port}/v1/compile",
        "llm_settings": {
            "base_url": config.llm.base_url,
            "configured_thinking": config.llm.thinking,
            "benchmark_thinking": args.thinking,
        },
        "request": payload,
        "results": results,
    }
    output_dir = Path("runs")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamped_path = output_dir / f"oneapi_compile_benchmark_{timestamp}.json"
    latest_path = output_dir / "oneapi_compile_benchmark_latest.json"
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    timestamped_path.write_text(serialized, encoding="utf-8")
    latest_path.write_text(serialized, encoding="utf-8")
    print(f"report: {timestamped_path}", flush=True)
    print(f"latest: {latest_path}", flush=True)
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import load_config
from .json_utils import pretty_json
from .llm import HTTPJSONLLM
from .prompts import PromptStore
from .schema import IRSpec
from .workflow import WorldIRWorkflow, WorkflowError


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    if args.prompt_file is not None:
        return Path(args.prompt_file).read_text(encoding="utf-8").strip()
    raise SystemExit("Provide --prompt or --prompt-file")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compile a world prompt/edit into World IR using a small LLM-agent workflow.")
    p.add_argument("--prompt", help="User world description or edit instruction")
    p.add_argument("--prompt-file", help="Read user prompt from a text file")
    state_group = p.add_mutually_exclusive_group()
    state_group.add_argument("--state", help="Current World IR JSON file. Omit for initial State0 generation.")
    state_group.add_argument("--state-json", help="Current World IR as an inline JSON string.")
    p.add_argument("--out", help="Write final IR/result JSON to this path")
    p.add_argument("--trace", help="Optional JSON trace containing every node prompt/response")
    p.add_argument("--config", default="config/config.toml", help="TOML config path")
    p.add_argument("--schema", default="config/world_ir_v0.json", help="World IR spec path")
    p.add_argument("--prompts-dir", default="prompts", help="Directory containing external prompt templates")
    p.add_argument("--route", choices=["auto", "bypass", "deliberate"], default="auto", help="Override edit routing for experiments")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    user_prompt = _read_prompt(args)
    current_ir = None
    if args.state:
        current_ir = json.loads(Path(args.state).read_text(encoding="utf-8"))
    elif args.state_json:
        current_ir = json.loads(args.state_json)

    workflow = None
    try:
        config = load_config(args.config)
        workflow = WorldIRWorkflow(
            llm=HTTPJSONLLM(config.llm),
            prompts=PromptStore(args.prompts_dir),
            spec=IRSpec(args.schema),
            config=config.workflow,
            route_override=args.route,
        )
        result = workflow.run(user_prompt, current_ir)
    except (WorkflowError, RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        if args.trace and workflow is not None and workflow.last_trace is not None:
            Path(args.trace).parent.mkdir(parents=True, exist_ok=True)
            Path(args.trace).write_text(
                pretty_json(workflow.last_trace.to_dict()) + "\n", encoding="utf-8"
            )
            print(f"TRACE: partial trace written to {args.trace}", file=sys.stderr)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    payload = result.ir if result.status == "ok" else {
        "status": result.status,
        "detail": result.detail,
    }
    output = pretty_json(payload)
    print(output)
    if args.out:
        Path(args.out).write_text(output + "\n", encoding="utf-8")
    if args.trace:
        Path(args.trace).write_text(pretty_json(result.trace.to_dict()) + "\n", encoding="utf-8")
    return 0 if result.status == "ok" else 1

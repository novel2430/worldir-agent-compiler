from __future__ import annotations

import argparse

import uvicorn

from ..config import load_config
from ..trace import ServerTraceWriter
from .app import build_compiler, create_app
from .cache import CompileCache
from ..demo.llm_intent import LLMDemoIntentInterpreter
from ..llm import HTTPJSONLLM


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local WorldIR LLM Compiler Server V0 sidecar."
    )
    parser.add_argument(
        "--config",
        default="config/config.toml",
        help="TOML configuration path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    compiler = build_compiler(config)
    app = create_app(
        compiler,
        trace_writer=ServerTraceWriter(config.trace),
        compile_cache=CompileCache(config.cache),
        demo_intent_interpreter=LLMDemoIntentInterpreter(
            HTTPJSONLLM(config.llm),
            "prompts/demo_intent.md",
        ),
    )
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

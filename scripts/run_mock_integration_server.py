from __future__ import annotations

import argparse

import uvicorn

from worldir_agent.demo.mock_compiler import MockWorldCompiler
from worldir_agent.server.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline ArtLab integration server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    args = parser.parse_args()
    app = create_app(MockWorldCompiler(), enable_demo_intent=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

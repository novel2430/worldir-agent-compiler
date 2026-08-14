from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from urllib import error, request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run input-only semantic regression cases against a live compiler server."
    )
    parser.add_argument(
        "--cases",
        default="evals/semantic_regression_v1.json",
        help="Input-only semantic regression suite",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8787",
        help="Compiler server base URL",
    )
    parser.add_argument("--out", required=True, help="Write run results to this JSON file")
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser


def _load_current_ir(case: dict[str, Any], suite_path: Path) -> Any:
    if "current_ir_file" not in case:
        return case.get("current_ir")
    path = (suite_path.parent / case["current_ir_file"]).resolve()
    return json.loads(path.read_text(encoding="utf-8"))


def _compile(
    base_url: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        base_url.rstrip("/") + "/v1/compile",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"raw_response": raw}
        return exc.code, detail


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    suite_path = Path(args.cases)
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = suite.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Regression suite cases must be an array")

    results: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Every regression case must be an object")
        payload = {
            "prompt": case["prompt"],
            "current_ir": _load_current_ir(case, suite_path),
            "runtime_context": case.get(
                "runtime_context",
                {"version": "1", "facts": []},
            ),
        }
        status_code, response = _compile(args.base_url, payload, args.timeout)
        results.append({
            "case_id": case["id"],
            "request": payload,
            "http_status": status_code,
            "response": response,
        })

    output = {
        "suite_version": suite.get("version"),
        "run_at": datetime.now().astimezone().isoformat(),
        "base_url": args.base_url,
        "results": results,
    }
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if all(item["http_status"] == 200 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

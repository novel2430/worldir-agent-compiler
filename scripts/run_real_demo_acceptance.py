from __future__ import annotations

import json
import sys
from urllib import error, request


BASE_URL = "http://127.0.0.1:8787"


def call(method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=240) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} failed with HTTP {exc.code}: {detail}") from exc


def interpret(prompt: str, world_ir: dict, evolution: dict) -> dict:
    return call("POST", "/v1/demo/interpret", {
        "prompt": prompt,
        "current_ir": world_ir,
        "evolution_state": evolution,
        "current_chunk_coord": [-1, 0],
        "frontier_coord": [1, 0],
    })


def main() -> int:
    health = call("GET", "/health")
    assert health["status"] == "ok"
    print("REAL_ACCEPTANCE_SERVER passed=true")

    initial_prompt = "我想要一片靠海的森林，里面树很多，还有一条小路。"
    empty_evolution = {"version": "0", "history": []}
    initial_intent = interpret(initial_prompt, {}, empty_evolution)
    assert initial_intent["action"] == "compile_world", initial_intent
    print("REAL_INTENT_INITIAL passed=true action=compile_world")

    compiled = call("POST", "/v1/compile", {
        "prompt": initial_prompt,
        "current_ir": None,
        "runtime_context": {"version": "1", "facts": []},
    })
    assert compiled["status"] == "ok", compiled
    world_ir = compiled["world_ir"]
    print(
        "REAL_COMPILE_INITIAL passed=true regions=%d networks=%d distributions=%d"
        % (
            len(world_ir.get("regions", [])),
            len(world_ir.get("networks", [])),
            len(world_ir.get("distributions", [])),
        )
    )

    plan = call("POST", "/v1/backend/plan", {
        "world_ir": world_ir,
        "world_seed": 12345,
        "backend_target": "godot_artlab_v2",
        "evolution_state": empty_evolution,
    })
    assert plan["status"] == "ok", plan
    print("REAL_BACKEND_PLAN passed=true version=%s" % plan["spatial_plan"]["version"])

    future = interpret(
        "前面的世界从现在开始变成废弃研究基地。",
        world_ir,
        empty_evolution,
    )
    assert future["action"] == "set_future_policy", future
    evolution = {
        "version": "0",
        "future_policy": future["future_policy"],
        "history": [],
    }
    print("REAL_INTENT_FUTURE passed=true action=set_future_policy")

    history = interpret(
        "假设这里十年前就开始下雪，而且从未停止。",
        world_ir,
        evolution,
    )
    assert history["action"] == "add_history", history
    print("REAL_INTENT_HISTORY passed=true action=add_history")
    print("REAL_LLM_ACCEPTANCE_RESULT PASS model=deepseek-v4-flash")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"REAL_LLM_ACCEPTANCE_RESULT FAIL type={type(exc).__name__}", file=sys.stderr)
        raise

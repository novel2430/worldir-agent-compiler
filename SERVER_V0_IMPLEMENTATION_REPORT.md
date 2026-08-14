# Server V0 Implementation Report

## Scope implemented

Implemented the missing LLM Compiler Server V0 code defined by `docs/server_v0/LLM_COMPILER_SERVER_V0_DESIGN.md`:

- FastAPI sidecar with `POST /v1/compile`, `GET /health`, and `GET /info`.
- Runnable `worldir-agent-server` / `python -m worldir_agent.server` entrypoints.
- Strict CompileRequest V1, Runtime Context V1, Runtime Binding V1, Runtime Fact Ops V1, and Compile Result V1 models.
- Runtime binding/fact-op reference validation against candidate World IR and request Runtime Context.
- Runtime-aware Router, Planner, Planner Checker, Expressibility, Editor, and Semantic Validator prompts.
- Editor Compile Draft output (`world_ir`, `runtime_bindings`, `runtime_fact_ops`) and retry feedback for all three deterministic validation layers.
- HTTP 400/422/500/502/504 error mapping and transactional failure responses.
- Request-scoped trace files containing request data, compiler events, deterministic validation results, and final result/error.
- Server, runtime-contract, error-path, trace, and no-partial-result tests using Fake LLMs.

## Files added or changed

Added:

- `worldir_agent/compiler.py`
- `worldir_agent/runtime/`
- `worldir_agent/server/`
- `prompts/common/runtime_semantics.md`
- `tests/test_runtime_contract.py`
- `tests/test_server.py`
- `tests/smoke_server_app.py`
- `uv.lock`

Updated:

- `worldir_agent/workflow.py`, `config.py`, `llm.py`, `prompts.py`, and `trace.py`
- existing compiler prompts, translated faithfully to English and adapted for Runtime Context / Compile Draft
- example TOML configurations, package metadata/entrypoints, README, and one prompt-language assertion in the existing tests

## Existing capabilities reused

The Server calls the existing `WorldIRWorkflow` through `WorldCompiler`. It directly reuses:

- `IRSpec` and `IRValidator`, including World IR V2 semantic guidance;
- `HTTPJSONLLM` provider adapters;
- `PromptStore` and external Markdown prompts;
- Router / Planner / Expressibility / Editor / Validator workflow and retry loops;
- JSON parsing and repair;
- existing trace events.

No second compiler pipeline or additional Agent was introduced.

## Automated tests

Command:

```bash
uv run --extra test python -m unittest discover -s tests -v
```

Result:

```text
Ran 59 tests
OK
```

This includes all original tests and the new Server V0 tests. No test uses a real API key or public LLM endpoint.

## Smoke test

The real configured entrypoint was started with:

```bash
uv run worldir-agent-server --config config/config.example.toml
```

`GET /health` returned HTTP 200 with `{"status":"ok"}`.

An API-key-free Uvicorn process using `tests/smoke_server_app.py` was also started. Results:

- `GET /health`: HTTP 200.
- `GET /info`: HTTP 200 with compiler `0.3.0`, World IR `2`, Runtime Context `1`, and Compile Result `1`.
- valid initial compile: HTTP 200 and a contract-valid Compile Result.
- valid runtime-aware edit: traversed Router → Expressibility → Editor → deterministic validation → Semantic Validator, returning an `inside` binding from `graveyard` to `clearing_01`.
- request missing `prompt`: HTTP 400 without entering the Compiler.

## Design/current-code adaptations

Two existing-code mismatches required minimal compatibility handling:

1. The existing CLI Editor contract returned raw World IR, while Server V0 requires a Compile Draft. Server calls now strictly require a Compile Draft; legacy workflow calls without Runtime Context may still accept raw World IR so the existing CLI/tests remain usable.
2. FastAPI normally reports all request-model validation as HTTP 422. A narrow request-validation handler maps malformed API envelopes to 400 while retaining 422 for invalid Runtime Context, as required by the design.

No unresolved protocol conflict or known unimplemented Server V0 design item remains in this implementation scope. External LLM traffic was not exercised without an API key; the existing provider adapter is reused and its provider/timeout paths are covered at the adapter boundary with controlled test doubles.

## Final review remediation

The final review findings were addressed without changing the Server V0 contracts or workflow:

- connection/read failures and malformed provider payloads are normalized by the existing provider adapter and map to HTTP 502; timeout behavior remains HTTP 504;
- malformed request and invalid Runtime Context responses now write request-level traces with validation detail and HTTP failure category, without fabricated workflow nodes or results;
- Runtime Context `marked_area.count` accepts JSON integer-valued floats such as `23.0`, normalizes them to `int`, and continues to reject fractional, string, and negative values.

The final automated run contains 59 passing tests. External LLM traffic was not attempted without an API key; adapter-boundary network/response failures were exercised with controlled test doubles, and the process-level smoke test covered HTTP 200, 400, 422, and 502 plus validation trace output.

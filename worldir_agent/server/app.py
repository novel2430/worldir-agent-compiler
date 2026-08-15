from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..compiler import (
    CompilerExecutionError,
    CompilerInputError,
    WorldCompiler,
)
from ..config import AppConfig
from ..llm import (
    HTTPJSONLLM,
    LLMConfigurationError,
    LLMProviderError,
    LLMTimeoutError,
)
from ..prompts import PromptStore
from ..schema import IRSpec
from ..trace import RunTrace, ServerTraceWriter
from ..workflow import WorkflowError, WorkflowResult, WorldIRWorkflow
from .cache import CompileCache
from .models import (
    CompileMeta,
    CompileRequest,
    CompileResult,
    CompileResultIRGap,
    CompileResultOk,
    HealthResult,
    IRGap,
    InfoResult,
)
from ..backend.models import SpatialPlanRequest, SpatialPlanResult
from ..backend.spatial_lowerer import SpatialLowerer, SpatialLoweringInvalidIR
from ..demo.mock_intent import (
    DemoInterpretRequest,
    DemoInterpretResult,
    interpret_demo_prompt,
)


class CompilerProtocol(Protocol):
    def compile_world(
        self,
        prompt: str,
        current_ir: dict[str, Any] | None,
        runtime_context: Any,
    ) -> WorkflowResult: ...


def build_compiler(config: AppConfig) -> WorldCompiler:
    if config.ir.version != "2":
        raise ValueError("Server V0 requires World IR version 2")
    if config.runtime.context_version != "1":
        raise ValueError("Server V0 requires Runtime Context version 1")

    spec = IRSpec(config.ir.spec)
    if spec.data.get("version") != "World IR V2":
        raise ValueError("Configured IR spec is not World IR V2")
    if spec.catalog is None or spec.catalog.version != "World Catalog V1":
        raise ValueError("Server V0 requires World Catalog version 1")

    configured_semantics = Path(config.ir.semantics).resolve()
    declared_semantics = (
        Path(config.ir.spec).parent / str(spec.data.get("semantic_guidance_file", ""))
    ).resolve()
    if configured_semantics != declared_semantics:
        raise ValueError(
            "Configured IR semantics path does not match the active IR spec sidecar"
        )

    prompt_store = PromptStore(config.prompts.dir)
    runtime_semantics = Path(config.prompts.runtime_semantics).read_text(
        encoding="utf-8"
    ).strip()
    generator_llm = HTTPJSONLLM(config.llm)
    judge_config = replace(config.llm, temperature=0.0)
    judge_llm = HTTPJSONLLM(judge_config)

    def workflow_factory() -> WorldIRWorkflow:
        return WorldIRWorkflow(
            llm=generator_llm,
            judge_llm=judge_llm,
            prompts=prompt_store,
            spec=spec,
            config=config.workflow,
            runtime_semantics=runtime_semantics,
        )

    fingerprint_payload = {
        "format": 1,
        "generator_llm": asdict(config.llm),
        "judge_llm": asdict(judge_config),
        "workflow": asdict(config.workflow),
        "ir": spec.data,
        "ir_semantic_guidance": spec.semantic_guidance,
        "world_catalog": spec.catalog.data if spec.catalog is not None else None,
        "runtime_context_version": config.runtime.context_version,
        "runtime_semantics": runtime_semantics,
        "prompts": prompt_store.snapshot(),
    }
    fingerprint = sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    return WorldCompiler(workflow_factory, fingerprint=fingerprint)


def create_app(
    compiler: CompilerProtocol,
    *,
    trace_writer: ServerTraceWriter | None = None,
    compile_cache: CompileCache | None = None,
    spatial_lowerer: SpatialLowerer | None = None,
    enable_demo_intent: bool = False,
) -> FastAPI:
    app = FastAPI(title="WorldIR LLM Compiler Server", version="0.3.0")
    compiler_fingerprint = getattr(compiler, "fingerprint", "unspecified")
    lowerer = spatial_lowerer or SpatialLowerer.default()

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # The envelope/API shape is a 400. Runtime Context is a separate
        # semantic contract and therefore maps to the design's 422 path.
        errors = exc.errors()
        malformed_envelope = any(
            "runtime_context" not in error.get("loc", ())
            or (
                error.get("type") == "missing"
                and tuple(error.get("loc", ())) == ("body", "runtime_context")
            )
            for error in errors
        )
        status_code = 400 if malformed_envelope else 422
        if trace_writer is not None:
            request_id = str(uuid4())
            request_payload = await _safe_validation_request(
                request,
                store_prompt=trace_writer.config.store_prompts,
            )
            try:
                _write_trace(
                    trace_writer,
                    request_id=request_id,
                    request=request_payload,
                    trace=None,
                    error={
                        "type": "RequestValidationError",
                        "message": "Request validation failed",
                        "detail": _safe_validation_errors(errors),
                        "http_status": status_code,
                        "category": (
                            "malformed_request"
                            if status_code == 400
                            else "invalid_runtime_context"
                        ),
                    },
                    compiler_fingerprint=compiler_fingerprint,
                )
            except OSError:
                pass
        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder({"detail": errors}),
        )

    @app.get("/health", response_model=HealthResult)
    def health() -> HealthResult:
        return HealthResult(status="ok")

    @app.get("/info", response_model=InfoResult)
    def info() -> InfoResult:
        return InfoResult(
            compiler_version="0.3.0",
            world_ir_version="2",
            world_catalog_version="1",
            runtime_context_version="1",
            compile_result_version="1",
        )

    @app.post(
        "/v1/backend/plan",
        response_model=SpatialPlanResult,
        response_model_exclude_none=True,
    )
    def backend_plan_endpoint(request: SpatialPlanRequest) -> SpatialPlanResult:
        try:
            return lowerer.lower(request)
        except SpatialLoweringInvalidIR as exc:
            raise HTTPException(
                status_code=422,
                detail={"kind": "invalid_world_ir", "issues": exc.issues},
            ) from exc

    if enable_demo_intent:
        @app.post(
            "/v1/demo/interpret",
            response_model=DemoInterpretResult,
            response_model_exclude_none=True,
        )
        def demo_interpret_endpoint(
            request: DemoInterpretRequest,
        ) -> DemoInterpretResult:
            return interpret_demo_prompt(request)

    @app.post(
        "/v1/compile",
        response_model=CompileResult,
        response_model_exclude_none=True,
    )
    def compile_endpoint(request: CompileRequest) -> CompileResult:
        request_id = str(uuid4())
        request_payload = {
            "prompt": request.prompt,
            "current_ir": request.current_ir,
            "runtime_context": request.runtime_context.model_dump(
                mode="json",
                exclude_none=True,
            ),
        }
        trace: RunTrace | None = None
        response: CompileResultOk | CompileResultIRGap | None = None

        if compile_cache is not None:
            cached = compile_cache.get(
                request_payload,
                request_id=request_id,
                compiler_fingerprint=compiler_fingerprint,
            )
            if cached is not None:
                _write_trace(
                    trace_writer,
                    request_id=request_id,
                    request=request_payload,
                    trace=None,
                    result=cached.model_dump(mode="json", exclude_none=True),
                    compiler_fingerprint=compiler_fingerprint,
                )
                return cached

        try:
            workflow_result = compiler.compile_world(
                request.prompt,
                request.current_ir,
                request.runtime_context,
            )
            trace = workflow_result.trace
            response = _to_compile_result(request_id, workflow_result)
            if compile_cache is not None:
                compile_cache.put(
                    request_payload,
                    response,
                    compiler_fingerprint=compiler_fingerprint,
                )
            _write_trace(
                trace_writer,
                request_id=request_id,
                request=request_payload,
                trace=trace,
                result=response.model_dump(mode="json", exclude_none=True),
                compiler_fingerprint=compiler_fingerprint,
            )
            return response
        except CompilerInputError as exc:
            trace = exc.trace
            _write_trace_error(
                trace_writer,
                request_id,
                request_payload,
                trace,
                exc,
                compiler_fingerprint=compiler_fingerprint,
            )
            raise HTTPException(status_code=422, detail=exc.detail) from exc
        except LLMTimeoutError as exc:
            trace = getattr(exc, "compiler_trace", None)
            _write_trace_error(
                trace_writer,
                request_id,
                request_payload,
                trace,
                exc,
                compiler_fingerprint=compiler_fingerprint,
            )
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except LLMProviderError as exc:
            trace = getattr(exc, "compiler_trace", None)
            _write_trace_error(
                trace_writer,
                request_id,
                request_payload,
                trace,
                exc,
                compiler_fingerprint=compiler_fingerprint,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (
            CompilerExecutionError,
            LLMConfigurationError,
            WorkflowError,
        ) as exc:
            trace = getattr(exc, "trace", None) or getattr(exc, "compiler_trace", None)
            _write_trace_error(
                trace_writer,
                request_id,
                request_payload,
                trace,
                exc,
                compiler_fingerprint=compiler_fingerprint,
            )
            detail = exc.detail if isinstance(exc, CompilerExecutionError) else str(exc)
            raise HTTPException(status_code=500, detail=detail) from exc
        except Exception as exc:
            trace = getattr(exc, "compiler_trace", None)
            _write_trace_error(
                trace_writer,
                request_id,
                request_payload,
                trace,
                exc,
                compiler_fingerprint=compiler_fingerprint,
            )
            raise HTTPException(status_code=500, detail="Unexpected compiler failure") from exc

    return app


async def _safe_validation_request(
    request: Request,
    *,
    store_prompt: bool,
) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raw_body = await request.body()
        return {
            "body_parse_error": "invalid_json",
            "body_bytes": len(raw_body),
        }

    if not isinstance(body, dict):
        return {"body_type": type(body).__name__}

    safe: dict[str, Any] = {}
    expected_fields = {"prompt", "current_ir", "runtime_context"}
    for field in expected_fields:
        if field not in body:
            continue
        if field == "prompt" and not store_prompt:
            safe[field] = None
        else:
            safe[field] = body[field]

    unexpected = sorted(str(field) for field in body if field not in expected_fields)
    if unexpected:
        safe["unexpected_fields"] = unexpected
    return safe


def _safe_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": error.get("type"),
            "loc": list(error.get("loc", ())),
            "msg": error.get("msg"),
        }
        for error in errors
    ]


def _to_compile_result(
    request_id: str,
    result: WorkflowResult,
) -> CompileResultOk | CompileResultIRGap:
    mode = result.detail.get("mode") or result.trace.mode
    route = result.detail.get("route")
    meta_values: dict[str, Any] = {"request_id": request_id, "mode": mode}
    if route is not None:
        meta_values["route"] = route
    meta = CompileMeta.model_validate(meta_values)

    if result.status == "ok":
        if result.ir is None:
            raise CompilerExecutionError(
                "Successful compiler result has no World IR",
                {},
                result.trace,
            )
        return CompileResultOk(
            status="ok",
            world_ir=result.ir,
            runtime_bindings=result.runtime_bindings,
            runtime_fact_ops=result.runtime_fact_ops,
            meta=meta,
        )

    if result.status == "ir_gap":
        expressibility = result.detail.get("expressibility")
        if not isinstance(expressibility, dict):
            raise CompilerExecutionError(
                "IR GAP result is missing expressibility detail",
                result.detail,
                result.trace,
            )
        reason = expressibility.get("reason")
        unsupported = expressibility.get("unsupported")
        if not isinstance(reason, str) or not reason.strip():
            raise CompilerExecutionError(
                "IR GAP result has no reason",
                result.detail,
                result.trace,
            )
        if not isinstance(unsupported, list) or not all(
            isinstance(item, str) for item in unsupported
        ):
            raise CompilerExecutionError(
                "IR GAP result has an invalid unsupported list",
                result.detail,
                result.trace,
            )
        return CompileResultIRGap(
            status="ir_gap",
            gap=IRGap(reason=reason, unsupported=unsupported),
            meta=meta,
        )

    raise CompilerExecutionError(
        f"Unsupported compiler result status: {result.status!r}",
        result.detail,
        result.trace,
    )


def _write_trace(
    writer: ServerTraceWriter | None,
    *,
    request_id: str,
    request: dict[str, Any],
    trace: RunTrace | None,
    compiler_fingerprint: str | None = None,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    if writer is not None:
        writer.write(
            request_id=request_id,
            request=request,
            trace=trace,
            compiler_fingerprint=compiler_fingerprint,
            result=result,
            error=error,
        )


def _write_trace_error(
    writer: ServerTraceWriter | None,
    request_id: str,
    request: dict[str, Any],
    trace: RunTrace | None,
    exc: Exception,
    *,
    compiler_fingerprint: str | None = None,
) -> None:
    detail = getattr(exc, "detail", None)
    try:
        _write_trace(
            writer,
            request_id=request_id,
            request=request,
            trace=trace,
            compiler_fingerprint=compiler_fingerprint,
            error={
                "type": type(exc).__name__,
                "message": str(exc),
                "detail": detail,
            },
        )
    except OSError:
        # If trace persistence itself caused the request failure, do not let a
        # second write attempt escape FastAPI's HTTP 500 mapping.
        pass

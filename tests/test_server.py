from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from worldir_agent.compiler import CompilerInputError, WorldCompiler
from worldir_agent.config import CacheConfig, LLMConfig, TraceConfig, WorkflowConfig
from worldir_agent.llm import HTTPJSONLLM, LLMProviderError, LLMTimeoutError
from worldir_agent.prompts import PromptStore
from worldir_agent.runtime.models import RuntimeContext
from worldir_agent.schema import IRSpec
from worldir_agent.server.app import create_app
from worldir_agent.server.cache import CompileCache
from worldir_agent.trace import ServerTraceWriter
from worldir_agent.workflow import WorldIRWorkflow


ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    def __init__(self, responses=None, errors=None):
        self.responses = {key: list(value) for key, value in (responses or {}).items()}
        self.errors = errors or {}
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def complete(self, node, prompt):
        self.calls.append(node)
        self.prompts.append(prompt)
        if node in self.errors:
            raise self.errors[node]
        if node == "expressibility" and node not in self.responses:
            return json.dumps({
                "expressible": True,
                "reason": "supported",
                "unsupported": [],
            })
        return self.responses[node].pop(0)


def state_v2():
    return json.loads((ROOT / "examples/state0_v2.json").read_text(encoding="utf-8"))


def runtime_context_payload():
    return {
        "version": "1",
        "facts": [{
            "id": "clearing_01",
            "kind": "marked_area",
            "mark": "cleared",
            "location": {"inside": "coastal_forest", "anchor": "east"},
            "affected_type": "tree",
            "count": 23,
        }],
    }


def pass_judgment():
    return json.dumps({
        "verdict": "pass",
        "faithful": True,
        "complete": True,
        "restrained": True,
        "preserved": True,
        "unsupported_user_meaning": [],
        "missing_observable_evidence": [],
        "invented_content": [],
        "critique": "",
    })


def make_compiler(llm, *, editor_attempts=3, fingerprint="test-fingerprint"):
    def factory():
        return WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v2.json"),
            config=WorkflowConfig(editor_max_attempts=editor_attempts),
        )
    return WorldCompiler(factory, fingerprint=fingerprint)


def make_http_llm():
    return HTTPJSONLLM(LLMConfig(
        provider="openai_compatible",
        base_url="https://llm.invalid/v1",
        model="test-model",
        api_key_env="TEST_LLM_API_KEY",
    ))


def compile_request(current_ir=None, runtime_context=None):
    return {
        "prompt": "生成一片森林。",
        "current_ir": current_ir,
        "runtime_context": runtime_context or {"version": "1", "facts": []},
    }


class CompilerCoreServerModeTests(unittest.TestCase):
    def test_runtime_aware_editor_draft_uses_existing_workflow(self):
        candidate = copy.deepcopy(state_v2())
        candidate["entities"].append({
            "id": "tent_in_clearing",
            "type": "tent",
            "placement": {
                "relations": [
                    {"type": "inside", "target": "coastal_forest"}
                ]
            },
        })
        draft = {
            "world_ir": candidate,
            "runtime_bindings": [{
                "ir_object_id": "tent_in_clearing",
                "runtime_fact_id": "clearing_01",
                "placement": "inside",
            }],
            "runtime_fact_ops": [],
        }
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [json.dumps({
                "expressible": True,
                "reason": "World IR plus Runtime Binding V1 can execute the edit",
                "unsupported": [],
            })],
            "editor": [json.dumps(draft)],
            "semantic_judge": [pass_judgment()],
        })
        compiler = make_compiler(llm)
        result = compiler.compile_world(
            "在我刚刚砍出来的地方搭一个帐篷。",
            state_v2(),
            RuntimeContext.model_validate(runtime_context_payload()),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.runtime_bindings[0]["runtime_fact_id"], "clearing_01")
        self.assertIn("# Shared Runtime rules", llm.prompts[0])
        self.assertIn("clearing_01", llm.prompts[0])

    def test_runtime_reference_error_retries_editor_before_semantic_judge(self):
        candidate = copy.deepcopy(state_v2())
        candidate["entities"].append({
            "id": "tent_in_clearing",
            "type": "tent",
            "placement": {
                "relations": [
                    {"type": "inside", "target": "coastal_forest"}
                ]
            },
        })
        invalid_draft = {
            "world_ir": candidate,
            "runtime_bindings": [{
                "ir_object_id": "tent_in_clearing",
                "runtime_fact_id": "not_in_context",
                "placement": "inside",
            }],
            "runtime_fact_ops": [],
        }
        valid_draft = copy.deepcopy(invalid_draft)
        valid_draft["runtime_bindings"][0]["runtime_fact_id"] = "clearing_01"
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [json.dumps({
                "expressible": True,
                "reason": "supported",
                "unsupported": [],
            })],
            "editor": [json.dumps(invalid_draft), json.dumps(valid_draft)],
            "semantic_judge": [pass_judgment()],
        })
        result = make_compiler(llm).compile_world(
            "在空地搭一个帐篷。",
            state_v2(),
            RuntimeContext.model_validate(runtime_context_payload()),
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(llm.calls.count("editor"), 2)
        second_editor_prompt = [
            prompt for node, prompt in zip(llm.calls, llm.prompts) if node == "editor"
        ][1]
        self.assertIn("Runtime contract errors", second_editor_prompt)

    def test_initial_generation_rejects_nonempty_runtime_context_without_llm(self):
        llm = FakeLLM()
        with self.assertRaises(CompilerInputError):
            make_compiler(llm).compile_world(
                "生成一个世界。",
                None,
                RuntimeContext.model_validate(runtime_context_payload()),
            )
        self.assertEqual(llm.calls, [])


class ServerHTTPTests(unittest.TestCase):
    def test_health_and_info_do_not_call_llm(self):
        llm = FakeLLM()
        client = TestClient(create_app(make_compiler(llm)))
        self.assertEqual(client.get("/health").json(), {"status": "ok"})
        self.assertEqual(client.get("/info").json(), {
            "compiler_version": "0.3.0",
            "world_ir_version": "2",
            "world_catalog_version": "2",
            "runtime_context_version": "1",
            "compile_result_version": "1",
        })
        self.assertEqual(llm.calls, [])

    def test_initial_compile_returns_compile_result_contract(self):
        llm = FakeLLM({
            "initial_translator": [json.dumps(state_v2())],
            "semantic_judge": [pass_judgment()],
        })
        client = TestClient(create_app(make_compiler(llm)))
        response = client.post(
            "/v1/compile",
            json=compile_request(current_ir=None),
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["world_ir"], state_v2())
        self.assertEqual(payload["runtime_bindings"], [])
        self.assertEqual(payload["runtime_fact_ops"], [])
        self.assertEqual(payload["meta"]["mode"], "initial")
        self.assertNotIn("route", payload["meta"])

    def test_enabled_cache_skips_compiler_for_identical_request(self):
        llm = FakeLLM({
            "initial_translator": [json.dumps(state_v2())],
            "semantic_judge": [pass_judgment()],
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = CompileCache(CacheConfig(enabled=True, dir=temp_dir))
            client = TestClient(
                create_app(make_compiler(llm), compile_cache=cache)
            )
            request = compile_request(current_ir=None)

            first = client.post("/v1/compile", json=request)
            second = client.post("/v1/compile", json=request)

            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(
                llm.calls,
                ["expressibility", "initial_translator", "semantic_judge"],
            )
            self.assertEqual(first.json()["world_ir"], second.json()["world_ir"])
            self.assertNotEqual(
                first.json()["meta"]["request_id"],
                second.json()["meta"]["request_id"],
            )
            self.assertEqual(len(list(Path(temp_dir).glob("*.json"))), 1)

    def test_cache_persists_across_app_restart(self):
        request = compile_request(current_ir=None)
        with tempfile.TemporaryDirectory() as temp_dir:
            config = CacheConfig(enabled=True, dir=temp_dir)

            first_llm = FakeLLM({
                "initial_translator": [json.dumps(state_v2())],
                "semantic_judge": [pass_judgment()],
            })
            first_client = TestClient(
                create_app(
                    make_compiler(first_llm),
                    compile_cache=CompileCache(config),
                )
            )
            first = first_client.post("/v1/compile", json=request)
            self.assertEqual(first.status_code, 200, first.text)

            second_llm = FakeLLM()
            second_client = TestClient(
                create_app(
                    make_compiler(second_llm),
                    compile_cache=CompileCache(config),
                )
            )
            second = second_client.post("/v1/compile", json=request)

            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(second_llm.calls, [])
            self.assertEqual(first.json()["world_ir"], second.json()["world_ir"])

    def test_cache_is_invalidated_by_compiler_fingerprint(self):
        request = compile_request(current_ir=None)
        with tempfile.TemporaryDirectory() as temp_dir:
            config = CacheConfig(enabled=True, dir=temp_dir)
            first_llm = FakeLLM({
                "initial_translator": [json.dumps(state_v2())],
                "semantic_judge": [pass_judgment()],
            })
            second_llm = FakeLLM({
                "initial_translator": [json.dumps(state_v2())],
                "semantic_judge": [pass_judgment()],
            })

            first = TestClient(create_app(
                make_compiler(first_llm, fingerprint="compiler-a"),
                compile_cache=CompileCache(config),
            )).post("/v1/compile", json=request)
            second = TestClient(create_app(
                make_compiler(second_llm, fingerprint="compiler-b"),
                compile_cache=CompileCache(config),
            )).post("/v1/compile", json=request)

            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(
                second_llm.calls,
                ["expressibility", "initial_translator", "semantic_judge"],
            )
            self.assertEqual(len(list(Path(temp_dir).glob("*.json"))), 2)

    def test_disabled_cache_does_not_reuse_response(self):
        llm = FakeLLM({
            "initial_translator": [json.dumps(state_v2()), json.dumps(state_v2())],
            "semantic_judge": [pass_judgment(), pass_judgment()],
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = CompileCache(CacheConfig(enabled=False, dir=temp_dir))
            client = TestClient(
                create_app(make_compiler(llm), compile_cache=cache)
            )
            request = compile_request(current_ir=None)

            first = client.post("/v1/compile", json=request)
            second = client.post("/v1/compile", json=request)

            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(
                llm.calls,
                [
                    "expressibility",
                    "initial_translator",
                    "semantic_judge",
                    "expressibility",
                    "initial_translator",
                    "semantic_judge",
                ],
            )
            self.assertEqual(list(Path(temp_dir).glob("*.json")), [])

    def test_ir_gap_is_http_200(self):
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [json.dumps({
                "expressible": False,
                "reason": "Exact metric distance is not representable",
                "unsupported": ["minimum_distance(houses, church, 50m)"],
            })],
        })
        client = TestClient(create_app(make_compiler(llm)))
        request = compile_request(current_ir=state_v2())
        request["prompt"] = "所有房屋距离教堂至少 50 米。"
        response = client.post("/v1/compile", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ir_gap")
        self.assertEqual(response.json()["meta"]["route"], "bypass")

    def test_malformed_envelope_is_400_and_does_not_call_compiler(self):
        llm = FakeLLM()
        client = TestClient(create_app(make_compiler(llm)))
        request = compile_request()
        del request["prompt"]
        response = client.post("/v1/compile", json=request)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(llm.calls, [])

    def test_missing_runtime_context_is_a_malformed_400_request(self):
        llm = FakeLLM()
        client = TestClient(create_app(make_compiler(llm)))
        request = compile_request()
        del request["runtime_context"]
        response = client.post("/v1/compile", json=request)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(llm.calls, [])

    def test_invalid_runtime_context_is_422_and_does_not_call_compiler(self):
        llm = FakeLLM()
        client = TestClient(create_app(make_compiler(llm)))
        request = compile_request()
        request["runtime_context"] = {
            "version": "1",
            "facts": [{
                "id": "area_01",
                "kind": "marked_area",
                "mark": "frozen",
                "location": {"inside": "forest"},
            }],
        }
        response = client.post("/v1/compile", json=request)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(llm.calls, [])

    def test_empty_runtime_location_is_422_not_an_internal_error(self):
        llm = FakeLLM()
        client = TestClient(create_app(make_compiler(llm)))
        request = compile_request()
        request["runtime_context"] = {
            "version": "1",
            "facts": [{
                "id": "area_01",
                "kind": "marked_area",
                "mark": "cleared",
                "location": {},
            }],
        }
        response = client.post("/v1/compile", json=request)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(llm.calls, [])

    def test_invalid_current_ir_is_422_before_any_llm_call(self):
        llm = FakeLLM()
        client = TestClient(create_app(make_compiler(llm)))
        response = client.post(
            "/v1/compile",
            json=compile_request(current_ir={"regions": []}),
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(llm.calls, [])

    def test_initial_compile_with_runtime_facts_is_422_before_llm(self):
        llm = FakeLLM()
        client = TestClient(create_app(make_compiler(llm)))
        response = client.post(
            "/v1/compile",
            json=compile_request(
                current_ir=None,
                runtime_context=runtime_context_payload(),
            ),
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(llm.calls, [])

    def test_upstream_provider_failure_is_502(self):
        llm = FakeLLM(errors={"router": LLMProviderError("provider unavailable")})
        client = TestClient(create_app(make_compiler(llm)))
        response = client.post(
            "/v1/compile",
            json=compile_request(current_ir=state_v2()),
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertNotIn("world_ir", response.json())

    def test_upstream_timeout_is_504(self):
        llm = FakeLLM(errors={"router": LLMTimeoutError("provider timed out")})
        client = TestClient(create_app(make_compiler(llm)))
        response = client.post(
            "/v1/compile",
            json=compile_request(current_ir=state_v2()),
        )
        self.assertEqual(response.status_code, 504, response.text)
        self.assertNotIn("world_ir", response.json())

    def test_connection_reset_from_real_adapter_is_502(self):
        llm = make_http_llm()
        client = TestClient(create_app(make_compiler(llm)))
        with (
            patch.dict(os.environ, {"TEST_LLM_API_KEY": "test-key"}),
            patch(
                "worldir_agent.llm.request.urlopen",
                side_effect=ConnectionResetError("connection reset by peer"),
            ),
        ):
            response = client.post(
                "/v1/compile",
                json=compile_request(current_ir=state_v2()),
            )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("connection failed", response.json()["detail"])

    def test_malformed_real_provider_response_is_502(self):
        llm = make_http_llm()
        client = TestClient(create_app(make_compiler(llm)))
        malformed = {"choices": [{"message": {"content": {"not": "text"}}}]}
        with (
            patch.dict(os.environ, {"TEST_LLM_API_KEY": "test-key"}),
            patch.object(HTTPJSONLLM, "_post", return_value=malformed),
        ):
            response = client.post(
                "/v1/compile",
                json=compile_request(current_ir=state_v2()),
            )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("invalid Chat Completions", response.json()["detail"])

    def test_failed_compile_never_returns_a_partial_result(self):
        invalid_draft = {
            "world_ir": {"regions": []},
            "runtime_bindings": [],
            "runtime_fact_ops": [],
        }
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [json.dumps({
                "expressible": True,
                "reason": "supported",
                "unsupported": [],
            })],
            "editor": [json.dumps(invalid_draft)],
        })
        client = TestClient(create_app(make_compiler(llm, editor_attempts=1)))
        response = client.post(
            "/v1/compile",
            json=compile_request(current_ir=state_v2()),
        )
        self.assertEqual(response.status_code, 500, response.text)
        self.assertNotIn("world_ir", response.json())
        self.assertNotIn("runtime_fact_ops", response.json())

    def test_server_trace_contains_request_workflow_and_result(self):
        llm = FakeLLM({
            "initial_translator": [json.dumps(state_v2())],
            "semantic_judge": [pass_judgment()],
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ServerTraceWriter(TraceConfig(enabled=True, dir=temp_dir))
            client = TestClient(create_app(make_compiler(llm), trace_writer=writer))
            response = client.post("/v1/compile", json=compile_request())
            self.assertEqual(response.status_code, 200, response.text)
            paths = list(Path(temp_dir).glob("*/*.trace.json"))
            self.assertEqual(len(paths), 1)
            trace = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(trace["request_id"], response.json()["meta"]["request_id"])
            self.assertEqual(trace["compiler_fingerprint"], "test-fingerprint")
            self.assertEqual(trace["workflow"]["mode"], "initial")
            self.assertEqual(trace["workflow"]["validations"][0]["layer"], "world_ir")
            self.assertEqual(
                trace["workflow"]["validations"][1]["layer"],
                "semantic_judge",
            )
            self.assertEqual(trace["result"]["status"], "ok")

    def test_request_validation_failures_write_trace_without_workflow(self):
        llm = FakeLLM()
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ServerTraceWriter(TraceConfig(enabled=True, dir=temp_dir))
            client = TestClient(create_app(make_compiler(llm), trace_writer=writer))

            missing_prompt = compile_request()
            del missing_prompt["prompt"]
            malformed_response = client.post("/v1/compile", json=missing_prompt)

            invalid_runtime = compile_request()
            invalid_runtime["runtime_context"] = {"version": "2", "facts": []}
            runtime_response = client.post("/v1/compile", json=invalid_runtime)

            self.assertEqual(malformed_response.status_code, 400)
            self.assertEqual(runtime_response.status_code, 422)
            self.assertEqual(llm.calls, [])

            paths = list(Path(temp_dir).glob("*/*.trace.json"))
            self.assertEqual(len(paths), 2)
            traces = [
                json.loads(path.read_text(encoding="utf-8")) for path in paths
            ]
            by_category = {trace["error"]["category"]: trace for trace in traces}
            self.assertEqual(set(by_category), {
                "malformed_request",
                "invalid_runtime_context",
            })
            for trace in traces:
                self.assertTrue(trace["request_id"])
                self.assertIsNone(trace["workflow"])
                self.assertIsNone(trace["result"])
                self.assertEqual(trace["error"]["type"], "RequestValidationError")
                self.assertTrue(trace["error"]["detail"])
            self.assertEqual(
                by_category["malformed_request"]["error"]["http_status"],
                400,
            )
            self.assertEqual(
                by_category["invalid_runtime_context"]["error"]["http_status"],
                422,
            )


if __name__ == "__main__":
    unittest.main()

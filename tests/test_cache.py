import json
from pathlib import Path
import tempfile
import unittest

from worldir_agent.config import CacheConfig
from worldir_agent.server.cache import CompileCache
from worldir_agent.server.models import CompileMeta, CompileResultOk


ROOT = Path(__file__).resolve().parents[1]


class CompileCacheFingerprintTests(unittest.TestCase):
    def test_compiler_fingerprint_is_part_of_cache_identity(self):
        request = {
            "prompt": "生成一个世界。",
            "current_ir": None,
            "runtime_context": {"version": "1", "facts": []},
        }
        response = CompileResultOk(
            status="ok",
            world_ir=json.loads(
                (ROOT / "examples/state0_v2.json").read_text(encoding="utf-8")
            ),
            runtime_bindings=[],
            runtime_fact_ops=[],
            meta=CompileMeta(request_id="original", mode="initial"),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = CompileCache(CacheConfig(enabled=True, dir=temp_dir))
            cache.put(request, response, compiler_fingerprint="compiler-a")

            hit = cache.get(
                request,
                request_id="cache-hit",
                compiler_fingerprint="compiler-a",
            )
            miss = cache.get(
                request,
                request_id="cache-miss",
                compiler_fingerprint="compiler-b",
            )

            self.assertIsNotNone(hit)
            self.assertEqual(hit.meta.request_id, "cache-hit")
            self.assertIsNone(miss)


if __name__ == "__main__":
    unittest.main()

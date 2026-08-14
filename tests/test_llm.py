import os
import unittest
from unittest.mock import patch

from worldir_agent.config import LLMConfig
from worldir_agent.llm import HTTPJSONLLM


def make_llm(*, thinking=None):
    return HTTPJSONLLM(
        LLMConfig(
            provider="openai_compatible",
            base_url="https://llm.invalid/v1",
            model="test-model",
            api_key_env="TEST_LLM_API_KEY",
            thinking=thinking,
        )
    )


class OpenAICompatibleThinkingTests(unittest.TestCase):
    response = {
        "choices": [
            {
                "message": {"content": "ok"},
                "finish_reason": "stop",
            }
        ]
    }

    def request_payload(self, *, thinking=None):
        llm = make_llm(thinking=thinking)
        with (
            patch.dict(os.environ, {"TEST_LLM_API_KEY": "test-key"}),
            patch.object(HTTPJSONLLM, "_post", return_value=self.response) as post,
        ):
            self.assertEqual(llm.complete("test", "hello"), "ok")
        return post.call_args.args[1]

    def test_thinking_is_omitted_by_default(self):
        self.assertNotIn("thinking", self.request_payload())

    def test_thinking_can_be_enabled(self):
        self.assertEqual(
            self.request_payload(thinking=True)["thinking"],
            {"type": "enabled"},
        )

    def test_thinking_can_be_disabled(self):
        self.assertEqual(
            self.request_payload(thinking=False)["thinking"],
            {"type": "disabled"},
        )

    def test_thinking_rejects_non_boolean_values(self):
        with self.assertRaisesRegex(ValueError, "llm.thinking"):
            make_llm(thinking="false")


if __name__ == "__main__":
    unittest.main()

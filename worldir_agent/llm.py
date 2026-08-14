from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib import request, error

from .config import LLMConfig


class LLM(Protocol):
    def complete(self, node: str, prompt: str) -> str: ...


@dataclass(slots=True)
class HTTPJSONLLM:
    config: LLMConfig

    def _api_key(self) -> str:
        key = os.environ.get(self.config.api_key_env)
        if not key:
            raise RuntimeError(
                f"Missing API key environment variable: {self.config.api_key_env}"
            )
        return key

    def complete(self, node: str, prompt: str) -> str:
        provider = self.config.provider.lower().strip()
        if provider == "openai_compatible":
            return self._openai_compatible(prompt)
        if provider == "anthropic":
            return self._anthropic(prompt)
        raise ValueError(f"Unsupported provider: {self.config.provider}")

    def _post(self, url: str, payload: dict, headers: dict[str, str]) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from LLM API: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Could not reach LLM API: {exc}") from exc

    def _openai_compatible(self, prompt: str) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "Return only the format requested by the task."},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        data = self._post(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
            },
        )
        choice = data["choices"][0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        if not content.strip():
            reasoning = message.get("reasoning_content") or ""
            finish_reason = choice.get("finish_reason")
            raise RuntimeError(
                "LLM returned empty content "
                f"(finish_reason={finish_reason!r}, reasoning_content_chars={len(reasoning)}). "
                "If this is a reasoning model and finish_reason is 'length', increase max_tokens."
            )
        return content

    def _anthropic(self, prompt: str) -> str:
        url = self.config.base_url.rstrip("/") + "/v1/messages"
        payload = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "system": "Return only the format requested by the task.",
            "messages": [{"role": "user", "content": prompt}],
        }
        data = self._post(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "x-api-key": self._api_key(),
                "anthropic-version": self.config.anthropic_version,
            },
        )
        chunks = [x.get("text", "") for x in data.get("content", []) if x.get("type") == "text"]
        return "".join(chunks)

from __future__ import annotations

import json
import os
import socket
from http import client as http_client
from dataclasses import dataclass
from typing import Protocol
from urllib import request, error

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


class LLMConfigurationError(LLMError):
    pass


class LLMProviderError(LLMError):
    pass


class LLMTimeoutError(LLMProviderError):
    pass


class LLM(Protocol):
    def complete(self, node: str, prompt: str) -> str: ...


@dataclass(slots=True)
class HTTPJSONLLM:
    config: LLMConfig

    def _api_key(self) -> str:
        key = os.environ.get(self.config.api_key_env)
        if not key:
            raise LLMConfigurationError(
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
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except (
                ConnectionError,
                OSError,
                http_client.HTTPException,
                EOFError,
                UnicodeError,
                AttributeError,
            ) as read_exc:
                raise LLMProviderError(
                    f"HTTP {exc.code} from LLM API with an unreadable error response"
                ) from read_exc
            if exc.code in {408, 504}:
                raise LLMTimeoutError(
                    f"HTTP {exc.code} timeout from LLM API: {detail}"
                ) from exc
            raise LLMProviderError(f"HTTP {exc.code} from LLM API: {detail}") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMTimeoutError(f"LLM API request timed out: {exc}") from exc
            raise LLMProviderError(f"Could not reach LLM API: {exc}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise LLMTimeoutError(f"LLM API request timed out: {exc}") from exc
        except (ConnectionError, OSError, http_client.HTTPException, EOFError) as exc:
            raise LLMProviderError(
                f"LLM API connection failed while reading the response: {exc}"
            ) from exc
        except (UnicodeError, AttributeError) as exc:
            raise LLMProviderError(
                "LLM API returned a malformed response body"
            ) from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMProviderError("LLM API returned a non-JSON response") from exc

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
        if self.config.thinking is not None:
            payload["thinking"] = {
                "type": "enabled" if self.config.thinking else "disabled"
            }
        data = self._post(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
            },
        )
        try:
            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMProviderError(
                "LLM API returned an invalid Chat Completions response"
            ) from exc
        if not isinstance(content, str) or not isinstance(reasoning, str):
            raise LLMProviderError(
                "LLM API returned an invalid Chat Completions response"
            )
        if not content.strip():
            raise LLMProviderError(
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
        try:
            content_items = data.get("content", [])
            if not isinstance(content_items, list):
                raise TypeError("content must be an array")
            chunks = []
            for item in content_items:
                if not isinstance(item, dict):
                    raise TypeError("content items must be objects")
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if not isinstance(text, str):
                        raise TypeError("text content must be a string")
                    chunks.append(text)
            content = "".join(chunks)
        except (TypeError, AttributeError) as exc:
            raise LLMProviderError(
                "LLM API returned an invalid Anthropic Messages response"
            ) from exc
        if not content.strip():
            raise LLMProviderError("LLM returned empty content")
        return content

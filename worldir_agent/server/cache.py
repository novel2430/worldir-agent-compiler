from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from ..config import CacheConfig
from .models import CompileResult, CompileResultIRGap, CompileResultOk


_COMPILE_RESULT_ADAPTER = TypeAdapter(CompileResult)
_CACHE_FORMAT_VERSION = 1


class CompileCache:
    """Small persistent cache keyed by the canonical CompileRequest body."""

    def __init__(self, config: CacheConfig):
        self.config = config
        self.root = Path(config.dir)

    def get(
        self,
        request_payload: dict[str, Any],
        *,
        request_id: str,
    ) -> CompileResultOk | CompileResultIRGap | None:
        if not self.config.enabled:
            return None

        path = self._path_for(request_payload)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(record, dict):
            return None
        if record.get("version") != _CACHE_FORMAT_VERSION:
            return None
        # The request is stored as a cheap collision/corruption guard in
        # addition to using a SHA-256 filename.
        if record.get("request") != request_payload:
            return None

        cached_response = record.get("response")
        if not isinstance(cached_response, dict):
            return None

        payload = copy.deepcopy(cached_response)
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            return None
        # A cache hit is still a new HTTP request, so keep request IDs unique.
        meta["request_id"] = request_id

        try:
            return _COMPILE_RESULT_ADAPTER.validate_python(payload)
        except ValidationError:
            return None

    def put(
        self,
        request_payload: dict[str, Any],
        response: CompileResultOk | CompileResultIRGap,
    ) -> None:
        if not self.config.enabled:
            return

        path = self._path_for(request_payload)
        record = {
            "version": _CACHE_FORMAT_VERSION,
            "request": request_payload,
            "response": response.model_dump(mode="json", exclude_none=True),
        }

        # Cache persistence is best-effort: a read-only/full cache directory
        # must never turn an otherwise successful compile into HTTP 500.
        temp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            temp_path.write_text(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temp_path.replace(path)
        except OSError:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _path_for(self, request_payload: dict[str, Any]) -> Path:
        canonical = json.dumps(
            request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        key = sha256(canonical).hexdigest()
        return self.root / f"{key}.json"

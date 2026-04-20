"""
HTTP Bridge - OpenAI/ZhiPu compatible API strategy.

Supports any OpenAI-compatible API endpoint (ZhiPu/DashScope,
OpenAI, local models via vLLM/ollama, etc.).
"""

import json
import logging
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from .base import ModelBridgeBase, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


class HTTPBridge(ModelBridgeBase):
    """
    HTTP-based model bridge for OpenAI-compatible APIs.

    Config (from map.json strategies.http):
        api_key: API key
        api_base: Base URL (e.g. https://open.bigmodel.cn/api/paas/v4)
        default_model: Default model name (e.g. glm-5.1)
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "",
        default_model: str = "",
        timeout: int = 120,
    ):
        self._api_key = api_key
        self._api_base = api_base.rstrip("/") if api_base else ""
        self._default_model = default_model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "http"

    def is_available(self) -> bool:
        return bool(self._api_key and self._api_base)

    def call(self, request: ModelRequest) -> ModelResponse:
        model = request.model or self._default_model
        if not model:
            return ModelResponse(
                success=False,
                error="No model specified and no default_model configured",
                request_id=request.model_id,
            )

        url = f"{self._api_base}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        start = time.time()
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=request.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            latency = (time.time() - start) * 1000
            content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = body.get("usage", {})

            return ModelResponse(
                content=content,
                model=model,
                request_id=request.model_id,
                success=True,
                usage={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                latency_ms=latency,
                finish_reason=body.get("choices", [{}])[0].get("finish_reason", ""),
                raw=body,
            )
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            latency = (time.time() - start) * 1000
            logger.error(f"HTTP bridge error {e.code}: {error_body[:500]}")
            return ModelResponse(
                success=False,
                error=f"HTTP {e.code}: {error_body[:300]}",
                model=model,
                request_id=request.model_id,
                latency_ms=latency,
            )
        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error(f"HTTP bridge exception: {e}")
            return ModelResponse(
                success=False,
                error=str(e),
                model=model,
                request_id=request.model_id,
                latency_ms=latency,
            )

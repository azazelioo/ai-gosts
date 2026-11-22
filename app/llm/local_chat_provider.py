import json
import os
from typing import Optional
from urllib import error, request
from urllib.parse import urlsplit

from app.llm.base import LLMProvider


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalChatProvider(LLMProvider):
    def __init__(
        self,
        backend: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,
    ):
        self.backend = (backend or os.getenv("LOCAL_CHAT_BACKEND") or "ollama").strip().lower()
        if self.backend != "ollama":
            raise ValueError("Unsupported LOCAL_CHAT_BACKEND. Use 'ollama'.")

        self.base_url = (base_url or os.getenv("LOCAL_CHAT_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        endpoint = urlsplit(self.base_url)
        loopback_hosts = {"127.0.0.1": "127.0.0.1", "localhost": "127.0.0.1", "::1": "[::1]"}
        host = loopback_hosts.get(endpoint.hostname)
        if (
            endpoint.scheme != "http"
            or host is None
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.path or endpoint.query or endpoint.fragment
        ):
            raise ValueError("LOCAL_CHAT_BASE_URL must be an HTTP loopback address.")
        self.base_url = f"http://{host}:{endpoint.port or 80}"
        self._opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        self.model = (model or os.getenv("LOCAL_CHAT_MODEL") or "qwen2.5:1.5b").strip()
        if not self.model:
            raise ValueError("LOCAL_CHAT_MODEL must not be empty")

        if self.model.lower().endswith((":cloud", "-cloud")):
            raise ValueError("Ollama cloud models are disabled.")

        raw_temperature = temperature
        if raw_temperature is None:
            raw_temperature = os.getenv("LOCAL_CHAT_TEMPERATURE", "0.2")
        self.temperature = float(raw_temperature)
        self.system_prompt = system_prompt or os.getenv("LOCAL_CHAT_SYSTEM_PROMPT") or ""

    def chat(self, message: str) -> str:
        payload = {
            "model": self.model,
            "prompt": message,
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        if self.system_prompt:
            payload["system"] = self.system_prompt

        self._check_local_model()
        response = self._post_json("/api/generate", payload)
        text = str(response.get("response") or "").strip()
        if not text:
            raise ValueError(f"Local chat response is invalid: {response}")
        return text

    def _check_local_model(self) -> None:
        info = self._post_json("/api/show", {"model": self.model})
        if info.get("remote_model") or info.get("remote_host") or not info.get("details", {}).get("format"):
            raise ValueError("Ollama must use a model with weights installed locally.")

    def chat_messages(self, messages: list[dict], max_tokens: Optional[int] = None):
        """Yield text from the local chat endpoint, preserving message roles."""
        self._check_local_model()
        options = {"temperature": self.temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload = {"model": self.model, "messages": messages, "options": options, "stream": True}
        with self._open_request("/api/chat", payload) as response:
            for line in response:
                chunk = json.loads(line)
                if "error" in chunk:
                    raise ValueError(chunk["error"])
                text = chunk.get("message", {}).get("content")
                if text:
                    yield text

    def _open_request(self, path: str, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.base_url}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            return self._opener.open(req, timeout=120)
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"Local chat request failed: {exc.code} {raw}") from exc
        except error.URLError as exc:
            raise ValueError(
                "Local chat backend is unreachable. Ensure Ollama is running and LOCAL_CHAT_BASE_URL is correct. "
                f"Details: {exc.reason}"
            ) from exc

    def _post_json(self, path: str, payload: dict) -> dict:
        with self._open_request(path, payload) as response:
            raw = response.read().decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Local chat backend returned invalid JSON: {raw[:200]}") from exc

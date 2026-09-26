"""llama-server sidecar lifecycle and a minimal OpenAI-compatible client.

The server is started lazily on the first AI job, bound to 127.0.0.1 on a random port with a
random API key, and stopped after an idle period to free RAM. Model files are verified against
models/manifest.json (sha256) before loading. Only loopback endpoints are ever contacted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from verismo_engine.ai import hardware

log = logging.getLogger("verismo.ai.runtime")


class AIUnavailable(RuntimeError):
    pass


# All HTTP use lives in this module (a test enforces it); tasks catch timeouts via this alias.
LLMTimeout = httpx.TimeoutException


class ModelVerificationError(AIUnavailable):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def assert_loopback(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise AIUnavailable(f"refusing non-loopback AI endpoint {host!r}")


def sha256_file(path: Path, chunk: int = 8 * 2**20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def verify_model(path: Path, manifest_path: Path) -> str:
    if not path.exists():
        raise ModelVerificationError(f"model file not found: {path.name}")
    if not manifest_path.exists():
        raise ModelVerificationError("models/manifest.json not found")
    manifest = json.loads(manifest_path.read_text())
    entry = next((m for m in manifest.get("models", []) if m.get("file") == path.name), None)
    if entry is None:
        raise ModelVerificationError(f"{path.name} is not listed in the model manifest")
    if entry.get("license") not in ("Apache-2.0", "MIT"):
        raise ModelVerificationError(f"{path.name} licence {entry.get('license')!r} is not permitted")
    digest = sha256_file(path)
    if digest != entry["sha256"]:
        raise ModelVerificationError(f"sha256 mismatch for {path.name}")
    return digest


@dataclass
class ChatResult:
    content: str | None
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]
    latency_ms: int


class LLMClient:
    def __init__(
        self, base_url: str, api_key: str, model_id: str, model_sha256: str | None, timeout: float = 120.0
    ) -> None:
        assert_loopback(base_url)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.model_sha256 = model_sha256
        self._http = httpx.Client(
            timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}, trust_env=False
        )  # never pick up proxy settings

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        seed: int = 42,
        timeout: float | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0,
            "seed": seed,
            "max_tokens": max_tokens,
            "cache_prompt": True,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": True},
            }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        t0 = time.perf_counter()
        r = self._http.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        return ChatResult(msg.get("content"), msg.get("tool_calls") or [], data, int((time.perf_counter() - t0) * 1000))

    def close(self) -> None:
        self._http.close()


class LlamaRuntime:
    """Owns the llama-server process. Thread-safe; one model loaded at a time."""

    def __init__(self, models_dir: Path, *, idle_seconds: int = 600, binary: str | None = None) -> None:
        self.models_dir = models_dir
        self.idle_seconds = idle_seconds
        self.binary = binary or os.environ.get("VERISMO_LLAMA_SERVER") or shutil.which("llama-server")
        self._proc: subprocess.Popen[bytes] | None = None
        self._client: LLMClient | None = None
        self._lock = threading.RLock()
        self._last_used = 0.0
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._idle_loop, daemon=True, name="llama-idle")
        self._reaper.start()
        self.current_model: str | None = None

    def client(self, model_path: str) -> LLMClient:
        """Return a client for the configured model, starting llama-server if needed."""
        with self._lock:
            self._last_used = time.monotonic()
            external = os.environ.get("VERISMO_LLM_BASE_URL")
            if external:  # tests and development: an already-running loopback server
                if self._client is None:
                    self._client = LLMClient(
                        external,
                        os.environ.get("VERISMO_LLM_API_KEY", "test"),
                        Path(model_path).name or "external",
                        None,
                    )
                return self._client
            path = Path(model_path)
            if not path.is_absolute():
                path = self.models_dir / path
            if self._client is not None and self.current_model == str(path) and self._alive():
                return self._client
            self.stop()
            if not self.binary:
                raise AIUnavailable("llama-server binary not found")
            digest = verify_model(path, self.models_dir / "manifest.json")
            port = _free_port()
            key = secrets.token_urlsafe(24)
            threads = hardware.detect()["llama_threads"]
            args = [
                self.binary,
                "-m",
                str(path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--api-key",
                key,
                "--threads",
                str(threads),
                "--ctx-size",
                "8192",
                "--jinja",
                "--no-webui",
                "--log-disable",
            ]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
            self._proc = subprocess.Popen(  # noqa: S603 - fixed argv (verified model path), no shell
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags
            )
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    raise AIUnavailable("llama-server exited during startup")
                try:
                    if httpx.get(f"{base}/health", timeout=2, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                self.stop()
                raise AIUnavailable("llama-server did not become healthy")
            self._client = LLMClient(base, key, path.name, digest)
            self.current_model = str(path)
            log.info("llama-server started for %s", path.name)
            return self._client

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._alive() or bool(os.environ.get("VERISMO_LLM_BASE_URL")),
            "model": Path(self.current_model).name if self.current_model else None,
            "binary_found": bool(self.binary),
            "idle_seconds": int(time.monotonic() - self._last_used) if self._last_used else None,
        }

    def stop(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            if self._proc is not None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                self._proc = None
                log.info("llama-server stopped")
            self.current_model = None

    def _idle_loop(self) -> None:
        while not self._stop.wait(15):
            with self._lock:
                if self._proc is not None and time.monotonic() - self._last_used > self.idle_seconds:
                    self.stop()

    def shutdown(self) -> None:
        self._stop.set()
        self.stop()

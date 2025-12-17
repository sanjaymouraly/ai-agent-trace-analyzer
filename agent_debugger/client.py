"""Small, dependency-free HTTP client for the TraceLens API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit


class TraceLensError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class TraceLensClient:
    """Minimal client for the versioned ``/api/v1`` endpoints.

    The base URL is validated: only plain http(s) URLs without embedded
    credentials, query strings or fragments are accepted, so a mistyped
    ``TRACELENS_URL`` cannot leak a token to another host.
    """

    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 20.0):
        configured_url = (base_url or os.getenv("TRACELENS_URL") or "http://127.0.0.1:8000").rstrip("/")
        parsed_url = urlsplit(configured_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("TraceLens base URL must be an http(s) URL without embedded credentials, query, or fragment.")
        try:
            port = parsed_url.port
        except ValueError as exc:
            raise ValueError("TraceLens base URL contains an invalid port.") from exc
        if port == 0:
            raise ValueError("TraceLens base URL must use a non-zero port.")
        self.base_url = configured_url
        self.token = token if token is not None else os.getenv("TRACELENS_TOKEN", "")
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Low level
    # ------------------------------------------------------------------ #
    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        if query:
            filtered = {k: v for k, v in query.items() if v is not None and v != ""}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"
        request_headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            # The constructor validates an explicit HTTP(S) URL without credentials.
            with urllib.request.urlopen(req, timeout=self.timeout) as response:  # nosec B310
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw)
                message = payload.get("error", {}).get("message", str(payload))
            except Exception:
                payload = raw.decode("utf-8", errors="replace")
                message = payload
            raise TraceLensError(message, exc.code, payload) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TraceLensError(f"Could not reach TraceLens API: {exc}") from exc
        if "json" in content_type:
            return json.loads(raw)
        return raw.decode("utf-8", errors="replace")

    def _raw(self, method: str, path: str, data: bytes, headers: dict[str, str]) -> Any:
        url = f"{self.base_url}{path}"
        request_headers = {"Accept": "application/json", **headers}
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            # The constructor validates an explicit HTTP(S) URL without credentials.
            with urllib.request.urlopen(req, timeout=self.timeout) as response:  # nosec B310
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            raise TraceLensError(payload.get("error", {}).get("message", str(payload)), exc.code, payload) from exc

    # ------------------------------------------------------------------ #
    # Traces
    # ------------------------------------------------------------------ #
    def submit_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/v1/ingest", trace)

    def upload_file(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        boundary = "----TraceLensBoundary7MA4YWxkTrZu0gW"
        body = file_path.read_bytes()
        if file_path.suffix.lower() not in {".json", ".jsonl"}:
            raise ValueError("Only .json and .jsonl uploads are supported")
        content = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
                "Content-Type: application/json\r\n\r\n"
            ).encode()
            + body
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self._multipart(content, boundary)

    def _multipart(self, data: bytes, boundary: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1/ingest/file"
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            # The constructor validates an explicit HTTP(S) URL without credentials.
            with urllib.request.urlopen(req, timeout=self.timeout) as response:  # nosec B310
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            raise TraceLensError(payload.get("error", {}).get("message", str(payload)), exc.code, payload) from exc

    def validate(self, content: bytes, filename: str = "upload.json") -> dict[str, Any]:
        return self._raw("POST", "/api/v1/validate", content, {"X-Filename": filename, "Content-Type": "application/json"})

    def list_runs(self, **filters: Any) -> dict[str, Any]:
        return self.request("GET", "/api/v1/runs", query=filters)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/runs/{quote(run_id, safe='')}")

    def analyze(self, run_id: str, reference_run_id: str | None = None) -> dict[str, Any]:
        query = {"reference_run_id": reference_run_id} if reference_run_id else None
        return self.request("GET", f"/api/v1/runs/{quote(run_id, safe='')}/analysis", query=query)

    def compare(self, run_ids: list[str]) -> dict[str, Any]:
        return self.request("POST", "/api/v1/compare", {"run_ids": run_ids})

    def report(self, run_id: str, format: str = "markdown") -> str:
        return self.request("GET", f"/api/v1/runs/{quote(run_id, safe='')}/report", query={"format": format})

    def report_bytes(self, run_id: str, format: str = "pdf") -> bytes:
        """Fetch a binary report (for example a PDF) without corrupting it."""
        url = f"{self.base_url}/api/v1/runs/{quote(run_id, safe='')}/report?format={format}"
        headers = {"Accept": "*/*"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            # The constructor validates an explicit HTTP(S) URL without credentials.
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310
                return response.read()
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read())
            raise TraceLensError(payload.get("error", {}).get("message", str(payload)), exc.code, payload) from exc

    def tool_analytics(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/tool-analytics")

    def overview(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/overview")

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/health")

    def llm_status(self) -> dict[str, Any]:
        return self.request("GET", "/api/v1/llm/status")

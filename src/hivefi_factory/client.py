"""HTTP client for the multi-tenant Strategy API.

Wraps the endpoints documented in
``infrastructure/docs/design/multi-tenant-strategy-api.md``:

* ``GET    /v1/strategies``
* ``POST   /v1/strategies``
* ``GET    /v1/strategies/{id}``
* ``PUT    /v1/strategies/{id}``
* ``DELETE /v1/strategies/{id}``
* ``POST   /v1/strategies/{id}/code``  (multipart, 1 MiB max, auto-triggers Stage 1)
* ``GET    /v1/strategies/{id}/jobs``

Auth is ``X-API-Key: hvf_<env>_<32hex>``. Errors are mapped to
``StrategyApiError`` with status code + sanitized server detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, load_settings


class StrategyApiError(RuntimeError):
    """Non-2xx response from the Strategy API."""

    def __init__(self, status_code: int, detail: str, *, correlation_id: str | None = None):
        self.status_code = status_code
        self.detail = detail
        self.correlation_id = correlation_id
        suffix = f" (correlation_id={correlation_id})" if correlation_id else ""
        super().__init__(f"HTTP {status_code}: {detail}{suffix}")


@dataclass
class CodeUploadResult:
    strategy_id: str
    version: str
    job_id: str
    run_id: str


class StrategyApiClient:
    """Synchronous httpx-based client.

    Pass ``transport`` to inject ``httpx.MockTransport`` in tests.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._settings = settings or load_settings()
        api_key = self._settings.require_api_key()
        self._client = httpx.Client(
            base_url=self._settings.api_base,
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> "StrategyApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        detail: str
        correlation_id: str | None = None
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or payload)
                correlation_id = payload.get("correlation_id") or None
            else:
                detail = str(payload)
        except (ValueError, httpx.DecodingError):
            detail = resp.text or resp.reason_phrase
        raise StrategyApiError(resp.status_code, detail, correlation_id=correlation_id)

    # ---- strategies CRUD --------------------------------------------------

    def list_strategies(self) -> list[dict[str, Any]]:
        resp = self._client.get("/v1/strategies")
        self._raise_for_status(resp)
        body = resp.json()
        return body.get("items", [])

    def create_strategy(self, config: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post("/v1/strategies", json=config)
        self._raise_for_status(resp)
        return resp.json()

    def get_strategy(self, strategy_id: str) -> dict[str, Any]:
        resp = self._client.get(f"/v1/strategies/{strategy_id}")
        self._raise_for_status(resp)
        return resp.json()

    def update_strategy(self, strategy_id: str, config: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.put(f"/v1/strategies/{strategy_id}", json=config)
        self._raise_for_status(resp)
        return resp.json()

    def delete_strategy(self, strategy_id: str) -> None:
        resp = self._client.delete(f"/v1/strategies/{strategy_id}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    # ---- code upload (auto-triggers Stage 1) -----------------------------

    def upload_code(self, strategy_id: str, file_path: str | Path) -> CodeUploadResult:
        path = Path(file_path)
        with path.open("rb") as fh:
            files = {"file": (path.name, fh, "text/x-python")}
            resp = self._client.post(
                f"/v1/strategies/{strategy_id}/code",
                files=files,
            )
        self._raise_for_status(resp)
        body = resp.json()
        return CodeUploadResult(
            strategy_id=body["strategy_id"],
            version=body["version"],
            job_id=body["job_id"],
            run_id=body["run_id"],
        )

    # ---- jobs -------------------------------------------------------------

    def list_jobs(self, strategy_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        resp = self._client.get(f"/v1/strategies/{strategy_id}/jobs", params=params)
        self._raise_for_status(resp)
        body = resp.json()
        return body.get("items", [])

    def health(self) -> dict[str, Any]:
        resp = self._client.get("/health")
        self._raise_for_status(resp)
        return resp.json()

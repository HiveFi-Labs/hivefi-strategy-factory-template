"""Unit tests for ``hivefi_factory.client``.

Uses ``httpx.MockTransport`` so the tests run fully offline. We verify:

* API key header is sent
* CRUD endpoints serialize / deserialize correctly
* error responses raise ``StrategyApiError`` with the server-supplied detail
* multipart upload posts the file body
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hivefi_factory.client import (  # noqa: E402
    StrategyApiClient,
    StrategyApiError,
)
from hivefi_factory.config import Settings  # noqa: E402


def _settings() -> Settings:
    return Settings(
        api_key="hvf_test_" + "0" * 32,
        api_base="https://example.test",
        ch_host="example.clickhouse",
        ch_user=None,
        ch_password=None,
        ch_port=8443,
        ch_database="default",
        ch_secure=True,
    )


def _make_client(handler) -> StrategyApiClient:
    transport = httpx.MockTransport(handler)
    return StrategyApiClient(_settings(), transport=transport)


def test_list_strategies_passes_api_key():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"items": [{"strategy_id": "demo-D-D-hl-all-ls-v2"}]})

    with _make_client(handler) as api:
        items = api.list_strategies()

    assert captured["headers"].get("x-api-key") == "hvf_test_" + "0" * 32
    assert captured["url"] == "https://example.test/v1/strategies"
    assert items == [{"strategy_id": "demo-D-D-hl-all-ls-v2"}]


def test_create_strategy_posts_json():
    body = {
        "strategy_id": "demo-D-D-hl-all-ls-v2",
        "title": "Demo",
        "description": "x",
        "exchange": "hyperliquid",
        "universe": "hl_all",
        "rebalance_freq": "D",
        "rebalance_enabled": True,
        "auto_close_missing": True,
        "timeframe": "D",
        "warmup_periods": 30,
    }
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json=body)

    with _make_client(handler) as api:
        out = api.create_strategy(body)

    assert captured["method"] == "POST"
    assert captured["body"]["strategy_id"] == body["strategy_id"]
    assert out == body


def test_get_strategy_404_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "strategy not found"})

    with _make_client(handler) as api:
        with pytest.raises(StrategyApiError) as exc:
            api.get_strategy("missing-D-D-hl-all-ls-v2")
    assert exc.value.status_code == 404
    assert "strategy not found" in exc.value.detail


def test_update_strategy_puts():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"strategy_id": "demo-D-D-hl-all-ls-v2"})

    with _make_client(handler) as api:
        out = api.update_strategy("demo-D-D-hl-all-ls-v2", {"strategy_id": "demo-D-D-hl-all-ls-v2"})

    assert captured["method"] == "PUT"
    assert captured["url"].endswith("/v1/strategies/demo-D-D-hl-all-ls-v2")
    assert out["strategy_id"] == "demo-D-D-hl-all-ls-v2"


def test_delete_strategy_204_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with _make_client(handler) as api:
        api.delete_strategy("demo-D-D-hl-all-ls-v2")  # no exception


def test_delete_strategy_409_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Stop pending jobs first"})

    with _make_client(handler) as api:
        with pytest.raises(StrategyApiError) as exc:
            api.delete_strategy("demo-D-D-hl-all-ls-v2")
    assert exc.value.status_code == 409


def test_upload_code_returns_job_ids():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        # multipart bodies contain the filename
        captured["has_my_strategy"] = b"my_strategy.py" in request.content
        return httpx.Response(
            201,
            json={
                "strategy_id": "demo-D-D-hl-all-ls-v2",
                "version": "v3",
                "job_id": "abc",
                "run_id": "def",
            },
        )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "my_strategy.py"
        path.write_text("x = 1\n", encoding="utf-8")
        with _make_client(handler) as api:
            result = api.upload_code("demo-D-D-hl-all-ls-v2", path)

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/strategies/demo-D-D-hl-all-ls-v2/code")
    assert captured["has_my_strategy"]
    assert result.version == "v3"
    assert result.job_id == "abc"
    assert result.run_id == "def"


def test_list_jobs_passes_limit():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json={"items": []})

    with _make_client(handler) as api:
        api.list_jobs("demo-D-D-hl-all-ls-v2", limit=25)

    assert captured["query"].get("limit") == "25"


def test_health_no_auth_required():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with _make_client(handler) as api:
        assert api.health() == {"status": "ok"}


def test_500_with_correlation_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"detail": "Internal error (correlation_id=bd0003fc)"},
        )

    with _make_client(handler) as api:
        with pytest.raises(StrategyApiError) as exc:
            api.list_strategies()
    assert exc.value.status_code == 500
    assert "bd0003fc" in str(exc.value)

from __future__ import annotations

from datetime import UTC, datetime
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hivefi_factory.cli import main  # noqa: E402
from hivefi_factory.data_requests import (  # noqa: E402
    DataRequestInput,
    format_data_request,
    split_cli_values,
    write_data_request,
)


def test_format_data_request_contains_research_context():
    req = DataRequestInput(
        idea="microprice imbalance alpha",
        needed_data=["top-of-book bid/ask size history"],
        reason="price candles cannot reconstruct order-book pressure.",
        task_id="LOCAL-MICROPRICE-001",
        current_data=["hyperliquid_kline_1d: OHLCV only"],
        source="Hyperliquid",
        symbols=["BTC", "ETH"],
        start="2022-01-01",
        end="2025-12-31",
        frequency="1m",
        fields=["bid_size", "ask_size"],
        acceptance=["at least 3y history for major symbols"],
        notes=["Use only pre-2026 data for evidence."],
        request_id="microprice-data",
    )

    text = format_data_request(req, now=datetime(2026, 5, 18, tzinfo=UTC))

    assert 'id: "microprice-data"' in text
    assert "microprice imbalance alpha" in text
    assert "top-of-book bid/ask size history" in text
    assert "hyperliquid_kline_1d: OHLCV only" in text
    assert "- Fields: bid_size, ask_size" in text


def test_write_data_request_refuses_to_clobber(tmp_path: Path):
    req = DataRequestInput(
        idea="funding crowding fade",
        needed_data=["historical funding by symbol"],
        reason="price-only evidence cannot observe funding crowding.",
        request_id="funding-data",
    )
    path = write_data_request(req, output_dir=tmp_path, now=datetime(2026, 5, 18, tzinfo=UTC))

    assert path == tmp_path / "funding-data.md"
    assert path.exists()
    with pytest.raises(FileExistsError):
        write_data_request(req, output_dir=tmp_path, now=datetime(2026, 5, 18, tzinfo=UTC))


def test_split_cli_values_accepts_comma_separated_chunks():
    assert split_cli_values(["BTC,ETH", "SOL"]) == ["BTC", "ETH", "SOL"]


def test_cli_data_request_writes_json_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    code = main(
        [
            "data",
            "request",
            "--idea",
            "order flow imbalance",
            "--needed-data",
            "signed trade flow",
            "--reason",
            "OHLCV cannot identify aggressor-side volume.",
            "--output-dir",
            str(tmp_path),
            "--request-id",
            "ofi-data",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["request_id"] == "ofi-data"
    assert Path(payload["path"]).exists()

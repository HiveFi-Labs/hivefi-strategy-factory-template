"""Environment-based configuration for the factory client.

Design philosophy: keep `.env` ultra-minimal. Only **user-specific secrets**
live there. Everything else (production API endpoint, ClickHouse host/port/
database/TLS) is hardcoded in this module and committed to the repo, so the
participant never has to touch them.

What the participant puts in `.env` (received from the HiveFi admin):

* ``HIVEFI_API_KEY``
* ``CLICKHOUSE_USER``
* ``CLICKHOUSE_PASSWORD``

Everything else is a constant below. Power users (admin / staging) can still
override any constant via env, but those overrides are not documented in
`.env.example` to keep the user-facing surface tiny.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Production defaults (committed to the repo, never user-touched).
# ---------------------------------------------------------------------------

#: Public Strategy API endpoint. ACM cert + WAFv2 backed.
DEFAULT_API_BASE = "https://strategy-api.hivefi.xyz"

#: ClickHouse Cloud secondary endpoint (the `external-backtest` service).
#: Admins who run the factory against a different warehouse can override via
#: ``CLICKHOUSE_HOST`` env, but participants should not touch this.
DEFAULT_CH_HOST = "secondary.hivefi.clickhouse.cloud"

DEFAULT_CH_PORT = 8443
DEFAULT_CH_DATABASE = "default"
DEFAULT_CH_SECURE = True


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    api_base: str
    ch_host: str
    ch_user: str | None
    ch_password: str | None
    ch_port: int
    ch_database: str
    ch_secure: bool

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "HIVEFI_API_KEY is not set. Copy .env.example to .env and fill in "
                "the API key the HiveFi admin sent you."
            )
        return self.api_key

    def require_clickhouse(self) -> tuple[str, str, str, int]:
        missing = [
            name
            for name, value in (
                ("CLICKHOUSE_USER", self.ch_user),
                ("CLICKHOUSE_PASSWORD", self.ch_password),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "ClickHouse credentials missing: "
                + ", ".join(missing)
                + ". Ask the HiveFi admin for your `u_<6hex>` user + password."
            )
        assert self.ch_user and self.ch_password  # for type checker
        return self.ch_host, self.ch_user, self.ch_password, self.ch_port


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        api_key=os.environ.get("HIVEFI_API_KEY") or None,
        api_base=os.environ.get("HIVEFI_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        ch_host=os.environ.get("CLICKHOUSE_HOST", DEFAULT_CH_HOST),
        ch_user=os.environ.get("CLICKHOUSE_USER") or None,
        ch_password=os.environ.get("CLICKHOUSE_PASSWORD") or None,
        ch_port=int(os.environ.get("CLICKHOUSE_PORT", str(DEFAULT_CH_PORT))),
        ch_database=os.environ.get("CLICKHOUSE_DATABASE", DEFAULT_CH_DATABASE),
        ch_secure=os.environ.get(
            "CLICKHOUSE_SECURE", "true" if DEFAULT_CH_SECURE else "false"
        ).lower()
        != "false",
    )

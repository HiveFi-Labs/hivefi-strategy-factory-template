"""HiveFi Strategy Factory client package.

Wraps the multi-tenant Strategy API (`https://strategy-api.hivefi.xyz`)
and direct ClickHouse access for backtest result retrieval. Used by skills
in `.claude/skills/` and `.agents/skills/`.
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]

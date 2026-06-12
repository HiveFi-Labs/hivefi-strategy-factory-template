"""Console-script entry point for ``hivefi-factory``."""

from __future__ import annotations

import sys

from .cli import main as _main


def main() -> None:
    sys.exit(_main())

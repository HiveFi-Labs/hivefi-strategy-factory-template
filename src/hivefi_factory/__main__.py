"""Allow running ``python -m hivefi_factory ...`` without ``pip install -e .``."""

from __future__ import annotations

from .cli_entry import main

if __name__ == "__main__":
    main()

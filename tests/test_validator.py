"""Unit tests for ``hivefi_factory.validator``.

Two layers:

1. Every shipped strategy in ``extensions/`` must pass — validator + extensions
   are kept in sync, so a drift is a regression.
2. Hand-crafted snippets cover the deny-list categories: forbidden imports,
   forbidden builtins, dunder traversal, suspicious string trampolines, and
   syntax errors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hivefi_factory.validator import (  # noqa: E402
    ValidationError,
    validate_file,
    validate_strategy_code,
)

EXT_DIR = REPO_ROOT / "extensions"


# ---------------------------------------------------------------------------
# 1. Bundled strategies all pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ext_path",
    sorted(EXT_DIR.glob("*.py")),
    ids=lambda p: p.stem,
)
def test_bundled_strategies_pass(ext_path: Path):
    report = validate_file(ext_path)
    assert report.ok, (
        f"{ext_path.name} unexpectedly failed:\n  " + "\n  ".join(report.violations)
    )


# ---------------------------------------------------------------------------
# 2. Forbidden imports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "import os",
        "import subprocess",
        "import socket",
        "import requests",
        "import boto3",
        "import inspect",
        "import operator",
        "import functools",
        "import pickle",
        "import importlib",
        "import builtins",
        "from os import system",
        "from urllib import request",
        "from operator import attrgetter",
    ],
)
def test_forbidden_imports_rejected(snippet: str):
    with pytest.raises(ValidationError):
        validate_strategy_code(snippet)


# ---------------------------------------------------------------------------
# 3. Forbidden calls / dunder traversal / name references
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "x = eval('1 + 1')",
        "x = exec('print(1)')",
        "x = compile('1', '<x>', 'eval')",
        "x = open('a.txt')",
        "x = getattr(object(), 'foo')",
        "x = setattr(object(), 'foo', 1)",
        "x = globals()",
        "x = locals()",
        "x = breakpoint()",
        "x = __import__('os')",
        "x = ().__class__.__bases__",
        "x = (1).__class__.__subclasses__()",
        "x = type.__mro__",
        "def f(): return f.__globals__",
        "x = __builtins__['__import__']",
    ],
)
def test_forbidden_calls_and_dunders_rejected(snippet: str):
    with pytest.raises(ValidationError):
        validate_strategy_code(snippet)


# ---------------------------------------------------------------------------
# 4. String trampolines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "x = '__subclasses__'",
        'x = "__sub" + "classes__"',
        'x = f"__sub{""}classes__"',
        "x = '__import__'",
        "x = '__reduce__'",
    ],
)
def test_suspicious_strings_rejected(snippet: str):
    with pytest.raises(ValidationError):
        validate_strategy_code(snippet)


# ---------------------------------------------------------------------------
# 5. Syntax / size guards
# ---------------------------------------------------------------------------


def test_syntax_error_rejected():
    with pytest.raises(ValidationError):
        validate_strategy_code("def f(:\n    pass\n")


def test_oversize_rejected():
    with pytest.raises(ValidationError):
        validate_strategy_code("x = 0\n" * 200_000, max_size_bytes=1024)


def test_paren_complexity_rejected():
    with pytest.raises(ValidationError):
        validate_strategy_code("x = " + "(" * 600 + "1" + ")" * 600)


# ---------------------------------------------------------------------------
# 6. Allowed code passes
# ---------------------------------------------------------------------------


def test_minimal_strategy_passes():
    code = (
        "from __future__ import annotations\n"
        "import pandas as pd\n"
        "import numpy as np\n"
        "from core.base import StrategyV2\n"
        "from core.context import Signal, StrategyContext\n"
        "\n"
        "class Strategy(StrategyV2):\n"
        "    warmup_periods = 30\n"
        "    data_requirements = ['price']\n"
        "    def compute_signals(self, ctx: StrategyContext):\n"
        "        df = ctx.data['price']\n"
        "        if df.empty:\n"
        "            return []\n"
        "        rets = df.pct_change(20).iloc[-1].dropna()\n"
        "        longs = rets.nlargest(3).index.tolist()\n"
        "        pct = 1.0 / max(len(longs), 1)\n"
        "        return [Signal(symbol=s, side='buy', percentage=pct, time=ctx.date.isoformat()) for s in longs]\n"
    )
    validate_strategy_code(code)

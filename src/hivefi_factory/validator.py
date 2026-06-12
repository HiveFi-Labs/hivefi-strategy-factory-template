"""Local AST denylist validator for strategy code.

Mirrors the server-side validator in bot-2509:
``lib/strategy_security/code_validator.py``. The constants below are kept in
sync with that file (Issue #1885 Step 1, Phase 2 Issue #1826). When the
server tightens or relaxes the rules, update this module to match — running
this client-side gives faster feedback and avoids burning the per-user
write-rate quota on rejected uploads.

Public entry points:

* ``validate_strategy_code(code_text)`` — raises ``ValidationError`` on
  the first violation, with a list of human-readable reasons.
* ``validate_file(path)`` — convenience wrapper that reads the file.

The factory does NOT enforce a positive allow-list; the server uses a
deny-list and we mimic that. Anything not explicitly forbidden is allowed
through; anything in the deny-list is rejected here BEFORE the upload.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

# Default upload size limit (bytes). Matches APIGatewaySettings.max_code_size_bytes.
DEFAULT_MAX_SIZE_BYTES = 1_048_576  # 1 MiB

# ---------------------------------------------------------------------------
# Source: bot-2509 lib/strategy_security/code_validator.py
# Keep in sync with `_FORBIDDEN_NAMES`.
# ---------------------------------------------------------------------------
FORBIDDEN_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        # Network / concurrency
        "subprocess",
        "socket",
        "ctypes",
        "multiprocessing",
        "requests",
        "urllib",
        "http",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
        "telnetlib",
        "asyncio",
        "threading",
        # OS / FS
        "os",
        "sys",
        "signal",
        "pathlib",
        "tempfile",
        "shutil",
        "fcntl",
        "mmap",
        "resource",
        "gc",
        "weakref",
        "atexit",
        "inspect",
        "traceback",
        "linecache",
        # Dynamic import / code exec
        "importlib",
        "imp",
        "runpy",
        "pkgutil",
        "pydoc",
        "code",
        "codeop",
        "ast",
        # Serializer-based RCE
        "pickle",
        "marshal",
        "shelve",
        "dill",
        "cloudpickle",
        "dbm",
        # AWS SDK
        "boto3",
        "botocore",
        "aiobotocore",
        # More network
        "urllib3",
        "websocket",
        "websockets",
        # Misc / process control
        "xmlrpc",
        "wsgiref",
        "webbrowser",
        "cgi",
        "pty",
        # High-level callable objects → getattr/setattr restoration
        "operator",
        "functools",
        # builtins direct import (would restore __import__ / getattr)
        "builtins",
        # Low-level internals
        "_io",
        "_thread",
        "_signal",
        # Codec / decode trampolines
        "codecs",
        "encodings",
        # XXE / zip-based dynamic import
        "xml",
        "zipimport",
    }
)

# Source: `_FORBIDDEN_CALLS`.
FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "breakpoint",
        "help",
    }
)

# Source: `_FORBIDDEN_ATTRIBUTE_PATHS`.
FORBIDDEN_ATTRIBUTE_PATHS: frozenset[tuple[str, str]] = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("os", "exec"),
        ("os", "spawn"),
        ("os", "execv"),
        ("os", "execvp"),
        ("os", "execve"),
        ("os", "spawnl"),
        ("os", "spawnv"),
    }
)

# Source: pandas / filesystem reader-writer denylist used by the Stage 1 sandbox.
FORBIDDEN_ATTRIBUTE_NAMES: frozenset[str] = frozenset(
    {
        "_eval_type",
        "get_type_hints",
        "read_clipboard",
        "read_csv",
        "read_excel",
        "read_feather",
        "read_fwf",
        "read_hdf",
        "read_html",
        "read_json",
        "read_orc",
        "read_parquet",
        "read_pickle",
        "read_sas",
        "read_spss",
        "read_sql",
        "read_sql_query",
        "read_sql_table",
        "read_stata",
        "read_table",
        "read_xml",
        "to_clipboard",
        "to_csv",
        "to_excel",
        "to_feather",
        "to_gbq",
        "to_hdf",
        "to_html",
        "to_json",
        "to_latex",
        "to_markdown",
        "to_orc",
        "to_parquet",
        "to_pickle",
        "to_sql",
        "to_stata",
        "to_xml",
    }
)

# Source: `_FORBIDDEN_DUNDER_ATTRS`.
FORBIDDEN_DUNDER_ATTRS: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__base__",
        "__class__",
        "__globals__",
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        "__reduce__",
        "__reduce_ex__",
        "__code__",
        "__func__",
        "__self__",
        "__dict__",
        "__getattribute__",
        "__getattr__",
        "f_globals",
        "f_locals",
        "f_back",
        "f_builtins",
        "gi_frame",
        "cr_frame",
        "__init_subclass__",
        "__class_getitem__",
        "__set_name__",
    }
)

# Source: `_FORBIDDEN_NAMES_REFERENCED`.
FORBIDDEN_NAMES_REFERENCED: frozenset[str] = frozenset(
    {
        "__builtins__",
        "__loader__",
        "__spec__",
        "__import__",
        "eval",
        "exec",
        "compile",
    }
)

# Source: `_SUSPICIOUS_STRING_LITERALS`.
SUSPICIOUS_STRING_LITERALS: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__bases__",
        "__import__",
        "__globals__",
        "__builtins__",
        "__reduce__",
        "__init_subclass__",
        "__class_getitem__",
        "__set_name__",
    }
)

_BINOP_DEPTH_LIMIT = 50
_BINOP_MULT_LIMIT = 100
_PAREN_NEST_LIMIT = 500
_BRACKET_NEST_LIMIT = 500


class ValidationError(ValueError):
    """Raised when strategy code violates the denylist or syntax rules."""


@dataclass
class ValidationReport:
    path: str | None = None
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _eval_string_binop(node: ast.AST, depth: int = 0) -> str | None:
    if depth > _BINOP_DEPTH_LIMIT:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Add):
            left = _eval_string_binop(node.left, depth + 1)
            right = _eval_string_binop(node.right, depth + 1)
            if left is not None and right is not None:
                return left + right
        elif isinstance(node.op, ast.Mult):
            for str_side, int_side in (
                (node.left, node.right),
                (node.right, node.left),
            ):
                s = _eval_string_binop(str_side, depth + 1)
                if (
                    s is not None
                    and isinstance(int_side, ast.Constant)
                    and isinstance(int_side.value, int)
                ):
                    n = int_side.value
                    if 0 <= n <= _BINOP_MULT_LIMIT:
                        return s * n
    return None


def _eval_static_joined_str(node: ast.JoinedStr) -> str | None:
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue) and value.format_spec is None:
            inner = value.value
            if isinstance(inner, ast.Constant):
                parts.append(str(inner.value))
            else:
                return None
        else:
            return None
    return "".join(parts)


class _DenylistVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in FORBIDDEN_IMPORT_ROOTS:
                self.violations.append(f"import {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top = node.module.split(".")[0]
            if top in FORBIDDEN_IMPORT_ROOTS:
                self.violations.append(f"from {node.module} import ...")
        for alias in node.names:
            if alias.name in FORBIDDEN_DUNDER_ATTRS:
                self.violations.append(f"from {node.module or '?'} import {alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
            self.violations.append(f"call to {node.func.id}()")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in FORBIDDEN_ATTRIBUTE_NAMES:
                self.violations.append(f"call to .{node.func.attr}()")
            if isinstance(node.func.value, ast.Name):
                path = (node.func.value.id, node.func.attr)
                if path in FORBIDDEN_ATTRIBUTE_PATHS:
                    self.violations.append(f"call to {path[0]}.{path[1]}()")
            if node.func.attr == "load":
                for idx, arg in enumerate(node.args):
                    if (
                        idx == 2
                        and isinstance(arg, ast.Constant)
                        and arg.value is True
                    ):
                        self.violations.append("call to .load(..., allow_pickle=True)")
                for keyword in node.keywords:
                    if (
                        keyword.arg == "allow_pickle"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        self.violations.append("call to .load(..., allow_pickle=True)")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_DUNDER_ATTRS:
            self.violations.append(f"attribute access: .{node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES_REFERENCED:
            self.violations.append(f"name reference: {node.id}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            for suspicious in SUSPICIOUS_STRING_LITERALS:
                if suspicious in node.value:
                    self.violations.append(
                        f"suspicious string literal: {suspicious!r}"
                    )
                    break
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in FORBIDDEN_DUNDER_ATTRS:
            self.violations.append(f"function definition: def {node.name}")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name in FORBIDDEN_DUNDER_ATTRS:
            self.violations.append(f"async function definition: async def {node.name}")
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        val = _eval_string_binop(node)
        if val is not None:
            for suspicious in SUSPICIOUS_STRING_LITERALS:
                if suspicious in val:
                    self.violations.append(
                        f"string concatenation produces suspicious literal: {suspicious!r}"
                    )
                    break
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        joined = _eval_static_joined_str(node)
        if joined is not None:
            for suspicious in SUSPICIOUS_STRING_LITERALS:
                if suspicious in joined:
                    self.violations.append(
                        f"f-string produces suspicious literal: {suspicious!r}"
                    )
                    break
        self.generic_visit(node)


def validate_strategy_code(
    code_text: str, *, max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
) -> None:
    """Validate strategy code; raise ``ValidationError`` on first failure."""
    code_bytes = code_text.encode("utf-8")
    if len(code_bytes) > max_size_bytes:
        raise ValidationError(f"code size {len(code_bytes)} exceeds {max_size_bytes} bytes")

    if code_text.count("(") > _PAREN_NEST_LIMIT:
        raise ValidationError(f"parenthesis count exceeds {_PAREN_NEST_LIMIT}")
    if code_text.count("[") > _BRACKET_NEST_LIMIT:
        raise ValidationError(f"bracket count exceeds {_BRACKET_NEST_LIMIT}")

    try:
        tree = ast.parse(code_text)
    except SyntaxError as exc:
        raise ValidationError(f"SyntaxError: {exc}") from exc

    try:
        compile(code_text, "<strategy>", "exec")
    except SyntaxError as exc:
        raise ValidationError(f"SyntaxError: {exc}") from exc

    visitor = _DenylistVisitor()
    visitor.visit(tree)
    if visitor.violations:
        raise ValidationError("Forbidden constructs: " + ", ".join(visitor.violations))


def validate_file(
    path: str | Path, *, max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
) -> ValidationReport:
    """Validate a file on disk; return a report instead of raising."""
    p = Path(path)
    report = ValidationReport(path=str(p))
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        report.violations.append(f"could not read file: {exc}")
        return report
    try:
        validate_strategy_code(text, max_size_bytes=max_size_bytes)
    except ValidationError as exc:
        report.violations.append(str(exc))
    return report

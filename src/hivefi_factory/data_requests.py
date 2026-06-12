"""Local data-request records for research ideas blocked by missing data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tools" / "symphony" / "data_requests"


@dataclass(frozen=True)
class DataRequestInput:
    idea: str
    needed_data: list[str]
    reason: str
    task_id: str | None = None
    current_data: list[str] = field(default_factory=list)
    source: str | None = None
    symbols: list[str] = field(default_factory=list)
    start: str | None = None
    end: str | None = None
    frequency: str | None = None
    fields: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    priority: str = "medium"
    state: str = "Open"
    request_id: str | None = None


def slugify(value: str, *, fallback: str = "data-request", max_length: int = 72) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = fallback
    return slug[:max_length].strip("-") or fallback


def split_cli_values(values: list[str] | None) -> list[str]:
    """Split repeated CLI values and comma-separated chunks into clean strings."""
    if not values:
        return []
    out: list[str] = []
    for raw in values:
        for part in raw.split(","):
            item = part.strip()
            if item:
                out.append(item)
    return out


def format_data_request(req: DataRequestInput, *, now: datetime | None = None) -> str:
    created_at = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    request_id = req.request_id or _default_request_id(req, created_at)
    fields = {
        "id": request_id,
        "state": req.state,
        "priority": req.priority,
        "created_at": created_at,
        "task_id": req.task_id or "",
    }

    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(
        [
            "---",
            "",
            f"# Data Request: {req.idea}",
            "",
            "## Blocked Idea",
            "",
            req.idea.strip(),
            "",
            "## Missing Data",
            "",
            _bullet_list(req.needed_data),
            "",
            "## Why Existing Data Is Insufficient",
            "",
            req.reason.strip(),
            "",
            "## Checked Data",
            "",
            _bullet_list(req.current_data),
            "",
            "## Requested Coverage",
            "",
            f"- Source: {req.source or '(not specified)'}",
            f"- Symbols / universe: {_inline_list(req.symbols)}",
            f"- Start: {req.start or '(not specified)'}",
            f"- End: {req.end or '(not specified)'}",
            f"- Frequency: {req.frequency or '(not specified)'}",
            f"- Fields: {_inline_list(req.fields)}",
            "",
            "## Acceptance Criteria",
            "",
            _bullet_list(req.acceptance),
            "",
            "## Notes",
            "",
            _bullet_list(req.notes),
            "",
        ]
    )
    return "\n".join(lines)


def write_data_request(
    req: DataRequestInput,
    *,
    output_dir: str | Path | None = None,
    output: str | Path | None = None,
    overwrite: bool = False,
    now: datetime | None = None,
) -> Path:
    actual_now = now or datetime.now(UTC)
    markdown = format_data_request(req, now=actual_now)
    created_at = actual_now.replace(microsecond=0).isoformat()
    request_id = req.request_id or _default_request_id(req, created_at)

    if output is not None:
        path = Path(output)
    else:
        directory = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR
        path = directory / f"{request_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"data request already exists: {path}")
    path.write_text(markdown, encoding="utf-8")
    return path


def _default_request_id(req: DataRequestInput, created_at: str) -> str:
    prefix = req.task_id or req.idea
    stamp = created_at[:19].replace("-", "").replace(":", "").replace("T", "t")
    return f"{slugify(prefix)}-{stamp}"


def _bullet_list(items: list[str]) -> str:
    cleaned = [item.strip() for item in items if item.strip()]
    if not cleaned:
        return "- (not specified)"
    return "\n".join(f"- {item}" for item in cleaned)


def _inline_list(items: list[str]) -> str:
    cleaned = [item.strip() for item in items if item.strip()]
    return ", ".join(cleaned) if cleaned else "(not specified)"

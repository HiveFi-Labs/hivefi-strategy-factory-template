from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO / "tools" / "symphony" / "local_tasks"
OUT = REPO / "tools" / "symphony" / "TASK_STATUS.md"

STATE_ORDER = ["Todo", "In Progress", "Rework", "On Hold", "Done"]


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def _field(fm: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", fm, flags=re.M)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _labels(fm: str) -> list[str]:
    labels: list[str] = []
    in_labels = False
    for line in fm.splitlines():
        if line.startswith("labels:"):
            in_labels = True
            continue
        if not in_labels:
            continue
        if re.match(r"^[A-Za-z_][\w-]*:", line):
            break
        match = re.match(r"\s*-\s*(.+)", line)
        if match:
            labels.append(match.group(1).strip().strip('"').strip("'"))
    return labels


def _strategy_id(body: str) -> str:
    match = re.search(r"strategy_id:\s*`([^`]+)`", body)
    if match:
        return match.group(1)
    match = re.search(r"new strategy_id:\s*`([^`]+)`", body)
    if match:
        return match.group(1)
    return ""


def main() -> None:
    rows: list[dict[str, object]] = []
    for path in sorted(TASKS_DIR.glob("*.md")):
        text = path.read_text()
        fm = _frontmatter(text)
        rows.append(
            {
                "state": _field(fm, "state") or "(missing)",
                "identifier": _field(fm, "identifier") or path.stem,
                "title": _field(fm, "title"),
                "labels": _labels(fm),
                "strategy_id": _strategy_id(text),
                "path": path,
            }
        )

    state_counts = Counter(str(row["state"]) for row in rows)
    active_count = sum(state_counts.get(s, 0) for s in ["Todo", "In Progress", "Rework"])

    lines = [
        "# Symphony Task Status",
        "",
        f"全 {len(rows)} tasks。active は Todo / In Progress / Rework。",
        "",
        "| state | count |",
        "| --- | ---: |",
    ]
    ordered_states = STATE_ORDER + sorted(k for k in state_counts if k not in STATE_ORDER)
    for state in ordered_states:
        if state in state_counts:
            lines.append(f"| {state} | {state_counts[state]} |")

    lines.extend(
        [
            "",
            f"Active: {active_count}",
            "",
        ]
    )

    by_state: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_state[str(row["state"])].append(row)

    for state in ordered_states:
        items = by_state.get(state)
        if not items:
            continue
        lines.extend(
            [
                f"## {state} ({len(items)})",
                "",
                "| identifier | strategy_id | labels | title | task |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in items:
            labels = ", ".join(row["labels"]) if row["labels"] else "-"
            strategy_id = str(row["strategy_id"]) or "-"
            path = Path(row["path"])
            rel = path.relative_to(OUT.parent)
            lines.append(
                f"| `{row['identifier']}` | `{strategy_id}` | {labels} | {row['title']} | [{path.name}]({rel}) |"
            )
        lines.append("")

    OUT.write_text("\n".join(lines).rstrip() + "\n")
    print(f"wrote {OUT.relative_to(REPO)} ({len(rows)} rows)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

COMMENTS = REPO / "tools" / "symphony" / "local_comments"
TASKS = REPO / "tools" / "symphony" / "local_tasks"
OUT = REPO / "STRATEGY_STATUS.md"
BT_CSV = Path("/tmp/bt_full.csv")
NUM_RE = r"[+\-]?[0-9]*\.?[0-9]+(?:[eE][+\-]?[0-9]+)?"
INT_RE = r"[0-9][0-9,]*"
BT_FIELDS = (
    "strategy_id",
    "executed_at",
    "total_return",
    "sharpe_ratio",
    "max_drawdown",
    "total_trades",
)

STATUS_DEFINITIONS = (
    ("公式BTあり", "公式BTの指標あり"),
    ("分析済み・BTなし", "結果コメントあり、公式BTなし"),
    ("未記録", "結果コメントがない、または task が未完了"),
)
STATUS_ORDER = {name: idx for idx, (name, _desc) in enumerate(STATUS_DEFINITIONS)}

EVIDENCE_TAGS = (
    "strong-alpha",
    "moderate",
    "adopt",
    "invert",
    "diversifier",
    "positive",
    "weak",
    "reject",
    "broken",
    "negative",
    "pending",
    "retry",
    "skip",
    "inconclusive",
    "blocked",
    "skipped",
)

EVIDENCE_NOTES = {
    "strong-alpha": "",
    "moderate": "",
    "adopt": "",
    "invert": "",
    "diversifier": "",
    "positive": "",
    "weak": "",
    "reject": "仮説を支持する統計証拠なし",
    "broken": "検証結果に異常あり",
    "negative": "事前仮説と逆方向",
    "pending": "",
    "retry": "再分析が必要",
    "skip": "",
    "inconclusive": "",
    "blocked": "",
    "skipped": "",
}

TARGET_NOTES = {
    "weak": "",
    "missing": "対象条件で期待反応なし",
    "blocked": "対象条件のproxyなし",
    "positive": "",
    "present": "",
}

TRACKER_STATE_NOTES = {
    "Todo": "task未着手",
    "In Progress": "task実行中",
    "Rework": "task再作業",
    "On Hold": "task保留",
    "Canceled": "task取消",
    "Cancelled": "task取消",
    "Duplicate": "task重複",
    "Closed": "task終了",
}


def _refresh_bt_cache() -> dict[str, dict[str, str]]:
    if not BT_CSV.exists() or "--no-refresh" not in sys.argv:
        try:
            from hivefi_factory.clickhouse import ClickHouseClient

            with ClickHouseClient() as ch:
                rows = ch.query_rows(
                    "SELECT strategy_id, executed_at, total_return, sharpe_ratio, "
                    "max_drawdown, total_trades FROM backtest_runs "
                    "ORDER BY executed_at DESC LIMIT 100000"
                )
            with BT_CSV.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(BT_FIELDS))
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in BT_FIELDS})
        except Exception:
            try:
                subprocess.run(
                    [
                        "hivefi-factory",
                        "data",
                        "fetch",
                        "backtest_runs",
                        "--output",
                        str(BT_CSV),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
            if BT_CSV.exists():
                # The generic data fetch helper emits a wide panel, not the
                # run-summary table expected below, so ignore unusable output.
                header = BT_CSV.read_text(errors="ignore").splitlines()[:1]
                if header and not set(BT_FIELDS).issubset(set(header[0].split(","))):
                    BT_CSV.unlink(missing_ok=True)

    out: dict[str, dict[str, str]] = {}
    if not BT_CSV.exists():
        return out
    with BT_CSV.open() as f:
        for row in csv.DictReader(f):
            sid = row.get("strategy_id", "")
            if not sid:
                continue
            key = sid.lower()
            if key not in out or row.get("executed_at", "") > out[key].get("executed_at", ""):
                out[key] = {**row, "canonical_id": sid}
    return out


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def _frontmatter_value(text: str, key: str) -> str | None:
    fm = _frontmatter(text)
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", fm, flags=re.M)
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'")


def _task_strategy_id(text: str) -> str | None:
    for pattern in (
        r"new strategy_id:\s*`([^`]+)`",
        r"strategy_id:\s*`([^`]+)`",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _task_priority(path: Path) -> int:
    if path.name.startswith("local-retry-"):
        return 3
    if path.name.startswith("local-eval-"):
        return 2
    return 1


def _find_num(text: str, *patterns: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        try:
            return float(match.group(1).replace(",", ""))
        except (ValueError, IndexError):
            continue
    return None


def _find_p_values(text: str) -> list[float]:
    matches: list[tuple[int, float]] = []
    for pattern in (
        rf"\bp[_\- ]?value[=:\s]*[`]?({NUM_RE})",
        rf"(?<![A-Za-z_])p\s*[=:]\s*[`]?({NUM_RE})",
    ):
        for match in re.finditer(pattern, text, flags=re.I):
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0.0 <= value <= 1.0:
                matches.append((match.start(), value))
    return [value for _pos, value in sorted(matches)]


def _primary_p_values(primary: float | None, values: list[float]) -> list[float]:
    if primary is None:
        return values

    ordered = [primary]
    skipped_primary = False
    for value in values:
        if not skipped_primary and abs(value - primary) < 1e-12:
            skipped_primary = True
            continue
        ordered.append(value)
    return ordered


def _p_value_from_t_stat(t_stat: float, sample_n: int | None) -> float | None:
    if sample_n is None or sample_n <= 2:
        return None

    try:
        from scipy import stats

        return float(stats.t.sf(abs(t_stat), df=sample_n - 1) * 2.0)
    except Exception:
        pass
    return math.erfc(abs(t_stat) / math.sqrt(2.0))


def _find_int(text: str, *patterns: str) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        try:
            return int(match.group(1).replace(",", ""))
        except (ValueError, IndexError):
            continue
    return None


def _last_block(text: str, heading: str) -> str:
    matches = re.findall(
        rf"^## {re.escape(heading)}\s*$.*?(?=^##\s|\Z)",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    return matches[-1] if matches else ""


def _comment_path(identifier: str) -> Path:
    return COMMENTS / f"{identifier}.md"


def _latest_timestamp(text: str) -> str | None:
    timestamps = re.findall(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\b", text)
    return max(timestamps) if timestamps else None


def _target_evidence(result: str) -> str:
    match = re.search(r"target_evidence\s*[=:]\s*([A-Za-z_\-]+)", result)
    return match.group(1).lower() if match else ""


def _data_issue_note(result: str) -> str:
    lower = result.lower()

    has_l1 = bool(
        re.search(
            r"\bl1\b|top[- ]of[- ]book|best bid|best ask|bid_size|ask_size|order[- ]book",
            lower,
        )
    )
    if "data_request" in lower:
        return "必要データなし: L1板" if has_l1 else "必要データなし"
    if re.search(r"timeout|タイムアウト|fetch 停滞|完走しなかった|完走せず|120 秒制限", lower):
        return "集計未完走"
    if re.search(r"最低 5 symbols|ほぼ 1 銘柄|各日ほぼ 1 銘柄|cross-section.*symbol.*満たさず|sparse event", lower):
        return "横断銘柄数不足"
    if re.search(r"volume.*2025-08|positive volume|volume 履歴|出来高履歴", lower):
        return "出来高履歴不足"
    if has_l1 and re.search(r"無か|無い|ない|不在|欠落|見つからな|確認できな", result):
        return "必要データなし: L1板"
    if re.search(r"価格取得不可|price data unavailable|price.*missing|close.*missing", lower):
        return "価格データ不足"
    if re.search(r"直接 symbol がなく|mapping でき|symbol がなく|銘柄.*足り", result):
        return "対象銘柄mapping不足"
    if re.search(r"volume_z.*欠|出来高.*欠|1h volume.*0", result):
        return "出来高proxy不足"
    if re.search(r"coverage.*不足|coverage.*低|欠損.*多|欠損.*あり|欠損が|揃わない", result):
        return "データcoverage不足"
    return ""


def _extract_evidence(result: str, text: str) -> str:
    for source in (result, text):
        if not source:
            continue
        for pattern in (
            r"(?<!target_)\bevidence\s*[=:]\s*([A-Za-z_\-/]+)",
            r"\bverdict\s*[=:]\s*([A-Za-z_\-/]+)",
        ):
            match = re.search(pattern, source)
            if not match:
                continue
            evidence_text = match.group(1)
            for tag in EVIDENCE_TAGS:
                if tag in evidence_text:
                    return tag
    return "—"


def _status_note(
    result: str,
    evidence: str,
    trades: int | None,
    sample_n: int | None,
) -> str:
    if not result:
        return "結果コメントなし"

    data_note = _data_issue_note(result)
    if data_note:
        return data_note

    notes: list[str] = []
    target = _target_evidence(result)
    if target:
        note = TARGET_NOTES.get(target, f"対象={target}")
        if note:
            notes.append(note)

    if evidence not in {"", "—"}:
        note = EVIDENCE_NOTES.get(evidence, f"証拠分析: {evidence}")
        if note:
            notes.append(note)

    if trades is not None and trades < 2000:
        notes.append("BT取引数が2000未満")

    if sample_n is None and re.search(r"sample未記録|sample が未記録|sample.*not recorded", result):
        notes.append("sample未記録")

    if notes:
        return " / ".join(list(dict.fromkeys(notes))[:2])

    return "—"


def _parse_comment(path: Path, trades: int | None) -> dict[str, Any]:
    if not path.exists():
        return {
            "has_comment": False,
            "evidence": "—",
            "ic_mean": None,
            "r2_mean": None,
            "t_stat": None,
            "p_value": None,
            "p_values": [],
            "p_value_source": None,
            "q_value": None,
            "global_q_value": None,
            "test_family_n": None,
            "comment_updated_at": None,
            "sample_n": None,
            "sample_y": None,
            "q_spread": None,
            "note": "結果コメントなし",
        }

    text = path.read_text()
    result = _last_block(text, "結果")
    stage2 = re.search(
        r"## 工程レポート:\s*2\..*?(?=^##\s|\Z)",
        text,
        flags=re.DOTALL | re.MULTILINE,
    )
    metric_text = result or (stage2.group(0) if stage2 else text)

    ic_mean = _find_num(
        metric_text,
        rf"IC\s*mean[=\s]*[`]?({NUM_RE})",
        rf"ic_mean[=\s]*[`]?({NUM_RE})",
        rf"W-FRI[^|\n]*IC[^|\n]*?mean[=\s]*[`]?({NUM_RE})",
        rf"weekly[^|\n]*?mean[=\s]*[`]?({NUM_RE})",
    )
    r2_mean = _find_num(
        metric_text,
        rf"R2_mean[=\s]*[`]?({NUM_RE})",
        rf"r2_mean[=\s]*[`]?({NUM_RE})",
    )
    if r2_mean is not None and not (0.0 <= r2_mean <= 1.0):
        r2_mean = None
    if r2_mean is None and ic_mean is not None and -1.0 <= ic_mean <= 1.0:
        r2_mean = ic_mean * ic_mean

    sample_n = _find_int(
        metric_text,
        rf"sample_n[=\s]*[`]?({INT_RE})",
        rf"ic_n[=\s]*[`]?({INT_RE})",
        rf"\bn[=\s]*[`]?({INT_RE})",
        rf"samples?[=\s]*[`]?({INT_RE})",
        rf"events?[=\s]*[`]?({INT_RE})",
        rf"target 群は n=({INT_RE})",
        rf"({INT_RE})\s*件",
    )
    t_stat = _find_num(
        metric_text,
        rf"\bt[_\- ]?stat[=:\s]*[`]?({NUM_RE})",
        rf"\bt\s*[=:]\s*[`]?({NUM_RE})",
    )
    raw_p_values = _find_p_values(metric_text)
    p_value = _find_num(
        metric_text,
        rf"\bp[_\- ]?value[=:\s]*[`]?({NUM_RE})",
    )
    if p_value is not None and not (0.0 <= p_value <= 1.0):
        p_value = None
    p_value_source = "recorded" if p_value is not None else None
    if p_value is None and raw_p_values:
        p_value = raw_p_values[0]
        p_value_source = "recorded"
    if p_value is None and t_stat is not None:
        inferred_p_value = _p_value_from_t_stat(t_stat, sample_n)
        if inferred_p_value is not None:
            p_value = inferred_p_value
            p_value_source = "t_stat"
    p_values = _primary_p_values(p_value, raw_p_values)
    q_value = _find_num(
        metric_text,
        rf"\bq[_\- ]?value[=:\s]*[`]?({NUM_RE})",
    )
    if q_value is not None and not (0.0 <= q_value <= 1.0):
        q_value = None
    test_family_n = _find_int(
        metric_text,
        rf"\btest[_\- ]?family[_\- ]?n[=:\s]*[`]?({INT_RE})",
        rf"\bfamily[_\- ]?size[=:\s]*[`]?({INT_RE})",
        rf"\btests?[=:\s]*[`]?({INT_RE})",
        rf"検定ファミリー[^0-9\n]*({INT_RE})",
    )

    sample_y = _find_num(
        metric_text,
        rf"sample[^|\n]*?({NUM_RE})\s*y\b",
        rf"({NUM_RE})\s*y\s*(?:proxy|sample)",
        rf"({NUM_RE})\s*年",
    )
    if sample_y is not None and sample_y > 20:
        sample_y = None

    q_spread = _find_num(
        metric_text,
        rf"Q5-Q1\s*spread[=\s]*[`]?({NUM_RE})",
        rf"Q5-Q1[=\s]*[`]?({NUM_RE})",
        rf"Q1-Q5[=\s]*[`]?({NUM_RE})",
        rf"spread[(]?Q5-Q1[)]?[=\s]*[`]?({NUM_RE})",
    )

    evidence = _extract_evidence(result, text)

    return {
        "has_comment": True,
        "evidence": evidence,
        "ic_mean": ic_mean,
        "r2_mean": r2_mean,
        "t_stat": t_stat,
        "p_value": p_value,
        "p_values": p_values,
        "p_value_source": p_value_source,
        "q_value": q_value,
        "global_q_value": None,
        "test_family_n": test_family_n,
        "comment_updated_at": _latest_timestamp(text),
        "sample_n": sample_n,
        "sample_y": sample_y,
        "q_spread": q_spread,
        "note": _status_note(result or text, evidence, trades, sample_n),
    }


def _collect_strategies(bt: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    strategies: dict[str, dict[str, Any]] = {}
    priorities: dict[str, int] = {}

    for task_path in sorted(TASKS.glob("local-*.md")):
        text = task_path.read_text()
        sid = _task_strategy_id(text)
        if not sid:
            continue
        priority = _task_priority(task_path)
        key = sid.lower()
        if "w-fri-hyperliquid-hl_all-long-short-v2" in key:
            short_key = re.sub(
                r"-w-fri-hyperliquid-hl_all-long-short-v2$",
                "-d-w-hl-all-ls-v2",
                key,
            )
            if (COMMENTS / f"{_frontmatter_value(text, 'identifier')}.md").exists() and short_key in priorities:
                continue
        if key in priorities and priority < priorities[key]:
            continue

        bt_row = bt.get(key)
        trades = int(bt_row["total_trades"]) if bt_row and bt_row.get("total_trades") else None
        identifier = _frontmatter_value(text, "identifier") or task_path.stem
        parsed = _parse_comment(_comment_path(identifier), trades)
        state = _frontmatter_value(text, "state") or "Unknown"
        task_created_at = _frontmatter_value(text, "created_at") or ""
        task_updated_at = _frontmatter_value(text, "updated_at") or _frontmatter_value(text, "created_at")

        row = {
            "sid": bt_row.get("canonical_id", sid) if bt_row else sid,
            "state": state,
            "created_at": task_created_at,
            "updated_at": parsed.get("comment_updated_at") or task_updated_at or "",
            "comment": f"{identifier}.md",
            "task": task_path.name,
            "sr": float(bt_row["sharpe_ratio"]) if bt_row and bt_row.get("sharpe_ratio") else None,
            "mdd": float(bt_row["max_drawdown"]) if bt_row and bt_row.get("max_drawdown") else None,
            "trades": trades,
            **parsed,
        }
        row["status"] = _row_status(row)
        strategies[key] = row
        priorities[key] = priority

    return list(strategies.values())


def _row_status(row: dict[str, Any]) -> str:
    if row["trades"] is not None:
        return "公式BTあり"
    if row["has_comment"]:
        return "分析済み・BTなし"
    return "未記録"


def _format(value: Any, spec: str, fallback: str = "—") -> str:
    return spec.format(value) if value is not None else fallback


def _format_date(timestamp: str) -> str:
    if not timestamp:
        return "—"
    match = re.match(r"([0-9]{4}-[0-9]{2}-[0-9]{2})", timestamp)
    return match.group(1) if match else timestamp


def _apply_global_fdr(rows: list[dict[str, Any]]) -> int:
    tests: list[tuple[float, dict[str, Any], int]] = []
    for row in rows:
        row["global_q_value"] = None
        for idx, p_value in enumerate(row.get("p_values") or []):
            tests.append((p_value, row, idx))

    if not tests:
        return 0

    ranked = sorted(range(len(tests)), key=lambda idx: tests[idx][0])
    q_values = [1.0] * len(tests)
    running_min = 1.0
    total = len(tests)
    for rank_pos in range(total - 1, -1, -1):
        test_idx = ranked[rank_pos]
        p_value, _row, _within_row_idx = tests[test_idx]
        rank = rank_pos + 1
        running_min = min(running_min, p_value * total / rank, 1.0)
        q_values[test_idx] = running_min

    for test_idx, q_value in enumerate(q_values):
        _p_value, row, within_row_idx = tests[test_idx]
        if within_row_idx == 0:
            row["global_q_value"] = q_value

    return total


def _append_note(existing: str, note: str) -> str:
    if not note:
        return existing
    if existing in {"", "—"}:
        return note
    parts = existing.split(" / ")
    if note not in parts and len(parts) < 2:
        parts.append(note)
    return " / ".join(parts)


def _apply_global_notes(rows: list[dict[str, Any]], q_threshold: float = 0.10) -> None:
    for row in rows:
        q_value = row.get("global_q_value")
        if q_value is not None and q_value > q_threshold:
            row["note"] = _append_note(row.get("note", "—"), "全体補正で有意性なし")


def _significance_note(row: dict[str, Any], q_threshold: float = 0.10) -> str:
    q_value = row.get("global_q_value")
    p_value = row.get("p_value")

    if q_value is None:
        if p_value is not None:
            return "全体q未計算"
        return "p未記録"

    return f"全体q<={q_threshold:.2f}" if q_value <= q_threshold else f"全体q>{q_threshold:.2f}"


def main() -> int:
    rows = _collect_strategies(_refresh_bt_cache())
    global_test_count = _apply_global_fdr(rows)
    _apply_global_notes(rows)

    lines = [
        "# 戦略一覧",
        "",
        f"全 {len(rows)} 戦略。状態別、各状態内は新しい順。",
        f"全体補正は、結果コメントから抽出できた p_value {global_test_count} 件に Benjamini-Hochberg FDR を適用。",
        "本ファイルは `tools/refresh_strategy_status.py` で再生成される。",
    ]

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["status"]].append(row)

    for status, description in STATUS_DEFINITIONS:
        group = sorted(
            grouped.get(status, []),
            key=lambda row: (row["updated_at"] or "", row["sid"]),
            reverse=True,
        )
        if not group:
            continue
        lines.extend(
            [
                "",
                f"## {status} ({len(group)})",
                "",
                f"{description}。",
                "",
                "| strategy | 作成日 | 問題点 | R2_mean | sample | p_value | 全体q_value | 全体補正 | SR | MaxDD | trades | 詳細 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in group:
            sample = _format(row["sample_n"], "{:,}")
            mdd = "—" if row["mdd"] is None else f"-{abs(row['mdd']) * 100:.0f}%"
            detail_path = (
                f"tools/symphony/local_comments/{row['comment']}"
                if row["has_comment"]
                else f"tools/symphony/local_tasks/{row['task']}"
            )
            lines.append(
                f"| `{row['sid']}` | "
                f"{_format_date(row['created_at'])} | "
                f"{row['note']} | "
                f"{_format(row['r2_mean'], '{:.4f}')} | "
                f"{sample} | "
                f"{_format(row['p_value'], '{:.3g}')} | "
                f"{_format(row['global_q_value'], '{:.3g}')} | "
                f"{_significance_note(row)} | "
                f"{_format(row['sr'], '{:+.2f}')} | "
                f"{mdd} | "
                f"{_format(row['trades'], '{:,}')} | "
                f"[md]({detail_path}) |"
            )

    status_legend = " / ".join(f"`{name}`={desc}" for name, desc in STATUS_DEFINITIONS)
    lines.extend(
        [
            "",
            "## 凡例",
            "",
            f"- **状態**: 作業成果物の有無。{status_legend}",
            "- **作成日**: local task frontmatter の `created_at`。`—` は未記録。",
            "- **問題点**: 結果コメントから抽出した具体的な問題または未解決事項のみ。内部状態タグを出さず、問題を抽出しない場合は `—`。",
            "- **R2_mean**: 評価時点ごとの `IC^2` 平均。方向は `IC mean`、説明力は `R2_mean` で見る。固定閾値は置かない。",
            "- **sample**: IC / event 診断のサンプル数。`—` は結果コメントに数が未記録。",
            f"- **p_value / 全体q_value / 全体補正**: `p_value` は各 strategy row の主 p 値。明示値がなく `t` / `t_stat` と sample 数がある場合だけ、t 分布で両側 p を推定する。`全体q_value` は一覧内の結果コメントから抽出または推定できた p_value {global_test_count} 件すべてに Benjamini-Hochberg FDR をかけた値。p_value も、sample 付き t も未記録の row は補正対象外。",
            "- **SR / MaxDD / trades**: 公式BT結果 (`backtest_runs`) から取得。`—` は公式BT未実行、または一覧データ未取得。",
            "",
            "## 再生成",
            "",
            "```bash",
            "python tools/refresh_strategy_status.py",
            "```",
        ]
    )

    OUT.write_text("\n".join(lines).rstrip() + "\n")
    print(f"wrote {OUT.relative_to(REPO)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Static smoke tests for .claude/skills/ and .agents/skills/.

agent が skill を invoke する際に破綻しないよう、以下を検査する:
1. 各 skill directory に SKILL.md が存在
2. YAML frontmatter に name / description / trigger が揃っている
3. SKILL.md 内の bash code block に書かれた `hivefi-factory ...` コマンドが
   実在の CLI subcommand を叩いている (typo / 廃止コマンド検知)
4. ルートドキュメント (README/CLAUDE/AGENTS) が新 API を指している
5. .claude/skills/ と .agents/skills/ のミラー整合
6. Symphony / WORKFLOW の契約

`hivefi-factory --help` 出力だけで primitives の名前空間を検証するので、
オフライン (network 不要) で全テストが回る。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
AGENTS_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
SRC_DIR = REPO_ROOT / "src"

# Make the in-repo package importable without `pip install -e .` so the test
# suite runs in CI before that step is part of the workflow.
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# ---------------------------------------------------------------------------
# 1. directory / SKILL.md の存在
# ---------------------------------------------------------------------------


def test_skills_directory_exists():
    assert SKILLS_DIR.is_dir(), f"missing skills dir: {SKILLS_DIR}"


def test_each_skill_has_skill_md():
    skills = [d for d in SKILLS_DIR.iterdir() if d.is_dir()]
    assert skills, "no skill subdirectories found"
    for skill_dir in skills:
        md = skill_dir / "SKILL.md"
        assert md.exists(), f"missing SKILL.md: {md}"


# ---------------------------------------------------------------------------
# 2. frontmatter に name / description / trigger が揃う
# ---------------------------------------------------------------------------


def _load_frontmatter(md: Path) -> dict[str, str]:
    """SKILL.md 先頭の YAML frontmatter を dict にして返す (簡易 parser)。"""
    text = md.read_text()
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    yaml_block = parts[1]
    fm: dict[str, str] = {}
    current_key: str | None = None
    for line in yaml_block.splitlines():
        if ":" in line and not line.lstrip().startswith("-") and not line.startswith(" "):
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            fm[key] = rest
            current_key = key
        elif current_key and line.strip():
            fm[current_key] = (fm[current_key] + " " + line.strip()).strip()
    return fm


@pytest.mark.parametrize(
    "skill_dir",
    [d for d in sorted(SKILLS_DIR.iterdir()) if d.is_dir()],
    ids=lambda d: d.name,
)
def test_skill_frontmatter(skill_dir: Path):
    fm = _load_frontmatter(skill_dir / "SKILL.md")
    assert "name" in fm and fm["name"], f"{skill_dir.name}: missing `name`"
    assert "description" in fm, f"{skill_dir.name}: missing `description`"
    assert "trigger" in fm, f"{skill_dir.name}: missing `trigger`"
    assert fm["name"] == skill_dir.name, (
        f"{skill_dir.name}: frontmatter name={fm['name']!r} != dir name"
    )


# ---------------------------------------------------------------------------
# 3. SKILL.md 内の hivefi-factory コマンドが実在の subcommand か
# ---------------------------------------------------------------------------


_CODE_BLOCK_RE = re.compile(r"```(?:bash|sh)\n(.*?)\n```", re.DOTALL)


def _build_factory_help_index() -> dict[str | None, set[str]]:
    """`hivefi-factory <group> --help` を実行せず、argparse の parser を直接読む。

    Returns:
        { None: {top-level subcommand names},
          "strategy": {nested subcommand names},
          "code": {...},
          "bt": {...},
          "data": {...} }
    """
    try:
        from hivefi_factory.cli import build_parser  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        pytest.skip("hivefi_factory package not importable; run `pip install -e .`")
    parser = build_parser()
    index: dict[str | None, set[str]] = {None: set()}
    # The top-level subparsers action.
    for action in parser._actions:  # noqa: SLF001 - argparse private API
        if action.__class__.__name__ != "_SubParsersAction":
            continue
        for name, subparser in action.choices.items():
            index[None].add(name)
            for sub_action in subparser._actions:  # noqa: SLF001
                if sub_action.__class__.__name__ != "_SubParsersAction":
                    continue
                index.setdefault(name, set()).update(sub_action.choices.keys())
    return index


def _extract_factory_calls(md: Path) -> list[tuple[str, str | None]]:
    """SKILL.md の bash block から `hivefi-factory <group> [<sub>]` を抽出する。"""
    text = md.read_text()
    calls: list[tuple[str, str | None]] = []
    for block in _CODE_BLOCK_RE.findall(text):
        for line in block.splitlines():
            line = line.strip()
            m = re.match(r"hivefi-factory\s+([A-Za-z_-]+)(?:\s+([A-Za-z_-]+))?", line)
            if m:
                group = m.group(1)
                sub = m.group(2)
                # `--help`, `--version`, `--all` などのオプションは sub として扱わない
                if sub and sub.startswith("-"):
                    sub = None
                calls.append((group, sub))
    return calls


@pytest.mark.parametrize(
    "skill_dir",
    [d for d in sorted(SKILLS_DIR.iterdir()) if d.is_dir()],
    ids=lambda d: d.name,
)
def test_skill_references_valid_cli_commands(skill_dir: Path):
    """SKILL.md 内の `hivefi-factory <group> <sub>` が実在するか検証。"""
    index = _build_factory_help_index()
    top = index[None]
    if not top:
        pytest.skip("hivefi-factory top-level commands not detected")

    calls = _extract_factory_calls(skill_dir / "SKILL.md")
    if not calls:
        pytest.skip(f"{skill_dir.name}: no hivefi-factory invocation in SKILL.md")

    # Groups whose argparse uses subcommands (require nested choice match).
    nested = {g for g in index if g is not None}

    errors: list[str] = []
    for group, sub in calls:
        if group not in top:
            errors.append(f"unknown group: hivefi-factory {group}")
            continue
        if sub is None:
            # `hivefi-factory validate` etc. accept positional args, no nested sub
            continue
        if group in nested and sub not in index[group]:
            errors.append(f"unknown subcommand: hivefi-factory {group} {sub}")

    assert not errors, f"{skill_dir.name}:\n  " + "\n  ".join(errors)


# ---------------------------------------------------------------------------
# 4. README.md / CLAUDE.md / AGENTS.md の存在と最低限の内容
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc", ["README.md", "CLAUDE.md", "AGENTS.md"])
def test_root_doc_exists_and_nontrivial(doc: str):
    p = REPO_ROOT / doc
    assert p.exists(), f"missing: {doc}"
    text = p.read_text()
    assert len(text) > 500, f"{doc} is suspiciously short ({len(text)} chars)"
    assert "hivefi" in text.lower(), f"{doc}: no mention of hivefi"


def test_example_strategies_exist():
    """参考戦略が configs + extensions に揃っていること。"""
    configs = list((REPO_ROOT / "configs").glob("*.json"))
    extensions = list((REPO_ROOT / "extensions").glob("*.py"))
    assert configs, "no example configs/*.json found"
    assert extensions, "no example extensions/*.py found"
    config_ids = {p.stem for p in configs}
    ext_ids = {p.stem for p in extensions}
    paired = config_ids & ext_ids
    assert paired, (
        f"no strategy with both config and extension\n"
        f"configs: {config_ids}\nextensions: {ext_ids}"
    )


# ---------------------------------------------------------------------------
# 5. BT 評価の規範が docs に残っていること (drift 検知)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "doc_path",
    [
        "CLAUDE.md",
        "AGENTS.md",
        ".claude/skills/submit-flow/SKILL.md",
    ],
)
def test_bt_window_norm_documented(doc_path: str):
    """BT window / KPI 提示の規範がテンプレ docs から消えていないことを確認する。"""
    text = (REPO_ROOT / doc_path).read_text()
    required_any = ["2000", "trades", "diagnostic"]
    missing = [kw for kw in required_any if kw not in text]
    assert not missing, (
        f"{doc_path}: BT sample-size norm から以下 keyword が消えている: {missing}"
    )


def test_deprecated_old_cli_not_referenced():
    """旧 hivefi CLI の data/strategy/bt/signal subcommand が docs から消えていること。"""
    deprecated = [
        "hivefi data list",
        "hivefi data fetch",
        "hivefi data schema",
        "hivefi data catalog",
        "hivefi strategy submit",
        "hivefi strategy status",
        "hivefi strategy list",
        "hivefi bt run",
        "hivefi bt diag",
        "hivefi signal submit",
        "HIVEFI_API_TOKEN",
        "HIVEFI_API_URL=",
    ]
    failures: list[str] = []
    for doc in [
        "CLAUDE.md",
        "AGENTS.md",
        "README.md",
        "WORKFLOW.md",
        ".env.example",
    ]:
        text = (REPO_ROOT / doc).read_text()
        for term in deprecated:
            if term in text:
                failures.append(f"{doc}: deprecated `{term}` 参照が残っている")
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# 6. Claude (.claude/skills/) と Codex (.agents/skills/) の skill ミラー整合
# ---------------------------------------------------------------------------


def _collect_skills(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        d.name: (d / "SKILL.md").read_text()
        for d in sorted(root.iterdir())
        if d.is_dir() and (d / "SKILL.md").exists()
    }


def test_agents_skills_dir_exists():
    assert AGENTS_SKILLS_DIR.is_dir(), (
        f"missing {AGENTS_SKILLS_DIR}: Codex CLI の skill auto-discovery path。"
        f".claude/skills/ を複製してミラーを保つこと"
    )


def test_skill_sets_match_between_claude_and_agents():
    claude = set(_collect_skills(SKILLS_DIR).keys())
    agents = set(_collect_skills(AGENTS_SKILLS_DIR).keys())
    only_in_claude = claude - agents
    only_in_agents = agents - claude
    assert not only_in_claude and not only_in_agents, (
        f"skill sets drift:\n"
        f"  only in .claude/skills: {sorted(only_in_claude)}\n"
        f"  only in .agents/skills: {sorted(only_in_agents)}\n"
        f"→ 片方で追加 / 削除したらもう片方も更新すること (複製 + 同期方針)"
    )


@pytest.mark.parametrize(
    "skill_name",
    sorted(_collect_skills(SKILLS_DIR).keys()) or ["__no_skills_found__"],
)
def test_skill_md_content_identical(skill_name: str):
    if skill_name == "__no_skills_found__":
        pytest.skip("no skills in .claude/skills/")
    claude_file = SKILLS_DIR / skill_name / "SKILL.md"
    agents_file = AGENTS_SKILLS_DIR / skill_name / "SKILL.md"
    if not agents_file.exists():
        pytest.fail(f"missing {agents_file}")
    assert claude_file.read_text() == agents_file.read_text(), (
        f"{skill_name}: SKILL.md が .claude と .agents で差分。"
        f"diff を確認して同期すること"
    )


def test_symphony_workflow_uses_new_factory_cli():
    """Strategy issue は hivefi-factory health 通過なしで Codex 実行へ進めないこと。"""
    workflow = (REPO_ROOT / "WORKFLOW.md").read_text()
    bootstrap = (REPO_ROOT / "tools" / "symphony" / "bootstrap_codex_workspace.sh").read_text()
    data_check = (REPO_ROOT / "tools" / "symphony" / "check_data_access.sh").read_text()
    readme = (REPO_ROOT / "tools" / "symphony" / "README.md").read_text()

    assert "hivefi-factory health" in workflow, "WORKFLOW.md must call hivefi-factory health"
    assert "hivefi-factory validate --all" in workflow
    assert "hivefi-factory data fetch" in data_check
    assert "HIVEFI_DATA_ACCESS_TTL_SECONDS" in data_check
    assert "HIVEFI_API_KEY must be exported before starting Symphony" in workflow
    assert 'export PATH="$PWD/.venv/bin:$PATH"' in workflow
    assert "./.venv/bin/hivefi-factory --version" in bootstrap, (
        "bootstrap script must smoke-test the hivefi-factory console script"
    )
    assert "set -a" in readme and ". ./.env" in readme and "set +a" in readme


def test_symphony_reports_single_result_to_local_task():
    """local tracker への報告は `## 結果` 1 コメントにすること。"""
    workflow = (REPO_ROOT / "WORKFLOW.md").read_text()
    report_format = (
        REPO_ROOT / "tools" / "symphony" / "STRATEGY_REPORT_FORMAT.md"
    ).read_text()
    template = (REPO_ROOT / "tools" / "symphony" / "LOCAL_TASK_TEMPLATE.md").read_text()
    readme = (REPO_ROOT / "tools" / "symphony" / "README.md").read_text()

    assert "kind: file" in workflow
    assert "tasks_dir: $HIVEFI_STRATEGY_FACTORY_TASKS_DIR" in workflow
    assert "comments_dir: $HIVEFI_STRATEGY_FACTORY_COMMENTS_DIR" in workflow
    assert "HIVEFI_STRATEGY_FACTORY_SOURCE:?" in workflow
    assert "tracker_comment" in workflow
    assert "tracker_update_state" in workflow
    assert "linear_graphql" not in workflow
    assert "commentCreate" not in workflow
    assert "Work on at most one strategy idea for this task" in workflow
    assert "Post exactly one short `## 結果` comment" in workflow
    assert "Do not post `## 工程レポート`" in workflow
    assert "state: Todo" in template
    assert "1 task は 1 strategy" in template
    assert "evidence gate" in template
    assert "Workflow result checks should be run against one selected task first" in readme
    assert "Each local task should handle at most one strategy idea" in readme
    assert "HIVEFI_STRATEGY_FACTORY_TASKS_DIR" in readme
    assert "HIVEFI_STRATEGY_FACTORY_COMMENTS_DIR" in readme
    assert "## 結果" in report_format
    assert "## 工程レポート` 見出しは使わない" in report_format


def test_validator_rejects_pandas_file_readers_and_writers():
    """server denylist と drift しないよう pandas file I/O を local validator でも拒否する。"""
    from hivefi_factory.validator import ValidationError, validate_strategy_code

    snippets = {
        "reader": "import pandas as pd\nx = pd.read_csv('x.csv')\n",
        "writer": "def f(df):\n    return df.to_parquet('x.parquet')\n",
    }
    for name, code in snippets.items():
        with pytest.raises(ValidationError, match="Forbidden constructs"):
            validate_strategy_code(code)

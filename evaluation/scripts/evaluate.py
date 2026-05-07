import argparse
import base64
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import libcst as cst
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
PYMIGBENCH_ROOT = PROJECT_ROOT.parent / "PyMigBench"
if PYMIGBENCH_ROOT.exists() and str(PYMIGBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(PYMIGBENCH_ROOT))

from migration_checker.collector import collect_facts
from migration_checker.main import analyze_source_code
from migration_checker.rules import (
    RuleResolutionError,
    iter_rule_files,
    load_rules,
    resolve_rule_file,
)
from pymigbench.database import Database
from pymigbench.line_range import LineRange

DEFAULT_FAULT_TYPES = ("source-import", "source-call-swap", "mixed-usage")
PAIR_FAULT_MODE_CHOICES = ("default", "none", "only")
LEFTOVER_GROUPS = ("leftover",)
GITHUB_API_BASE = "https://api.github.com"
USER_AGENT = "migration-checker-eval"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "evaluation" / "data" / "results"
TRACK_SUMMARY_FILE_SUFFIXES = {
    "clean-post": "clean_post_summary",
    "fault-inject": "fault_inject_summary",
    "pre-leftover": "pre_leftover_summary",
}


@dataclass(frozen=True)
class DiagnosticRecord:
    line: int
    code: str
    severity: str
    message: str

    @classmethod
    def from_mapping(cls, mapping: dict) -> "DiagnosticRecord":
        return cls(
            line=int(mapping["line"]),
            code=str(mapping["code"]),
            severity=str(mapping.get("severity", "warning")),
            message=str(mapping["message"]),
        )

    def key(self) -> Tuple[int, str]:
        return (self.line, self.code)

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True)
class ExpectedDiagnostic:
    fault_type: str
    code: str
    line_range: LineRange

    def matches(self, diagnostic: DiagnosticRecord) -> bool:
        return self.code == diagnostic.code and self.line_range.intersects_range(diagnostic.line)

    def to_dict(self) -> dict:
        return {
            "fault_type": self.fault_type,
            "code": self.code,
            "line_range": str(self.line_range),
        }


class SnapshotCache:
    def __init__(self, root: Path, token: Optional[str], refresh: bool = False):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.refresh = refresh
        self.parent_cache: Dict[str, Optional[str]] = {}
        self.file_cache: Dict[Tuple[str, str, str], Optional[str]] = {}
        self.stats = {
            "metadata_hits": 0,
            "metadata_writes": 0,
            "snapshot_hits": 0,
            "snapshot_writes": 0,
            "refresh_fallback_hits": 0,
            "fetch_failures": 0,
        }

    def _migration_dir(self, migration) -> Path:
        safe_id = sanitize_component(migration.id())
        return self.root / safe_id

    def _metadata_path(self, migration) -> Path:
        return self._migration_dir(migration) / "metadata.json"

    def _load_metadata(self, migration) -> dict:
        metadata_path = self._metadata_path(migration)
        if metadata_path.exists():
            try:
                return json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_metadata(self, migration, metadata: dict) -> None:
        metadata_path = self._metadata_path(migration)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        self.stats["metadata_writes"] += 1

    def get_parent_sha(self, migration) -> Optional[str]:
        cache_key = f"{migration.repo}@{migration.commit}"
        if cache_key in self.parent_cache and not self.refresh:
            return self.parent_cache[cache_key]

        metadata = self._load_metadata(migration)
        if metadata.get("parent_sha") and not self.refresh:
            self.stats["metadata_hits"] += 1
            self.parent_cache[cache_key] = metadata["parent_sha"]
            return metadata["parent_sha"]

        parent_sha = fetch_parent_sha(migration.repo, migration.commit, self.token, self.parent_cache)
        if parent_sha:
            metadata.update(
                {
                    "migration_id": migration.id(),
                    "repo": migration.repo,
                    "commit": migration.commit,
                    "parent_sha": parent_sha,
                    "source": migration.source,
                    "target": migration.target,
                }
            )
            self._save_metadata(migration, metadata)
        return parent_sha

    def get_snapshot(self, migration, migration_file, stage: str) -> Tuple[Optional[str], Optional[str]]:
        if stage not in {"pre", "post"}:
            return None, f"unknown-stage:{stage}"

        commit_sha = migration.commit
        if stage == "pre":
            commit_sha = self.get_parent_sha(migration)
            if not commit_sha:
                self.stats["fetch_failures"] += 1
                return None, "missing-parent"

        snapshot_path = self._migration_dir(migration) / stage / migration_file.path
        if snapshot_path.exists() and not self.refresh:
            self.stats["snapshot_hits"] += 1
            return snapshot_path.read_text(encoding="utf-8"), None

        content, reason = fetch_file_content(
            migration.repo,
            commit_sha,
            migration_file.path,
            self.token,
            self.file_cache,
        )
        if content is None:
            if snapshot_path.exists():
                self.stats["refresh_fallback_hits"] += 1
                return snapshot_path.read_text(encoding="utf-8"), None
            self.stats["fetch_failures"] += 1
            return None, f"{stage}-{reason or 'fetch-failed'}"

        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(content, encoding="utf-8")
        self.stats["snapshot_writes"] += 1

        metadata = self._load_metadata(migration)
        metadata.update(
            {
                "migration_id": migration.id(),
                "repo": migration.repo,
                "commit": migration.commit,
                "source": migration.source,
                "target": migration.target,
            }
        )
        if stage == "pre" and commit_sha:
            metadata["parent_sha"] = commit_sha
        self._save_metadata(migration, metadata)
        return content, None

    def report(self) -> dict:
        return {
            "cache_dir": str(self.root),
            **self.stats,
        }


def sanitize_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)


def load_evaluator_env() -> None:
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_github_token() -> Optional[str]:
    return os.environ.get("GITHUB_TOKEN") or None


def normalize_output_json_path(output_path: Path) -> Path:
    if output_path.is_absolute():
        return output_path if output_path.suffix else output_path.with_suffix(".json")
    if output_path.parent == Path("."):
        filename = output_path.name if output_path.suffix else f"{output_path.name}.json"
        return DEFAULT_RESULTS_DIR / filename
    return output_path if output_path.suffix else output_path.with_suffix(".json")


def get_track_summary_csv_path(json_path: Path, track: str) -> Path:
    suffix = TRACK_SUMMARY_FILE_SUFFIXES[track]
    return json_path.with_name(f"{json_path.stem}_{suffix}.csv")


def get_by_code_csv_path(json_path: Path) -> Path:
    return json_path.with_name(f"{json_path.stem}_by_code.csv")


def build_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def fetch_json(url: str, token: Optional[str]) -> Optional[dict]:
    request = Request(url, headers=build_headers(token))
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError):
        return None


def fetch_json_with_status(url: str, token: Optional[str]) -> Tuple[Optional[dict], Optional[int]]:
    request = Request(url, headers=build_headers(token))
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8")), None
    except HTTPError as exc:
        return None, exc.code
    except (URLError, json.JSONDecodeError):
        return None, None


def fetch_text(url: str, token: Optional[str]) -> Optional[str]:
    request = Request(url, headers=build_headers(token))
    try:
        with urlopen(request) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError):
        return None


def fetch_parent_sha(repo: str, commit_sha: str, token: Optional[str], cache: Dict[str, Optional[str]]) -> Optional[str]:
    cache_key = f"{repo}@{commit_sha}"
    if cache_key in cache:
        return cache[cache_key]

    url = f"{GITHUB_API_BASE}/repos/{repo}/commits/{commit_sha}"
    data = fetch_json(url, token)
    if not data or not data.get("parents"):
        cache[cache_key] = None
        return None

    parent_sha = data["parents"][0].get("sha")
    cache[cache_key] = parent_sha
    return parent_sha


def fetch_file_content(
    repo: str,
    commit_sha: str,
    file_path: str,
    token: Optional[str],
    cache: Dict[Tuple[str, str, str], Optional[str]],
) -> Tuple[Optional[str], Optional[str]]:
    cache_key = (repo, commit_sha, file_path)
    if cache_key in cache:
        return cache[cache_key], None if cache[cache_key] is not None else "cached-missing"

    encoded_path = quote(file_path, safe="/")
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{encoded_path}?ref={commit_sha}"
    data, status = fetch_json_with_status(url, token)
    if not data or data.get("type") != "file":
        raw_url = f"https://raw.githubusercontent.com/{repo}/{commit_sha}/{encoded_path}"
        raw_content = fetch_text(raw_url, token)
        if raw_content is not None:
            cache[cache_key] = raw_content
            return raw_content, None
        cache[cache_key] = None
        if status == 404:
            return None, "missing-file"
        if status is not None:
            return None, f"http-{status}"
        return None, "api-error"

    content = None
    if data.get("encoding") == "base64" and data.get("content"):
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            content = None
    elif data.get("download_url"):
        content = fetch_text(data["download_url"], token)

    if content is None:
        raw_url = f"https://raw.githubusercontent.com/{repo}/{commit_sha}/{encoded_path}"
        content = fetch_text(raw_url, token)

    cache[cache_key] = content
    return content, None if content is not None else "download-failed"


def get_benchmark_version(pymigbench_path: Path) -> str:
    version_path = pymigbench_path / "version"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return "unknown"


def get_pair_key(source: str, target: str) -> Tuple[str, str]:
    return (source, target)


def get_rule_pair(rules: dict) -> dict:
    return {
        "source": rules["pair"]["source"],
        "target": rules["pair"]["target"],
    }


def get_target_to_source_map(rules: dict) -> dict:
    mapping = {}
    for rule in rules["call_rules"]:
        mapping.setdefault(rule["target"]["symbol"], rule["source"]["symbol"])
    return mapping


def load_migrations_by_pair(pymigbench_path: Path) -> dict:
    migration_data_path = pymigbench_path / "data" / "migration"
    db = Database.load_from_dir(migration_data_path)
    grouped = {}
    for migration in db.migs():
        grouped.setdefault(get_pair_key(migration.source, migration.target), []).append(migration)
    return grouped


def discover_feasible_pair_configs(
    pymigbench_path: Path,
    *,
    source: Optional[str] = None,
    target: Optional[str] = None,
    rule_file: Optional[Path] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    migrations_by_pair = load_migrations_by_pair(pymigbench_path)
    configs = []

    if rule_file or (source and target):
        resolved = resolve_rule_file(
            source=source,
            target=target,
            rule_file=rule_file,
            project_root=PROJECT_ROOT,
        )
        rules = load_rules(resolved)
        pair = get_rule_pair(rules)
        pair_key = get_pair_key(pair["source"], pair["target"])
        migrations = list(migrations_by_pair.get(pair_key, []))
        if not migrations:
            raise RuleResolutionError(
                f"No direct PyMigBench migrations found for {pair['source']} -> {pair['target']}"
            )
        configs.append(
            {
                "rule_file": resolved,
                "rules": rules,
                "pair": pair,
                "migrations": migrations[:limit] if limit else migrations,
            }
        )
        return configs

    for candidate in iter_rule_files(project_root=PROJECT_ROOT):
        rules = load_rules(candidate)
        pair = get_rule_pair(rules)
        pair_key = get_pair_key(pair["source"], pair["target"])
        migrations = list(migrations_by_pair.get(pair_key, []))
        if not migrations:
            continue
        configs.append(
            {
                "rule_file": candidate.resolve(),
                "rules": rules,
                "pair": pair,
                "migrations": migrations[:limit] if limit else migrations,
            }
        )
    return configs


def analyze_content(content: str, rules: dict, enabled_groups: Optional[Sequence[str]] = None) -> Tuple[Optional[list[DiagnosticRecord]], Optional[str]]:
    try:
        diagnostics = analyze_source_code(content, rules, enabled_groups=enabled_groups)
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"analysis-error:{type(exc).__name__}"
    return [DiagnosticRecord.from_mapping(diag) for diag in diagnostics], None


def validate_python(content: str) -> Optional[str]:
    try:
        cst.parse_module(content)
    except Exception as exc:  # pragma: no cover - defensive
        return f"syntax-error:{type(exc).__name__}"
    return None


def join_lines(lines: Sequence[str]) -> str:
    return "\n".join(lines) + "\n"


def append_code_block(content: str, block_lines: Sequence[str]) -> Tuple[str, int]:
    lines = content.splitlines()
    if lines and lines[-1] != "":
        lines.append("")
    start_line = len(lines) + 1
    lines.extend(block_lines)
    return join_lines(lines), start_line


def has_source_import(facts, rules: dict) -> bool:
    return any(
        any(imp["name"] == root or imp["name"].startswith(f"{root}.") for root in rules["source_roots"])
        for imp in facts.imports
    )


def append_source_import(lines: list[str], rules: dict) -> Tuple[list[str], Optional[int]]:
    import_line = f"import {rules['source_roots'][0]}"
    if import_line in lines:
        return lines, None
    updated = list(lines)
    updated.append(import_line)
    return updated, len(updated)


def inject_source_import(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    facts = collect_facts(content)
    if has_source_import(facts, rules):
        return None, [], "source-import-already-present"

    lines, import_line = append_source_import(content.splitlines(), rules)
    new_content = join_lines(lines)
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error

    expected = [
        ExpectedDiagnostic("source-import", "leftover_source_import", LineRange.from_bounds(import_line, import_line))
    ]
    return new_content, expected, None


def inject_source_call_swap(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    mappings = get_target_to_source_map(rules)
    short_name_map = {target.split(".")[-1]: source for target, source in mappings.items()}
    facts = collect_facts(content)
    lines = content.splitlines()

    for call in facts.calls:
        target_symbol = call["qualified_name"]
        source_symbol = mappings.get(target_symbol) or short_name_map.get(target_symbol.split(".")[-1])
        if source_symbol is None:
            continue

        line_index = call["line"] - 1
        if line_index >= len(lines):
            continue

        target_name = target_symbol.split(".")[-1]
        pattern = rf"\b[\w\.]+\.{re.escape(target_name)}\s*\("
        replacement = f"{source_symbol}("
        new_line, substitutions = re.subn(pattern, replacement, lines[line_index], count=1)
        if substitutions != 1:
            continue

        updated_lines = list(lines)
        updated_lines[line_index] = new_line
        expected = [
            ExpectedDiagnostic("source-call-swap", "leftover_source_call", LineRange.from_bounds(call["line"], call["line"]))
        ]

        if not has_source_import(facts, rules):
            updated_lines, import_line = append_source_import(updated_lines, rules)
            if import_line is not None:
                expected.append(
                    ExpectedDiagnostic(
                        "source-call-swap",
                        "leftover_source_import",
                        LineRange.from_bounds(import_line, import_line),
                    )
                )

        new_content = join_lines(updated_lines)
        syntax_error = validate_python(new_content)
        if syntax_error:
            return None, [], syntax_error
        return new_content, expected, None

    return None, [], "no-target-call"


def inject_mixed_usage(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    mappings = get_target_to_source_map(rules)
    target_symbols = set(mappings)
    facts = collect_facts(content)
    lines = content.splitlines()

    for assignment in facts.assignments:
        target_symbol = assignment.get("qualified_call_name")
        if target_symbol not in target_symbols:
            continue

        end_line = int(assignment.get("end_line", assignment["line"]))
        if end_line > len(lines):
            continue

        indent_match = re.match(r"\s*", lines[end_line - 1])
        indent = indent_match.group(0) if indent_match else ""
        variable_name = assignment["variable_name"]
        source_symbol = mappings[target_symbol]
        inserted_line = f"{indent}{variable_name} = {source_symbol}('http://example.com')"

        updated_lines = list(lines)
        updated_lines.insert(end_line, inserted_line)
        injected_line = end_line + 1
        expected = [
            ExpectedDiagnostic("mixed-usage", "leftover_source_call", LineRange.from_bounds(injected_line, injected_line)),
            ExpectedDiagnostic(
                "mixed-usage",
                "mixed_source_target_assignment",
                LineRange.from_bounds(injected_line, injected_line),
            ),
        ]

        if not has_source_import(facts, rules):
            updated_lines, import_line = append_source_import(updated_lines, rules)
            if import_line is not None:
                expected.append(
                    ExpectedDiagnostic(
                        "mixed-usage",
                        "leftover_source_import",
                        LineRange.from_bounds(import_line, import_line),
                    )
                )

        new_content = join_lines(updated_lines)
        syntax_error = validate_python(new_content)
        if syntax_error:
            return None, [], syntax_error
        return new_content, expected, None

    return None, [], "no-target-assignment"


def inject_aiohttp_missing_context(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "import aiohttp",
            "",
            "async def __mv_fault_aiohttp_missing_context():",
            "    session = aiohttp.ClientSession()",
            "    response = session.get('https://example.com')",
            "    return response",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "aiohttp-missing-context",
            "missing_context_manager",
            LineRange.from_bounds(start_line + 4, start_line + 4),
        )
    ]
    return new_content, expected, None


def inject_aiohttp_missing_await_method(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "import aiohttp",
            "",
            "async def __mv_fault_aiohttp_missing_await():",
            "    async with aiohttp.ClientSession() as session:",
            "        async with session.get('https://example.com') as response:",
            "            return response.text()",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "aiohttp-missing-await-method",
            "missing_await",
            LineRange.from_bounds(start_line + 5, start_line + 5),
        )
    ]
    return new_content, expected, None


def inject_aiohttp_renamed_attribute(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "import aiohttp",
            "",
            "async def __mv_fault_aiohttp_status_code():",
            "    async with aiohttp.ClientSession() as session:",
            "        async with session.get('https://example.com') as response:",
            "            return response.status_code",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "aiohttp-renamed-attribute",
            "renamed_attribute_access",
            LineRange.from_bounds(start_line + 5, start_line + 5),
        )
    ]
    return new_content, expected, None


def inject_aiohttp_forbidden_attribute(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "import aiohttp",
            "",
            "async def __mv_fault_aiohttp_forbidden_attr():",
            "    async with aiohttp.ClientSession() as session:",
            "        async with session.get('https://example.com') as response:",
            "            return response.text",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "aiohttp-forbidden-attribute",
            "forbidden_attribute_access",
            LineRange.from_bounds(start_line + 5, start_line + 5),
        )
    ]
    return new_content, expected, None


def inject_click_forbidden_keyword(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "import click",
            "",
            "@click.command()",
            "@click.option('--broken', nargs=1)",
            "def __mv_fault_click_forbidden(broken):",
            "    return broken",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "click-forbidden-keyword",
            "forbidden_keyword_argument",
            LineRange.from_bounds(start_line + 3, start_line + 3),
        )
    ]
    return new_content, expected, None


def inject_click_leftover_parse_args(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "import argparse",
            "",
            "def __mv_fault_click_parse_args():",
            "    parser = argparse.ArgumentParser()",
            "    return parser.parse_args()",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "click-leftover-parse-args",
            "leftover_source_import",
            LineRange.from_bounds(start_line, start_line),
        ),
        ExpectedDiagnostic(
            "click-leftover-parse-args",
            "leftover_source_call",
            LineRange.from_bounds(start_line + 4, start_line + 4),
        ),
    ]
    return new_content, expected, None


def inject_quart_missing_await_template(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "from quart import render_template",
            "",
            "async def __mv_fault_quart_template():",
            "    return render_template('index.html')",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "quart-missing-await-template",
            "missing_await",
            LineRange.from_bounds(start_line + 3, start_line + 3),
        )
    ]
    return new_content, expected, None


def inject_quart_missing_await_request_json(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "from quart import request",
            "",
            "async def __mv_fault_quart_request_json():",
            "    return request.get_json()",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "quart-missing-await-request-json",
            "missing_await",
            LineRange.from_bounds(start_line + 3, start_line + 3),
        )
    ]
    return new_content, expected, None


def inject_quart_leftover_request_form(content: str, rules: dict) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    new_content, start_line = append_code_block(
        content,
        [
            "from flask import request",
            "",
            "async def __mv_fault_quart_request_form():",
            "    return request.form",
        ],
    )
    syntax_error = validate_python(new_content)
    if syntax_error:
        return None, [], syntax_error
    expected = [
        ExpectedDiagnostic(
            "quart-leftover-request-form",
            "leftover_source_import",
            LineRange.from_bounds(start_line, start_line),
        ),
        ExpectedDiagnostic(
            "quart-leftover-request-form",
            "leftover_source_call",
            LineRange.from_bounds(start_line + 3, start_line + 3),
        ),
    ]
    return new_content, expected, None


PAIR_SPECIFIC_INJECTOR_REGISTRY = {
    ("requests", "aiohttp"): {
        "aiohttp-missing-context": inject_aiohttp_missing_context,
        "aiohttp-missing-await-method": inject_aiohttp_missing_await_method,
        "aiohttp-renamed-attribute": inject_aiohttp_renamed_attribute,
        "aiohttp-forbidden-attribute": inject_aiohttp_forbidden_attribute,
    },
    ("argparse", "click"): {
        "click-forbidden-keyword": inject_click_forbidden_keyword,
        "click-leftover-parse-args": inject_click_leftover_parse_args,
    },
    ("flask", "quart"): {
        "quart-missing-await-template": inject_quart_missing_await_template,
        "quart-missing-await-request-json": inject_quart_missing_await_request_json,
        "quart-leftover-request-form": inject_quart_leftover_request_form,
    },
}


def get_pair_specific_injectors(rules: dict) -> dict:
    pair = rules["pair"]
    return PAIR_SPECIFIC_INJECTOR_REGISTRY.get((pair["source"], pair["target"]), {})


def get_effective_fault_types(
    rules: dict,
    generic_fault_types: Sequence[str],
    pair_fault_mode: str,
) -> Tuple[list[str], Optional[str]]:
    pair_specific = list(get_pair_specific_injectors(rules))
    if pair_fault_mode == "none":
        return list(generic_fault_types), None
    if pair_fault_mode == "only":
        if pair_specific:
            return pair_specific, None
        return [], "pair-faults-unsupported"

    effective = list(generic_fault_types)
    for fault_type in pair_specific:
        if fault_type not in effective:
            effective.append(fault_type)
    return effective, None


def inject_fault(content: str, rules: dict, fault_type: str) -> Tuple[Optional[str], list[ExpectedDiagnostic], Optional[str]]:
    if fault_type == "source-import":
        return inject_source_import(content, rules)
    if fault_type == "source-call-swap":
        return inject_source_call_swap(content, rules)
    if fault_type == "mixed-usage":
        return inject_mixed_usage(content, rules)
    pair_specific = get_pair_specific_injectors(rules)
    injector = pair_specific.get(fault_type)
    if injector is not None:
        return injector(content, rules)
    return None, [], f"unknown-fault:{fault_type}"


def score_expected(
    diagnostics: Sequence[DiagnosticRecord],
    expected: Sequence[ExpectedDiagnostic],
    baseline: Optional[Sequence[DiagnosticRecord]] = None,
) -> dict:
    relevant = [diag for diag in diagnostics if any(item.matches(diag) for item in expected)]
    baseline_keys = {
        diag.key()
        for diag in (baseline or [])
        if any(item.matches(diag) for item in expected)
    }
    relevant = [diag for diag in relevant if diag.key() not in baseline_keys]

    matched_keys = set()
    matched_records = []
    missing_expected = []
    tp = 0
    fn = 0

    for item in expected:
        match = next((diag for diag in relevant if item.matches(diag) and diag.key() not in matched_keys), None)
        if match is None:
            fn += 1
            missing_expected.append(item.to_dict())
            continue
        tp += 1
        matched_keys.add(match.key())
        matched_records.append(match)

    fp_records = [diag for diag in relevant if diag.key() not in matched_keys]
    return {
        "tp": tp,
        "fp": len(fp_records),
        "fn": fn,
        "matched": [diag.to_dict() for diag in matched_records],
        "unexpected": [diag.to_dict() for diag in fp_records],
        "missing": missing_expected,
    }


def derive_leftover_oracle(content: str, rules: dict) -> list[ExpectedDiagnostic]:
    facts = collect_facts(content)
    expected = []
    source_symbols = set(rules["source_call_symbols"])
    source_access_symbols = set(rules["source_access_symbols"])

    for item in facts.imports:
        if any(item["name"] == root or item["name"].startswith(f"{root}.") for root in rules["source_roots"]):
            expected.append(
                ExpectedDiagnostic(
                    "pre-leftover",
                    "leftover_source_import",
                    LineRange.from_bounds(item["line"], item["line"]),
                )
            )

    for item in facts.calls:
        if item["qualified_name"] in source_symbols:
            expected.append(
                ExpectedDiagnostic(
                    "pre-leftover",
                    "leftover_source_call",
                    LineRange.from_bounds(item["line"], item["line"]),
                )
            )
    for item in facts.accesses:
        if item["qualified_name"] in source_access_symbols:
            expected.append(
                ExpectedDiagnostic(
                    "pre-leftover",
                    "leftover_source_call",
                    LineRange.from_bounds(item["line"], item["line"]),
                )
            )
    return expected


def safe_div(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return numerator / denominator


def make_track_report(
    benchmark_version: str,
    rule_pair: dict,
    track: str,
    summary: dict,
    per_file: list[dict],
    summary_by_code: Optional[dict],
    skips: dict,
    cache: SnapshotCache,
) -> dict:
    return {
        "benchmark_version": benchmark_version,
        "rule_pair": rule_pair,
        "track": track,
        "summary": summary,
        "per_file": per_file,
        "summary_by_code": summary_by_code or {},
        "skips": skips,
        "cache_stats": cache.report(),
    }


def record_skip(skip_counts: Dict[str, int], reason: str) -> None:
    skip_counts[reason] = skip_counts.get(reason, 0) + 1


def summarize_code_scores(per_file: Sequence[dict]) -> dict:
    code_totals: Dict[str, Dict[str, int]] = {}
    for item in per_file:
        for matched in item.get("matched", []):
            code_totals.setdefault(matched["code"], {"true_positives": 0, "false_positives": 0, "false_negatives": 0})
            code_totals[matched["code"]]["true_positives"] += 1
        for unexpected in item.get("unexpected", []):
            code_totals.setdefault(unexpected["code"], {"true_positives": 0, "false_positives": 0, "false_negatives": 0})
            code_totals[unexpected["code"]]["false_positives"] += 1
        for missing in item.get("missing", []):
            code_totals.setdefault(missing["code"], {"true_positives": 0, "false_positives": 0, "false_negatives": 0})
            code_totals[missing["code"]]["false_negatives"] += 1

    summary_by_code = {}
    for code, totals in sorted(code_totals.items()):
        tp = totals["true_positives"]
        fp = totals["false_positives"]
        fn = totals["false_negatives"]
        summary_by_code[code] = {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": safe_div(tp, tp + fp),
            "recall": safe_div(tp, tp + fn),
        }
    return summary_by_code


def evaluate_clean_post(
    migrations: Sequence,
    rules: dict,
    cache: SnapshotCache,
    benchmark_version: str,
) -> dict:
    per_file = []
    skip_counts: Dict[str, int] = {}
    clean_files = 0
    false_positive_diagnostics = 0
    rule_pair = get_rule_pair(rules)

    for migration in migrations:
        for migration_file in migration.files:
            if not migration_file.path.lower().endswith(".py"):
                record_skip(skip_counts, "non-python")
                continue

            post_content, reason = cache.get_snapshot(migration, migration_file, "post")
            if post_content is None:
                record_skip(skip_counts, reason or "post-missing")
                continue

            diagnostics, analysis_error = analyze_content(post_content, rules)
            if diagnostics is None:
                record_skip(skip_counts, analysis_error or "analysis-error")
                continue

            false_positive_diagnostics += len(diagnostics)
            if not diagnostics:
                clean_files += 1

            per_file.append(
                {
                    "migration_id": migration.id(),
                    "path": migration_file.path,
                    "false_positive_count": len(diagnostics),
                    "diagnostics": [diag.to_dict() for diag in diagnostics],
                }
            )

    total_files = len(per_file)
    summary = {
        "files_evaluated": total_files,
        "clean_files": clean_files,
        "files_with_diagnostics": total_files - clean_files,
        "false_positive_diagnostics": false_positive_diagnostics,
        "clean_file_rate": safe_div(clean_files, total_files),
    }
    return make_track_report(benchmark_version, rule_pair, "clean-post", summary, per_file, {}, skip_counts, cache)


def evaluate_fault_injection(
    migrations: Sequence,
    rules: dict,
    cache: SnapshotCache,
    benchmark_version: str,
    fault_types: Sequence[str],
    pair_fault_mode: str,
) -> dict:
    per_file = []
    skip_counts: Dict[str, int] = {}
    total_tp = total_fp = total_fn = 0
    variants = 0
    rule_pair = get_rule_pair(rules)
    effective_fault_types, pair_fault_skip = get_effective_fault_types(rules, fault_types, pair_fault_mode)
    if pair_fault_skip:
        record_skip(skip_counts, pair_fault_skip)
    if not effective_fault_types:
        summary = {
            "variants_evaluated": 0,
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": 0,
            "precision": None,
            "recall": None,
        }
        return make_track_report(benchmark_version, rule_pair, "fault-inject", summary, per_file, {}, skip_counts, cache)

    for migration in migrations:
        for migration_file in migration.files:
            if not migration_file.path.lower().endswith(".py"):
                record_skip(skip_counts, "non-python")
                continue

            post_content, reason = cache.get_snapshot(migration, migration_file, "post")
            if post_content is None:
                record_skip(skip_counts, reason or "post-missing")
                continue

            baseline, analysis_error = analyze_content(post_content, rules)
            if baseline is None:
                record_skip(skip_counts, analysis_error or "analysis-error")
                continue

            for fault_type in effective_fault_types:
                injected_content, expected, injection_error = inject_fault(post_content, rules, fault_type)
                if injected_content is None:
                    record_skip(skip_counts, f"{fault_type}:{injection_error or 'no-injection'}")
                    continue

                diagnostics, injected_analysis_error = analyze_content(injected_content, rules)
                if diagnostics is None:
                    record_skip(skip_counts, injected_analysis_error or "analysis-error")
                    continue

                score = score_expected(diagnostics, expected, baseline=baseline)
                total_tp += score["tp"]
                total_fp += score["fp"]
                total_fn += score["fn"]
                variants += 1

                per_file.append(
                    {
                        "migration_id": migration.id(),
                        "path": migration_file.path,
                        "fault_type": fault_type,
                        "expected": [item.to_dict() for item in expected],
                        "baseline_diagnostics": [diag.to_dict() for diag in baseline],
                        "diagnostics": [diag.to_dict() for diag in diagnostics],
                        "tp": score["tp"],
                        "fp": score["fp"],
                        "fn": score["fn"],
                        "matched": score["matched"],
                        "unexpected": score["unexpected"],
                        "missing": score["missing"],
                    }
                )

    summary = {
        "variants_evaluated": variants,
        "true_positives": total_tp,
        "false_positives": total_fp,
        "false_negatives": total_fn,
        "precision": safe_div(total_tp, total_tp + total_fp),
        "recall": safe_div(total_tp, total_tp + total_fn),
    }
    return make_track_report(
        benchmark_version,
        rule_pair,
        "fault-inject",
        summary,
        per_file,
        summarize_code_scores(per_file),
        skip_counts,
        cache,
    )


def evaluate_pre_leftover(
    migrations: Sequence,
    rules: dict,
    cache: SnapshotCache,
    benchmark_version: str,
) -> dict:
    per_file = []
    skip_counts: Dict[str, int] = {}
    total_tp = total_fp = total_fn = 0
    rule_pair = get_rule_pair(rules)

    for migration in migrations:
        for migration_file in migration.files:
            if not migration_file.path.lower().endswith(".py"):
                record_skip(skip_counts, "non-python")
                continue

            pre_content, reason = cache.get_snapshot(migration, migration_file, "pre")
            if pre_content is None:
                record_skip(skip_counts, reason or "pre-missing")
                continue

            diagnostics, analysis_error = analyze_content(pre_content, rules, enabled_groups=LEFTOVER_GROUPS)
            if diagnostics is None:
                record_skip(skip_counts, analysis_error or "analysis-error")
                continue

            expected = derive_leftover_oracle(pre_content, rules)
            score = score_expected(diagnostics, expected)
            total_tp += score["tp"]
            total_fp += score["fp"]
            total_fn += score["fn"]

            per_file.append(
                {
                    "migration_id": migration.id(),
                    "path": migration_file.path,
                    "expected": [item.to_dict() for item in expected],
                    "diagnostics": [diag.to_dict() for diag in diagnostics],
                    "tp": score["tp"],
                    "fp": score["fp"],
                    "fn": score["fn"],
                    "matched": score["matched"],
                    "unexpected": score["unexpected"],
                    "missing": score["missing"],
                }
            )

    files_evaluated = len(per_file)
    summary = {
        "files_evaluated": files_evaluated,
        "true_positives": total_tp,
        "false_positives": total_fp,
        "false_negatives": total_fn,
        "precision": safe_div(total_tp, total_tp + total_fp),
        "recall": safe_div(total_tp, total_tp + total_fn),
    }
    return make_track_report(
        benchmark_version,
        rule_pair,
        "pre-leftover",
        summary,
        per_file,
        summarize_code_scores(per_file),
        skip_counts,
        cache,
    )


def run_selected_tracks(
    modes: Sequence[str],
    migrations: Sequence,
    rules: dict,
    cache: SnapshotCache,
    benchmark_version: str,
    fault_types: Sequence[str],
    pair_fault_mode: str,
) -> list[dict]:
    reports = []
    for mode in modes:
        if mode == "clean-post":
            reports.append(evaluate_clean_post(migrations, rules, cache, benchmark_version))
        elif mode == "fault-inject":
            reports.append(evaluate_fault_injection(migrations, rules, cache, benchmark_version, fault_types, pair_fault_mode))
        elif mode == "pre-leftover":
            reports.append(evaluate_pre_leftover(migrations, rules, cache, benchmark_version))
    return reports


def summarize_track_group(track: str, reports: Sequence[dict]) -> dict:
    if track == "clean-post":
        files_evaluated = sum(report["summary"].get("files_evaluated", 0) for report in reports)
        clean_files = sum(report["summary"].get("clean_files", 0) for report in reports)
        files_with_diagnostics = sum(report["summary"].get("files_with_diagnostics", 0) for report in reports)
        false_positive_diagnostics = sum(report["summary"].get("false_positive_diagnostics", 0) for report in reports)
        return {
            "files_evaluated": files_evaluated,
            "clean_files": clean_files,
            "files_with_diagnostics": files_with_diagnostics,
            "false_positive_diagnostics": false_positive_diagnostics,
            "clean_file_rate": safe_div(clean_files, files_evaluated),
        }

    key_for_count = "variants_evaluated" if track == "fault-inject" else "files_evaluated"
    count_value = sum(report["summary"].get(key_for_count, 0) for report in reports)
    true_positives = sum(report["summary"].get("true_positives", 0) for report in reports)
    false_positives = sum(report["summary"].get("false_positives", 0) for report in reports)
    false_negatives = sum(report["summary"].get("false_negatives", 0) for report in reports)
    return {
        key_for_count: count_value,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": safe_div(true_positives, true_positives + false_positives),
        "recall": safe_div(true_positives, true_positives + false_negatives),
    }


def summarize_pair_reports(benchmark_version: str, pair_reports: Sequence[dict], cache: SnapshotCache) -> dict:
    track_names = []
    for pair_report in pair_reports:
        for track_report in pair_report["tracks"]:
            if track_report["track"] not in track_names:
                track_names.append(track_report["track"])

    summary = {}
    summary_by_code = {}
    for track in track_names:
        matching_reports = [
            track_report
            for pair_report in pair_reports
            for track_report in pair_report["tracks"]
            if track_report["track"] == track
        ]
        summary[track] = summarize_track_group(track, matching_reports)
        code_totals: Dict[str, Dict[str, int]] = {}
        for report in matching_reports:
            for code, code_summary in report.get("summary_by_code", {}).items():
                totals = code_totals.setdefault(code, {"true_positives": 0, "false_positives": 0, "false_negatives": 0})
                totals["true_positives"] += code_summary.get("true_positives", 0)
                totals["false_positives"] += code_summary.get("false_positives", 0)
                totals["false_negatives"] += code_summary.get("false_negatives", 0)
        if code_totals:
            summary_by_code[track] = {
                code: {
                    "true_positives": totals["true_positives"],
                    "false_positives": totals["false_positives"],
                    "false_negatives": totals["false_negatives"],
                    "precision": safe_div(totals["true_positives"], totals["true_positives"] + totals["false_positives"]),
                    "recall": safe_div(totals["true_positives"], totals["true_positives"] + totals["false_negatives"]),
                }
                for code, totals in sorted(code_totals.items())
            }

    evaluated_pairs = [
        {
            "source": pair_report["rule_pair"]["source"],
            "target": pair_report["rule_pair"]["target"],
            "migration_count": pair_report["migration_count"],
        }
        for pair_report in pair_reports
    ]

    return {
        "benchmark_version": benchmark_version,
        "evaluated_pairs": evaluated_pairs,
        "summary": summary,
        "summary_by_code": summary_by_code,
        "pair_reports": list(pair_reports),
        "cache_stats": cache.report(),
    }


def print_track_summary(track_report: dict) -> None:
    print(f"  Track: {track_report['track']}")
    for key, value in track_report["summary"].items():
        if isinstance(value, float):
            print(f"    {key}: {value:.2f}")
        else:
            print(f"    {key}: {value}")
    if track_report["skips"]:
        print("    skips:")
        for reason, count in sorted(track_report["skips"].items()):
            print(f"      {reason}: {count}")


def print_pair_summary(pair_report: dict) -> None:
    pair = pair_report["rule_pair"]
    print(f"\nPair: {pair['source']} -> {pair['target']} ({pair_report['migration_count']} migrations)")
    for track_report in pair_report["tracks"]:
        print_track_summary(track_report)


def _average_metric(values: Sequence[Optional[float]]) -> Optional[float]:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def print_aggregate_summary(final_report: dict) -> None:
    print("\nAggregate Track Metrics:")
    for track, aggregate in final_report.get("summary", {}).items():
        print(f"  Track: {track}")
        matching_reports = [
            track_report
            for pair_report in final_report.get("pair_reports", [])
            for track_report in pair_report.get("tracks", [])
            if track_report.get("track") == track
        ]
        if track == "clean-post":
            average_clean_rate = _average_metric(
                [track_report.get("summary", {}).get("clean_file_rate") for track_report in matching_reports]
            )
            print(f"    total_files_evaluated: {aggregate.get('files_evaluated', 0)}")
            print(f"    total_clean_files: {aggregate.get('clean_files', 0)}")
            if average_clean_rate is not None:
                print(f"    average_clean_file_rate_across_pairs: {average_clean_rate:.2f}")
            pooled = aggregate.get("clean_file_rate")
            if pooled is not None:
                print(f"    aggregate_clean_file_rate: {pooled:.2f}")
            continue

        average_precision = _average_metric(
            [track_report.get("summary", {}).get("precision") for track_report in matching_reports]
        )
        average_recall = _average_metric(
            [track_report.get("summary", {}).get("recall") for track_report in matching_reports]
        )
        count_key = "variants_evaluated" if track == "fault-inject" else "files_evaluated"
        print(f"    total_{count_key}: {aggregate.get(count_key, 0)}")
        print(f"    total_true_positives: {aggregate.get('true_positives', 0)}")
        print(f"    total_false_positives: {aggregate.get('false_positives', 0)}")
        print(f"    total_false_negatives: {aggregate.get('false_negatives', 0)}")
        if average_precision is not None:
            print(f"    average_precision_across_pairs: {average_precision:.2f}")
        if average_recall is not None:
            print(f"    average_recall_across_pairs: {average_recall:.2f}")
        pooled_precision = aggregate.get("precision")
        pooled_recall = aggregate.get("recall")
        if pooled_precision is not None:
            print(f"    aggregate_precision: {pooled_precision:.2f}")
        if pooled_recall is not None:
            print(f"    aggregate_recall: {pooled_recall:.2f}")


def write_output(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def _iter_summary_track_reports(report: dict) -> list[tuple[dict, dict]]:
    if "pair_reports" in report:
        return [
            (pair_report, track_report)
            for pair_report in report["pair_reports"]
            for track_report in pair_report.get("tracks", [])
        ]
    if "tracks" in report and "rule_pair" in report:
        pair_report = {
            "rule_pair": report["rule_pair"],
            "migration_count": report.get("migration_count", ""),
        }
        return [(pair_report, track_report) for track_report in report["tracks"]]
    return [({"rule_pair": report.get("rule_pair", {}), "migration_count": report.get("migration_count", "")}, report)]


def build_track_summary_rows(report: dict, track: str) -> list[dict]:
    rows = []
    for pair_report, track_report in _iter_summary_track_reports(report):
        if track_report.get("track") != track:
            continue
        summary = track_report.get("summary", {})
        rule_pair = pair_report.get("rule_pair", {})
        row = {
            "benchmark_version": track_report.get("benchmark_version", report.get("benchmark_version", "")),
            "source": rule_pair.get("source", ""),
            "target": rule_pair.get("target", ""),
            "migration_count": pair_report.get("migration_count", ""),
            "track": track_report.get("track", ""),
            "skip_count_total": sum(track_report.get("skips", {}).values()),
        }
        if track == "clean-post":
            row.update(
                {
                    "files_evaluated": summary.get("files_evaluated", ""),
                    "clean_files": summary.get("clean_files", ""),
                    "files_with_diagnostics": summary.get("files_with_diagnostics", ""),
                    "false_positive_diagnostics": summary.get("false_positive_diagnostics", ""),
                    "clean_file_rate": summary.get("clean_file_rate", ""),
                }
            )
        else:
            metrics = {
                "true_positives": summary.get("true_positives", ""),
                "false_positives": summary.get("false_positives", ""),
                "false_negatives": summary.get("false_negatives", ""),
                "precision": summary.get("precision", ""),
                "recall": summary.get("recall", ""),
            }
            if track == "fault-inject":
                metrics["variants_evaluated"] = summary.get("variants_evaluated", "")
            elif track == "pre-leftover":
                metrics["files_evaluated"] = summary.get("files_evaluated", "")
            row.update(metrics)
        rows.append(row)
    return rows


def build_by_code_rows(report: dict, track: Optional[str] = None) -> list[dict]:
    rows = []
    for pair_report, track_report in _iter_summary_track_reports(report):
        if track is not None and track_report.get("track") != track:
            continue
        rule_pair = pair_report.get("rule_pair", {})
        for code, summary in sorted(track_report.get("summary_by_code", {}).items()):
            rows.append(
                {
                    "benchmark_version": track_report.get("benchmark_version", report.get("benchmark_version", "")),
                    "source": rule_pair.get("source", ""),
                    "target": rule_pair.get("target", ""),
                    "migration_count": pair_report.get("migration_count", ""),
                    "track": track_report.get("track", ""),
                    "code": code,
                    "true_positives": summary.get("true_positives", ""),
                    "false_positives": summary.get("false_positives", ""),
                    "false_negatives": summary.get("false_negatives", ""),
                    "precision": summary.get("precision", ""),
                    "recall": summary.get("recall", ""),
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_track_summary_csv(path: Path, report: dict, track: str) -> None:
    rows = build_track_summary_rows(report, track)
    if track == "clean-post":
        fieldnames = [
            "benchmark_version",
            "source",
            "target",
            "migration_count",
            "track",
            "files_evaluated",
            "clean_files",
            "files_with_diagnostics",
            "false_positive_diagnostics",
            "clean_file_rate",
            "skip_count_total",
        ]
    elif track == "fault-inject":
        fieldnames = [
            "benchmark_version",
            "source",
            "target",
            "migration_count",
            "track",
            "variants_evaluated",
            "true_positives",
            "false_positives",
            "false_negatives",
            "precision",
            "recall",
            "skip_count_total",
        ]
    else:
        fieldnames = [
            "benchmark_version",
            "source",
            "target",
            "migration_count",
            "track",
            "files_evaluated",
            "true_positives",
            "false_positives",
            "false_negatives",
            "precision",
            "recall",
            "skip_count_total",
        ]
    _write_csv(path, rows, fieldnames)


def write_by_code_csv(path: Path, report: dict, track: Optional[str] = None) -> None:
    rows = build_by_code_rows(report, track=track)
    fieldnames = [
        "benchmark_version",
        "source",
        "target",
        "migration_count",
        "track",
        "code",
        "true_positives",
        "false_positives",
        "false_negatives",
        "precision",
        "recall",
    ]
    _write_csv(path, rows, fieldnames)


def parse_fault_types(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the migration checker against all direct benchmark-backed supported PyMigBench pairs."
    )
    parser.add_argument("pymigbench_path", type=Path, help="Path to the root of the PyMigBench repository.")
    parser.add_argument("--source", type=str, help="Optional source library filter.")
    parser.add_argument("--target", type=str, help="Optional target library filter.")
    parser.add_argument("--rule-file", type=Path, help="Optional YAML rule file override.")
    parser.add_argument(
        "--mode",
        choices=["clean-post", "fault-inject", "pre-leftover", "all"],
        default="all",
        help="Evaluation mode to run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Number of migrations to evaluate per feasible pair (0 means no limit).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "data" / "cache",
        help="Directory where immutable benchmark snapshots are cached.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path for a machine-readable JSON report.",
    )
    parser.add_argument(
        "--fault-types",
        type=str,
        default=",".join(DEFAULT_FAULT_TYPES),
        help="Comma-separated list of fault types for fault-inject mode.",
    )
    parser.add_argument(
        "--pair-faults",
        choices=PAIR_FAULT_MODE_CHOICES,
        default="default",
        help="Control whether pair-specific fault injectors run alongside generic injectors.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Refetch cached benchmark snapshots from GitHub before falling back to local cache.",
    )
    args = parser.parse_args()

    if not args.pymigbench_path.exists():
        print(f"Error: PyMigBench path not found at {args.pymigbench_path}")
        return

    if (args.source and not args.target) or (args.target and not args.source):
        print("Error: both --source and --target are required together.")
        return

    benchmark_version = get_benchmark_version(args.pymigbench_path)
    load_evaluator_env()
    token = get_github_token()
    if not token:
        print("Warning: GITHUB_TOKEN not found in the environment or .env files; initial cache population may be rate limited.")

    cache = SnapshotCache(args.cache_dir, token, refresh=args.refresh_cache)
    fault_types = parse_fault_types(args.fault_types)
    limit = args.limit if args.limit and args.limit > 0 else None

    try:
        pair_configs = discover_feasible_pair_configs(
            args.pymigbench_path,
            source=args.source,
            target=args.target,
            rule_file=args.rule_file,
            limit=limit,
        )
    except RuleResolutionError as exc:
        print(f"Error: {exc}")
        return

    if not pair_configs:
        print("Error: no direct benchmark-backed rule pairs found in the benchmark.")
        return

    modes = [args.mode] if args.mode != "all" else ["clean-post", "fault-inject", "pre-leftover"]
    pair_reports = []
    for config in pair_configs:
        track_reports = run_selected_tracks(
            modes,
            config["migrations"],
            config["rules"],
            cache,
            benchmark_version,
            fault_types,
            args.pair_faults,
        )
        pair_reports.append(
            {
                "rule_pair": config["pair"],
                "migration_count": len(config["migrations"]),
                "tracks": track_reports,
            }
        )

    final_report = summarize_pair_reports(benchmark_version, pair_reports, cache)

    print(f"Evaluated {len(pair_configs)} feasible pairs from PyMigBench {benchmark_version}.")
    for pair_report in pair_reports:
        print_pair_summary(pair_report)
    print_aggregate_summary(final_report)

    if args.output_json:
        output_json_path = normalize_output_json_path(args.output_json)
        write_output(output_json_path, final_report)
        print(f"\nWrote JSON report to {output_json_path}")
        for track in modes:
            summary_rows = build_track_summary_rows(final_report, track)
            if summary_rows:
                summary_csv_path = get_track_summary_csv_path(output_json_path, track)
                write_track_summary_csv(summary_csv_path, final_report, track)
                print(f"Wrote {track} CSV summary to {summary_csv_path}")
        by_code_rows = build_by_code_rows(final_report)
        if by_code_rows:
            by_code_csv_path = get_by_code_csv_path(output_json_path)
            write_by_code_csv(by_code_csv_path, final_report)
            print(f"Wrote combined per-code CSV to {by_code_csv_path}")


if __name__ == "__main__":
    main()

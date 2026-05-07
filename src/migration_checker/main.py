import argparse
import json
from pathlib import Path

from .collector import collect_facts
from .detector import (
    check_target_api_contract,
    check_target_usage_contract,
    find_downstream_return_use,
    find_duplicate_migration_usage,
    find_leftover_source_usage,
    find_mixed_migration,
)
from .rules import RuleResolutionError, get_project_root, load_rules, resolve_rule_file

CHECK_GROUPS = {
    "leftover": find_leftover_source_usage,
    "contract": check_target_api_contract,
    "usage": check_target_usage_contract,
    "mixed": find_mixed_migration,
    "downstream": find_downstream_return_use,
    "duplicate": find_duplicate_migration_usage,
}


def _normalize_enabled_groups(enabled_groups):
    """Normalize the optional CLI/API check-group filter."""
    if enabled_groups is None:
        return list(CHECK_GROUPS.keys())
    return [group for group in enabled_groups if group in CHECK_GROUPS]


def _enrich_diagnostic(diag, rules):
    serialized = dict(diag)
    serialized.pop("node", None)
    code = serialized.get("code", "unknown")
    serialized["severity"] = rules.get("severity", {}).get(code, "warning")
    return serialized


def analyze_source_code(source_code, rules, enabled_groups=None):
    """Analyze one Python source string and return serialized diagnostics."""
    facts = collect_facts(source_code)
    diagnostics = []
    for group in _normalize_enabled_groups(enabled_groups):
        diagnostics.extend(CHECK_GROUPS[group](facts, rules))
    return [
        _enrich_diagnostic(diag, rules)
        for diag in sorted(diagnostics, key=lambda item: (item.get("line", 0), item.get("code", "")))
    ]


def analyze_path(file_path, rules, enabled_groups=None):
    """Analyze one file or every Python file under a directory."""
    if file_path.is_dir():
        files_to_analyze = list(file_path.rglob("*.py"))
    else:
        files_to_analyze = [file_path]

    all_diagnostics = {}
    for file in files_to_analyze:
        with open(file, "r", encoding="utf-8") as f:
            source_code = f.read()
        diagnostics = analyze_source_code(source_code, rules, enabled_groups=enabled_groups)
        if diagnostics:
            all_diagnostics[str(file)] = diagnostics
    return all_diagnostics


def run():
    """CLI entry point for the checker."""
    parser = argparse.ArgumentParser(description="A static analysis tool for Python library migrations.")
    parser.add_argument("file_path", type=Path, help="Path to the Python file or directory to analyze.")
    parser.add_argument("--source", type=str, help="Source library name for rule selection.")
    parser.add_argument("--target", type=str, help="Target library name for rule selection.")
    parser.add_argument("--rule-file", type=Path, help="Path to a YAML rule file override.")
    parser.add_argument("--output-json", action="store_true", help="Output diagnostics as JSON.")
    args = parser.parse_args()

    try:
        rule_file = resolve_rule_file(
            source=args.source,
            target=args.target,
            rule_file=args.rule_file,
            project_root=get_project_root(),
        )
        rules = load_rules(rule_file)
    except RuleResolutionError as exc:
        print(f"Error: {exc}")
        return 2
    except Exception as exc:
        print(f"Error loading rule file: {exc}")
        return 2

    if not args.file_path.exists():
        print(f"Error: File or directory not found at {args.file_path}")
        return 2

    if not args.output_json:
        if args.file_path.is_dir():
            for file in args.file_path.rglob("*.py"):
                print(f"Analyzing {file}...")
        else:
            print(f"Analyzing {args.file_path}...")

    all_diagnostics = analyze_path(args.file_path, rules)

    if args.output_json:
        print(json.dumps(all_diagnostics, indent=2))
        return 0

    if all_diagnostics:
        print("\n--- Analysis Complete: Issues Found ---")
        for file, issues in all_diagnostics.items():
            print(f"\nIn {file}:")
            for issue in sorted(issues, key=lambda x: x["line"]):
                print(f"  - Line {issue['line']}: {issue['message']}")
    else:
        print("\n--- Analysis Complete: No Issues Found ---")
    return 0

if __name__ == "__main__":
    raise SystemExit(run())

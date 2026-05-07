from pathlib import Path
from typing import Optional

import yaml


class RuleResolutionError(ValueError):
    pass


SEVERITY_LEVELS = {"error", "warning", "info"}
USAGE_CONTEXT_VALUES = {"none", "with", "async_with"}
ACCESS_KIND_VALUES = {"attribute", "call"}
KNOWN_DIAGNOSTIC_CODES = {
    "leftover_source_import",
    "leftover_source_call",
    "renamed_keyword_argument",
    "forbidden_keyword_argument",
    "missing_required_keyword",
    "positional_argument_misuse",
    "missing_await",
    "missing_context_manager",
    "mixed_source_target_assignment",
    "renamed_attribute_access",
    "forbidden_attribute_access",
    "duplicate_migration_usage",
}
TOP_LEVEL_KEYS = {"pair", "libraries", "diagnostics", "rules"}
RULE_KEYS_BY_KIND = {
    "call": {"id", "kind", "source", "target", "contract", "usage", "return"},
    "import": {"id", "kind", "source", "target"},
    "access": {"id", "kind", "source", "target", "usage"},
}


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_rules_root(project_root: Optional[Path] = None) -> Path:
    base_dir = project_root or get_project_root()
    return base_dir / "rules"


def iter_rule_files(project_root: Optional[Path] = None, rules_root: Optional[Path] = None) -> list[Path]:
    root_dir = rules_root or get_rules_root(project_root)
    if not root_dir.exists():
        return []
    return sorted(root_dir.rglob("*.yml"))


def load_rules(rule_file: Path) -> dict:
    """Load, validate, and normalize one YAML rule file."""
    with rule_file.open("r", encoding="utf-8") as handle:
        raw_rules = yaml.safe_load(handle) or {}
    return _normalize_rules(raw_rules, rule_file)


def resolve_rule_file(
    *,
    source: Optional[str] = None,
    target: Optional[str] = None,
    rule_file: Optional[Path] = None,
    project_root: Optional[Path] = None,
    rules_root: Optional[Path] = None,
) -> Path:
    """Resolve a rule file from either selectors or a direct path override."""
    if rule_file and (source or target):
        raise RuleResolutionError("Choose either --rule-file or --source/--target, not both.")
    if (source and not target) or (target and not source):
        raise RuleResolutionError("Both --source and --target are required together.")
    if not rule_file and not (source and target):
        raise RuleResolutionError("Provide either --rule-file or both --source and --target.")

    if rule_file is not None:
        candidates = [rule_file]
        if project_root is not None and not rule_file.is_absolute():
            candidates.append(project_root / rule_file)
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        raise RuleResolutionError(f"Rule file not found at {rule_file}")

    root_dir = rules_root or get_rules_root(project_root)
    if not root_dir.exists():
        raise RuleResolutionError(f"Rules directory not found at {root_dir}")

    source_candidates = _selector_candidates(source)
    target_candidates = _selector_candidates(target)

    path_matches = []
    for src in source_candidates:
        for dst in target_candidates:
            path_matches.extend(root_dir.rglob(f"{src}_to_{dst}.yml"))

    unique_matches = _unique_paths(path_matches)
    if len(unique_matches) == 1:
        return unique_matches[0].resolve()
    if len(unique_matches) > 1:
        formatted = ", ".join(str(path) for path in unique_matches)
        raise RuleResolutionError(
            f"Multiple rule files match {source} -> {target}: {formatted}"
        )

    metadata_matches = []
    for candidate in root_dir.rglob("*.yml"):
        try:
            rules = load_rules(candidate)
        except (OSError, yaml.YAMLError, RuleResolutionError):
            continue
        pair = rules.get("pair", {})
        if pair.get("source") == source and pair.get("target") == target:
            metadata_matches.append(candidate)

    unique_matches = _unique_paths(metadata_matches)
    if len(unique_matches) == 1:
        return unique_matches[0].resolve()
    if len(unique_matches) > 1:
        formatted = ", ".join(str(path) for path in unique_matches)
        raise RuleResolutionError(
            f"Multiple rule files define pair {source} -> {target}: {formatted}"
        )

    raise RuleResolutionError(f"No rule file found for {source} -> {target}")


def _normalize_rules(raw_rules: dict, rule_file: Path) -> dict:
    """Normalize raw YAML into merged lookup structures used by the detectors."""
    _validate_mapping(raw_rules, "rule file", rule_file)
    legacy_keys = {"source_symbols", "target_symbols", "mappings", "suspicious_attributes"} & set(raw_rules)
    if legacy_keys:
        formatted = ", ".join(sorted(legacy_keys))
        raise RuleResolutionError(
            f"{rule_file}: legacy rule schema is not supported; found legacy keys: {formatted}"
        )

    unknown_top_level = set(raw_rules) - TOP_LEVEL_KEYS
    if unknown_top_level:
        formatted = ", ".join(sorted(unknown_top_level))
        raise RuleResolutionError(f"{rule_file}: unknown top-level keys: {formatted}")

    pair = _validate_pair(raw_rules.get("pair"), rule_file)
    libraries = _validate_libraries(raw_rules.get("libraries"), rule_file)
    severity = _validate_diagnostics(raw_rules.get("diagnostics"), rule_file)
    rule_entries = _validate_rule_entries(raw_rules.get("rules"), rule_file)

    call_rules = [entry for entry in rule_entries if entry["kind"] == "call"]
    import_rules = [entry for entry in rule_entries if entry["kind"] == "import"]
    access_rules = [entry for entry in rule_entries if entry["kind"] == "access"]
    call_rule_by_target = _merge_call_rules_by_target(call_rules, rule_file)
    access_rule_by_target = _merge_access_rules_by_target(access_rules, rule_file)

    call_rule_by_source = {entry["source"]["symbol"]: entry for entry in call_rules}
    access_rule_by_source = {entry["source"]["symbol"]: entry for entry in access_rules}

    return {
        "pair": pair,
        "libraries": libraries,
        "diagnostics": {"severity": severity},
        "rules": rule_entries,
        "severity": severity,
        "call_rules": call_rules,
        "import_rules": import_rules,
        "access_rules": access_rules,
        "source_roots": libraries["source_roots"],
        "target_roots": libraries["target_roots"],
        "source_call_symbols": [entry["source"]["symbol"] for entry in call_rules],
        "target_call_symbols": sorted({entry["target"]["symbol"] for entry in call_rules}),
        "source_access_symbols": [entry["source"]["symbol"] for entry in access_rules],
        "target_access_symbols": sorted({entry["target"]["symbol"] for entry in access_rules}),
        "call_rule_by_target": call_rule_by_target,
        "call_rule_by_source": call_rule_by_source,
        "access_rule_by_target": access_rule_by_target,
        "access_rule_by_source": access_rule_by_source,
        "return_rules_by_tag": _group_call_rules_by_return_tag(call_rule_by_target),
    }


def _validate_pair(pair: object, rule_file: Path) -> dict:
    _validate_mapping(pair, "pair", rule_file)
    source = _validate_string(pair.get("source"), f"{rule_file}: pair.source")
    target = _validate_string(pair.get("target"), f"{rule_file}: pair.target")
    return {"source": source, "target": target}


def _validate_libraries(libraries: object, rule_file: Path) -> dict:
    _validate_mapping(libraries, "libraries", rule_file)
    source_roots = _validate_string_list(libraries.get("source_roots"), f"{rule_file}: libraries.source_roots")
    target_roots = _validate_string_list(libraries.get("target_roots"), f"{rule_file}: libraries.target_roots")
    if not source_roots or not target_roots:
        raise RuleResolutionError(f"{rule_file}: libraries.source_roots and libraries.target_roots must be non-empty")
    return {
        "source_roots": source_roots,
        "target_roots": target_roots,
    }


def _validate_diagnostics(diagnostics: object, rule_file: Path) -> dict:
    _validate_mapping(diagnostics, "diagnostics", rule_file)
    severity = diagnostics.get("severity")
    _validate_mapping(severity, "diagnostics.severity", rule_file)
    normalized = {}
    for code, level in severity.items():
        if code not in KNOWN_DIAGNOSTIC_CODES:
            raise RuleResolutionError(f"{rule_file}: unknown diagnostic code '{code}' in diagnostics.severity")
        if level not in SEVERITY_LEVELS:
            raise RuleResolutionError(f"{rule_file}: invalid severity '{level}' for diagnostic '{code}'")
        normalized[code] = level
    return normalized


def _validate_rule_entries(entries: object, rule_file: Path) -> list[dict]:
    if not isinstance(entries, list) or not entries:
        raise RuleResolutionError(f"{rule_file}: rules must be a non-empty list")

    normalized = []
    seen_ids = set()
    for index, entry in enumerate(entries):
        _validate_mapping(entry, f"rules[{index}]", rule_file)
        kind = entry.get("kind")
        if kind not in RULE_KEYS_BY_KIND:
            raise RuleResolutionError(f"{rule_file}: rules[{index}] has unsupported kind '{kind}'")
        rule_id = _validate_string(entry.get("id"), f"{rule_file}: rules[{index}].id")
        if rule_id in seen_ids:
            raise RuleResolutionError(f"{rule_file}: duplicate rule id '{rule_id}'")
        seen_ids.add(rule_id)

        unknown_keys = set(entry) - RULE_KEYS_BY_KIND[kind]
        if unknown_keys:
            formatted = ", ".join(sorted(unknown_keys))
            raise RuleResolutionError(f"{rule_file}: rules[{index}] has unknown keys: {formatted}")

        if kind == "call":
            normalized.append(_validate_call_rule(entry, rule_file, index))
        elif kind == "import":
            normalized.append(_validate_import_rule(entry, rule_file, index))
        else:
            normalized.append(_validate_access_rule(entry, rule_file, index))
    return normalized


def _validate_call_rule(entry: dict, rule_file: Path, index: int) -> dict:
    source = entry.get("source")
    target = entry.get("target")
    contract = entry.get("contract")
    usage = entry.get("usage")
    return_value = entry.get("return")

    _validate_mapping(source, f"rules[{index}].source", rule_file)
    _validate_mapping(target, f"rules[{index}].target", rule_file)
    _validate_mapping(contract, f"rules[{index}].contract", rule_file)
    _validate_mapping(usage, f"rules[{index}].usage", rule_file)
    _validate_mapping(return_value, f"rules[{index}].return", rule_file)

    context_manager = usage.get("context_manager")
    if context_manager not in USAGE_CONTEXT_VALUES:
        raise RuleResolutionError(
            f"{rule_file}: rules[{index}].usage.context_manager must be one of {sorted(USAGE_CONTEXT_VALUES)}"
        )
    await_required = usage.get("await_required", False)
    if not isinstance(await_required, bool):
        raise RuleResolutionError(f"{rule_file}: rules[{index}].usage.await_required must be a boolean")
    require_keywords = contract.get("require_keywords", False)
    if not isinstance(require_keywords, bool):
        raise RuleResolutionError(f"{rule_file}: rules[{index}].contract.require_keywords must be a boolean")

    return {
        "id": entry["id"],
        "kind": "call",
        "source": {
            "symbol": _validate_string(source.get("symbol"), f"{rule_file}: rules[{index}].source.symbol")
        },
        "target": {
            "symbol": _validate_string(target.get("symbol"), f"{rule_file}: rules[{index}].target.symbol")
        },
        "contract": {
            "renamed_keywords": _validate_string_mapping(
                contract.get("renamed_keywords", {}),
                f"{rule_file}: rules[{index}].contract.renamed_keywords",
            ),
            "forbidden_keywords": _validate_string_list(
                contract.get("forbidden_keywords", []),
                f"{rule_file}: rules[{index}].contract.forbidden_keywords",
            ),
            "required_keywords": _validate_string_list(
                contract.get("required_keywords", []),
                f"{rule_file}: rules[{index}].contract.required_keywords",
            ),
            "require_keywords": require_keywords,
        },
        "usage": {
            "await_required": await_required,
            "context_manager": context_manager,
        },
        "return": {
            "tag": _validate_string(return_value.get("tag"), f"{rule_file}: rules[{index}].return.tag"),
            "renamed_attributes": _validate_string_mapping(
                return_value.get("renamed_attributes", {}),
                f"{rule_file}: rules[{index}].return.renamed_attributes",
            ),
            "forbidden_attributes": _validate_string_list(
                return_value.get("forbidden_attributes", []),
                f"{rule_file}: rules[{index}].return.forbidden_attributes",
            ),
            "await_required_methods": _validate_string_list(
                return_value.get("await_required_methods", []),
                f"{rule_file}: rules[{index}].return.await_required_methods",
            ),
        },
    }


def _validate_import_rule(entry: dict, rule_file: Path, index: int) -> dict:
    source = entry.get("source")
    target = entry.get("target")
    _validate_mapping(source, f"rules[{index}].source", rule_file)
    _validate_mapping(target, f"rules[{index}].target", rule_file)
    return {
        "id": entry["id"],
        "kind": "import",
        "source": {
            "module": _validate_string(source.get("module"), f"{rule_file}: rules[{index}].source.module"),
            "name": _validate_optional_string(source.get("name"), f"{rule_file}: rules[{index}].source.name"),
        },
        "target": {
            "module": _validate_string(target.get("module"), f"{rule_file}: rules[{index}].target.module"),
            "name": _validate_optional_string(target.get("name"), f"{rule_file}: rules[{index}].target.name"),
        },
    }


def _validate_access_rule(entry: dict, rule_file: Path, index: int) -> dict:
    source = entry.get("source")
    target = entry.get("target")
    usage = entry.get("usage")

    _validate_mapping(source, f"rules[{index}].source", rule_file)
    _validate_mapping(target, f"rules[{index}].target", rule_file)
    _validate_mapping(usage, f"rules[{index}].usage", rule_file)

    access_kind = usage.get("access_kind")
    if access_kind not in ACCESS_KIND_VALUES:
        raise RuleResolutionError(
            f"{rule_file}: rules[{index}].usage.access_kind must be one of {sorted(ACCESS_KIND_VALUES)}"
        )
    await_required = usage.get("await_required", False)
    if not isinstance(await_required, bool):
        raise RuleResolutionError(f"{rule_file}: rules[{index}].usage.await_required must be a boolean")

    return {
        "id": entry["id"],
        "kind": "access",
        "source": {
            "symbol": _validate_string(source.get("symbol"), f"{rule_file}: rules[{index}].source.symbol")
        },
        "target": {
            "symbol": _validate_string(target.get("symbol"), f"{rule_file}: rules[{index}].target.symbol")
        },
        "usage": {
            "await_required": await_required,
            "access_kind": access_kind,
        },
    }


def _merge_call_rules_by_target(call_rules: list[dict], rule_file: Path) -> dict:
    merged = {}
    for entry in call_rules:
        target_symbol = entry["target"]["symbol"]
        if target_symbol not in merged:
            merged[target_symbol] = {
                "id": entry["id"],
                "kind": "call",
                "source_symbols": [entry["source"]["symbol"]],
                "target": {"symbol": target_symbol},
                "contract": {
                    "renamed_keywords": dict(entry["contract"]["renamed_keywords"]),
                    "forbidden_keywords": list(entry["contract"]["forbidden_keywords"]),
                    "required_keywords": list(entry["contract"]["required_keywords"]),
                    "require_keywords": entry["contract"]["require_keywords"],
                },
                "usage": {
                    "await_required": entry["usage"]["await_required"],
                    "context_manager": entry["usage"]["context_manager"],
                },
                "return": {
                    "tag": entry["return"]["tag"],
                    "renamed_attributes": dict(entry["return"]["renamed_attributes"]),
                    "forbidden_attributes": list(entry["return"]["forbidden_attributes"]),
                    "await_required_methods": list(entry["return"]["await_required_methods"]),
                },
            }
            continue

        current = merged[target_symbol]
        current["source_symbols"].append(entry["source"]["symbol"])
        _merge_bool_field(
            current["usage"],
            entry["usage"],
            "await_required",
        )
        _merge_exact_field(
            current["usage"],
            entry["usage"],
            "context_manager",
            rule_file,
            target_symbol,
        )
        _merge_exact_field(
            current["return"],
            entry["return"],
            "tag",
            rule_file,
            target_symbol,
        )
        _merge_string_mapping_field(
            current["contract"]["renamed_keywords"],
            entry["contract"]["renamed_keywords"],
            rule_file,
            target_symbol,
            "renamed keyword",
        )
        _merge_string_mapping_field(
            current["return"]["renamed_attributes"],
            entry["return"]["renamed_attributes"],
            rule_file,
            target_symbol,
            "renamed attribute",
        )
        current["contract"]["forbidden_keywords"] = sorted(
            set(current["contract"]["forbidden_keywords"]) | set(entry["contract"]["forbidden_keywords"])
        )
        current["contract"]["required_keywords"] = sorted(
            set(current["contract"]["required_keywords"]) | set(entry["contract"]["required_keywords"])
        )
        current["contract"]["require_keywords"] = (
            current["contract"]["require_keywords"] or entry["contract"]["require_keywords"]
        )
        current["return"]["forbidden_attributes"] = sorted(
            set(current["return"]["forbidden_attributes"]) | set(entry["return"]["forbidden_attributes"])
        )
        current["return"]["await_required_methods"] = sorted(
            set(current["return"]["await_required_methods"]) | set(entry["return"]["await_required_methods"])
        )

    return merged


def _merge_access_rules_by_target(access_rules: list[dict], rule_file: Path) -> dict:
    merged = {}
    for entry in access_rules:
        target_symbol = entry["target"]["symbol"]
        if target_symbol not in merged:
            merged[target_symbol] = {
                "id": entry["id"],
                "kind": "access",
                "source_symbols": [entry["source"]["symbol"]],
                "target": {"symbol": target_symbol},
                "usage": {
                    "await_required": entry["usage"]["await_required"],
                    "access_kind": entry["usage"]["access_kind"],
                },
            }
            continue

        current = merged[target_symbol]
        current["source_symbols"].append(entry["source"]["symbol"])
        _merge_bool_field(current["usage"], entry["usage"], "await_required")
        _merge_exact_field(current["usage"], entry["usage"], "access_kind", rule_file, target_symbol)
    return merged


def _group_call_rules_by_return_tag(call_rule_by_target: dict) -> dict:
    grouped = {}
    for target_symbol, rule in call_rule_by_target.items():
        tag = rule["return"]["tag"]
        grouped.setdefault(tag, []).append((target_symbol, rule))
    return grouped


def _merge_bool_field(current: dict, incoming: dict, key: str) -> None:
    current[key] = current[key] or incoming[key]


def _merge_exact_field(current: dict, incoming: dict, key: str, rule_file: Path, target_symbol: str) -> None:
    if current[key] != incoming[key]:
        raise RuleResolutionError(
            f"{rule_file}: conflicting {key} values for target symbol '{target_symbol}'"
        )


def _merge_string_mapping_field(
    current: dict,
    incoming: dict,
    rule_file: Path,
    target_symbol: str,
    label: str,
) -> None:
    for key, value in incoming.items():
        existing = current.get(key)
        if existing is not None and existing != value:
            raise RuleResolutionError(
                f"{rule_file}: conflicting {label} mapping for target symbol '{target_symbol}' and key '{key}'"
            )
        current[key] = value


def _validate_mapping(value: object, label: str, rule_file: Path) -> None:
    if not isinstance(value, dict):
        raise RuleResolutionError(f"{rule_file}: {label} must be a mapping")


def _validate_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuleResolutionError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_optional_string(value: object, label: str) -> Optional[str]:
    if value is None:
        return None
    return _validate_string(value, label)


def _validate_string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise RuleResolutionError(f"{label} must be a list of strings")
    result = []
    for index, item in enumerate(value):
        result.append(_validate_string(item, f"{label}[{index}]"))
    return result


def _validate_string_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise RuleResolutionError(f"{label} must be a mapping of strings to strings")
    normalized = {}
    for key, mapped_value in value.items():
        normalized[_validate_string(key, f"{label} key")] = _validate_string(
            mapped_value,
            f"{label}[{key}]",
        )
    return normalized


def _selector_candidates(name: str) -> list[str]:
    values = []
    stripped = name.strip().lower()
    if stripped:
        values.append(stripped.split(".")[0])
        values.append(stripped.replace(".", "_"))
        values.append(stripped)
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return sorted(unique)

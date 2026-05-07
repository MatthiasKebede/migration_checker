def _make_diagnostic(line, message, code, node=None):
    diagnostic = {
        "line": line,
        "message": message,
        "code": code,
    }
    if node is not None:
        diagnostic["node"] = node
    return diagnostic


def _matches_source_import(import_name, rules):
    if any(import_name == module or import_name.startswith(f"{module}.") for module in rules["source_roots"]):
        return True

    for rule in rules["import_rules"]:
        source = rule["source"]
        expected = source["module"]
        if source.get("name"):
            expected = f"{expected}.{source['name']}"
        if import_name == expected:
            return True
    return False


def _group_bindings_by_scope(facts):
    grouped = {}
    for binding in sorted(facts.bindings, key=lambda item: (item["line"], item["end_line"], item["variable_name"])):
        grouped.setdefault(binding["scope"], []).append(binding)
    return grouped


def _lookup_provenance(events_by_scope, scope, variable_name, line):
    current_scope = scope
    visited = set()
    while current_scope is not None and current_scope not in visited:
        visited.add(current_scope)
        matches = [
            event
            for event in events_by_scope.get(current_scope, [])
            if event["variable_name"] == variable_name and event["line"] <= line
        ]
        if matches:
            return matches[-1]["provenance"]
        current_scope = current_scope.parent
    return None


def _provenance_target_symbol(qualified_name, rules):
    if not qualified_name:
        return None
    if qualified_name in rules["call_rule_by_target"]:
        return qualified_name
    if any(
        qualified_name == root or qualified_name.startswith(f"{root}.")
        for root in rules["target_roots"]
    ):
        return qualified_name
    return None


def _resolve_bound_call_target_symbol(binding, provenance_by_scope, rules):
    direct_symbol = _provenance_target_symbol(binding.get("qualified_name"), rules)
    if direct_symbol:
        return direct_symbol

    receiver_name = binding.get("receiver_name")
    method_name = binding.get("method_name")
    if not receiver_name or not method_name:
        return None

    receiver_provenance = _lookup_provenance(
        provenance_by_scope,
        binding["scope"],
        receiver_name,
        binding["line"],
    )
    if receiver_provenance is None:
        return None

    candidate_symbol = f"{receiver_provenance['target_symbol']}.{method_name}"
    if candidate_symbol in rules["call_rule_by_target"]:
        return candidate_symbol
    return None


def _resolve_call_rule(call, provenance_by_scope, rules):
    q_name = call["qualified_name"]
    if q_name in rules["call_rule_by_target"]:
        return q_name, rules["call_rule_by_target"][q_name]

    receiver_name = call.get("receiver_name")
    method_name = call.get("method_name")
    if not receiver_name or not method_name:
        return None, None

    receiver_provenance = _lookup_provenance(
        provenance_by_scope,
        call["scope"],
        receiver_name,
        call["line"],
    )
    if receiver_provenance is None:
        return None, None

    candidate_symbol = f"{receiver_provenance['target_symbol']}.{method_name}"
    rule = rules["call_rule_by_target"].get(candidate_symbol)
    if rule is None:
        return None, None
    return candidate_symbol, rule


def _build_provenance_index(facts, rules):
    """Track simple local provenance for names bound from target-side APIs."""
    bindings_by_scope = _group_bindings_by_scope(facts)
    provenance_by_scope = {}

    for scope, bindings in bindings_by_scope.items():
        for binding in bindings:
            provenance = None
            if binding["origin_kind"] == "call":
                target_symbol = _resolve_bound_call_target_symbol(binding, provenance_by_scope, rules)
                if target_symbol in rules["call_rule_by_target"]:
                    rule = rules["call_rule_by_target"][target_symbol]
                    provenance = {
                        "return_tag": rule["return"]["tag"],
                        "target_symbol": target_symbol,
                        "rule": rule,
                    }
                elif target_symbol is not None:
                    provenance = {
                        "return_tag": None,
                        "target_symbol": target_symbol,
                        "rule": None,
                    }
            elif binding["origin_kind"] == "access" and binding.get("qualified_name") in rules["access_rule_by_target"]:
                rule = rules["access_rule_by_target"][binding["qualified_name"]]
                provenance = {
                    "return_tag": None,
                    "target_symbol": binding["qualified_name"],
                    "rule": rule,
                }
            elif binding["origin_kind"] == "name" and binding.get("referenced_name"):
                provenance = _lookup_provenance(
                    provenance_by_scope,
                    scope,
                    binding["referenced_name"],
                    binding["line"],
                )

            if provenance is None:
                continue
            provenance_by_scope.setdefault(scope, []).append(
                {
                    "variable_name": binding["variable_name"],
                    "line": binding["line"],
                    "provenance": provenance,
                }
            )

    return provenance_by_scope


def find_leftover_source_usage(facts, rules):
    """Report leftover source imports, calls, and explicit source accesses."""
    diagnostics = []
    source_call_symbols = set(rules["source_call_symbols"])
    source_access_symbols = set(rules["source_access_symbols"])

    for imp in facts.imports:
        if _matches_source_import(imp["name"], rules):
            diagnostics.append(
                _make_diagnostic(
                    imp["line"],
                    f"Leftover import of source library: '{imp['name']}'",
                    "leftover_source_import",
                )
            )

    for call in facts.calls:
        if call["qualified_name"] in source_call_symbols:
            diagnostics.append(
                _make_diagnostic(
                    call["line"],
                    f"Leftover call to source library function: '{call['qualified_name']}'",
                    "leftover_source_call",
                    node=call.get("node"),
                )
            )

    for access in facts.accesses:
        if access["qualified_name"] in source_access_symbols:
            diagnostics.append(
                _make_diagnostic(
                    access["line"],
                    f"Leftover access to source library API: '{access['qualified_name']}'",
                    # Access leftovers currently share the historical call-level
                    # diagnostic code to preserve rule/test compatibility.
                    "leftover_source_call",
                    node=access.get("node"),
                )
            )

    return diagnostics


def check_target_api_contract(facts, rules):
    """Check target-call keyword and positional argument contracts."""
    diagnostics = []
    provenance_by_scope = _build_provenance_index(facts, rules)

    for call in facts.calls:
        q_name, rule = _resolve_call_rule(call, provenance_by_scope, rules)
        if rule is None:
            continue

        contract = rule["contract"]
        node = call["node"]
        keyword_names = set(call["keyword_names"])

        if contract["require_keywords"] and call["positional_arg_count"] > 0:
            diagnostics.append(
                _make_diagnostic(
                    call["line"],
                    f"Call to '{q_name}' should use keyword arguments instead of positional arguments.",
                    "positional_argument_misuse",
                    node=node,
                )
            )
        else:
            for required_name in contract["required_keywords"]:
                if required_name not in keyword_names:
                    diagnostics.append(
                        _make_diagnostic(
                            call["line"],
                            f"Call to '{q_name}' is missing required keyword argument '{required_name}'.",
                            "missing_required_keyword",
                            node=node,
                        )
                    )

        for arg in node.args:
            if hasattr(arg, "keyword") and arg.keyword and arg.keyword.value in contract["renamed_keywords"]:
                new_name = contract["renamed_keywords"][arg.keyword.value]
                diagnostics.append(
                    _make_diagnostic(
                        call["line"],
                        f"Argument '{arg.keyword.value}' was renamed to '{new_name}' in '{q_name}'.",
                        "renamed_keyword_argument",
                        node=node,
                    )
                )

        for arg in node.args:
            if hasattr(arg, "keyword") and arg.keyword and arg.keyword.value in contract["forbidden_keywords"]:
                diagnostics.append(
                    _make_diagnostic(
                        call["line"],
                        f"Argument '{arg.keyword.value}' is forbidden in '{q_name}'.",
                        "forbidden_keyword_argument",
                        node=node,
                    )
                )

    return diagnostics


def check_target_usage_contract(facts, rules):
    """Check await/context requirements on target APIs and returned values."""
    diagnostics = []
    provenance_by_scope = _build_provenance_index(facts, rules)

    for call in facts.calls:
        node = call["node"]
        resolved_q_name, resolved_rule = _resolve_call_rule(call, provenance_by_scope, rules)

        if resolved_rule is not None:
            usage = resolved_rule["usage"]
            if usage["await_required"] and not call["awaited"]:
                diagnostics.append(
                    _make_diagnostic(
                        call["line"],
                        f"Call to '{resolved_q_name}' should be awaited.",
                        "missing_await",
                        node=node,
                    )
                )
            if usage["context_manager"] != "none" and call["context_usage"] != usage["context_manager"]:
                diagnostics.append(
                    _make_diagnostic(
                        call["line"],
                        f"Call to '{resolved_q_name}' should be used in a '{usage['context_manager']}' context manager.",
                        "missing_context_manager",
                        node=node,
                    )
                )

        q_name = call["qualified_name"]
        if q_name in rules["access_rule_by_target"]:
            rule = rules["access_rule_by_target"][q_name]
            if rule["usage"]["access_kind"] == "call" and rule["usage"]["await_required"] and not call["awaited"]:
                diagnostics.append(
                    _make_diagnostic(
                        call["line"],
                        f"Call to '{q_name}' should be awaited.",
                        "missing_await",
                        node=node,
                    )
                )

        receiver_name = call.get("receiver_name")
        method_name = call.get("method_name")
        if not receiver_name or not method_name:
            continue

        provenance = _lookup_provenance(provenance_by_scope, call["scope"], receiver_name, call["line"])
        if provenance is None or provenance.get("rule") is None:
            continue

        return_rule = provenance["rule"]["return"]
        if method_name in return_rule["await_required_methods"] and not call["awaited"]:
            diagnostics.append(
                _make_diagnostic(
                    call["line"],
                    f"Method '{receiver_name}.{method_name}()' should be awaited.",
                    "missing_await",
                    node=node,
                )
            )

    return diagnostics


def find_mixed_migration(facts, rules):
    """Detect names assigned from both source and target APIs in one scope."""
    diagnostics = []
    source_symbols = set(rules["source_call_symbols"])
    target_symbols = set(rules["target_call_symbols"])
    assignments_by_scope = {}

    for assign in facts.assignments:
        scope = assign["scope"]
        var_name = assign["variable_name"]
        assignments_by_scope.setdefault(scope, {}).setdefault(var_name, []).append(assign)

    for assignments_in_scope in assignments_by_scope.values():
        for var_name, assignments in assignments_in_scope.items():
            if len(assignments) <= 1:
                continue

            from_source = any(assign["qualified_call_name"] in source_symbols for assign in assignments)
            from_target = any(assign["qualified_call_name"] in target_symbols for assign in assignments)
            if from_source and from_target:
                last_line = assignments[-1]["line"]
                diagnostics.append(
                    _make_diagnostic(
                        last_line,
                        f"Variable '{var_name}' is assigned values from both source and target libraries in the same scope.",
                        "mixed_source_target_assignment",
                    )
                )

    return diagnostics


def find_downstream_return_use(facts, rules):
    """Check downstream attribute usage on values returned from target APIs."""
    diagnostics = []
    provenance_by_scope = _build_provenance_index(facts, rules)

    for access in facts.accesses:
        base_name = access.get("base_name")
        if not base_name:
            continue

        provenance = _lookup_provenance(provenance_by_scope, access["scope"], base_name, access["line"])
        if provenance is None:
            continue

        return_rule = provenance["rule"]["return"]
        attr_name = access["attribute_name"]
        if attr_name in return_rule["renamed_attributes"]:
            diagnostics.append(
                _make_diagnostic(
                    access["line"],
                    f"Attribute '{attr_name}' was renamed to '{return_rule['renamed_attributes'][attr_name]}' on values returned from '{provenance['target_symbol']}'.",
                    "renamed_attribute_access",
                    node=access.get("node"),
                )
            )
        elif attr_name in return_rule["forbidden_attributes"]:
            diagnostics.append(
                _make_diagnostic(
                    access["line"],
                    f"Attribute '{attr_name}' should not be accessed on values returned from '{provenance['target_symbol']}'.",
                    "forbidden_attribute_access",
                    node=access.get("node"),
                )
            )

    return diagnostics


def find_duplicate_migration_usage(facts, rules):
    """Flag nearby source and target API usage that suggests append-not-replace."""
    diagnostics = []
    target_to_sources = {
        target_symbol: set(rule["source_symbols"])
        for target_symbol, rule in rules["call_rule_by_target"].items()
    }
    calls_by_scope = {}
    seen = set()

    for call in facts.calls:
        calls_by_scope.setdefault(call["scope"], []).append(call)

    for calls in calls_by_scope.values():
        sorted_calls = sorted(calls, key=lambda item: (item["line"], item["qualified_name"]))
        for target_call in sorted_calls:
            if target_call["qualified_name"] not in target_to_sources:
                continue
            source_symbols = target_to_sources[target_call["qualified_name"]]
            for source_call in sorted_calls:
                if source_call["line"] > target_call["line"]:
                    break
                if source_call["qualified_name"] not in source_symbols:
                    continue
                if target_call["line"] - source_call["line"] > 3:
                    continue
                key = (source_call["line"], target_call["line"], target_call["qualified_name"])
                if key in seen:
                    continue
                seen.add(key)
                diagnostics.append(
                    _make_diagnostic(
                        target_call["line"],
                        f"Source API '{source_call['qualified_name']}' and target API '{target_call['qualified_name']}' are used close together in the same scope; the migration may have been appended instead of replaced.",
                        "duplicate_migration_usage",
                        node=target_call.get("node"),
                    )
                )
    return diagnostics

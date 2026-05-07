from dataclasses import dataclass, field

import libcst as cst
from libcst.metadata import (
    MetadataWrapper,
    ParentNodeProvider,
    PositionProvider,
    QualifiedNameProvider,
    ScopeProvider,
)


def _name_to_str(node):
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        left = _name_to_str(node.value)
        if left is None:
            return None
        return f"{left}.{node.attr.value}"
    return None


def _root_name(node):
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        return _root_name(node.value)
    return None


@dataclass
class FactGraph:
    """Local structural facts collected from one source file."""

    imports: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    accesses: list[dict] = field(default_factory=list)
    symbols: list[dict] = field(default_factory=list)
    assignments: list[dict] = field(default_factory=list)
    attribute_accesses: list[dict] = field(default_factory=list)


class FactCollector(cst.CSTVisitor):
    """Collect the facts that drive the checker's local static analysis."""

    METADATA_DEPENDENCIES = (
        QualifiedNameProvider,
        PositionProvider,
        ScopeProvider,
        ParentNodeProvider,
    )

    def __init__(self):
        self.facts = FactGraph()

    def visit_Import(self, node: cst.Import):
        pos = self.get_metadata(PositionProvider, node)
        scope = self.get_metadata(ScopeProvider, node, default=None)
        for alias in node.names:
            name = _name_to_str(alias.name)
            if not name:
                continue
            self.facts.imports.append(
                {
                    "name": name,
                    "alias": alias.asname.name.value if alias.asname else None,
                    "line": pos.start.line,
                    "scope": scope,
                }
            )

    def visit_ImportFrom(self, node: cst.ImportFrom):
        pos = self.get_metadata(PositionProvider, node)
        scope = self.get_metadata(ScopeProvider, node, default=None)
        module = _name_to_str(node.module) if node.module else None
        if not module or isinstance(node.names, cst.ImportStar):
            return
        for name in node.names:
            self.facts.imports.append(
                {
                    "name": f"{module}.{name.name.value}",
                    "alias": name.asname.name.value if name.asname else None,
                    "line": pos.start.line,
                    "scope": scope,
                }
            )

    def visit_Name(self, node: cst.Name):
        self._record_symbol(node)

    def visit_Attribute(self, node: cst.Attribute):
        self._record_symbol(node)
        if self._is_call_target_attribute(node):
            return

        pos = self.get_metadata(PositionProvider, node)
        scope = self.get_metadata(ScopeProvider, node, default=None)
        q_name = self._first_qualified_name(node)
        self.facts.accesses.append(
            {
                "qualified_name": q_name,
                "base_name": _root_name(node.value),
                "base_text": _name_to_str(node.value),
                "attribute_name": node.attr.value,
                "access_kind": "attribute",
                "line": pos.start.line,
                "scope": scope,
                "node": node,
            }
        )
        if isinstance(node.value, cst.Name):
            self.facts.attribute_accesses.append(
                {
                    "variable_name": node.value.value,
                    "attribute_name": node.attr.value,
                    "line": pos.start.line,
                    "scope": scope,
                }
            )

    def visit_Call(self, node: cst.Call):
        pos = self.get_metadata(PositionProvider, node)
        scope = self.get_metadata(ScopeProvider, node, default=None)
        q_names = self.get_metadata(QualifiedNameProvider, node.func, default=())
        keyword_names = [arg.keyword.value for arg in node.args if arg.keyword]
        positional_arg_count = sum(1 for arg in node.args if arg.keyword is None)
        receiver_name = None
        method_name = None
        if isinstance(node.func, cst.Attribute):
            receiver_name = _root_name(node.func.value)
            method_name = node.func.attr.value

        for q_name in q_names:
            self.facts.calls.append(
                {
                    "qualified_name": q_name.name,
                    "line": pos.start.line,
                    "end_line": pos.end.line,
                    "scope": scope,
                    "node": node,
                    "awaited": self._is_awaited(node),
                    "in_decorator": self._is_in_decorator(node),
                    "context_usage": self._context_usage(node),
                    "receiver_name": receiver_name,
                    "method_name": method_name,
                    "positional_arg_count": positional_arg_count,
                    "keyword_names": keyword_names,
                }
            )

    def visit_Assign(self, node: cst.Assign):
        for target in node.targets:
            self._record_binding(target.target, node.value, node)

    def visit_AnnAssign(self, node: cst.AnnAssign):
        self._record_binding(node.target, node.value, node)

    def visit_With(self, node: cst.With):
        for item in node.items:
            self._record_with_binding(item, node)

    def _record_symbol(self, node):
        pos = self.get_metadata(PositionProvider, node)
        scope = self.get_metadata(ScopeProvider, node, default=None)
        q_names = self.get_metadata(QualifiedNameProvider, node, default=())
        for q_name in q_names:
            self.facts.symbols.append(
                {
                    "qualified_name": q_name.name,
                    "line": pos.start.line,
                    "scope": scope,
                    "node_type": type(node).__name__,
                }
            )

    def _record_binding(self, target, value, owner_node):
        if value is None or not isinstance(target, cst.Name):
            return

        source_info = self._extract_binding_source(value)
        if source_info is None:
            return

        pos = self.get_metadata(PositionProvider, owner_node)
        scope = self.get_metadata(ScopeProvider, owner_node, default=None)
        binding = {
            "variable_name": target.value,
            "origin_kind": source_info["origin_kind"],
            "qualified_name": source_info.get("qualified_name"),
            "referenced_name": source_info.get("referenced_name"),
            "receiver_name": source_info.get("receiver_name"),
            "method_name": source_info.get("method_name"),
            "line": pos.start.line,
            "end_line": pos.end.line,
            "scope": scope,
            "awaited": source_info.get("awaited", False),
            "context_usage": source_info.get("context_usage", "none"),
        }
        self.facts.bindings.append(binding)
        if source_info["origin_kind"] == "call":
            self.facts.assignments.append(
                {
                    "variable_name": target.value,
                    "qualified_call_name": source_info.get("qualified_name"),
                    "line": pos.start.line,
                    "end_line": pos.end.line,
                    "scope": scope,
                }
            )

    def _record_with_binding(self, item: cst.WithItem, owner_node):
        if item.asname is None or not isinstance(item.asname.name, cst.Name):
            return
        source_info = self._extract_binding_source(item.item)
        if source_info is None:
            return
        target_name = item.asname.name
        pos = self.get_metadata(PositionProvider, owner_node)
        scope = self.get_metadata(ScopeProvider, owner_node, default=None)
        binding = {
            "variable_name": target_name.value,
            "origin_kind": source_info["origin_kind"],
            "qualified_name": source_info.get("qualified_name"),
            "referenced_name": source_info.get("referenced_name"),
            "receiver_name": source_info.get("receiver_name"),
            "method_name": source_info.get("method_name"),
            "line": pos.start.line,
            "end_line": pos.end.line,
            "scope": scope,
            "awaited": source_info.get("awaited", False),
            "context_usage": source_info.get("context_usage", "none"),
        }
        self.facts.bindings.append(binding)
        if source_info["origin_kind"] == "call":
            self.facts.assignments.append(
                {
                    "variable_name": target_name.value,
                    "qualified_call_name": source_info.get("qualified_name"),
                    "line": pos.start.line,
                    "end_line": pos.end.line,
                    "scope": scope,
                }
            )

    def _extract_binding_source(self, node):
        awaited = False
        context_usage = "none"
        expr = node
        if isinstance(expr, cst.Await):
            awaited = True
            expr = expr.expression

        if isinstance(expr, cst.Call):
            receiver_name = None
            method_name = None
            if isinstance(expr.func, cst.Attribute):
                receiver_name = _root_name(expr.func.value)
                method_name = expr.func.attr.value
            context_usage = self._context_usage(expr)
            return {
                "origin_kind": "call",
                "qualified_name": self._first_qualified_name(expr.func),
                "awaited": awaited or self._is_awaited(expr),
                "context_usage": context_usage,
                "receiver_name": receiver_name,
                "method_name": method_name,
            }
        if isinstance(expr, cst.Attribute):
            return {
                "origin_kind": "access",
                "qualified_name": self._first_qualified_name(expr),
                "awaited": awaited,
                "context_usage": context_usage,
            }
        if isinstance(expr, cst.Name):
            return {
                "origin_kind": "name",
                "referenced_name": expr.value,
                "awaited": awaited,
                "context_usage": context_usage,
            }
        return None

    def _first_qualified_name(self, node):
        q_names = self.get_metadata(QualifiedNameProvider, node, default=())
        names = [q.name for q in q_names]
        return names[0] if names else _name_to_str(node)

    def _is_call_target_attribute(self, node: cst.Attribute) -> bool:
        parent = self.get_metadata(ParentNodeProvider, node, default=None)
        return isinstance(parent, cst.Call) and parent.func is node

    def _is_awaited(self, node) -> bool:
        current = node
        while True:
            parent = self.get_metadata(ParentNodeProvider, current, default=None)
            if parent is None:
                return False
            if isinstance(parent, cst.Await):
                return True
            if isinstance(parent, (cst.Arg, cst.Assign, cst.AnnAssign, cst.Expr, cst.Return, cst.WithItem)):
                current = parent
                continue
            return False

    def _is_in_decorator(self, node) -> bool:
        current = node
        visited = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            parent = self.get_metadata(ParentNodeProvider, current, default=None)
            if isinstance(parent, cst.Decorator):
                return True
            current = parent
        return False

    def _context_usage(self, node) -> str:
        current = node
        while current is not None:
            parent = self.get_metadata(ParentNodeProvider, current, default=None)
            if isinstance(parent, cst.WithItem) and parent.item is current:
                grandparent = self.get_metadata(ParentNodeProvider, parent, default=None)
                if isinstance(grandparent, cst.With):
                    return "async_with" if grandparent.asynchronous is not None else "with"
                return "none"
            current = parent
        return "none"


def collect_facts(source_code: str):
    """Parse a module and collect imports, calls, accesses, and local bindings."""
    tree = cst.parse_module(source_code)
    wrapper = MetadataWrapper(tree)
    collector = FactCollector()
    wrapper.visit(collector)
    return collector.facts

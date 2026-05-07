import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from migration_checker.rules import RuleResolutionError, load_rules, resolve_rule_file


class RuleResolverTests(unittest.TestCase):
    def test_resolve_rule_file_finds_current_requests_pair(self):
        resolved = resolve_rule_file(
            source="requests",
            target="aiohttp",
            project_root=REPO_ROOT,
        )

        self.assertEqual(resolved.name, "requests_to_aiohttp.yml")
        self.assertEqual(resolved.parent.name, "http_clients")

    def test_resolve_rule_file_rejects_missing_pair(self):
        with self.assertRaises(RuleResolutionError) as context:
            resolve_rule_file(
                source="requests",
                target="definitely_missing_target",
                project_root=REPO_ROOT,
            )

        self.assertIn("No rule file found", str(context.exception))

    def test_resolve_rule_file_rejects_ambiguous_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_root = Path(temp_dir)
            (rules_root / "domain_a").mkdir()
            (rules_root / "domain_b").mkdir()
            (rules_root / "domain_a" / "requests_to_aiohttp.yml").write_text("pair: {}\n", encoding="utf-8")
            (rules_root / "domain_b" / "requests_to_aiohttp.yml").write_text("pair: {}\n", encoding="utf-8")

            with self.assertRaises(RuleResolutionError) as context:
                resolve_rule_file(
                    source="requests",
                    target="aiohttp",
                    rules_root=rules_root,
                )

        self.assertIn("Multiple rule files match", str(context.exception))


class RuleContentTests(unittest.TestCase):
    def test_load_rules_accepts_finalized_schema(self):
        rules = load_rules(REPO_ROOT / "rules/http_clients/requests_to_aiohttp.yml")

        self.assertEqual(rules["pair"]["source"], "requests")
        self.assertEqual(rules["pair"]["target"], "aiohttp")
        self.assertIn("requests", rules["source_roots"])
        self.assertIn("aiohttp", rules["target_roots"])
        self.assertTrue(rules["call_rules"])
        self.assertTrue(rules["import_rules"])

    def test_load_rules_rejects_legacy_schema(self):
        legacy_text = """
pair:
  source: requests
  target: aiohttp
source_symbols:
  - requests.get
target_symbols:
  - aiohttp.ClientSession.get
mappings:
  - from: requests.get
    to: aiohttp.ClientSession.get
suspicious_attributes:
  - text
diagnostics:
  leftover_source_import: error
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            rule_path = Path(temp_dir) / "legacy.yml"
            rule_path.write_text(legacy_text, encoding="utf-8")
            with self.assertRaises(RuleResolutionError) as context:
                load_rules(rule_path)

        self.assertIn("legacy rule schema is not supported", str(context.exception))

    def test_requests_aiohttp_rule_covers_common_http_surface(self):
        rules = load_rules(REPO_ROOT / "rules/http_clients/requests_to_aiohttp.yml")
        mapped_targets = {rule["target"]["symbol"] for rule in rules["call_rules"]}
        get_rule = rules["call_rule_by_target"]["aiohttp.ClientSession.get"]

        self.assertIn("requests.request", rules["source_call_symbols"])
        self.assertIn("aiohttp.ClientSession.request", mapped_targets)
        self.assertEqual(get_rule["return"]["renamed_attributes"]["status_code"], "status")
        self.assertIn("text", get_rule["return"]["await_required_methods"])
        self.assertEqual(get_rule["usage"]["context_manager"], "async_with")

    def test_argparse_click_rule_covers_subcommands_and_arguments(self):
        rules = load_rules(REPO_ROOT / "rules/utility/argparse_to_click.yml")
        mapped_targets = {rule["target"]["symbol"] for rule in rules["call_rules"]}

        self.assertIn("argparse.ArgumentParser.add_subparsers", rules["source_call_symbols"])
        self.assertIn("click.group", mapped_targets)
        self.assertIn("click.argument", rules["target_call_symbols"])

    def test_flask_quart_rule_covers_template_and_abort_paths(self):
        rules = load_rules(REPO_ROOT / "rules/web_framework/flask_to_quart.yml")
        mapped_targets = {rule["target"]["symbol"] for rule in rules["call_rules"]}

        self.assertIn("flask.render_template", rules["source_call_symbols"])
        self.assertIn("quart.render_template", mapped_targets)
        self.assertIn("quart.abort", mapped_targets)
        self.assertIn("quart.request.get_json", rules["target_access_symbols"])

    def test_load_rules_rejects_invalid_call_usage_contract(self):
        invalid_text = """
pair:
  source: source_lib
  target: target_lib
libraries:
  source_roots: [source_lib]
  target_roots: [target_lib]
diagnostics:
  severity:
    leftover_source_import: error
    leftover_source_call: error
    renamed_keyword_argument: error
    forbidden_keyword_argument: error
    missing_required_keyword: error
    positional_argument_misuse: error
    missing_await: error
    missing_context_manager: error
    mixed_source_target_assignment: warning
    renamed_attribute_access: error
    forbidden_attribute_access: error
    duplicate_migration_usage: warning
rules:
  - id: source-import-root
    kind: import
    source:
      module: source_lib
    target:
      module: target_lib
  - id: bad-call
    kind: call
    source:
      symbol: source_lib.run
    target:
      symbol: target_lib.run
    contract:
      renamed_keywords: {}
      forbidden_keywords: []
      required_keywords: []
      require_keywords: false
    usage:
      await_required: false
      context_manager: sometimes
    return:
      tag: result
      renamed_attributes: {}
      forbidden_attributes: []
      await_required_methods: []
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            rule_path = Path(temp_dir) / "invalid_call.yml"
            rule_path.write_text(invalid_text, encoding="utf-8")
            with self.assertRaises(RuleResolutionError) as context:
                load_rules(rule_path)

        self.assertIn("usage.context_manager", str(context.exception))

    def test_load_rules_rejects_invalid_access_usage_contract(self):
        invalid_text = """
pair:
  source: source_lib
  target: target_lib
libraries:
  source_roots: [source_lib]
  target_roots: [target_lib]
diagnostics:
  severity:
    leftover_source_import: error
    leftover_source_call: error
    renamed_keyword_argument: error
    forbidden_keyword_argument: error
    missing_required_keyword: error
    positional_argument_misuse: error
    missing_await: error
    missing_context_manager: error
    mixed_source_target_assignment: warning
    renamed_attribute_access: error
    forbidden_attribute_access: error
    duplicate_migration_usage: warning
rules:
  - id: source-import-root
    kind: import
    source:
      module: source_lib
    target:
      module: target_lib
  - id: bad-access
    kind: access
    source:
      symbol: source_lib.request.form
    target:
      symbol: target_lib.request.get_json
    usage:
      await_required: false
      access_kind: property
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            rule_path = Path(temp_dir) / "invalid_access.yml"
            rule_path.write_text(invalid_text, encoding="utf-8")
            with self.assertRaises(RuleResolutionError) as context:
                load_rules(rule_path)

        self.assertIn("usage.access_kind", str(context.exception))


if __name__ == "__main__":
    unittest.main()
